import os
import binascii
import struct

import bpy

from vray_blender.plugins.BRDF import BRDFScanned
from vray_blender.nodes.tools import calculateTreeBounds, deselectNodes, rearrangeTree
from vray_blender.lib import attribute_utils, attribute_types
from vray_blender.lib import path_utils
from vray_blender.plugins import getPluginModule
from vray_blender.plugins.skipped_plugins import SKIPPED_PLUGINS
from vray_blender.plugins.texture.TexRemap import getCurvesNode
from vray_blender.nodes import utils as NodeUtils
from vray_blender.nodes.sockets import addInput, getHiddenInput
from vray_blender import debug
from vray_blender.exporting.tools import isColorSocket, getVRayBaseSockType, getInputSocketByName, getInputSocketByAttr
from vray_blender.exporting.node_exporters.uvw_node_export import UVWGenRandomizerModes
from vray_blender.lib.names import syncObjectUniqueName

from numpy import allclose
from mathutils import Matrix, Color, Vector


# Some plugins that we import should be remapped to other plugins, e.g. TexBezierCurve to TexRemap.
# For these, we must also remap the output sockets. It is not convenient to add an additional return
# value from createNode() for the socket as these cases are very rare, so describe them here
_REMAPPED_OUTPUT_SOCKETS = {
    'TexBezierCurve': {
        'Color' : 'Out Color'
    },
    'TexBezierCurveColor': {
        'Color' : 'Out Color'
    }
}

# The plugins below are included in the SKIPPED_PLUGINS list but we need to create
# some kind of node for them - either include in the node associated with another
# plugin or remap to a different plugin
_UNSUPPORTED_PLUGINS = set(SKIPPED_PLUGINS).difference({
    'TexBitmap', 'TexBezierCurve', 'TexBezierCurveColor'
})

class ImportContext:
    def __init__(self, nodeTree: bpy.types.NodeTree, vrsceneDict: dict, isConversion = False, locationsMap = None):
        self.isConversion = isConversion
        self.nodeTree = nodeTree
        self.vrsceneDict = vrsceneDict
        self.locationsMap = locationsMap

    def resolvePath(self, path):
        if self.locationsMap and path in self.locationsMap:
            path = self.locationsMap[path]

        return path

   
def convertMaterial(material: bpy.types.Material, operator: bpy.types.Operator):
    from vray_blender.exporting.mtl_export import MtlExporter
    from vray_blender.exporting.plugin_tracker import FakeScopedNodeTracker, FakeObjTracker
    from vray_blender.exporting.tools import FakeTimeStats
    from vray_blender.lib.defs import ExporterContext, AttrPlugin, ExporterContext, RendererMode, NodeContext, ExporterType, PluginDesc
    from vray_blender.engine import NODE_TRACKERS, OBJ_TRACKERS

    syncObjectUniqueName(material, reset=False)
    syncObjectUniqueName(material.original, reset=False)

    # Remove any pre-existing V-Ray nodes from the tree to avoid confusion.
    # It is the responsibility of the caller of this function to notify the user
    # that existing nodes will be deleted.
    _removeVRayNodes(material.node_tree)

    _fakeNodeTrackers = dict([(t, FakeScopedNodeTracker()) for t in NODE_TRACKERS])
    _fakeObjTrackers = dict([(t, FakeObjTracker()) for t in OBJ_TRACKERS])

    exporterContext = ExporterContext()
    exporterContext.rendererMode = RendererMode.Preview
    exporterContext.objTrackers  = _fakeObjTrackers
    exporterContext.nodeTrackers = _fakeNodeTrackers
    exporterContext.ctx          = bpy.context
    exporterContext.fullExport   = True
    exporterContext.ts           = FakeTimeStats()

    vrsceneDict = []
    # Custom handler that converts exportable plugins to an importable vrscene dict.
    def _vrsceneDictCollector(nodeCtx: NodeContext, pluginDesc: PluginDesc):
        for key, value in pluginDesc.attrs.items():
            if isinstance(value, AttrPlugin):
                if value.output:
                    pluginDesc.attrs[key] = value.name+"::"+value.output
                else:
                    pluginDesc.attrs[key] = value.name
            if isinstance(value, list) and all(isinstance(item, AttrPlugin) for item in value):
                plNames = []
                for pl in value:
                    plNames.append(pl.name)
                pluginDesc.attrs[key] = plNames
        vrsceneDict.append({
            "ID"         : pluginDesc.type,
            "Name"       : pluginDesc.name,
            "Attributes" : pluginDesc.attrs,
        })
        return AttrPlugin(pluginDesc.name, pluginType=pluginDesc.type)

    nodeCtx = NodeContext(exporterContext, bpy.context.scene, bpy.data, None)
    nodeCtx.rootObj = material
    nodeCtx.nodeTracker = _fakeNodeTrackers['MTL']
    nodeCtx.ntree = material.node_tree
    nodeCtx.customHandler = _vrsceneDictCollector

    def reportErrors():
        # Intentionally print the warnings here manually. They won't be reported since we use a preview context.
        if (errors := NodeContext.getErrors()):
            msg = 'Error while converting ' + material.name
            for err in errors:
                msg += '\n\t ' + err
            operator.report({ 'WARNING' }, msg)

    mtlExporter = MtlExporter(exporterContext)
    exportedMaterial, _ = mtlExporter.exportMtl(material, nodeCtx)
    if not exportedMaterial:
        reportErrors()
        return

    material.use_nodes = True
    material.vray.is_vray_class = True
    material.node_tree.vray.tree_type = 'MATERIAL'
    importContext = ImportContext(material.node_tree, vrsceneDict, isConversion=True)
    _convertMaterialFromDict(importContext)

    syncObjectUniqueName(material, reset=False)
    syncObjectUniqueName(material.original, reset=False)

    reportErrors()

def _convertMaterialFromDict(importContext: ImportContext):
    fixPluginParams(importContext.vrsceneDict, True)
    deselectNodes(importContext.nodeTree)

    endNodePluginDesc = None
    for plgDesc in importContext.vrsceneDict:
        if plgDesc['ID']=='MtlSingleBRDF':
            endNodePluginDesc = plgDesc
    if endNodePluginDesc is None:
        return

    brdfName = endNodePluginDesc['Attributes']['brdf']
    matPlugin = getPluginByName(importContext.vrsceneDict, brdfName)
    mtlNode = createNode(importContext, matPlugin)

    outputNode = importContext.nodeTree.nodes.new('VRayNodeOutputMaterial')
    importContext.nodeTree.links.new(mtlNode.outputs['BRDF'], outputNode.inputs['Material'])

    bounds = calculateTreeBounds(importContext.nodeTree)
    rearrangeTree(importContext.nodeTree, outputNode, bounds=bounds)

    return {'FINISHED'}


def _removeVRayNodes(nodeTree: bpy.types.NodeTree):
    from vray_blender.nodes.tools import isVrayNode

    vrayNodes = [n for n in nodeTree.nodes if isVrayNode(n)]
    
    for n in vrayNodes:
        nodeTree.nodes.remove(n)


def fixPluginParams(vrsceneDict: dict, materialAssetsOnly: bool):
    """ Fix any plugin parameters that need special handling.

    materialAssetsOnly(bool) : True if the asset being imported is a material (has no geometry).
                               Geometry models have their channels explicitly set so we don't want
                               to change the indices.
    """

    if materialAssetsOnly:
        for uvwGenChannel in [v for v in vrsceneDict if v['ID'] == 'UVWGenChannel']:
            # V4B and Cosmos materials often disagree on what the indexes of the UVW maps should be.
            # Cosmos usually starts from 1 while V4B expects 0. This is fine for geometry assets imported
            # from Cosmos, but the materials cannot be assigned to non-cosmos objects without modification.
            # If just a material is being imported, it is safe to fix the UVW channel numbers so that Blender
            # objects could render correctly with the imported material.
            #   Set to -1 which is a special value designating the default UVW channel
            uvwGenChannel['Attributes']['uvw_channel'] = -1

    for vrayMtl in [p for p in vrsceneDict if p['ID'] == 'BRDFVRayMtl']:
        attrs = vrayMtl['Attributes']
        if (value := attrs.get('gtr_energy_compensation', None)) is not None:
            if type(value) is bool:
                # Upgrade from the older version, set to the new default
                vrayMtl['Attributes']['gtr_energy_compensation'] = '2'

        if (opacity := attrs.get('opacity', None)) != None:
            if type(opacity) is str:
                # The VRayMtl node shows only the 'opacity_color' to which both color and float
                # inputs can be attached. The export code then determines whether to export
                # 'opacity' or 'opacity_color' based on the type of the connected socket.
                # If the 'opacity' socket is connected in the imported material, reconnect the
                # link to the 'opacity_color' socket.
                attrs['opacity_color'] = opacity
                attrs['opacity_source'] = 1 # set opacity_color as the valid opacity source
                del attrs['opacity']
            elif isinstance(opacity, (int, float)):
                attrs['opacity_color'] = Color((opacity, opacity, opacity))
                attrs['opacity_source'] = 1 # set opacity_color as the valid opacity source
                del attrs['opacity']

        if fogMult := attrs.get("fog_mult", None):
            # Invert the fog_mult during import too similar to the logic in the exporter.
            fogDepthValue = 1.0/fogMult if fogMult > 1e-6 else 0.0
            vrayMtl['Attributes']['fog_mult'] = fogDepthValue

    for brdfBump in [p for p in vrsceneDict if p['ID'] == 'BRDFBump']:
        attrs = brdfBump['Attributes']
        if (value := attrs.get('bump_tex', None)) is not None:
            attrs['bump_tex_color'] = attrs['bump_tex']
            del attrs['bump_tex']

        if (value := attrs.get('bump_tex_mult', None)) is not None:
            attrs['bump_tex_mult_tex'] = attrs['bump_tex_mult']
            del attrs['bump_tex_mult']

        if 'bump_object_space' not in attrs:
            attrs['bump_object_space'] = False


def _getDefaultOutputSocketName(pluginType: str):
    """ Returns the name of the default output socket for this plugin type. """

    pluginModule = getPluginModule(pluginType)

    # Get the Blender name of sockets redefined in Node.output_sockets section of the plugin module description 
    if outputSockets := pluginModule.Node.get('output_sockets'):
        if defaultOutput := next((o for o in outputSockets if o['name'] == '_default_'), None):
            if 'label' in defaultOutput:
                return defaultOutput['label']

    # No explicit name has been found, return the generic name for the plugin type
    if pluginModule.TYPE == 'MATERIAL':
        return "Material"
    elif pluginModule.TYPE == 'UVWGEN':
        return "Mapping"
    elif pluginModule.TYPE == 'BRDF':
        return "BRDF"
    elif pluginModule.TYPE == 'GEOMETRY':
        return "Geometry"
    elif pluginModule.TYPE == 'EFFECT':
        return "Output"
    elif pluginModule.TYPE == 'RENDERCHANNEL':
        return "Channel"
    elif pluginModule.TYPE == 'TEXTURE':
        return "Color"



def getPluginByName(vrsceneDict, pluginName):
    for pluginDesc in vrsceneDict:
        if pluginDesc['Name'] == pluginName:
            return pluginDesc
    return None


def getPluginByType(vrsceneDict, pluginType):
    for pluginDesc in vrsceneDict:
        if pluginDesc['ID'] == pluginType:
            return pluginDesc
    return None


def _getInputSocketNameByAttr(pluginModule, attrName: str):
    """ Return the Blender name of an input socket given its V-Ray attribute name. """
    if attrDesc := attribute_utils.getAttrDesc(pluginModule, attrName):
        return attrDesc.get('name', attribute_utils.getAttrDisplayName(attrDesc))
    
    return None


def getOutputSocketByAttr(node: bpy.types.Node, attrName:str):
    """ Return an output socket of a node given its V-Ray attribute name. """
    # (Iavor) Not sure if the lower() conversion is nedessary, but the original code was
    # written like this and checking all Cosmos assets will be too time consuming.
    return next((s for s in node.outputs if s.vray_attr.lower() == attrName.lower()), None)


def _getOutputSocketNameByAttr(pluginType: str, outputAttrName: str):
    """ Return the Blender name of an output socket given its V-Ray attribute name """
    pluginModule = getPluginModule(pluginType)
    
    if outputSockets := pluginModule.Node.get('output_sockets'):
        if outputSocketDesc := next((o for o in outputSockets if o['name'] == outputAttrName), None):
            if label := outputSocketDesc.get('label'):
                return label
            else:
                return attribute_utils.formatAttributeName(outputSocketDesc)['name']
    
    else:
        pluginOutputParams = [a for a in pluginModule.Parameters if a['type'] in attribute_types.NodeOutputTypes]
        if outputSocketDesc := next((o for o in pluginOutputParams if o['attr'] == outputAttrName), None):
            return attribute_utils.formatAttributeName(outputSocketDesc['attr'])
    
    assert False, f"Plugin {pluginType} does not have output attribute {outputAttrName}" 


def _createNodeBRDFLayered(importContext: ImportContext, pluginDesc):
    from vray_blender.plugins.BRDF.BRDFLayered import getLayerSocketNames

    def processSocket(inputSocket, attrValue):
        # Could happen with some broken files
        if not attrValue:
            return

        connectedPlugin, connectedOutput = _getPluginFromLink(importContext, attrValue)
        
        if connectedPlugin:
            _createLinkedNode(importContext, inputSocket, connectedOutput, connectedPlugin)
        else:
             inputSocket.value = attrValue

    brdfs     = pluginDesc['Attributes'].get('brdfs')
    weights   = pluginDesc['Attributes'].get('weights')
    opacities = pluginDesc['Attributes'].get('opacities')

    brdfLayeredNode = NodeUtils.createNode(importContext.nodeTree, 'VRayNodeBRDFLayered', pluginDesc['Name'])
    _fillNodeProperties(importContext, brdfLayeredNode, pluginDesc, "BRDFLayered")

    if brdfs:
        for i, brdf in enumerate(brdfs[:-1]):
            humanIndex = i + 1

            brdfSockName, weightSockName, opacitySockName = getLayerSocketNames(humanIndex)

            # NOTE: Node already has two inputs
            if not brdfSockName in brdfLayeredNode.inputs:
                addInput(brdfLayeredNode, 'VRaySocketBRDF',   brdfSockName)
                addInput(brdfLayeredNode, 'VRaySocketColor',  weightSockName).setValue((1.0, 1.0, 1.0))
                addInput(brdfLayeredNode, 'VRaySocketWeight', opacitySockName).setValue(1.0)

            brdfSocket   = brdfLayeredNode.inputs[brdfSockName]
            weightSocket = brdfLayeredNode.inputs[weightSockName]
            opacitySocket = brdfLayeredNode.inputs[weightSockName]

            processSocket(brdfSocket, brdf)

            # NOTE: 'weights' could be optional
            if weights:
                processSocket(weightSocket, weights[i])

            if opacities:
                processSocket(opacitySocket, opacities[i])

        brdfSock = getInputSocketByName(brdfLayeredNode, "Base Material")
        processSocket(brdfSock, brdfs[-1])

    return brdfLayeredNode


def _createNodeBRDFScanned(importContext: ImportContext, pluginDesc):
    brdfScannedNode = NodeUtils.createNode(importContext.nodeTree, 'VRayNodeBRDFScanned', pluginDesc["Name"])

    _fillNodeProperties(importContext, brdfScannedNode, pluginDesc, "BRDFScanned")

    scannedFile = pluginDesc['Attributes'].get('file')
    brdfScannedNode.BRDFScanned.file = importContext.resolvePath(scannedFile)
    BRDFScanned.onFileUpdate(brdfScannedNode.BRDFScanned)
    return brdfScannedNode


def loadImage(imageFilepath, importDir, bitmapTexture, makeRelative=False):
    if imageFilepath is not None:
        if not os.path.exists(imageFilepath):
            debug.printInfo("Couldn't find file: %s" % imageFilepath)
            debug.printInfo("Trying to search under import directory...")

            # NOTE: Windows style filepath could be stored here
            # Convert to UNIX slashes
            imageFilepath = path_utils.unifyPath(imageFilepath)

            if importDir:
                imageFilepath = os.path.join(importDir, os.path.basename(imageFilepath))

        if not os.path.exists(imageFilepath):
            debug.printError("Unable to find file: %s" % imageFilepath)
        else:
            imageBlockName = bpy.path.display_name_from_filepath(imageFilepath)

            if imageBlockName in bpy.data.images:
                bitmapTexture.image = bpy.data.images[imageBlockName]
            else:
                bitmapTexture.image = bpy.data.images.load(imageFilepath)
                bitmapTexture.image.name = imageBlockName

            if makeRelative:
                bitmapTexture.image.filepath = bpy.path.relpath(bitmapTexture.image.filepath)


def _pluginAttrsToNodeProps(pluginDesc, node):
    pluginType = pluginDesc['ID']

    assert hasattr(node, pluginType)

    propGroup = getattr(node, pluginType)
    pluginModule = getPluginModule(pluginType)

    for attrName in pluginDesc['Attributes']:
        attrDesc  = attribute_utils.getAttrDesc(pluginModule, attrName)

        attrValue = pluginDesc['Attributes'][attrName]

        if hasattr(propGroup, attrName):
            if attrDesc['type'] == 'ENUM' and not attribute_utils.valueInEnumItems(attrDesc, str(attrValue)):
                debug.printError(f"Unsupported ENUM value '{str(attrValue)}' for attribute: {pluginType}.{attrName}")
                continue

            attrType = type(getattr(propGroup, attrName))
            attrValue = attrType(attrValue)
            setattr(propGroup, attrName, attrValue)


def _createNodeTexBitmap(importContext: ImportContext, pluginDescTexBitmap):
    imageTextureNode = NodeUtils.createNode(importContext.nodeTree, 'VRayNodeMetaImageTexture', pluginDescTexBitmap['Name'])

    bitmapBufferName = pluginDescTexBitmap['Attributes']['bitmap']
    pluginDescBitmapBuffer = getPluginByName(importContext.vrsceneDict, bitmapBufferName)

    bitmapTexture = imageTextureNode.texture

    imageFilepath = pluginDescBitmapBuffer['Attributes'].get('file')
    imageFilepath = importContext.resolvePath(imageFilepath)

    importSettings = getPluginByName(importContext.vrsceneDict, "Import Settings")
    importDir = None
    if importSettings:
        importDir = importSettings['Attributes']['dirpath']

    loadImage(imageFilepath, importDir, bitmapTexture)

    # Filling the Mapping Socket of Texture
    if "uvwgen" in pluginDescTexBitmap['Attributes']:
        connectedPlugin, outputSocketName = _getPluginConnectedToInput(importContext, "uvwgen", pluginDescTexBitmap)
        if connectedPlugin:
            connectedNode = createNode(importContext, connectedPlugin)
            if connectedNode:
                importContext.nodeTree.links.new(connectedNode.outputs[outputSocketName], imageTextureNode.inputs["Mapping"])

    _pluginAttrsToNodeProps(pluginDescTexBitmap, imageTextureNode)
    _pluginAttrsToNodeProps(pluginDescBitmapBuffer, imageTextureNode)

    return imageTextureNode


CollapsibleTypes = {
    'TexAColor': 'texture',
    'TexFloatToColor': 'input',
    'TexColorToFloat': 'input',
    'TexInvertFloat': 'texture',
    'TexInvert': 'texture',
    'FloatToTex': 'input'
}


def _collapseToValue(importContext: ImportContext, pluginDesc: dict):
    """ Transforms plugin from 'CollapsibleTypes' to a simple value (either float or list of floats). """

    pluginType    = pluginDesc['ID']
    pluginAttrs = pluginDesc['Attributes']

    if pluginType not in CollapsibleTypes:
        return None

    value = None
    valueAttr = CollapsibleTypes[pluginType]
    pluginValue = pluginAttrs[valueAttr]

    if _isPluginLink(pluginValue):
        plugin, _ = _getPluginFromLink(importContext, pluginValue) 
        value = _collapseToValue(importContext, plugin)
    else:
        if pluginType == 'TexFloatToColor':
            value = Color((pluginValue, pluginValue, pluginValue))
        elif pluginType == 'TexColorToFloat':
            return (pluginValue[0] + pluginValue[1] + pluginValue[2]) / 3.0
        elif pluginType == 'TexInvertFloat':
            return 1.0 - pluginValue
        elif pluginType == 'TexInvert':
            return Color((1.0 - pluginValue[0], 1.0 - pluginValue[1], 1.0 - pluginValue[2]))
        else:
            value = pluginValue
    return value


def _fillRamp(importContext: ImportContext, ramp: bpy.types.ColorRamp, colors, positions, interpolation):
    rampElements = []

    for col, pos in zip(colors, positions):
        rampElement = {
            'color'    : col,
            'position' : pos,
        }

        for key in rampElement:
            value = rampElement[key]

            if _isPluginLink(value):
                conPlugin   = getPluginByName(importContext.vrsceneDict, value)
                conPluginType = conPlugin['ID']

                if conPluginType not in CollapsibleTypes:
                    debug.printError(f"Plugin '{conPluginType}': Unsupported parameter value! This shouldn't happen! Please, report this!")
                    rampElement[key] = None
                else:
                    rampElement[key] = _collapseToValue(importContext, conPlugin)

        rampElements.append(rampElement)

    # Create ramp elements
    # Ramp already has 2 elements
    elementsToCreate = len(rampElements) - 2
    for i in range(elementsToCreate):
        # We will setup proper position later
        ramp.elements.new(0.0)

    if interpolation == 1:
        ramp.interpolation = 'LINEAR'
    elif interpolation == 0:
        ramp.interpolation = 'CONSTANT'
    else:
        ramp.interpolation = 'EASE'

    # Setup elements values
    elementStep = 1.0 / len(ramp.elements)
    for i, rampElement in enumerate(rampElements):
        col = rampElement['color']
        if col is None:
            col = (1.0,1.0,1.0)

        pos = rampElement['position']
        if pos is None:
            pos = i * elementStep

        el = ramp.elements[i]
        # TODO: Alpha?
        el.color    = [col[0], col[1], col[2], 1.0]
        el.position = pos


def _createNodeTexGradRamp(importContext: ImportContext, pluginDesc: dict):
    texGradRamp = NodeUtils.createNode(importContext.nodeTree, 'VRayNodeTexGradRamp', pluginDesc['Name'])

    _fillNodeProperties(importContext, texGradRamp, pluginDesc, 'TexGradRamp')
    attributes = pluginDesc['Attributes']

    _fillRamp(importContext,
        texGradRamp.texture.color_ramp,
        attributes['colors'],
        attributes['positions'],
        attributes['interpolation']
    )

    return texGradRamp

def _importRemapSplineData(positions, values, interpolations, curve: bpy.types.CurveMap):
    numKnots = min(len(positions), len(values), len(interpolations))
    for i in range(numKnots):
        # Splines in Blender will always have at least 2 points.
        if i < 2:
            p = curve.points[i]
            p.location.x = positions[i]
            p.location.y = values[i]
        else:
            p = curve.points.new(positions[i], values[i])
        p.handle_type = 'VECTOR' if interpolations[i] in (0, 1) else 'AUTO'

    
def _fillRemap(texRemap, mapping: bpy.types.CurveMapping, attributes: dict):
    if attributes.get("remapType", 0) == 0:
        redPositions = attributes['red_positions']
        redValues = attributes['red_values']
        redInterpolations = attributes['red_types']
        greenPositions = attributes['green_positions']
        greenValues = attributes['green_values']
        greenInterpolations = attributes['green_types']
        bluePositions = attributes['blue_positions']
        blueValues = attributes['blue_values']
        blueInterpolations = attributes['blue_types']

        def _compareFloatLists(pos1, pos2):
            return len(pos1) == len(pos2) and allclose(pos1, pos2)
        if _compareFloatLists(redPositions, greenPositions) and _compareFloatLists(greenPositions, bluePositions)\
            and _compareFloatLists(redValues, greenValues) and _compareFloatLists(greenValues, blueValues)\
            and redInterpolations == blueInterpolations == greenInterpolations:
                texRemap.color_spline_type = "0"
                _importRemapSplineData(redPositions, redValues, redInterpolations, mapping.curves[3])
        else:
            _importRemapSplineData(redPositions, redValues, redInterpolations, mapping.curves[0])
            _importRemapSplineData(greenPositions, greenValues, greenInterpolations, mapping.curves[1])
            _importRemapSplineData(bluePositions, blueValues, blueInterpolations, mapping.curves[2])
            texRemap.color_spline_type = "1"
    else:
        huePositions = attributes['hue_positions']
        hueValues = attributes['hue_values']
        hueInterpolations = attributes['hue_types']
        saturationPositions = attributes['saturation_positions']
        saturationValues = attributes['saturation_values']
        saturationInterpolations = attributes['saturation_types']
        valuePositions = attributes['value_positions']
        valueValues = attributes['value_values']
        valueInterpolations = attributes['value_types']

        _importRemapSplineData(huePositions, hueValues, hueInterpolations, mapping.curves[0])
        _importRemapSplineData(saturationPositions, saturationValues, saturationInterpolations, mapping.curves[1])
        _importRemapSplineData(valuePositions, valueValues, valueInterpolations, mapping.curves[2])


def _fillRemapFromBezierCurve(texRemap, mapping: bpy.types.CurveMapping, attributes: dict):
    
    attrPoints = attributes['points']
    
    redPositions = [float(p) for p in attrPoints[::6]]
    redValues    = [float(v) for v in attrPoints[1::6]]
    types        = [4] * len(attributes['types']) # Set all point types to Bezier
    
    remapAttributes = {
        'remapType': 0,
        'red_positions':   redPositions,
        'red_values':      redValues,
        'red_types':       types,
        'green_positions': redPositions,
        'green_values':    redValues,
        'green_types':     types,
        'blue_positions':  redPositions,
        'blue_values':     redValues,
        'blue_types':      types
    }

    _fillRemap(texRemap, mapping, remapAttributes)


def _fillRemapFromBezierCurveColor(texRemap, mapping: bpy.types.CurveMapping, attributes: dict):
    def getPoints(attrPoints):
        return [float(p) for p in attrPoints[::6]]

    def getValues(attrPoints):
        return [float(v) for v in attrPoints[1::6]]

    def getTypes(attrTypes):
        return [4] * len(attrTypes) # Set all point types to Bezier

    # The red points should always be defined. The green and blue may be skipped,
    # convert to greyscale in this case
    redPoints  = getPoints(attributes['points_r'])
    greenPoints = getPoints(attributes.get('points_g', attributes['points_r']))
    bluePoints = getPoints(attributes.get('points_b', attributes['points_r']))

    redTypes = getTypes(attributes['types_r'])
    greenTypes = getTypes(attributes.get('types_g', attributes['types_r']))
    blueTypes = getTypes(attributes.get('types_b', attributes['types_r']))

    remapAttributes = {
        'remapType': 0,
        'red_positions':   getPoints(redPoints),
        'red_values':      getValues(redPoints),
        'red_types':       getTypes(redTypes),
        'green_positions': getPoints(greenPoints),
        'green_values':    getValues(greenPoints),
        'green_types':     getTypes(greenTypes),
        'blue_positions':  getPoints(bluePoints),
        'blue_values':     getValues(bluePoints),
        'blue_types':      getTypes(blueTypes),
    }

    _fillRemap(texRemap, mapping, remapAttributes)


def _createNodeTexRemap(importContext: ImportContext, pluginDesc: dict):
    
    def _removeFromDict(dictionary, filterFn):
        toRemove = []
        for attr in dictionary:
            if filterFn(attr):
                toRemove.append(attr)
        for attr in toRemove:
            del dictionary[attr]

    texRemapNode = NodeUtils.createNode(importContext.nodeTree, 'VRayNodeTexRemap', pluginDesc['Name'])

    attributes = pluginDesc['Attributes']
    curvesNode = getCurvesNode(texRemapNode)
    _fillRemap(
        texRemapNode.TexRemap,
        curvesNode.mapping,
        attributes
    )

    _removeFromDict(attributes, lambda attr: '_types' in attr)
    _fillNodeProperties(importContext, texRemapNode, pluginDesc, 'TexRemap')

    return texRemapNode


def _createNodeTexRemapFromBezierCurve(importContext: ImportContext, pluginDesc: dict, isColor: False):
    texRemapNode = NodeUtils.createNode(importContext.nodeTree, 'VRayNodeTexRemap', pluginDesc['Name'])

    attributes = pluginDesc['Attributes']
    curvesNode = getCurvesNode(texRemapNode)
    
    if isColor:
        _fillRemapFromBezierCurveColor(texRemapNode.TexRemap, curvesNode.mapping, attributes)
    else:
        _fillRemapFromBezierCurve(texRemapNode.TexRemap, curvesNode.mapping, attributes)

    # Manually import connected inputs because we have created a different type of plugin and
    # the automatic walk won't find the correct sockets
    inputAttrName = 'input_color' if isColor else 'input_float'
    connectedPlugin, connectedOutput = _getPluginConnectedToInput(importContext, inputAttrName, pluginDesc)

    if connectedPlugin:
        inputColorSocket = getInputSocketByAttr(texRemapNode, 'input_color')
        _createLinkedNode(importContext, inputColorSocket, connectedOutput, connectedPlugin)

    return texRemapNode


def _createNodeTexVectorProduct(importContext: ImportContext, pluginDesc: dict):
    texVectorProductNode = NodeUtils.createNode(importContext.nodeTree, 'VRayNodeTexVectorProduct', pluginDesc['Name'])

    _fillNodeProperties(importContext, texVectorProductNode, pluginDesc, 'TexVectorProduct')

    attributes = pluginDesc['Attributes']
    if (inp1 := attributes.get("input1")) and isinstance(inp1, Color):
        vectorNode = NodeUtils.createNode(importContext.nodeTree, 'VRayNodeVector')
        vectorNode.value = Vector(inp1[:])
        importContext.nodeTree.links.new(vectorNode.outputs[0], getInputSocketByAttr(texVectorProductNode, "input1"))
    return texVectorProductNode

def _createNodeTexLayered(importContext: ImportContext, pluginDesc: dict):
    from vray_blender.nodes.specials.texture import VRayNodeTexLayeredMax

    textures = pluginDesc['Attributes'].get('textures')
    masks = pluginDesc['Attributes'].get('masks')
    opacities = pluginDesc['Attributes'].get('opacities')
    blendModes = pluginDesc['Attributes'].get('blend_modes')

    texLayeredNode = NodeUtils.createNode(importContext.nodeTree, 'VRayNodeTexLayeredMax', pluginDesc['Name'])
    if masks:
        del pluginDesc['Attributes']['masks']
    if textures:
        del pluginDesc['Attributes']['textures']
    if opacities:
        del pluginDesc['Attributes']['opacities']
    _fillNodeProperties(importContext, texLayeredNode, pluginDesc, "TexLayeredMax")

    if textures:
        for i, texture in enumerate(textures):
            humanIndex = i + 1
            # NOTE: Node already has two inputs
            texSocketName = f"Texture {humanIndex}"
            if not texSocketName in texLayeredNode.inputs:
                VRayNodeTexLayeredMax.addLayer(texLayeredNode, i)

            textureSocket = texLayeredNode.inputs[texSocketName]
            maskSocket = texLayeredNode.inputs[f"Mask {humanIndex}"]
            opacitySocket = getHiddenInput(texLayeredNode, f"Opacity {humanIndex}")
            blendModeSocket = getHiddenInput(texLayeredNode, f"Blend Mode {humanIndex}")

            if _isPluginLink(texture):
                connectedPlugin, connectedOutput = _getPluginFromLink(importContext, texture)
                if connectedPlugin:
                    _createLinkedNode(importContext, textureSocket, connectedOutput, connectedPlugin)
            else:
                textureSocket.value = texture

            if masks and i < len(masks):
                mask = masks[i]
                if _isPluginLink(mask):
                    connectedPlugin, connectedOutput = _getPluginFromLink(importContext, mask)
                    if connectedPlugin:
                        _createLinkedNode(importContext, maskSocket, connectedOutput, connectedPlugin)
                else:
                    maskSocket.value = mask

            opacitySocket.value = float(opacities[i]) if opacities and i < len(opacities) else 1.0
            blendModeSocket.value = str(blendModes[i])
    texLayeredNode.layers = len(textures)

    return texLayeredNode


def _createLightIES(importContext: ImportContext, pluginDesc: dict):
    lightIES = NodeUtils.createNode(importContext.nodeTree, 'VRayNodeLightIES', pluginDesc["Name"])

    _fillNodeProperties(importContext, lightIES, pluginDesc, 'LightIES')

    iesFile = pluginDesc['Attributes'].get('ies_file')
    lightIES.LightIES.ies_file = importContext.resolvePath(iesFile)

    return lightIES

def _createTexNormalMapNode(importContext: ImportContext, pluginDesc: dict):
    assert pluginDesc['ID'] in [ "TexNormalBump", "TexNormalMapFlip" ]

    normalBumpNode = NodeUtils.createNode(importContext.nodeTree, 'VRayNodeTexNormalBump')

    # We need to handle both directions in graphs with TexNormalBump->TexNormalMapFlip
    # and TexNormalMapFlip->TexNormalBump.
    normalMap, texmap, normalFlip, outputName = None, None, None, ""
    if pluginDesc['ID'] == "TexNormalBump":
        normalMap = pluginDesc
        texmap, outputName = _getPluginConnectedToInput(importContext, "bump_tex_color", pluginDesc)
        if texmap is not None and texmap['ID'] == "TexNormalMapFlip":
            normalFlip = texmap
            texmap, outputName = _getPluginConnectedToInput(importContext, "texmap", texmap)
    elif pluginDesc['ID'] == "TexNormalMapFlip":
        normalFlip = pluginDesc
        texmap, outputName = _getPluginConnectedToInput(importContext, "texmap", pluginDesc)
        if texmap is not None and texmap['ID'] == "TexNormalBump":
            normalMap = texmap
            texmap, outputName = _getPluginConnectedToInput(importContext, "bump_tex_color", texmap)

    if normalFlip is not None:
        normalBumpNode.TexNormalBump.flip_red = normalFlip['Attributes'].get('flip_red', False)
        normalBumpNode.TexNormalBump.flip_green = normalFlip['Attributes'].get('flip_green', False)
        normalBumpNode.TexNormalBump.swap_red_green = normalFlip['Attributes'].get('swap_redgreen', False)
    if normalMap is not None:
        normalMap['Attributes'].setdefault('bump_object_space', False)
        _fillNodeProperties(importContext, normalBumpNode, normalMap, "TexNormalBump", {'bump_tex_color'})
    if texmap:
        if bumpTexSocket := _getInputSocket(normalBumpNode, "bump_tex_color"):
            _createLinkedNode(importContext, bumpTexSocket, outputName, texmap)

    return normalBumpNode

def _checkIdentityMatrix(pluginDesc: dict, paramName: str):
    if not paramName in pluginDesc['Attributes']:
        return True
    attrValue = pluginDesc['Attributes'][paramName]
    transform = attrValue if isinstance(attrValue, Matrix) else attribute_utils.attrValueToMatrix(attrValue, True)
    return allclose(transform, Matrix.Identity(4))

def _createUVWGenChannelNode(importContext: ImportContext, pluginDesc: dict):
    if not importContext.isConversion:
        return _createGenericNode(importContext, pluginDesc)

    # UVWGenChannel is used in a few places as default UVWGen for conversion so it can be safely ignored.
    isIdentity = _checkIdentityMatrix(pluginDesc, "uvw_transform") and _checkIdentityMatrix(pluginDesc, "tex_transform")
    # If the transformation is identity, do not create a separate V-Ray Transform node for it
    if importContext.isConversion and isIdentity:
        return None
    return _createGenericNode(importContext, pluginDesc)


def _createMappingNode(importContext: ImportContext, pluginDesc, pluginType):
    pluginName = pluginDesc['Name']
    mappingNode = NodeUtils.createNode(importContext.nodeTree, 'VRayNodeUVWMapping', pluginName)


    match pluginType:
        case 'UVWGenMayaPlace2dTexture':
            mappingNode.mapping_node_type = 'UV'
        case 'UVWGenProjection':
            mappingNode.mapping_node_type = 'PROJECTION'
        case 'UVWGenObject':
            mappingNode.mapping_node_type = 'OBJECT'
        case 'UVWGenEnvironment':
            mappingNode.mapping_node_type = 'ENVIRONMENT'
        case _: # Just in case
            return mappingNode

    _fillNodeProperties(importContext, mappingNode, pluginDesc, pluginType)
    return mappingNode


def _createTransformNode(importContext: ImportContext, attrValue, attrSocketName: str, node: bpy.types.Node):
    m = attrValue if isinstance(attrValue, Matrix) else attribute_utils.attrValueToMatrix(attrValue, True)

    if allclose(m, Matrix.Identity(4)):
        # If the transformation is identity, do not create a separate V-Ray Transform node for it
        return

    offset, rotate, scale = m.decompose()
    rotate = rotate.to_euler('XYZ')

    tmNode = NodeUtils.createNode(importContext.nodeTree, 'VRayNodeTransform')
    tmNode.inputs['Offset'].value   = (offset[0],
                                      offset[1],
                                      offset[2])
    tmNode.inputs['Rotation'].value = (rotate[0], rotate[1], rotate[2])
    tmNode.inputs['Scale'].value    = (scale[0],  scale[1],  scale[2])

    importContext.nodeTree.links.new(tmNode.outputs['Transform'], node.inputs[attrSocketName])


def _createMatrixNode(ntree: bpy.types.NodeTree, attrValue, attrSocketName: str, node: bpy.types.Node):
    m = Matrix()

    if type(attrValue) in {list, tuple}:
        # Transpose the matrix, Blender's format is row-first
        for c in range(3):
            for r in range(3):
                m[c][r] = attrValue[r][c]

    else:
        tmArray = struct.unpack("fffffffff", binascii.unhexlify(bytes(attrValue, 'ascii')))
        i = 0
        for c in range(3):
            for r in range(3):
                m[c][r] = tmArray[i]
                i += 1

    if allclose(m, Matrix.Identity(3)):
        # If the transformation is identity, do not create a separate V-Ray Transform node for it
        return

    _, rotate, scale = m.decompose()
    rotate = rotate.to_euler('XYZ')

    mNode = NodeUtils.createNode(ntree, 'VRayNodeMatrix')
    mNode.rotate = (rotate[0], rotate[1], rotate[2])
    mNode.scale  = (scale[0],  scale[1],  scale[2])

    ntree.links.new( mNode.outputs['Matrix'],  node.inputs[attrSocketName])


def _applyTexCombineToSock(texCombineDesc: dict, inSocket: bpy.types.NodeSocket):
    """ Applies TexCombineColor/Float plugin's attributes to socket """

    texCombAttrs = texCombineDesc["Attributes"]
    if "texture_multiplier" in texCombAttrs:
        textureMultiplier =  texCombAttrs["texture_multiplier"]
        if hasattr(inSocket, "multiplier"):
            inSocket.multiplier *= float(textureMultiplier)

    if "color" in texCombAttrs:
        if isColorSocket(getVRayBaseSockType(inSocket)):
            inSocket.value =  texCombAttrs["color"]


def _isPluginLink(value):
    return type(value) is str


def _parsePluginLink(pluginLink: str):
    """ Parse a plugin name set as attribute value into plugin name and output socket attribute name. """
    if pluginLink.find("::") != -1:
        pluginName, outSocketName = pluginLink.split("::")
        assert pluginName != "" and outSocketName != ""
        return pluginName, outSocketName
    else:
        return pluginLink, None


def _getPluginFromLink(importContext: ImportContext, pluginLink: str):
    """ Returns the plugin and output socket name given a fully qualified plugin link (plugnName::output) """
    if (not pluginLink) or (not _isPluginLink(pluginLink)):
        return None, None
    
    pluginName, outputSocketAttr = _parsePluginLink(pluginLink)
    
    if not pluginName:
        raise Exception(f"Plugin refereced by {pluginLink} not found in .vrmat")
    
    plugin = getPluginByName(importContext.vrsceneDict, pluginName)
    
    if not plugin:
        # This may happen when the plugin is referenced from another .vrnat file
        # like in e.g. HDRI definitions
        return None, None
                         
    pluginType = plugin['ID']

    if outputSocketAttr:
        outputSocketName = _getOutputSocketNameByAttr(pluginType, outputSocketAttr)
    else:
        outputSocketName = _getDefaultOutputSocketName(pluginType)

    return plugin, outputSocketName


def _getPluginConnectedToInput(importContext, inputAttrName, pluginDesc):
    """ Returns the plugin and output referenced by attribute named "inputAttrName" """

    inputValue = pluginDesc['Attributes'][inputAttrName]
    if _isPluginLink(inputValue):
        return _getPluginFromLink(importContext, inputValue)
        
    return None, None


def _setNodeEnumProperty(attrDesc: dict, attrName: str, attrValue, pluginType: str, propGroup):
    """ Set value of Enum property of node """

    # Attribute is not mappable, so simply set it's value
    attrValue = str(attrValue)
    if not attribute_utils.valueInEnumItems(attrDesc, attrValue):
        debug.printError(f"Unsupported ENUM value '{attrValue}' for attribute: {pluginType}.{attrName}")
        return
    
    setattr(propGroup, attrName, attrValue)

def _setNodePrimitiveProperty(attrName: str, attrValue, pluginType: str, propGroup):
    """ Setting properties with Int, Float or String types"""

    # UVWGenRandomizer's 'mode' attribute is used as mask on which the first five bits
    # represent different modes.
    # In the UI they are visualized as different check boxes
    # and the code below fills them based on the 'mode' mask value.
    if pluginType == "UVWGenRandomizer" and attrName == "mode":
        attrValue = int(attrValue)
        for modeName, modeMask in UVWGenRandomizerModes.items():
            # Explicitly set all mode flags in order to override any plugin defaults.
            setattr(propGroup, modeName, bool(attrValue & modeMask))
    else:
        setattr(propGroup, attrName, attrValue)


def _getMetaInputSocket(pluginModule, node, attrName):
    """ If the attribute is part of a meta input socket, return the meta socket
        instead of the socket for the actual attribute.
    """
    for s in (s for s in node.inputs if s.bl_idname.startswith('VRaySocketColorTexture_')):
        for p in (p for p in pluginModule.Parameters if p['type'] == 'COLOR_TEXTURE'):
            if p['color_prop'] == attrName or p['tex_prop'] == attrName:
                return s
    return None


def _getInputSocket(node, attrName):
    pluginModule = getPluginModule(node.vray_plugin)
    
    if (sock := _getMetaInputSocket(pluginModule, node, attrName)) is not None:
        excludedParams = pluginModule.Options.get('excluded_parameters', [])
        if sock.vray_attr not in excludedParams:
            # It is unlikely that a meta socket would be excluded from export, but
            # do the check for consistency
            return sock
        else:
            debug.printError(f"Import: Parameter '{attrName}' is not part of node {node.name} definition")
            return None
    
    if (sock := getInputSocketByAttr(node, attrName)) is None:
        excludedParams = pluginModule.Options.get('excluded_parameters', [])
        if attrName not in excludedParams:
            debug.printWarning(f"Import: Parameter '{attrName}' is not part of node {node.name} definition")

    return sock


def _processNonNodePlugins(importContext: ImportContext, inPluginOutput: str, connectedPlugin: dict, inputSocket: bpy.types.NodeSocket):
    """ Find the first plugin in the chain starting from 'inputSocket' for which a node should be created.
        Apply any TexCombineColor properties to the input socket.
    
        Returns:
            The plugin description and the name of the output socket of the next plugin in the chain 
            for which a node should be created.
    """
    assert inputSocket is not None
    assert connectedPlugin is not None
    
    while connectedPlugin:
        connectedPluginType = connectedPlugin['ID']
        connectedThroughAttr = ""

        # MtlSingleBRDF, TexCombineColor, TexColorToFloat and TexFloatToColor node conversions are skipped
        # (they are created only during node tree export and are not visible in the UI)
        # Only the plugins connected through their input attributes are imported as nodes
        match connectedPluginType:
            case "TexCombineColor" | "TexCombineFloat":
                _applyTexCombineToSock(connectedPlugin, inputSocket)
                connectedThroughAttr = "texture"
            case "TexColorToFloat" | "TexFloatToColor":
                connectedThroughAttr = "input"
            case "MtlSingleBRDF":
                connectedThroughAttr = "brdf"
            case _:
                break

        # Multiplier or converter is not connected
        if connectedThroughAttr not in connectedPlugin["Attributes"]:
            return connectedPlugin, inPluginOutput

        nextPlugin, nextPluginOutput = _getPluginConnectedToInput(importContext, connectedThroughAttr, connectedPlugin)

        if not nextPlugin:
            # No plugin is connected to the input
            break    

        assert nextPluginOutput

        connectedPlugin, inPluginOutput = nextPlugin, nextPluginOutput

    return connectedPlugin, inPluginOutput


                    
def _createLinkedNode(
    importContext: ImportContext,
    inputSocket: bpy.types.NodeSocket,
    connectedPluginOutput: str,
    connectedPlugin: dict
):
    """ Create the node connected to 'inputSocket'

    Args:
        importContext (ImportContext): import context
        inputSocket (bpy.types.NodeSocket): The input socket to connect.
        connectedPluginOutput (str): The name of the output socket to connect to.
        connectedPlugin (dict):      The type of the plugin of the new node.
    """
    assert connectedPlugin is not None

    connectedPlugin, connectedPluginOutput = _processNonNodePlugins(importContext, connectedPluginOutput, connectedPlugin, inputSocket)
    assert connectedPlugin is not None
    
    if (collapsedValue := _collapseToValue(importContext, connectedPlugin)) is not None:
        # No need to create a new node for the connected plugin as it has been collapsed to a value
        inputSocket.value = collapsedValue
        return None
    
    newNode = createNode(importContext, connectedPlugin)
    
    if newNode is None:
        # Some leaf nodes may be omitted if they have their default value, e.g. UVWGenChannel
        return 
    
    # Deal with remapped socket names
    if remappedPluginOutput := _REMAPPED_OUTPUT_SOCKETS.get(connectedPlugin['ID'], {}).get(connectedPluginOutput):
        connectedPluginOutput = remappedPluginOutput

    assert connectedPluginOutput in (o.name for o in newNode.outputs)
        
    # Some plugins, e.g. TexSampler, have part of their output sockets hidden depending on the state of the node.
    # Make sure the socket is visible and enabled before creating the link to it or the creation will fail.
    outSock = next((o for o in newNode.outputs if o.name == connectedPluginOutput))
    if outSock.hide:
        outSock.hide = False
        outSock.enabled = True

    importContext.nodeTree.links.new(outSock, inputSocket)


def _fillNodeProperties(importContext: ImportContext, node: bpy.types.Node, pluginDesc: dict, pluginType: str, skippedAttrs = {}):
    """ Create Node without specific parameters"""

    from vray_blender.plugins import PLUGIN_MODULES

    pluginAttrs = pluginDesc['Attributes']
    pluginModule = getPluginModule(pluginType)

    if pluginModule is None:
        debug.printError(f"Plugin '{pluginType}' is not yet supported! This shouldn't happen! Please, report this!")
        return None

    # This property group holds all plugin settings
    propGroup = getattr(node, pluginType)

    # list of all "COLOR_TEXTURE" attributes
    colorTexAttributes = [attr for attr in pluginModule.Parameters if attr["type"] == "COLOR_TEXTURE"]

    # Now go through all plugin attributes and check
    # if we should create other nodes or simply set the value
    for attrName in pluginAttrs:
        if attrName in skippedAttrs:
            continue
        attrValue = pluginAttrs[attrName]

        attrDesc = attribute_utils.getAttrDesc(pluginModule, attrName)
        # TODO: Figure out what to do with these params
        if not attrDesc:
            continue

        if not importContext.isConversion and "ui" in attrDesc and attrDesc["ui"].get("quantityType") == "distance":
            lengthUnit = attrDesc["ui"].get("units", "centimeters")
            attrValue = attribute_utils.scaleToSceneLengthUnit(attrValue, lengthUnit)
        
        # Catching socket mismatches during generic nodes creation.
        # Error occurring in the code below could interrupt importing of cosmos asset.
        try:
            attrSocketName = _getInputSocketNameByAttr(pluginModule, attrName)

            origAttrName = attrName
            # Handling attributes that are represented as COLOR_TEXTURE
            if attrDesc['type'] in ("COLOR", "ACOLOR", "TEXTURE"):
                for clrTex in colorTexAttributes:
                    if clrTex["tex_prop" if attrDesc['type'] == "TEXTURE"  else "color_prop"] == attrName:
                        attrName = clrTex["attr"]
                        attrSocketName = _getInputSocketNameByAttr(pluginModule, attrName)

            # Attribute is a output type - nothing to do
            if attrDesc['type'] in attribute_types.NodeOutputTypes:
                continue

            elif attrDesc['type'] == 'MATRIX':
                assert False, "Should not get here. See vrmat_parser.parseVrmat()"
                # Currently we don't support properties of type Matrix in any plugin.
                # Uncomment the following line if such support is needed.
                # _createMatrixNode(ntree, attrValue, attrSocketName, node)

            elif attrDesc['type'] == 'TRANSFORM':
                _createTransformNode(importContext, attrValue, attrSocketName, node)

            elif attrDesc['type'] == 'ENUM':
                _setNodeEnumProperty(attrDesc, attrName, attrValue, pluginType, propGroup)

            elif attrDesc['type'] not in attribute_types.NodeInputTypes:
                _setNodePrimitiveProperty(attrName, attrValue, pluginType, propGroup)

            elif _isPluginLink(attrValue):
                connectedPlugin, outputName = _getPluginFromLink(importContext, attrValue)
                if connectedPlugin is not None:
                    if inputSock := _getInputSocket(node, origAttrName):
                        _createLinkedNode(importContext, inputSock, outputName, connectedPlugin)

            elif  attrSocket := _getInputSocket(node, attrName):
                attrSocket.value = attrValue

        except Exception as ex:
            debug.printExceptionInfo(ex, "nodes.importing._fillNodeProperties")

    return node

def _createGenericNode(importContext: ImportContext, pluginDesc: dict):
    pluginType  = pluginDesc['ID']
    pluginName  = pluginDesc['Name']

    node = NodeUtils.createNode(importContext.nodeTree, f'VRayNode{pluginType}', pluginName)

    _fillNodeProperties(importContext, node, pluginDesc, pluginType)

    return node


def createNode(importContext: ImportContext, pluginDesc: dict):
    pluginType = pluginDesc['ID']
    pluginName = pluginDesc['Name']

    if pluginType in _UNSUPPORTED_PLUGINS:
        debug.printWarning(f"The asset being imported contains a plugin of type {pluginType}, which is not recognized by V-Ray for Blender. Please contact support.")
        return None

    ntree = importContext.nodeTree
    for node in ntree.nodes:
        if node.name == pluginName:
            return ntree.nodes[pluginName]

    match pluginType:
        case 'BRDFLayered':
            return _createNodeBRDFLayered(importContext, pluginDesc)
        case 'BRDFScanned':
            return _createNodeBRDFScanned(importContext, pluginDesc)
        case 'TexBitmap':
            return _createNodeTexBitmap(importContext, pluginDesc)
        case 'TexGradRamp':
            return _createNodeTexGradRamp(importContext, pluginDesc)
        case 'TexRemap':
            return _createNodeTexRemap(importContext, pluginDesc)
        case 'TexVectorProduct':
            return _createNodeTexVectorProduct(importContext, pluginDesc)
        case 'TexLayeredMax':
            return _createNodeTexLayered(importContext, pluginDesc)
        case 'UVWGenMayaPlace2dTexture' | 'UVWGenProjection' |'UVWGenObject' | 'UVWGenEnvironment':
            return _createMappingNode(importContext, pluginDesc, pluginType)
        case 'UVWGenChannel':
            return _createUVWGenChannelNode(importContext, pluginDesc)
        case 'LightIES':
            return _createLightIES(importContext, pluginDesc)
        case 'TexNormalMapFlip' | "TexNormalBump":
            return _createTexNormalMapNode(importContext, pluginDesc)
        case 'TexBezierCurve':
            return _createNodeTexRemapFromBezierCurve(importContext, pluginDesc, isColor=False)
        case 'TexBezierCurveColor':
            return _createNodeTexRemapFromBezierCurve(importContext, pluginDesc, isColor=True)

    return _createGenericNode(importContext, pluginDesc)
