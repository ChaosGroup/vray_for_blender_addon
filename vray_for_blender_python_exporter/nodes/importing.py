
import os
import binascii
import struct

import bpy
import mathutils

from vray_blender.lib import attribute_utils, attribute_types
from vray_blender.lib import path_utils
from vray_blender.nodes import importing as NodesImport
from vray_blender.plugins import PLUGIN_MODULES, getPluginModule
from vray_blender.plugins.skipped_plugins import SKIPPED_PLUGINS

from vray_blender.nodes import utils as NodeUtils
from vray_blender.nodes.sockets import addInput
from vray_blender import debug
from vray_blender.exporting.tools import isColorSocket, getVRayBaseSockType, getInputSocketByAttr
from vray_blender.exporting.node_exporters.uvw_node_export import UVWGenRandomizerModes


def getOutputSocket(pluginType):
    if pluginType == "BRDFLayered":
        return "BRDF"
    elif pluginType == "BitmapBuffer":
        return "Bitmap"
    elif pluginType in PLUGIN_MODULES:
        pluginModule = PLUGIN_MODULES[pluginType]

        if pluginModule.TYPE == 'MATERIAL':
            return "Material"
        elif pluginModule.TYPE == 'UVWGEN':
            return "Mapping"
        elif pluginModule.TYPE == 'BRDF':
            return "BRDF"
        elif pluginModule.TYPE == 'GEOMETRY':
            return "Geomtery"
        elif pluginModule.TYPE == 'EFFECT':
            return "Output"
        elif pluginModule.TYPE == 'RENDERCHANNEL':
            return "Channel"
        elif pluginModule.TYPE == 'TEXTURE':
            return "Color"

    return "Output"


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


def getSocketName(pluginModule, attrName):
    attrDesc = attribute_utils.getAttrDesc(pluginModule, attrName)
    if not attrDesc:
        return None
    return attrDesc.get('name', attribute_utils.getAttrDisplayName(attrDesc))


def getOutputSocketByAttr(node, attrName):
    for sock in node.outputs:
        if hasattr(sock, 'vray_attr'):
            if sock.vray_attr == attrName:
                return sock
    return getOutputSocket(node.vray_type)


def findAndCreateNode(vrsceneDict, pluginName, ntree, prevNode, locationsMap):
    if not pluginName:
        return None
    pluginDesc = getPluginByName(vrsceneDict, pluginName)
    if not pluginDesc:
        return None
    return createNode(ntree, prevNode, vrsceneDict, pluginDesc, locationsMap)


########  ########  ########  ########       ##          ###    ##    ## ######## ########  ######## ########
##     ## ##     ## ##     ## ##             ##         ## ##    ##  ##  ##       ##     ## ##       ##     ##
##     ## ##     ## ##     ## ##             ##        ##   ##    ####   ##       ##     ## ##       ##     ##
########  ########  ##     ## ######         ##       ##     ##    ##    ######   ########  ######   ##     ##
##     ## ##   ##   ##     ## ##             ##       #########    ##    ##       ##   ##   ##       ##     ##
##     ## ##    ##  ##     ## ##             ##       ##     ##    ##    ##       ##    ##  ##       ##     ##
########  ##     ## ########  ##             ######## ##     ##    ##    ######## ##     ## ######## ########

def _createNodeBRDFLayered(ntree, n, vrsceneDict, pluginDesc, locationsMap):
    def processSocket(thisNode, socket, attrValue):
        # Could happen with some broken files
        if not attrValue:
            return

        plName   = attrValue
        plOutput = None
        if plName.find("::") != -1:
            plName, plOutput = plName.split("::")
            plOutput = attribute_utils.formatAttributeName(plOutput)

        pl = getPluginByName(vrsceneDict, plName)
        if pl:
            # Get default output
            if plOutput is None:
                plOutput = getOutputSocket(pl['ID'])
            collValue = _collapseToValue(pl, toFloat=True, vrsceneDict=vrsceneDict)
            if collValue is not None:
                socket.value = fixValue(collValue)
            else:
                
                # TexCombineColor is not supported for VRaySocketFloat and texture_multiplier is mostly with value 1
                # TODO Find a way for multiplier implementation id needed
                plAttrs = pl['Attributes']
                if (pl['ID'] == 'TexCombineColor') and ("texture" in plAttrs) and (type(plAttrs["texture"]) is str):
                    pl = getPluginByName(vrsceneDict, plAttrs["texture"])

                inNode = createNode(ntree, thisNode, vrsceneDict, pl, locationsMap)
                if inNode:
                    ntree.links.new(inNode.outputs[plOutput], socket)
        else:
            socket.value = attrValue

    brdfs   = pluginDesc['Attributes'].get('brdfs')
    weights = pluginDesc['Attributes'].get('weights')

    brdfLayeredNode = NodeUtils.createNode(ntree, 'VRayNodeBRDFLayered', pluginDesc['Name'])

    if brdfs:
        for i,brdf in enumerate(brdfs):
            humanIndex = i + 1

            brdfSockName   = f"BRDF {humanIndex}"
            weightSockName = f"Weight {humanIndex}"

            # NOTE: Node already has two inputs
            if not brdfSockName in brdfLayeredNode.inputs:
                addInput(brdfLayeredNode, 'VRaySocketBRDF',       brdfSockName)
                addInput(brdfLayeredNode, 'VRaySocketFloatColor', weightSockName)
                brdfLayeredNode.inputs[weightSockName].value = 1.0

            brdfSocket   = brdfLayeredNode.inputs[brdfSockName]
            weightSocket = brdfLayeredNode.inputs[weightSockName]

            processSocket(brdfLayeredNode, brdfSocket, brdf)

            # NOTE: 'weights' could be optional
            if weights:
                processSocket(brdfLayeredNode, weightSocket, weights[i])

    for attrName in pluginDesc['Attributes']:
        # Skip lists
        if attrName in {'brdfs', 'weights'}:
            continue

        attrValue = pluginDesc['Attributes'][attrName]

        if hasattr(brdfLayeredNode, attrName):
            setattr(brdfLayeredNode, attrName, attrValue)

    return brdfLayeredNode


########  #### ######## ##     ##    ###    ########        ########  ##     ## ######## ######## ######## ########
##     ##  ##     ##    ###   ###   ## ##   ##     ##       ##     ## ##     ## ##       ##       ##       ##     ##
##     ##  ##     ##    #### ####  ##   ##  ##     ##       ##     ## ##     ## ##       ##       ##       ##     ##
########   ##     ##    ## ### ## ##     ## ########        ########  ##     ## ######   ######   ######   ########
##     ##  ##     ##    ##     ## ######### ##              ##     ## ##     ## ##       ##       ##       ##   ##
##     ##  ##     ##    ##     ## ##     ## ##              ##     ## ##     ## ##       ##       ##       ##    ##
########  ####    ##    ##     ## ##     ## ##              ########   #######  ##       ##       ######## ##     ##

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


def _createNodeBitmapBuffer(ntree: bpy.types.NodeTree, vrsceneDict, pluginDesc, locationsMap):
    pluginModule = PLUGIN_MODULES.get('BitmapBuffer')

    bitmatBuffer = NodeUtils.createNode(ntree, 'VRayNodeBitmapBuffer', pluginDesc['Name'])
    propGroup = bitmatBuffer.BitmapBuffer

    NodeUtils.createBitmapTexture(bitmatBuffer)
    bitmapTexture = bitmatBuffer.texture

    imageFilepath = pluginDesc['Attributes'].get('file')
    if locationsMap and imageFilepath in locationsMap:
        imageFilepath = locationsMap[imageFilepath]

    importSettings = getPluginByName(vrsceneDict, "Import Settings")
    if importSettings:
        importDir = importSettings['Attributes']['dirpath']

    loadImage(imageFilepath, importDir, bitmapTexture)

    for attrName in pluginDesc['Attributes']:
        attrDesc  = attribute_utils.getAttrDesc(pluginModule, attrName)

        attrValue = pluginDesc['Attributes'][attrName]

        if hasattr(propGroup, attrName):
            if attrDesc['type'] == 'ENUM':
                attrValue = str(attrValue)
                if not attribute_utils.valueInEnumItems(attrDesc, attrValue):
                    debug.printError("Unsupported ENUM value '%s' for attribute: %s.%s" %
                        (attrValue, 'BitmapBuffer', attrName))
                    continue
            setattr(propGroup, attrName, attrValue)

    return bitmatBuffer


def _pluginAttrsToNodeProps(pluginDesc, node):
    pluginType = pluginDesc['ID']

    assert hasattr(node, pluginType)

    propGroup = getattr(node, pluginType)
    pluginModule = PLUGIN_MODULES.get(pluginType)

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
         

def _getPluginOutputSocketName(connectedPlugin, inPluginOutput):
    connectedPluginType = connectedPlugin['ID']
    return attribute_utils.formatAttributeName(inPluginOutput) if inPluginOutput else getOutputSocket(connectedPluginType)

def _createNodeTexBitmap(ntree: bpy.types.NodeTree, vrsceneDict, pluginDescTexBitmap, locationsMap):
    imageTextureNode = NodeUtils.createNode(ntree, 'VRayNodeMetaImageTexture', pluginDescTexBitmap['Name'])

    bitmapBufferName = pluginDescTexBitmap['Attributes']['bitmap']
    pluginDescBitmapBuffer = NodesImport.getPluginByName(vrsceneDict, bitmapBufferName)

    bitmapTexture = imageTextureNode.texture

    imageFilepath = pluginDescBitmapBuffer['Attributes'].get('file')

    if locationsMap and imageFilepath in locationsMap:
        imageFilepath = locationsMap[imageFilepath]

    importSettings = getPluginByName(vrsceneDict, "Import Settings")
    if importSettings:
        importDir = importSettings['Attributes']['dirpath']

    loadImage(imageFilepath, importDir, bitmapTexture)

    # Filling the Mapping Socket of Texture
    if "uvwgen" in pluginDescTexBitmap['Attributes']:
        pluginOutAttrName, connectedPlugin = _getConnectedPluginInput(vrsceneDict, "uvwgen", pluginDescTexBitmap)
        if connectedPlugin:
            uvwgenOutputSocketName = _getPluginOutputSocketName(connectedPlugin, pluginOutAttrName)
            connectedNode = createNode(ntree, imageTextureNode, vrsceneDict, connectedPlugin, locationsMap)
            if connectedNode:
                ntree.links.new(connectedNode.outputs[uvwgenOutputSocketName], imageTextureNode.inputs["Mapping"])

    _pluginAttrsToNodeProps(pluginDescTexBitmap, imageTextureNode)
    _pluginAttrsToNodeProps(pluginDescBitmapBuffer, imageTextureNode)

    return imageTextureNode

########     ###    ##     ## ########   ######
##     ##   ## ##   ###   ### ##     ## ##    ##
##     ##  ##   ##  #### #### ##     ## ##
########  ##     ## ## ### ## ########   ######
##   ##   ######### ##     ## ##              ##
##    ##  ##     ## ##     ## ##        ##    ##
##     ## ##     ## ##     ## ##         ######

CollapsibleTypes = {
    'TexAColor',
    'TexCombineColor',
}


def _collapseToValue(pluginDesc, toFloat=False, vrsceneDict=None):
    """ Transforms plugin from 'CollapsibleTypes' to a simple value (either float or list of floats). """

    pluginType    = pluginDesc['ID']
    pluginAttrs = pluginDesc['Attributes']

    if pluginType not in CollapsibleTypes:
        return None

    value = None
    valueAttr = 'texture' if (pluginType == 'TexAColor') or ('texture' in pluginAttrs) else 'color'
    color = pluginAttrs[valueAttr]

    if type(color) is not str:
        value = color
    elif vrsceneDict:
        newDesc = getPluginByName(vrsceneDict, color)
        value = _collapseToValue(newDesc, None, vrsceneDict)

    if toFloat and value is not None:
        value = sum(value) / len(value)

    return value


def fixValue(attrValue):
    # Fix for color attribute
    if type(attrValue) in [list, tuple]:
        if len(attrValue) == 4:
            attrValue = attrValue[:3]
    return attrValue


def fillRamp(vrsceneDict, ramp, colors, positions):
    RampElements = []

    for col, pos in zip(colors, positions):
        rampElement = {
            'color'    : col,
            'position' : pos,
        }

        for key in rampElement:
            value = rampElement[key]

            if type(value) is str:
                conPlugin   = getPluginByName(vrsceneDict, value)
                conPluginType = conPlugin['ID']

                if conPluginType not in CollapsibleTypes:
                    debug.printError(f"Plugin '{conPluginType}': Unsupported parameter value! This shouldn't happen! Please, report this!")
                    rampElement[key] = None
                else:
                    rampElement[key] = _collapseToValue(conPlugin)

        RampElements.append(rampElement)

    # Create ramp elements
    # Ramp already has 2 elements
    elementsToCreate = len(RampElements) - 2
    for i in range(elementsToCreate):
        # We will setup proper position later
        ramp.elements.new(0.0)

    # Setup elements values
    elementStep = 1.0 / len(ramp.elements)

    for i,rampElement in enumerate(RampElements):
        col = rampElement['color']
        if col is None:
            col = (1.0,1.0,1.0)

        pos = rampElement['position']
        if pos is None:
            pos = i * elementStep

        el = ramp.elements[i]
        el.color    = col
        el.position = pos


def _createNodeTexGradRamp(ntree: bpy.types.NodeTree, prevNode, vrsceneDict, pluginDesc):
    texGradRamp  = NodeUtils.createNode(ntree, 'VRayNodeTexGradRamp', pluginDesc['Name'])

    attributes   = pluginDesc['Attributes']

    fillRamp(vrsceneDict,
        texGradRamp.texture.color_ramp,
        attributes['colors'],
        attributes['positions']
    )

    return texGradRamp


##     ## ##     ## ##      ##  ######   ######## ##    ## 
##     ## ##     ## ##  ##  ## ##    ##  ##       ###   ## 
##     ## ##     ## ##  ##  ## ##        ##       ####  ## 
##     ## ##     ## ##  ##  ## ##   #### ######   ## ## ## 
##     ##  ##   ##  ##  ##  ## ##    ##  ##       ##  #### 
##     ##   ## ##   ##  ##  ## ##    ##  ##       ##   ### 
 #######     ###     ###  ###   ######   ######## ##    ## 

def _createMappingNode(ntree: bpy.types.NodeTree, vrsceneDict, pluginDesc, pluginType, locationsMap):
    pluginName = pluginDesc['Name']
    mappingNode = NodeUtils.createNode(ntree, 'VRayNodeUVWMapping', pluginName)


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

    _fillNodeProperties(mappingNode, ntree, vrsceneDict, pluginDesc, pluginType, locationsMap)
    return mappingNode

 ######   ######## ##    ## ######## ########  ####  ######
##    ##  ##       ###   ## ##       ##     ##  ##  ##    ##
##        ##       ####  ## ##       ##     ##  ##  ##
##   #### ######   ## ## ## ######   ########   ##  ##
##    ##  ##       ##  #### ##       ##   ##    ##  ##
##    ##  ##       ##   ### ##       ##    ##   ##  ##    ##
 ######   ######## ##    ## ######## ##     ## ####  ######


def _createTransformNode(ntree: bpy.types.NodeTree, attrValue, attrSocketName: str, node: bpy.types.Node):
    from numpy import allclose
    from mathutils import Matrix

    m = attribute_utils.attrValueToMatrix(attrValue, True)

    if allclose(m, Matrix.Identity(4)):
        # If the transformation is identity, do not create a separate V-Ray Transform node for it
        return 
    
    offset, rotate, scale = m.decompose()
    rotate = rotate.to_euler('XYZ')

    tmNode = NodeUtils.createNode(ntree, 'VRayNodeTransform')
    tmNode.offset = (offset[0],
                     offset[1],
                     offset[2])
    tmNode.rotate = (rotate[0], rotate[1], rotate[2])
    tmNode.scale  = (scale[0],  scale[1],  scale[2])

    ntree.links.new(tmNode.outputs['Transform'], node.inputs[attrSocketName])


def _createMatrixNode(ntree: bpy.types.NodeTree, attrValue, attrSocketName: str, node: bpy.types.Node):
    from numpy import allclose
    from mathutils import Matrix

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

def _getConnectedPluginInput(vrsceneDict, inputAttrName, pluginDesc):
    """ Returns plugin referenced by attribute named "inputAttrName" """

    inputValue = pluginDesc['Attributes'][inputAttrName]
    if type(inputValue) is str:
        pluginOutAttrName = None
        inPluginName = inputValue
        # Check if a specific output is requested (like MyTexture::out_intensity)
        if inputValue.find("::") != -1:
            inPluginName, pluginOutAttrName = inputValue.split("::")

        return pluginOutAttrName, getPluginByName(vrsceneDict, inPluginName)

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

def _setNodeTransformProperty(attrName: str, attrValue, pluginType: str, propGroup):
    """ Setting property of type Transform """

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

def _getInputSock(node, attrName):
    sock = next((s for s in node.inputs if s.vray_attr == attrName), None)

    if sock is None:
        pluginModule = getPluginModule(node.vray_plugin)
        excludedParams = pluginModule.Options.get('excluded_parameters', [])
        if attrName not in excludedParams:
            debug.printWarning(f"Cosmos import: Parameter '{attrName}' is not part of node {node.name} definition")

    return sock

def _createNodeSocketConnection(ntree: bpy.types.NodeTree, node: bpy.types.Node, vrsceneDict: dict, attrName: str, attrSocketName: str, pluginDesc: dict, fixBump: bool, locationsMap):
    """ Create Node for a plugin that's referred by another plugin """

    inPluginOutput, connectedPlugin = _getConnectedPluginInput(vrsceneDict, attrName, pluginDesc)

    # Continue until 'connectedPlugin' is not converter or multiplier
    # Such situations are possible: TexCombineColor->TexColorToFloat->TexCombineFloat
    while connectedPlugin:
        connectedPluginType = connectedPlugin['ID']
        connectedThroughAttr = ""
        

        # MtlSingleBRDF, TexCombineColor, TexColorToFloat and TexFloatToColor node conversions are skipped
        # (they are created only during node tree export and are not visible in the UI)
        # Only the plugins connected through their input attributes are transformed to Nodes
        match connectedPluginType:
            case "TexCombineColor" | "TexCombineFloat":
                if attrSocket := _getInputSock(node, attrName):
                    _applyTexCombineToSock(connectedPlugin, attrSocket)
                    connectedThroughAttr = "texture"
                else: # Skip node creation if the plugin is connected through unsupported attribute
                    return
            case "TexColorToFloat" | "TexFloatToColor":
                connectedThroughAttr = "input"
            case "MtlSingleBRDF":
                connectedThroughAttr = "brdf"
            case _:
                break

        # Multiplier or converter is not connected
        if connectedThroughAttr not in connectedPlugin["Attributes"]:
            return

        inPluginOutput, connectedPlugin = _getConnectedPluginInput(vrsceneDict, connectedThroughAttr, connectedPlugin)


    # Checking again because the previous "if" could change connectedPlugin's value
    if connectedPlugin:
        connectedPluginType = connectedPlugin['ID']

        collapsedValue = _collapseToValue(connectedPlugin)
        if  attrSocket := _getInputSock(node, attrName) and collapsedValue is not None:
            attrSocket.value = fixValue(collapsedValue)

        else:
            inPluginOutputSocketName = _getPluginOutputSocketName(connectedPlugin, inPluginOutput)

            connectedNode = createNode(ntree, node, vrsceneDict, connectedPlugin, locationsMap)
            if connectedNode:
                if inPluginOutputSocketName not in connectedNode.outputs:
                    # TODO: Convert to exception after fixing the definitions
                    debug.printWarning(f"Node {connectedPluginType} does not have an output socket named {inPluginOutputSocketName}")
                else: 
                    ntree.links.new(connectedNode.outputs[inPluginOutputSocketName], node.inputs[attrSocketName])

                if fixBump:
                    ntree.links.new(getOutputSocketByAttr(connectedNode, 'out_intensity'), node.inputs['Float Texture'])

def _fillNodeProperties(node: bpy.types.Node, ntree: bpy.types.NodeTree, vrsceneDict: dict, pluginDesc: dict, pluginType: str, locationsMap):
    """ Create Node without specific parameters"""

    from vray_blender.plugins import PLUGIN_MODULES

    pluginAttrs = pluginDesc['Attributes']
    pluginModule = PLUGIN_MODULES.get(pluginType)

    if pluginModule is None:
        debug.printError(f"Plugin '{pluginType}' is not yet supported! This shouldn't happen! Please, report this!")
        return None


    # This property group holds all plugin settings
    propGroup = getattr(node, pluginType)

    # list of all "COLOR_TEXTURE" attributes
    colorTexPlugins = [attr for attr in pluginModule.Parameters if attr["type"] == "COLOR_TEXTURE"]

    # Now go through all plugin attributes and check
    # if we should create other nodes or simply set the value
    for attrName in pluginAttrs:

        attrValue = pluginAttrs[attrName]

        # NOTE: Fixes vrscene exported from other applications using deprecated 'bump_tex'
        # attribute
        fixBump = attrName == 'bump_tex'
        if fixBump:
            attrName = 'bump_tex_color'

        attrDesc  = attribute_utils.getAttrDesc(pluginModule, attrName)
        if "ui" in attrDesc and attrDesc["ui"].get("quantityType") == "distance":
            lengthUnit = attrDesc["ui"].get("units", "centimeters")
            attrValue = attribute_utils.scaleToSceneLengthUnit(attrValue, lengthUnit)

        if attrDesc is None:
            # XXX: This could happen when loading VISMATS; error message disabled here...
            # print("Plugin '%s': Attribute '%s' is not yet supported! This is very strange!" % (pluginType, attrName))
            continue

        # Catching socket mismatches during generic nodes creation.
        # Error occurring in the code below could interrupt importing of cosmos asset.
        try:
            attrSocketName = getSocketName(pluginModule, attrName)

            origAttrName = attrName
            # Handling attributes that are represented as COLOR_TEXTURE
            if attrDesc['type'] in ("COLOR", "TEXTURE"):
                for clrTex in colorTexPlugins:
                    if clrTex["color_prop" if attrDesc['type'] == "COLOR" else "tex_prop"] == attrName:
                        attrName = clrTex["attr"]
                        attrSocketName = getSocketName(pluginModule, attrName)

            # Attribute is a output type - nothing to do
            if attrDesc['type'] in attribute_types.NodeOutputTypes:
                continue

            elif attrDesc['type'] == 'MATRIX':
                assert False, "Should not get here. See vrmat_parser.parseVrmat()"
                # Currently we don't support properties of type Matrix in any plugin.
                # Uncomment the following line if such support is needed.
                # _createMatrixNode(ntree, attrValue, attrSocketName, node)

            elif attrDesc['type'] == 'TRANSFORM':
                _createTransformNode(ntree, attrValue, attrSocketName, node)

            elif attrDesc['type'] == 'ENUM':
                _setNodeEnumProperty(attrDesc, attrName, attrValue, pluginType, propGroup)

            elif attrDesc['type'] not in attribute_types.NodeInputTypes:
                _setNodePrimitiveProperty(attrName, attrValue, pluginType, propGroup)

            else:
                # Attribute could possibly be mapped with other node
                # Check if we could find requested node in a vrsceneDict
                if type(attrValue) is str:
                    # Here we need the 'attrName' from the material file. The ".vrmat" file format doesn't contain COLOR_TEXTURE attributes 
                    _createNodeSocketConnection(ntree, node, vrsceneDict, origAttrName, attrSocketName, pluginDesc, fixBump, locationsMap)
                # Attr is not linked - set socket default value
                elif  attrSocket := _getInputSock(node, attrName):
                    attrSocket.value = fixValue(attrValue)

        except Exception as ex:
            debug.printExceptionInfo(ex, "nodes.importing._fillNodeProperties")

    return node

def _createGenericNode(ntree: bpy.types.NodeTree, vrsceneDict: dict, pluginDesc: dict, locationsMap):
    pluginType  = pluginDesc['ID']
    pluginName  = pluginDesc['Name']

    node = NodeUtils.createNode(ntree, f'VRayNode{pluginType}', pluginName)

    _fillNodeProperties(node, ntree, vrsceneDict, pluginDesc, pluginType, locationsMap)

    return node


def createNode(ntree: bpy.types.NodeTree, prevNode: bpy.types.Node, vrsceneDict: dict, pluginDesc: dict, locationsMap=None):
    pluginType  = pluginDesc['ID']
    pluginName  = pluginDesc['Name']

    # TexBitmap is handled by custom meta node
    if pluginType != 'TexBitmap' and pluginType in SKIPPED_PLUGINS:
        debug.printWarning(f"The asset being imported contains a plugin of type {pluginType}, which is not recognized by V-Ray for Blender. Please contact support.")
        return None

    for node in ntree.nodes:
        if node.name == pluginName:
            return ntree.nodes[pluginName]

    match pluginType:
        case 'BRDFLayered':
            return _createNodeBRDFLayered(ntree, prevNode, vrsceneDict, pluginDesc, locationsMap)
        case 'BitmapBuffer':
            return _createNodeBitmapBuffer(ntree, prevNode, vrsceneDict, pluginDesc, locationsMap)
        case 'TexBitmap':
            return _createNodeTexBitmap(ntree, vrsceneDict, pluginDesc, locationsMap)
        case 'TexGradRamp':
            return _createNodeTexGradRamp(ntree, prevNode, vrsceneDict, pluginDesc)
        case 'UVWGenMayaPlace2dTexture' | 'UVWGenProjection' |'UVWGenObject' | 'UVWGenEnvironment':
            return _createMappingNode(ntree, vrsceneDict, pluginDesc, pluginType, locationsMap)


    return _createGenericNode(ntree, vrsceneDict, pluginDesc, locationsMap)
