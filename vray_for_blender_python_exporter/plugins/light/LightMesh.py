import bpy

from vray_blender import debug
from vray_blender.exporting.tools import getFarNodeLink
from vray_blender.exporting.plugin_tracker import getObjTrackId
from vray_blender.lib import export_utils, plugin_utils
from vray_blender.lib.defs import AttrPlugin, ExporterContext, PluginDesc
from vray_blender.lib.names import Names
from vray_blender.plugins.light.light_tools import onUpdateColorTemperature

plugin_utils.loadPluginOnModule(globals(), __name__)


def nodeUpdate(node: bpy.types.Node):
    if node.mute:
        node.mute = False


def onUpdateAttribute(src, context: bpy.types.Context, attrName: str):
    onUpdateColorTemperature(src, 'LightMesh', attrName)


def exportCustom(ctx: ExporterContext, pluginDesc: PluginDesc):
    # The LightMesh node allows either a single or a group object selector to be plugged into it.
    # This procedure will export one instance of LightMesh for each selected object and will
    # return a list of the exported plugins (instead of a single plugin)
    meshObjects: list[bpy.types.Mesh] = []  # A list of mesh objects attached to the same LightMesh
    exportedLights: list[AttrPlugin]   = []  # A list of the expored LightMesh plugins 

    exportedObjectSelectorNode = False

    if node := pluginDesc.node:
        geometrySocket = node.inputs['Geometry']
        if geomLink := getFarNodeLink(geometrySocket):
            # Even if an empty selector or a wrong node is attached to the 'geometry' socket, we
            # want to suppress the usage of internal objects list because it will be confusing
            # to the users (as list is hidden in the UI).
            exportedObjectSelectorNode = True
    
            linkedNode = geomLink.from_node
            
            if linkedNode.bl_idname not in ('VRayNodeSelectObject', 'VRayNodeMultiSelect'):
                debug.printError(f"A non-selector node attached to 'Geometry' socket of {node.name}.")
            elif linkedItem := pluginDesc.getAttribute('geometry'): 
                if type(linkedItem) is list:
                    # The node has an group selector connected to its 'geometry' socket
                    meshObjects = [pl.auxData['object'] for pl in linkedItem]
                elif (type(linkedItem) is AttrPlugin) and (not linkedItem.isEmpty()):
                    # The node has an object selector connected to its 'geometry' socket
                    meshObjects = [linkedItem.auxData['object']]

    if not exportedObjectSelectorNode:
        # The 'geometry' socket is not linked, use the value from the property page if any
        meshObjects = pluginDesc.vrayPropGroup.object_selector.getSelectedItems(ctx.ctx, 'objects')
        
    if meshObjects:
        baseName = pluginDesc.name
        
        # LightMesh plugin can only have one target geometry, so export one LightMesh plugin for each geometry
        for meshObj in meshObjects:
            meshName = Names.objectData(meshObj)
            pluginDesc.name = getLightMeshPluginName(baseName, getObjTrackId(meshObj))

            pluginDesc.setAttributes({
                'geometry': AttrPlugin(meshName),
                'transform' : meshObj.matrix_world
            })
            
            if lightObj := ctx.objectContext.get():
                if userAttributes := lightObj.vray.UserAttributes.getAsString():
                    pluginDesc.setAttribute('user_attributes', userAttributes)
            else:
                debug.printError(f"LightMesh {baseName} has no object context set. User attributes not exported.")
                
            exportedLights.append(export_utils.exportPluginCommon(ctx, pluginDesc))

    return exportedLights


def collectLightMeshInfo(exporterCtx: ExporterContext):
    """ Collect info about the visible and updated objects associated with LightMesh exports.

        LightMesh combines a light object and a geometry object, which are exported by LightExporter
        and GeometryExporter respectively. Both exporters need to know about the existing links in order
        to stay in sync about what is exported by each of them. This function will collect two lists
        of light/geometry object tuples - one for all currently visible in the scene, and one for 
        which depsgraph updates have been generated.

        Returns:
        set[ActiveConnectedMeshInfo]: currently active mesh light gizmos
        set[UpdatedConnectedMeshInfo]: list of updated lights/gizmos
    """

    meshLightObjects = [o for o in exporterCtx.sceneObjects if (o.type == 'LIGHT') \
                    and (o.data.vray.light_type == 'MESH') and (getObjTrackId(o) in exporterCtx.visibleObjects)]

    return export_utils.collectConnectedMeshInfo(
        exporterCtx, meshLightObjects, 'LightMesh', 'Geometry', 'data.node_tree', 'VRayNodeLightMesh')   


def getLightMeshPluginName(lightName: str, gizmoObjTrackId):
     return f"{lightName}_{gizmoObjTrackId}"