import bpy



from vray_blender.exporting.node_export import *
from vray_blender.exporting.tools import *
from vray_blender.lib.defs import *
from vray_blender.exporting.plugin_tracker import TrackObj, getObjTrackId, log as trackerLog
from vray_blender.exporting.update_tracker import UpdateTarget, UpdateTracker, UpdateFlags
from vray_blender.nodes import utils as NodesUtils

def _getMtlsOfTextures(textureNames: list[str]):
        """ Return the materials in whose node trees the texture names are used.
         
          NOTE: This is hackish, but for the moment we don't know how to make Blender generate 
          depsgraph updates for the node tree.
        """
        result = set()

        for mtl in [m for m in bpy.data.materials if hasattr(m, 'vray') and m.node_tree]:
            for n in mtl.node_tree.nodes:
                if (texture := getattr(n, 'texture', None)) and (texture.name in textureNames):
                    result.add(mtl)
                
        return result

                
class MtlExporter(ExporterBase):
    """ Export all objects in a depsgraph
    """
    def __init__(self, ctx: ExporterContext):
        super().__init__(ctx)
        # Materials can be shared between objects
        self.nodeTracker = ctx.nodeTrackers['MTL']
        
        # Default material to use for non-vray node trees
        self.defaultMaterial = None


    def exportMtl(self, mtl: bpy.types.Material):
        """ Export material.

        Args:
            mtl (bpy.types.Material): material to export

        Returns:
            tuple(AttrPlugin, SceneStats): the exported plugin and the update to the export statistics
        """
        assert mtl.vray.is_vray_class, "Cannot export non-vray materials"
        
        if mtl in self.exportedMtls:
            return self.exportedMtls[mtl], SceneStats()
        
        assert mtl.node_tree, f"Material has no node tree: {mtl.name}"
        
        nodeCtx = NodeContext(self, bpy.context.scene, bpy.data, self.renderer)
        nodeCtx.rootObj     = mtl
        nodeCtx.nodeTracker = self.nodeTracker
        nodeCtx.ntree       = mtl.node_tree
        
        with nodeCtx:
            if not (nodeOutput := NodesUtils.getOutputNode(mtl.node_tree, 'MATERIAL')):
                NodeContext.registerError(f"Output node not found in material tree")
                return  None, SceneStats()
            
            sock = getInputSocketByName(nodeOutput, "Material")
            assert sock, "Material output node has no input socket"
            
            if not (nodeLink := getNodeLink(sock)):
                # The output node has nothing connected to it. This is a normal situation when the BRDF
                # is changed, so don't report it to the status field.
                debug.printWarning(f"No tree connected to output node in material '{mtl.name}'")
                defaultMtlPlugin = self._exportDefaultMaterial()
                self.exportedMtls[mtl] = defaultMtlPlugin
                return defaultMtlPlugin, SceneStats()
            
            with nodeCtx.push(nodeOutput):
                # Track the plugins exported for each node of the tree
                with TrackObj(self.nodeTracker, getObjTrackId(mtl)):
                    brdfPlugin = exportVRayNode(nodeCtx, nodeLink) if nodeLink else AttrPlugin()

                    with TrackNode(self.nodeTracker, getNodeTrackId(nodeOutput)):
                        singleBRDFMtl = self._exportMtlSingleBRDF(nodeCtx, brdfPlugin)

                    nodeCtx.stats.uniqueMtls.add(mtl.name)
                    nodeCtx.stats.mtls += 1

                self.exportedMtls[mtl] = singleBRDFMtl
        
        return singleBRDFMtl, nodeCtx.stats


    def export(self):
        if self.preview:
            return self._exportPreviewMaterials()
        else:
            return self._exportSceneMaterials()


    def _exportPreviewMaterials(self):
        """ Export materials in the material preview scene """
        stats = SceneStats()
        
        for obj in self.dg.objects:
            # Only export V-Ray materials
            for mtl in [s.material for s in obj.material_slots if s.material.vray.is_vray_class]:
                _, mtlStats = self.exportMtl(mtl)
                if mtlStats:
                    stats += mtlStats

        return stats

    def _tagForUpdateMtlWithSelectorNode(self):
        """ Tag for update object's material if it has and object selector node,
            referencing object with updated transform
        """
        transformUpdates = (u.id.original.session_uid for u in self.dg.updates if u.is_updated_transform)
        for mtl in [m for m in bpy.data.materials if m.vray.is_vray_class and m.node_tree]:
            if not mtl.node_tree:
                continue

            for node in mtl.node_tree.nodes:
                if node.bl_idname == "VRayNodeSelectObject":
                    if node.objectPtr and (node.objectPtr.original.session_uid in transformUpdates):
                        UpdateTracker.tagUpdate(mtl, UpdateTarget.MATERIAL, UpdateFlags.DATA)
                        return


    def _exportSceneMaterials(self):
        stats = SceneStats()

        # All plugins for materials which should not be exported have been removed from VRay and the tracker
        # by the prune procedure. 

        # TODO: replace with materials list collected from the visible objects

        if self.fullExport:
            def mtlIsExportable(mtl: bpy.types.Material):
                usersOfMtl = mtl.users - int(mtl.use_fake_user)
                return getattr(mtl, 'vray', None) and (mtl.vray.is_vray_class) and (usersOfMtl > 0)

            updatedVrayMtls = [m for m in bpy.data.materials if mtlIsExportable(m)]
        else:
            self._tagForUpdateMtlWithSelectorNode()
            mtlUpdates = UpdateTracker.getUpdatesOfType(UpdateTarget.MATERIAL, UpdateFlags.ALL)
            updates = [u.id.original for u in self.dg.updates]
            
            updatedVrayMtlSIDs = {m[0] for m in mtlUpdates}
            updatedVrayMtls = {m for m in bpy.data.materials if (getObjTrackId(m) in updatedVrayMtlSIDs) or (m.vray.is_vray_class and (m.node_tree in updates))}
        
            updatedTextures = [t.name for t in updates if isinstance(t, bpy.types.Texture)]
            updatedVrayMtls = updatedVrayMtls.union(_getMtlsOfTextures(updatedTextures))

        for mtl in updatedVrayMtls:  
            _, mtlStats = self.exportMtl(mtl)
            
            if mtlStats:
                stats += mtlStats
        
        return stats


    def _exportDefaultMaterial(self):
        """ A BRDF material to use for all objects that do not have 
            an explicitly assigned material.
        """
        if not self.defaultMaterial:
            defaultBrdfDesc = PluginDesc("defaultBRDF", "BRDFVRayMtl")
            defaultBrdfDesc.setAttribute("diffuse", AColor((0.5, 0.5, 0.5)))
            defaultBrdf = export_utils.exportPlugin(self, defaultBrdfDesc)

            defaultMtlDesc = PluginDesc("defaultMtl", "MtlSingleBRDF")
            defaultMtlDesc.setAttribute("brdf", defaultBrdf)
            defaultMtlDesc.setAttribute("sceneName", ["DEFAULT_MATERIAL"])
            self.defaultMaterial = export_utils.exportPlugin(self, defaultMtlDesc)
        
        return self.defaultMaterial


    def _exportMtlSingleBRDF(self, nodeCtx: NodeContext, brdfPlugin: AttrPlugin):
        pluginType = 'MtlSingleBRDF'
        pluginName = Names.object(nodeCtx.rootObj)
        plDesc = PluginDesc(pluginName, pluginType)
        
        if not brdfPlugin.isEmpty():
            # We want to have the MtlSingleBRDF plugin even if it does not reference any
            # BSDF plugin, because in this case the object will be rendered in black. If there
            # is no MtlSingleBRDF referenced by the object node, the object will be invisible
            # and this might be confusing to the user.
            mtl = nodeCtx.rootObj
            wrappedMtl = self._exportMtlOptions(nodeCtx, mtl, brdfPlugin)
            plDesc.setAttribute('brdf', wrappedMtl)
        
        plDesc.setAttribute('scene_name',[pluginName])
        plDesc.setAttribute("allow_negative_colors", True)

        return exportPluginWithStats(nodeCtx, plDesc)


    def _exportMtlOptions(self, nodeCtx, mtl, singleBRDFMtl):
        mtlID = self._exportMtlOption(nodeCtx, mtl, singleBRDFMtl, 'MtlMaterialID', 'base_mtl')
        mtlRenderStats = self._exportMtlOption(nodeCtx, mtl, mtlID, 'MtlRenderStats', 'base_mtl')
        mtlWrapper     = self._exportMtlOption(nodeCtx, mtl, mtlRenderStats, 'MtlWrapper', 'base_material')
        mtlRoundEdges  = self._exportMtlOption(nodeCtx, mtl, mtlWrapper, 'MtlRoundEdges', 'base_mtl')

        return mtlRoundEdges

    def _exportMtlOption(self, nodeCtx: NodeContext, mtl, mtlPlugin: AttrPlugin, pluginType: str, baseMtlName: str):
        propGroup = getattr(mtl.vray, pluginType)
        pluginName = Names.pluginObject(pluginType, Names.object(mtl))
        
        if propGroup.use:
            plDesc = PluginDesc(pluginName, pluginType)
            
            # Material options set on the material override the ones set on the object
            plDesc.vrayPropGroup = propGroup
            plDesc.setAttribute(baseMtlName, mtlPlugin)

            return exportPluginWithStats(nodeCtx, plDesc)
        else:
            return mtlPlugin
    

    def prunePlugins(self):
        """ Delete all plugins associated with removed, orphaned or updated materials """
        assert(self.interactive)

        def forgetNodes(mtlId, nodeIds):
             if not nodeIds:
                 return
             
             for nodeId in nodeIds:
                for pluginName in self.nodeTracker.getNodePlugins(mtlId, nodeId):
                    vray.pluginRemove(self.renderer, pluginName)
                    trackerLog(f"REMOVE NODE PLUGIN: {pluginName}")
                self.nodeTracker.forgetNode(mtlId, nodeId)
        
        # Find all materials that are to be shown in the scene
        activeMtls = [mtl for mtl in bpy.data.materials if mtl.vray.is_vray_class and (not isObjectOrphaned(mtl))]

        # Remove from VRay the materials whose node trees' topology has changed.They will be 
        # fully re-exported during the current update cycle 
        topologyUpdates = self._getTopologyUpdates()    
        updatedMtls = [mtl for mtl in activeMtls if self.fullExport or (mtl in topologyUpdates)]

        for mtl in updatedMtls:
            mtlId = getObjTrackId(mtl)
            forgetNodes( mtlId, self.nodeTracker.getOwnedNodes(mtlId))
            # The material plugins have been deleted, generate update events for the objects 
            # that reference this matarial.
            # tagObjectsForMaterial(mtl)
            UpdateTracker.tagMtlTopology(self.ctx, mtl)

        activeMtlIds = [getObjTrackId(mtl) for mtl in activeMtls]

        # Remove from VRay the material trees for objects that have been removed from the scene or orphaned
        removedMtlNodeIds = self.nodeTracker.diffObjs(activeMtlIds)
        for mtlId in removedMtlNodeIds:
            forgetNodes(mtlId, self.nodeTracker.getOwnedNodes(mtlId))
    

    def _getTopologyUpdates(self):
        """ Return the track IDs of the Material data objects whose node tree topology has changed """
        
        topologyUpdates = UpdateTracker.getUpdatesOfType(UpdateTarget.MATERIAL, UpdateFlags.TOPOLOGY)
        return [t[0] for t in topologyUpdates]


def run(ctx: ExporterContext):
    return MtlExporter(ctx).export()
