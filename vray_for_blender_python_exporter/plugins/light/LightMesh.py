from dataclasses import dataclass
import bpy

from vray_blender import debug
from vray_blender.exporting.tools import getLinkedFromSocket
from vray_blender.exporting.plugin_tracker import getObjTrackId
from vray_blender.lib import export_utils, plugin_utils
from vray_blender.lib.defs import AttrPlugin, ExporterContext, PluginDesc
from vray_blender.lib.names import Names
from vray_blender.nodes.tools import isInputSocketLinked
from vray_blender.nodes.utils import getNodeByType, getObjectsFromSelector
from vray_blender.plugins.light.light_tools import onUpdateColorTemperature

plugin_utils.loadPluginOnModule(globals(), __name__)


@dataclass
class ActiveMeshLightInfo:
    """ Used to track active mesh light / gizmo pairs   """
    lightName: str
    lightObjTrackId: int
    gizmoObjTrackId: int

    def __hash__(self):
        # Do not include lightName in the hash as it may change without the object 
        # being otherwize modified.
        return hash((self.lightObjTrackId, self.gizmoObjTrackId))

@dataclass
class UpdatedMeshLightInfo:
    """ Used to track updated mesh light / gizmo pairs """
    lightObj: bpy.types.Object
    gizmoObjTrackId: int
    
    def __hash__(self):
        return hash((self.lightObj, self.gizmoObjTrackId))


def onUpdateAttribute(src, context, attrName):
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
        if isInputSocketLinked(geometrySocket):
            # Even if an empty selector or a wrong node is attached to the 'geometry' socket, we
            # want to suppress the usage of internal objects list because it will be confusing
            # to the users (as list is hidden in the UI).
            exportedObjectSelectorNode = True
    
            linkedNode = getLinkedFromSocket(geometrySocket).node
            
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
        and GeometryExporter resepctively. Both exporters need to know about the existing links in order
        to stay in sync about what is exported by each of them. This function will collect two lists
        of light/geometry object tuples - one for all currently visible in the scene, and one for 
        which depsgraph updates have been generted.

        Returns:
        set[ActiveMeshLightInfo]: currently active mesh light gizmos
        set[UpdatedMeshLightInfo]: list of updated lights/gizmos
    """
    meshLightObjects = [o for o in exporterCtx.sceneObjects if (o.type == 'LIGHT') \
                    and (o.data.vray.light_type == 'MESH') and (getObjTrackId(o) in exporterCtx.visibleObjects)]
    
    if not meshLightObjects:
        return set(), set()
    
    activeMeshLights = set()
    updatedMeshLights = set()

    def isUpdatedPair(updatedObjId, lightObj, geomObj):
        return exporterCtx.fullExport or (updatedObjId == lightObj) or (updatedObjId == lightObj.data.node_tree) or (updatedObjId == geomObj)
    
    def registerPair(lightObj, geomObj):
        activeMeshLights.add(ActiveMeshLightInfo(Names.object(lightObj), getObjTrackId(lightObj), getObjTrackId(geomObj)))
        
        if exporterCtx.fullExport:
            updatedMeshLights.add(UpdatedMeshLightInfo(lightObj, getObjTrackId(geomObj)))
            return
        
        for u in exporterCtx.dg.updates:
            updatedObjId = u.id.original
            if isUpdatedPair(updatedObjId, lightObj, geomObj):
                updatedMeshLights.add(UpdatedMeshLightInfo(lightObj, getObjTrackId(geomObj)))


    for lightObj in meshLightObjects:
        usesSelectorNode = False
        propGroup = lightObj.data.vray.LightMesh

        if ntree := lightObj.data.node_tree:
            # If the mesh light has a node tree, we need to obtain the names of the 
            # target objects from an object selector node attached to its 'Geometry' socket
            if outputNode := getNodeByType(ntree, "VRayNodeLightMesh"):
                propGroup = outputNode.LightMesh
                if fromSock := getLinkedFromSocket(outputNode.inputs['Geometry']):
                    targetObjects = getObjectsFromSelector(fromSock.node, exporterCtx.ctx)

                    for o in targetObjects:
                        registerPair(lightObj, o)
                    
                    usesSelectorNode = True

        if not usesSelectorNode:
            # If a selector node is not connected to the 'geometry' socket, take the list from the
            # template in the property page. Note that the property group will be different depending
            # on whether the light uses nodes or not.
            for targetObject in propGroup.object_selector.getSelectedItems(exporterCtx.ctx, 'objects'):
                registerPair(lightObj, targetObject)

    return activeMeshLights, updatedMeshLights


def getLightMeshPluginName(lightName: str, gizmoObjTrackId):
     return f"{lightName}_{gizmoObjTrackId}"