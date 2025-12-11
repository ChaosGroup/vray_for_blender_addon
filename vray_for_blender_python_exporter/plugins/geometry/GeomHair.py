
import bpy
from vray_blender.lib import plugin_utils, export_utils
from vray_blender.lib.defs import PluginDesc, AttrPlugin, ExporterContext
from vray_blender import debug
from vray_blender.lib.names import Names
from vray_blender.exporting.plugin_tracker import getObjTrackId
from vray_blender.exporting.tools import getLinkedFromSocket

from vray_blender.exporting.node_export import exportNodePlugin
from vray_blender.exporting.hair_export import HairExporter
from vray_blender.nodes.utils import getObjectsFromSelector
import mathutils

plugin_utils.loadPluginOnModule(globals(), __name__)

def getGeomHairPluginName(furObjName: str, meshObjName: str):
    return f"VRayFur@{furObjName}_{meshObjName}"


def exportCustom(exporterCtx: HairExporter, pluginDesc: PluginDesc):
    exportedFurs: list[AttrPlugin] = []  # A list of the exported GeomHair plugins 
    selectedObjects: list[bpy.types.Object] = []  # A list of mesh objects attached to the same VRayFur
    furObject = exporterCtx.objectContext.get()

    def exportVRayFurPlugins(obj: bpy.types.Object, isInstance: bool = False, objName: str = None, dataName: str = None):
        """ Export GeomHair plugin and a node for a V-Ray Fur object """

        objName = objName or Names.object(obj)
        dataName = dataName or Names.objectData(obj)
        if not exporterCtx.persistedState.objDataTracker.dataExported(dataName):
            # Return for objects without valid geometry (those that don't have an entry in objDataTracker).
            # No need to export if the mesh attribute is not set, as GeomHair requires it for correct results.
            return None

        objTrackId = getObjTrackId(obj)

        isVisible = False if isInstance else objTrackId in exporterCtx.visibleObjects

        pluginDesc.name = getGeomHairPluginName(Names.object(furObject), objName)    

        
        geomHairPluginAttr = AttrPlugin(pluginDesc.name)
        if pluginDesc.getAttribute('export_geometry'):
            geomPluginName = exporterCtx.persistedState.objDataTracker.getPluginName(dataName)
            pluginDesc.setAttribute('mesh', AttrPlugin(geomPluginName))
            geomHairPluginAttr = export_utils.exportPluginCommon(exporterCtx, pluginDesc)
            exporterCtx.objTracker.trackPlugin(getObjTrackId(furObject), geomHairPluginAttr.name, isInstance)


        nodePlugin = exportNodePlugin(
            exporterCtx,
            furObject,
            geomHairPluginAttr,
            pluginDesc.name,
            exporterCtx.objTracker,
            visible=isVisible, 
            tmOverride=mathutils.Matrix() if isInstance else obj.matrix_world)

        # This call of trackPlugin() is just to mark the node plugin as instanced (if it is an instance)
        # so that the visibility of the node plugin is not affected by GeometryExporter.syncObjVisibility()
        exporterCtx.objTracker.trackPlugin(getObjTrackId(furObject), nodePlugin.name, isInstance)

        exportedFurs.append(nodePlugin)

    
    if (node := pluginDesc.node) and (socketLink := getLinkedFromSocket(node.inputs['Mesh'])):
        # Even if an empty selector or a wrong node is attached to the 'mesh' socket, we
        # want to suppress the usage of internal objects list because it will be confusing
        # to the users (as list is hidden in the UI).
    
        if socketLink.node.bl_idname not in ('VRayNodeSelectObject', 'VRayNodeMultiSelect'):
            debug.printError(f"A non-selector node attached to 'Mesh' socket of '{node.name}' node," \
                f" belonging to V-Ray Fur object '{furObject.name}'.")
        else:
            selectedObjects = getObjectsFromSelector(socketLink.node, exporterCtx.ctx)
    else:
        # The 'mesh' socket is not linked, use the value from the property page if any
        selectedObjects = pluginDesc.vrayPropGroup.object_selector.getSelectedItems(exporterCtx.ctx, 'objects')
        
    if selectedObjects:
        instancesOfInstancer = exporterCtx.instancesOfInstancer
        for obj in selectedObjects:
            exportVRayFurPlugins(obj)
            
            # Export fur for instanced objects
            for dataName, (instancedObj, objName) in instancesOfInstancer.get(getObjTrackId(obj), {}).items():
                exportVRayFurPlugins(instancedObj, True, objName, dataName)

    return exportedFurs

def collectGeomHairInfo(exporterCtx: ExporterContext):
    furObjects = [o for o in exporterCtx.sceneObjects if o.vray.isVRayFur]

    activeFurInfo, updatedFurInfo, _ = export_utils.collectConnectedMeshInfo(
        exporterCtx, furObjects, 'GeomHair', 'Mesh', 'vray.ntree', 'VRayNodeFurOutput')   

    return activeFurInfo, updatedFurInfo