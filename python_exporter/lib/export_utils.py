# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
import contextlib
import mathutils
import os
import shutil
import subprocess

from vray_blender import debug
from vray_blender.bin import VRayBlenderLib as vray
from vray_blender.exporting import node_export as commonNodesExport
from vray_blender.lib import attribute_utils, blender_utils, plugin_utils, attribute_types, path_utils
from vray_blender.lib.defs import ExporterContext, ExporterType, PluginDesc, AttrPlugin, NodeContext
from vray_blender.lib.names import Names
from vray_blender.nodes.utils import getNonExportablePluginProperties, getNodeByType, getObjectsFromSelector
from vray_blender.plugins import getPluginModule, DEFAULTS_OVERRIDES
from vray_blender.exporting.plugin_tracker import getObjTrackId
from vray_blender.exporting.tools import getLinkedFromSocket, getNodeLinkToNode
from dataclasses import dataclass

# GeomScatter: exported with vrayPropGroup=None; writing descriptor defaults for its unset
# texture params is destructive (e.g. surface_random_density_map=0.0 zeroes the density).
NON_DEFAULT_EXPORTABLE_TYPES = [ "Node", "CameraDefault", "MtlSingleBRDF", "GeomScatter" ]

def _getOverridesFor(ctx: ExporterContext, pluginType: str):
    mode = "PRODUCTION"
    if ctx.preview:
        mode = "PREVIEW"
    elif ctx.interactive:
        mode = "VIEWPORT"

    return DEFAULTS_OVERRIDES.get(mode, {}).get(pluginType, {})


def _convertEnumValue(value):
    # Enum attribute is being reset
    if type(value) is AttrPlugin and value.isEmpty():
        return value

    # Attribute that takes a list of enums
    if type(value) is list:
        return [_convertEnumValue(v) for v in value]

    # Some enums, for whatever reason, are defined only as strings in AppSDK.
    # Try the default conversion to int, if that doesn't work, try converting to string
    with contextlib.suppress(ValueError):
        return int(value)
    return str(value)


class ParamSpec:
    """ Precomputed, object-independent export spec for a single plugin parameter.
        All the static per-parameter filter/flag decisions (derived, output/skipped type,
        linked_only, animatable) are resolved once per plugin type instead of once per object.
    """
    __slots__ = ('attr', 'type', 'desc', 'animatable', 'skipUnlessExplicit')

    def __init__(self, attr, type, desc, animatable, skipUnlessExplicit):
        self.attr = attr
        self.type = type
        self.desc = desc
        self.animatable = animatable
        self.skipUnlessExplicit = skipUnlessExplicit


class ExportDesc:
    __slots__ = ('params', 'isNonDefaultExportable')

    def __init__(self, params, isNonDefaultExportable):
        self.params = params
        self.isNonDefaultExportable = isNonDefaultExportable


def _getExportDesc(pluginModule, pluginType: str) -> ExportDesc:
    # Cache the descriptor on the plugin module itself (like ParametersByAttr). The plugin schema
    # is fixed, so it is built once per plugin type.
    exportDesc = getattr(pluginModule, 'ExportDesc', None)
    if exportDesc is not None:
        return exportDesc

    nonExportableParams = getNonExportablePluginProperties(pluginModule)
    pluginAnimatable = pluginModule.Options.get("animatable", True)

    params = []
    for attrDesc in pluginModule.Parameters:
        attrName = attrDesc['attr']
        attrType = attrDesc['type']

        # Skip parameters marked as non-exportable in the plugin description
        if attrName in nonExportableParams:
            continue

        options = attrDesc.get('options', {})

        # 'derived' attributes are Blender-only and never exported.
        if options.get('derived', False):
            continue

        # Skip output attributes
        if attrType in attribute_types.NodeOutputTypes:
            continue

        # Skipped types and linked-only inputs are exported only when the value is explicit
        # (present in pluginDesc.attrs). That check depends on the object, so it is deferred.
        skipUnlessExplicit = (attrType in attribute_types.SkippedTypes) or \
            (attrType in attribute_types.AllNodeInputTypes and options.get('linked_only', False))

        paramAnimatable = (pluginAnimatable and options.get("animatable", pluginAnimatable))

        params.append(ParamSpec(attrName, attrType, attrDesc, paramAnimatable, skipUnlessExplicit))

    exportDesc = ExportDesc(params, pluginType in NON_DEFAULT_EXPORTABLE_TYPES)
    pluginModule.ExportDesc = exportDesc
    return exportDesc


def exportPluginParams(ctx: ExporterContext, pluginDesc: PluginDesc):
    """ Export plugin from a pre-filled PluginDesc object.

    Returns:
        AttrPlugin: The exported plugin.
    """
    pluginModule = getPluginModule(pluginDesc.type)
    exportDesc = _getExportDesc(pluginModule, pluginDesc.type)

    overriddenParams = _getOverridesFor(ctx, pluginDesc.type)
    attrs = pluginDesc.attrs
    vrayPropGroup = pluginDesc.vrayPropGroup
    isNonDefaultExportable = exportDesc.isNonDefaultExportable
    renderer = ctx.renderer
    pluginName = pluginDesc.name

    # Attributes are counted here rather than at the call sites: this is the bottom of every generic
    # plugin export, so one counter covers settings, materials, node trees and object properties
    # alike. Accumulated in a local and committed once - this is the hottest loop in the exporter.
    # The plugin itself is counted by plugin_utils.createPlugin(), which every caller of this
    # function has already gone through.
    exportedAttrs = 0

    for spec in exportDesc.params:
        attrName = spec.attr
        attrType = spec.type
        isExplicit = attrName in attrs

        if spec.skipUnlessExplicit and not isExplicit:
            continue

        if attrName in overriddenParams:
            # Use the mode-specific user override read from the per-rendering-mode overrides/*.json file.
            value = overriddenParams[attrName]
        elif isExplicit:
            # Use the value set from the export code for the plugin. If a node is created for the plugin,
            # the values of the node input sockets have already been added to the attrs collection.
            value = pluginDesc.getAttribute(attrName)

            # This allows us to use None to skip particular parameter export
            if value is None:
                continue
        else:
            # There is no node for the plugin, get the value from its propGroup
            value = getattr(vrayPropGroup, attrName, None)

        if value is None:
            if isNonDefaultExportable:
                continue
            else:
                value = spec.desc['default']

        if value is None:
            # 'None' is a valid default for some attribute types
            value = AttrPlugin()

        # Handle special attribute types
        if attrType == 'ENUM':
            value = _convertEnumValue(value)

        elif attrType == 'STRING':
            subtype = spec.desc.get('subtype')

            if subtype in ('FILE_PATH', 'VRAY_FILE_PATH'):
                value = path_utils.formatResourcePath(value, allowRelative=ctx.exportOnly)
            elif subtype == 'DIR_PATH':
                # Add a trailing slash to directory paths
                value = os.path.normpath(value) + os.sep

        elif attrType == 'COLOR_TEXTURE':
            # Change the name of the attribute to export to the correct sub-attribute of the meta attribute.
            attrName = spec.desc['tex_prop'] if type(value) == AttrPlugin else spec.desc['color_prop']

            # Resetting the 'tex_prop' because most plugins will use it instead of 'color_prop'.
            if attrName == spec.desc['color_prop']:
                plugin_utils.updateValue(renderer, pluginName, spec.desc['tex_prop'], AttrPlugin())
            elif (flatColor := attrs.get(spec.desc['color_prop'])) is not None:
                # Texture linked: also write the flat color so V-Ray falls back to it, not its default.
                plugin_utils.updateValue(renderer, pluginName, spec.desc['color_prop'], flatColor)

        plugin_utils.updateValue(renderer, pluginName, attrName, attribute_utils.convertUIValueToVRay(spec.desc, value), animatable=spec.animatable, attrType=attrType)
        exportedAttrs += 1

    ctx.sceneStats.addAttrs(exportedAttrs)

    return AttrPlugin(pluginDesc.name, pluginType=pluginDesc.type)


def _exportTemplates(ctx: ExporterContext, pluginDesc: PluginDesc):
    if not pluginDesc.vrayPropGroup:
        # Some template are meant to be exported manually and they are not tied
        # to a specific plugin instance
        return

    pluginModule = getPluginModule(pluginDesc.type)

    for attrDesc in [p for p in pluginModule.Parameters if p['type'] == 'TEMPLATE']:
        attrName = attrDesc['attr']
        template = getattr(pluginDesc.vrayPropGroup, attrName)

        if not attrDesc['options']['template'].get('custom_exporter', False):
            template.exportToPluginDesc(ctx, pluginDesc)


# Export plugin parameters that do not require special handling
# NOTE: You could use this function from inside module's 'exportCustom'
# @param overrideParams - override the default param export
def exportPluginCommon(ctx: ExporterContext, pluginDesc: PluginDesc) -> AttrPlugin:
    plugin_utils.createPlugin(ctx, pluginDesc.name, pluginDesc.type)

    _exportTemplates(ctx, pluginDesc)
    return exportPluginParams(ctx, pluginDesc)


def exportReferencedPluginParams(renderer, ctx: ExporterContext):
    """ Export all plugin parameters that reference other plugins, deferred until the whole scene had
        been exported. At this point every referenceable plugin is guaranteed to exist, so the links
        resolve correctly.
    """
    for param in ctx.referencedPluginParams.values():
        plugin_utils.updateValue(renderer, param.targetPluginName, param.attrName, param.value)

    ctx.referencedPluginParams.clear()


def exportPlugin(ctx: ExporterContext, pluginDesc: PluginDesc) -> AttrPlugin:
    pluginModule = getPluginModule(pluginDesc.type)

    if not hasattr(pluginModule, 'Parameters'):
        debug.printError(f"Module {pluginDesc.type} doesn't have any parameters defined!")
        return AttrPlugin()

    try:
        if hasattr(pluginModule, 'exportCustom'):
            # Plugins may override part or all of the export procedure
            result = pluginModule.exportCustom(ctx, pluginDesc)
            assert result is not None, f"{pluginDesc.type}::exportCustom must return a non-null AttrPlugin object"
        else:
            result = exportPluginCommon(ctx, pluginDesc)
    except Exception as ex:
        debug.printError(f"{type(ex).__name__} while exporting plugin {pluginDesc.name}::{pluginDesc.type}: {ex}")
        # Rethrow the original exception
        raise

    return result


def wrapAsTexture(nodeCtx: NodeContext, listItem: mathutils.Color | AttrPlugin):
    """ Wrap a texture or color in a TexAColor plugin which can be added to a plugin list.

        The reason for wrapping the texture plugins is that non-default output sockets are not supported
        for the items set to a TEXTURE_LIST parameter, so we need to route the socket through a convetrer.
    """

    if isinstance(listItem, mathutils.Color) or listItem is None or (listItem.output != "" and listItem.output != None):
        texAColor = PluginDesc(Names.nextVirtualNode(nodeCtx, "TexAColor"), "TexAColor")
        color = listItem if listItem else mathutils.Color((1.0, 1.0, 1.0))
        texAColor.setAttribute("texture", color)

        return commonNodesExport.exportPluginWithStats(nodeCtx, texAColor)
    else:
        return listItem

def _getMaskTexturePluginNames(pluginBaseName: str):
    return (pluginBaseName + "|buffer", pluginBaseName + "|bitmap")

def exportRenderMaskBitmap(ctx: ExporterContext, imageFileName: str, uvwgen: AttrPlugin, pluginBaseName: str, allowNegativeColors=True):
    """
    Export a TexBitmap and BitmapBuffer that can be used for a texture mask.
    """

    bufferName, texBitmapName = _getMaskTexturePluginNames(pluginBaseName)
    bitmapBufferDesc = PluginDesc(bufferName, 'BitmapBuffer')
    bitmapBufferDesc.setAttributes({
        "file": path_utils.formatResourcePath(imageFileName, ctx.exportOnly),
        "gamma": 1,
        "allow_negative_colors": allowNegativeColors,
        "rgb_color_space": "raw",
        "transfer_function": 0 # linear
    })
    bitmapBuffer = exportPlugin(ctx, bitmapBufferDesc)

    texBitmap = PluginDesc(texBitmapName, 'TexBitmap')
    texBitmap.setAttributes({
        "bitmap": bitmapBuffer,
        "uvwgen": uvwgen
    })

    return exportPlugin(ctx, texBitmap)

def removeBitmapMaskPlugins(ctx: ExporterContext, pluginBaseName: str):
    """
    Remove TexBitmap and BitmapBuffer plugins that were exported for a texture mask.
    """
    bufferName, texBitmapName = _getMaskTexturePluginNames(pluginBaseName)
    removePlugin(ctx, bufferName)
    removePlugin(ctx, texBitmapName)

def removePlugin(exporterCtx: ExporterContext, pluginName):
    vray.pluginRemove(exporterCtx.renderer, pluginName)


def setupDistributedRendering(settings: vray.ExporterSettings, exporterType: ExporterType, isInteractive=False):
    """
    Export the necessary parameters used for distributed rendering i.e. hosts, dispatcher, in process rendering.
    Args:
        settings (ExporterSettings): An existing ExporterSettings instance where the parameters will be set.
        exporterType (ExporterType): The type of export, used to disable DR.
        isInteractive (bool): Flag indicating if the current export is for interactive.
    """
    preferences = blender_utils.getVRayPreferences()
    # Previews should never use DR, Vantage uses DR but it's enabled seperately.
    NON_DR_EXPORTERS = [ ExporterType.PREVIEW, ExporterType.VANTAGE_LIVE_LINK ]
    vrayDR = bpy.context.scene.vray.VRayDR
    if exporterType not in NON_DR_EXPORTERS and (not isInteractive or not vrayDR.ignoreInInteractive):
        settings.drUse = vrayDR.on
        settings.drRenderOnlyOnHosts = preferences.render_only_on_nodes
        if preferences.use_remote_dispatcher:
            settings.remoteDispatcher = preferences.dispatcher.address + ":" + str(preferences.dispatcher.port)
        hosts = []
        for node in preferences.nodes:
            if node.use:
                hosts.append(node.address + ":" + str(node.port))
        settings.setDRHosts(hosts)
    elif exporterType == ExporterType.VANTAGE_LIVE_LINK:
        settings.drUse = True
        settings.setDRHosts([preferences.vantage_host + ":" + str(preferences.vantage_port)])


def setupVRayProfiler(settings: vray.ExporterSettings, exporterType: ExporterType):
    """
    Export the V-Ray Profiler settings configured in the 'V-Ray Profiler' UI panel.
    Args:
        settings (ExporterSettings): An existing ExporterSettings instance where the parameters will be set.
        exporterType (ExporterType): The type of export. The profiler is only used for production renders (PROD, ANIMATION).
    """
    if exporterType not in (ExporterType.PROD, ExporterType.ANIMATION):
        return

    vrayProfiler = blender_utils.getVRayPreferences().VRayProfiler

    profilerEnabled = (vrayProfiler.mode != '0') and bool(vrayProfiler.outputDirectory)
    settings.profilerMode             = int(vrayProfiler.mode) if profilerEnabled else 0
    settings.profilerMaxDepth         = vrayProfiler.maxDepth
    settings.profilerOutputDirectory  = vrayProfiler.outputDirectory
    settings.profilerSceneName        = os.path.splitext(os.path.basename(bpy.data.filepath))[0] or "untitled"


@dataclass
class ActiveConnectedMeshInfo:
    """ Used to track active parent object / gizmo pairs   """
    parentName: str
    parentBlObjName: str # value of bpy.types.Object.name
    parentObjTrackId: int

    gizmoObjName: str
    gizmoObjTrackId: int

    def __hash__(self):
        # Do not include parentName in the hash as it may change without the object
        # being otherwise modified.
        return hash((self.parentObjTrackId, self.gizmoObjTrackId))

@dataclass
class UpdatedConnectedMeshInfo:
    """ Used to track updated connected parent object / gizmo pairs """
    parentObj: bpy.types.Object
    gizmoObjTrackId: int

    def __hash__(self):
        return hash((self.parentObj, self.gizmoObjTrackId))


def collectConnectedMeshInfo(exporterCtx: ExporterContext, parentObjects: list[bpy.types.Object],
    pluginType: str, socketName: str, treePath: str, outputNodeType: str):
    """ Collect info about the visible and updated objects selected by the output node.

        LightMesh and VRayFur combine a light|fur object and a geometry object, which are exported by LightExporter
        and GeometryExporter respectively. Both exporters need to know about the existing links in order
        to stay in sync about what is exported by each of them. This function will collect two lists
        of (light|fur)/geometry object tuples - one for all currently visible in the scene, and one for
        which depsgraph updates have been generated.

        Args:
        pluginType: plugin type as which the output node is exported
        socketName: socket name of the output node
        treePath: path to the data property group of the object
        outputNodeType: type of the output node

        Returns:
        set[ActiveConnectedMeshInfo]: currently active connected mesh gizmos
        set[UpdatedConnectedMeshInfo]: list of updated connected meshes/gizmos
        set[UpdatedConnectedMeshInfo]: the update info for all gizmos from the first set
    """

    if not parentObjects:
        return set(), set(), set()

    activeConnectedMeshes = set()
    updatedConnectedMeshes = set()
    activeMeshesUpdateInfo = set()


    # The ids of everything the depsgraph reported, resolved once instead of re-iterating
    # dg.updates for every parent object below.
    updatedIds = exporterCtx.dgUpdates['all']
    processedObjects = exporterCtx.persistedState.processedObjects

    def isUpdatedPair(parentObj, geomObj):
        """ A pair is updated if the depsgraph reported either half, or if either half has never
            been exported.

            NOTE - deliberate behaviour change: this used to be evaluated inside a
            `for u in exporterCtx.dg.updates` loop, so an empty update list registered nothing even
            for a pair the exporter had never seen. The never-exported half has to be exported
            regardless of what the depsgraph reports, so gating it on dg.updates being non-empty was
            wrong; the loop was only ever there to resolve the reported ids, which are now indexed
            once by ExporterContext._buildUpdateIndex(). Reachable only where dg.updates can be
            empty (dg.updates is populated only inside depsgraph_update_post), i.e. never in IPR.
        """
        return any((getObjTrackId(ob) in updatedIds) or (getObjTrackId(ob) not in processedObjects)
                   for ob in (parentObj, geomObj))

    def registerPair(parentObj, geomObj, ntree=None):
        parentTrackId = getObjTrackId(parentObj)
        geomTrackId = getObjTrackId(geomObj)

        activeConnectedMeshes.add(ActiveConnectedMeshInfo(Names.object(parentObj), parentObj.name, parentTrackId, Names.object(geomObj), geomTrackId))
        activeMeshesUpdateInfo.add(UpdatedConnectedMeshInfo(parentObj, geomTrackId))

        if exporterCtx.fullExport:
            updatedConnectedMeshes.add(UpdatedConnectedMeshInfo(parentObj, geomTrackId))
            return

        if isUpdatedPair(parentObj, geomObj) or ((ntree is not None) and (getObjTrackId(ntree) in updatedIds)):
            updatedConnectedMeshes.add(UpdatedConnectedMeshInfo(parentObj, geomTrackId))

    def getNodeTree(obj: bpy.types.Object, treePath: str):
        props = obj
        for name in treePath.split("."):
            if not (props := getattr(props, name)):
                return None
        return props

    for parentObj in parentObjects:
        usesSelectorNode = False
        propGroup = getattr(parentObj.data.vray, pluginType)

        if ntree := getNodeTree(parentObj, treePath):
            # If the mesh light has a node tree, we need to obtain the names of the
            # target objects from an object selector node attached to its 'Geometry' socket
            if outputNode := getNodeByType(ntree, outputNodeType):
                propGroup = getattr(outputNode, pluginType)
                if fromSock := getLinkedFromSocket(outputNode.inputs[socketName]):
                    targetObjects = getObjectsFromSelector(fromSock.node, exporterCtx.ctx)

                    for o in targetObjects:
                        registerPair(parentObj, o, ntree)

                    usesSelectorNode = True

        if not usesSelectorNode:
            # If a selector node is not connected to the 'geometry' socket, take the list from the
            # template in the property page. Note that the property group will be different depending
            # on whether the light uses nodes or not.
            for targetObject in propGroup.object_selector.getSelectedItems(exporterCtx.ctx, 'objects'):
                registerPair(parentObj, targetObject)


    return activeConnectedMeshes, updatedConnectedMeshes, activeMeshesUpdateInfo


def exportObjProperties(obj: bpy.types.Object, exporterCtx: ExporterContext, renderer, objTracker, nodeOutput, nodePluginNames):
    """ Exports the nodes connected in the "Matte", "Surface" and "Visibility" sockets of
        object output node, and maps Blender visibility/holdout flags to VRayObjectProperties.
    """

    pluginType = "VRayObjectProperties"
    objProps = obj.vray.VRayObjectProperties
    objPropsPluginName = Names.pluginObject(pluginType, Names.object(obj))
    objPropsPlDesc = PluginDesc(objPropsPluginName, pluginType)
    objPropsPlDesc.vrayPropGroup = objProps

    pluginModule = getPluginModule(pluginType)

    # Reset all attributes to their default values first.
    # This ensures that attributes are always initialized to defaults,
    # and then selectively overridden by node connections if present.
    for attrDesc in pluginModule.Parameters:
        objPropsPlDesc.setAttribute(attrDesc['attr'], attrDesc.get('default', None))

    # Map Blender's Holdout and Indirect Only toggles to VRayObjectProperties. These sit below
    # the V-Ray node connections, which override them. The Blender per-ray visibility options are
    # intentionally ignored - that panel is hidden in V-Ray.
    # NOTE: 'obj' is evaluated, so it must be paired with view_layer_eval, not view_layer,
    # or the Base lookup inside holdout_get()/indirect_only_get() silently returns False.
    viewLayerEval = exporterCtx.dg.view_layer_eval
    if obj.holdout_get(view_layer=viewLayerEval):
        objPropsPlDesc.setAttribute("matte_surface", True)
        objPropsPlDesc.setAttribute("alpha_contribution", -1.0)
    elif obj.indirect_only_get(view_layer=viewLayerEval):
        objPropsPlDesc.setAttribute("camera_visibility", False)

    # V-Ray node connections have the highest priority and override everything above.
    if nodeOutput:
        for objProp in ("Matte", "Surface", "Visibility"):
            nodeLink = getNodeLinkToNode(nodeOutput, objProp, f"VRayNodeObject{objProp}Props")
            if nodeLink:
                # Only the attributes of connected object property nodes should be exported
                node = nodeLink.from_node
                for attr in node.visibleAttrs:
                    objPropsPlDesc.setAttribute(attr, getattr(objProps, attr))

                if objProp == "Visibility":
                    node.fillReflectAndRefractLists(exporterCtx, objPropsPlDesc)

    objId = getObjTrackId(obj)

    objPropsAttrPlugin = exportPlugin(exporterCtx, objPropsPlDesc)

    objTracker.trackPlugin(objId, objPropsPluginName)

    # The fur objects have multiple node plugins, so we need to update the object properties for each of them
    for nodePluginName in nodePluginNames:
        plugin_utils.updateValue(renderer, nodePluginName, "object_properties", objPropsAttrPlugin, animatable=False)

def isObjectGeomUpdated(exporterCtx: ExporterContext, objTrackId: int):
    """ Check if the object needs to be updated. """
    isFirstMotionBlurFrame = (exporterCtx.commonSettings.exportMotionData and exporterCtx.motionBlurBuilder.isFirstFrame(exporterCtx.currentFrame))

    return exporterCtx.fullExport \
            or (objTrackId in exporterCtx.dgUpdates['geometry']) \
            or isFirstMotionBlurFrame \
            or (objTrackId not in exporterCtx.persistedState.processedObjects)

def isObjectTreeUpdated(exporterCtx: ExporterContext, obj: bpy.types.Object):
    """ Check if the object's "OBJECT" node tree has been updated. """
    objTrackId = getObjTrackId(obj)
    return objTrackId in exporterCtx.dgUpdates['shading'] and \
            exporterCtx.ctx.scene.vray.ActiveNodeEditorType == "OBJECT"


def packExportedScene(scenePath: str, archive: bool) -> str:
    """ Pack all assets referenced by an exported .vrscene into a folder next to it using
        the Chaos Cloud binary, optionally archiving that folder into a .zip file.

        Mirrors the 'Pack'/'Zip' behaviour of the 3ds Max .vrscene exporter, but uses Python's
        stdlib for zipping (we do not ship a 7-Zip binary).

        @param scenePath  full path to the exported .vrscene file
        @param archive    whether to zip the packed folder (and remove the folder afterwards)
        @return an error message on failure, or an empty string on success
    """
    cloudBinary = blender_utils.getVRayPreferences().vray_cloud_binary
    if not cloudBinary:
        return "Chaos Cloud is not installed, cannot pack the scene"

    scenePath = os.path.normpath(scenePath)
    sceneDir  = os.path.dirname(scenePath)
    sceneName = os.path.splitext(os.path.basename(scenePath))[0]

    # 'ccloud pack' collects the scene and its assets into <sceneDir>/<sceneName>/
    packedDir = os.path.join(sceneDir, sceneName)

    cmd = [cloudBinary, "pack", "--output", sceneDir, "--sceneFile", scenePath]
    try:
        if subprocess.call(cmd, env=os.environ) != 0:
            return "Chaos Cloud failed to pack the scene, check the console"

        if not os.path.isdir(packedDir):
            return "Packed scene folder was not created"

        if archive:
            # Archive the contents of the packed folder so the .vrscene sits at the zip root.
            shutil.make_archive(packedDir, 'zip', root_dir=packedDir)

            # Remove the intermediate packed folder to save space, mirroring 3ds Max.
            shutil.rmtree(packedDir, ignore_errors=True)
    except OSError as ex:
        # e.g. ccloud was removed after start-up (FileNotFoundError), or the archive step hit an IO error.
        # Report it without failing the export - the .vrscene itself was written successfully.
        return f"Failed to pack the scene: {ex}"

    return ""