import bpy
import mathutils
from collections import defaultdict
from vray_blender.bin import VRayBlenderLib as vray
from vray_blender.lib.names import Names

from vray_blender.nodes import utils as NodesUtils
from vray_blender.plugins.geometry.GeomHair import getGeomHairPluginName
from vray_blender.exporting.node_export import NodeContext, exportNodeTree, exportNodePlugin
from vray_blender.exporting.plugin_tracker import TrackObj
from vray_blender.lib.defs import AttrPlugin, ExporterBase, ExporterContext, PluginDesc
from vray_blender.lib.export_utils import exportPluginCommon, ActiveConnectedMeshInfo, isObjectGeomUpdated
from vray_blender.exporting.plugin_tracker import getObjTrackId

def syncFurInfo(exporterCtx: ExporterContext):
    """ Collect info about the visible and updated objects associated with Fur exports. """
    from vray_blender.plugins.geometry.GeomHair import collectGeomHairInfo
    
    # Collect the info for the current update 
    activePairs, updatedPairs = collectGeomHairInfo(exporterCtx)
    exporterCtx.activeFurInfo = activePairs
    exporterCtx.updatedFurInfo = updatedPairs


class FurExporter(ExporterBase):
    def __init__(self, ctx: ExporterContext):
        super().__init__(ctx)
        self.nodeTracker = ctx.nodeTrackers['OBJ']
        self.objTracker = ctx.objTrackers['OBJ']
        
        self.exportedFurPluginDesc: dict[int, tuple[bpy.types.Object, PluginDesc]] = {}

        # Maps track id of object to a list of fur track ids that have this object in their selection.
        self._furGizmoObjTrackIdMap = defaultdict(list)
        for p in self.activeFurInfo:
            self._furGizmoObjTrackIdMap[p.gizmoObjTrackId].append((p.parentObjTrackId, p.parentName))

        self.updatedFurGizmoObjTrackIdSet = set((p.gizmoObjTrackId, getObjTrackId(p.parentObj)) for p in self.updatedFurInfo) # Objects selected by fur objects that have been updated.

    def purgeFurInfo(self):
        instTracker = self.objTrackers['INSTANCER']
        disconnectedFurs = [p for p in self.persistedState.activeFurInfo.difference(self.activeFurInfo)]

        def removeFurPlugin(pluginName: str, furInfo: ActiveConnectedMeshInfo):
            if furInfo.gizmoObjName in pluginName:
                vray.pluginRemove(self.renderer, pluginName)
                return True
            return False


        for f in disconnectedFurs:
            for pluginName in self.objTracker.getOwnedPlugins(f.parentObjTrackId):
                if removeFurPlugin(pluginName, f):
                    self.objTracker.forgetPlugin(f.parentObjTrackId, pluginName)

            # Remove instancer plugins if any
            for pluginName in instTracker.getOwnedPlugins(f.parentObjTrackId):
                if removeFurPlugin(pluginName, f):
                    instTracker.forgetPlugin(f.parentObjTrackId, pluginName)

        # Remove furs for disconnected instancers
        disconnectedInstancers = self.persistedState.activeInstancers.difference(self.activeInstancers)
        for f in self.activeFurInfo:
            if f.gizmoObjTrackId in disconnectedInstancers:
                for pluginName in instTracker.getOwnedPlugins(f.parentObjTrackId):
                    if removeFurPlugin(pluginName, f):
                        instTracker.forgetPlugin(f.parentObjTrackId, pluginName)

    def addFurObjectsForExport(self):
        """ Add the fur objects for export to the furPluginDesc dictionary. """
        for inst in self.dg.object_instances:
            if inst.is_instance:
                continue
            
            obj = inst.object
            if obj.vray.isVRayFur:
                furPluginDesc = self._getPluginDescOfFurObject(obj)
                self.exportedFurPluginDesc[getObjTrackId(obj)] = obj.original, furPluginDesc
        

    def _getPluginDescOfFurObject(self, furObj: bpy.types.Object):
        assert furObj.vray.isVRayFur, f"Fur object expected: {furObj.name}"

        self.persistedState.processedObjects.add(getObjTrackId(furObj)) # Mark the object as processed, even if it is not exported.

        pluginDesc = PluginDesc("", "GeomHair")
        
        with self.objectContext.push(furObj):
            if NodesUtils.treeHasNodes(furObj.vray.ntree) and \
                (furOutputNode := NodesUtils.getOutputNode(furObj.vray.ntree)):
                
                pluginDesc.vrayPropGroup = getattr(furOutputNode, furOutputNode.vray_plugin)
                pluginDesc.node = furOutputNode

                nodeCtx = NodeContext(self, furObj, self.ctx.scene, self.renderer)
                nodeCtx.rootObj     = furObj
                nodeCtx.nodeTracker = self.nodeTracker
                nodeCtx.ntree       = furObj.vray.ntree

                with nodeCtx, nodeCtx.push(furOutputNode):
                    objTrackId = getObjTrackId(furObj)

                    if nodeCtx.getCachedNodePlugin(furOutputNode) is None:
                        nodeCtx.cacheNodePlugin(furOutputNode)
                        with TrackObj(self.nodeTracker, objTrackId):
                            exportNodeTree(nodeCtx, pluginDesc)
            else:
                pluginDesc.vrayPropGroup = furObj.data.vray.GeomHair

        return pluginDesc

    def exportFursOfObject(self, obj: bpy.types.Object, instance: bpy.types.DepsgraphObjectInstance = None):
        """ Export the GeomHair plugins for the fur objects that have selected the given object. """

        gizmoObjTrackId = getObjTrackId(obj) if not instance else getObjTrackId(instance.parent)
        return [
            (nodePlugin.name, furTrackId, furName)
            for furTrackId, furName in self._furGizmoObjTrackIdMap[gizmoObjTrackId]
            if (nodePlugin := self._exportGeomHairPlugin(furTrackId, obj, instance))
        ]

    def syncVisibility(self, object: bpy.types.Object, isShown: bool):
        objTrackId = getObjTrackId(object)
        for furTrackId, _ in self._furGizmoObjTrackIdMap[objTrackId]:
            for pluginName in self.objTracker.getPlugins(furTrackId):
                if pluginName.startswith("node@") and Names.object(object) in pluginName:
                    vray.pluginUpdateInt(self.renderer, pluginName, "visible", isShown)

    def _exportGeomHairPlugin(self, furObjTrackId: int, geometryObject: bpy.types.Object,
        instance: bpy.types.DepsgraphObjectInstance = None):
        
        objTrackId = getObjTrackId(geometryObject)
        furObject, furPluginDesc = self.exportedFurPluginDesc.get(furObjTrackId, (None, None))

        if (not furObject) or (not furPluginDesc):
            # The fur plugin description is not exported, because the fur object is not visible.
            return None

        if (furObjTrackId, objTrackId) in self.updatedFurGizmoObjTrackIdSet:
            # No updates, it's pointless to export the plugin again.
            return None
        
        objName = Names.object(geometryObject, instance)
        dataName = Names.objectData(geometryObject, instance)
        isInstance = instance is not None


        isVisible = False if isInstance else ((objTrackId in self.visibleObjects) and (furObjTrackId in self.visibleObjects))

        furPluginName = getGeomHairPluginName(Names.object(furObject), objName)
        furPluginDesc.name = furPluginName

        if (not self.persistedState.objDataTracker.dataExported(dataName)) and \
            (furPluginName not in self.objTracker.getOwnedPlugins(furObjTrackId)):
            
            # Return for objects without valid geometry (those that don't have an entry in objDataTracker).
            # No need to export if the mesh attribute is not set, as GeomHair requires it for correct results.
            # If the GeomHair plugin is already tracked, it means that the geometry of the object has been removed during the rendering.
            # In that case export empty plugin for its mesh attribute in order to hide the fur corresponding to this object.
            return None
        
        geomPlugin = AttrPlugin(self.persistedState.objDataTracker.getPluginName(dataName) or "")

        furPluginDesc.setAttribute('mesh', geomPlugin)
        furPluginAttr = exportPluginCommon(self, furPluginDesc)
        self.objTracker.trackPlugin(furObjTrackId, furPluginName, isInstance)

        nodePlugin = exportNodePlugin(
            self,
            furObject,
            furPluginAttr,
            furPluginName,
            self.objTracker,
            visible=isVisible, 
            tmOverride=mathutils.Matrix() if isInstance else geometryObject.matrix_world)

        # This call of trackPlugin() is just to mark the node plugin as instanced (if it is an instance)
        # so that the visibility of the node plugin is not affected by GeometryExporter.syncObjVisibility()
        self.objTracker.trackPlugin(furObjTrackId, nodePlugin.name, isInstance)

        return nodePlugin
        
        