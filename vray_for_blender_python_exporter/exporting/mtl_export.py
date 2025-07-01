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


def _tagForUpdateMtlWithSelectorNode(exporterCtx: ExporterContext):
    """ Tag for update object's material if it has and object selector node,
        referencing object with updated transform
    """
    transformUpdates = (u.id.original.session_uid for u in exporterCtx.dg.updates if u.is_updated_transform)
    for mtl in [m for m in bpy.data.materials if m.node_tree]:
        if not mtl.node_tree:
            continue

        for node in mtl.node_tree.nodes:
            if node.bl_idname == "VRayNodeSelectObject":
                if node.objectPtr and (node.objectPtr.original.session_uid in transformUpdates):
                    UpdateTracker.tagUpdate(mtl, UpdateTarget.MATERIAL, UpdateFlags.DATA)
                    return


def calculateMatExportCache(exporterCtx: ExporterContext):
    """ Refresh the cache of exported materials based on scene updates.

        If a full export is requested, clear the entire cache.
        Otherwise, determine which materials have been updated and remove them from
        the cache so that they will be re-exported.
    """
    if exporterCtx.fullExport:
        exporterCtx.exportedMtls.clear()
    else:
        _tagForUpdateMtlWithSelectorNode(exporterCtx)
        mtlUpdates = UpdateTracker.getUpdatesOfType(UpdateTarget.MATERIAL, UpdateFlags.ALL)
        updates = [u.id.original for u in exporterCtx.dg.updates]

        updatedVrayMtlSIDs = {m[0] for m in mtlUpdates}
        updatedVrayMtls = {m for m in bpy.data.materials if (getObjTrackId(m) in updatedVrayMtlSIDs) or (m and (m.node_tree in updates))}

        updatedTextures = [t.name for t in updates if isinstance(t, bpy.types.Texture)]
        updatedVrayMtls = updatedVrayMtls.union(_getMtlsOfTextures(updatedTextures))

        for m in (exporterCtx.exportedMtls.keys() & updatedVrayMtls):
            del exporterCtx.exportedMtls[m]


class MtlExporter(ExporterBase):
    """ Export all objects in a depsgraph
    """
    def __init__(self, ctx: ExporterContext):
        super().__init__(ctx)
        # Materials can be shared between objects
        self.nodeTracker = ctx.nodeTrackers['MTL']

        # Default material to use for non-vray node trees
        self.defaultMaterial = None

    def exportMtl(self, mtl: bpy.types.Material, nodeCtx: NodeContext = None):
        """ Export material.

        Args:
            mtl (bpy.types.Material): material to export

        Returns:
            tuple(AttrPlugin, SceneStats): the exported plugin and the update to the export statistics
        """

        if mtl in self.exportedMtls:
            return self.exportedMtls[mtl], SceneStats()

        assert mtl.use_nodes and mtl.node_tree, f"Material has no node tree: {mtl.name}"

        isVRayMtl = mtl.vray.is_vray_class

        if nodeCtx is None:
            nodeCtx = NodeContext(self, bpy.context.scene, bpy.data, self.renderer)
            nodeCtx.rootObj     = mtl
            nodeCtx.nodeTracker = self.nodeTracker
            nodeCtx.ntree       = mtl.node_tree
        
        with nodeCtx:
            if not (nodeOutput := NodesUtils.getOutputNode(mtl.node_tree, 'MATERIAL')) and not (nodeOutput := NodesUtils.getOutputNode(mtl.node_tree, 'SHADER')):
                NodeContext.registerError(f"Output node not found in material tree")
                return  None, SceneStats()

            sock = None
            if isVRayMtl:
                sock = getInputSocketByName(nodeOutput, "Material")
            if sock is None:
                sock = getInputSocketByName(nodeOutput, "Surface")
            assert sock, "Material output node has no input socket"

            if not (nodeLink := getNodeLink(sock)):
                # The output node has nothing connected to it. This is a normal situation when the BRDF
                # is changed, so don't report it to the status field.
                debug.printWarning(f"No tree connected to output node in material '{mtl.name}'")
                defaultMtlPlugin = MtlExporter.exportDefaultMaterial(self)
                self.exportedMtls[mtl] = defaultMtlPlugin
                return defaultMtlPlugin, SceneStats()
            
            with nodeCtx.push(nodeOutput):
                # Track the plugins exported for each node of the tree
                if (singleBRDFMtl := nodeCtx.getCachedNodePlugin(nodeOutput)) is None:
                    with TrackObj(self.nodeTracker, getObjTrackId(mtl)):
                        brdfPlugin = exportVRayNode(nodeCtx, nodeLink) if nodeLink else AttrPlugin()

                        with TrackNode(self.nodeTracker, getNodeTrackId(nodeOutput)):
                            singleBRDFMtl = self._exportMtlSingleBRDF(nodeCtx, brdfPlugin)

                        nodeCtx.stats.uniqueMtls.add(mtl.name)
                        nodeCtx.stats.mtls += 1

                    self.exportedMtls[mtl] = singleBRDFMtl
                    nodeCtx.cacheNodePlugin(nodeOutput, singleBRDFMtl)
        
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

    def _exportSceneMaterials(self):
        stats = SceneStats()

        def mtlIsExportable(mtl: bpy.types.Material):
            usersOfMtl = mtl.users - int(mtl.use_fake_user)
            return mtl.use_nodes and usersOfMtl > 0

        for mtl in bpy.data.materials:
            if not mtlIsExportable(mtl):
                continue

            _, mtlStats = self.exportMtl(mtl)

            if mtlStats:
                stats += mtlStats

        return stats

    @staticmethod
    def exportDefaultMaterial(exporterCtx: ExporterContext):
        """ A BRDF material to use for all objects that do not have 
            an explicitly assigned material.
        """
        DEFAULT_PLUGIN_TYPE = "MtlSingleBRDF"
        if DEFAULT_PLUGIN_TYPE in exporterCtx.defaultPlugins:
            return exporterCtx.defaultPlugins[DEFAULT_PLUGIN_TYPE]
        else:
            defaultBrdfDesc = PluginDesc("defaultBRDF", "BRDFVRayMtl")
            defaultBrdfDesc.setAttribute("diffuse", AColor((0.5, 0.5, 0.5)))
            defaultBrdf = export_utils.exportPlugin(exporterCtx, defaultBrdfDesc)

            defaultMtlDesc = PluginDesc("defaultMtl", "MtlSingleBRDF")
            defaultMtlDesc.setAttribute("brdf", defaultBrdf)
            defaultMtlDesc.setAttribute("sceneName", ["DEFAULT_MATERIAL"])
            defaultMaterial = export_utils.exportPlugin(exporterCtx, defaultMtlDesc)
            exporterCtx.defaultPlugins[DEFAULT_PLUGIN_TYPE] = defaultMaterial
            return defaultMaterial


    def _exportMtlSingleBRDF(self, nodeCtx: NodeContext, brdfPlugin: AttrPlugin):
        pluginType = 'MtlSingleBRDF'
        pluginName = Names.object(nodeCtx.rootObj)
        plDesc = PluginDesc(pluginName, pluginType)

        # We want to have the MtlSingleBRDF plugin even if it does not reference any
        # BSDF plugin (e.g. brdfPlugin is empty) because in this case the object will 
        # be rendered in black. If there is no MtlSingleBRDF referenced by the object node, 
        # the object will be invisible and this might be confusing to the user.
        plDesc.setAttribute('scene_name', [pluginName])
        plDesc.setAttribute('brdf', brdfPlugin)
        singleBrdfPlugin = exportPluginWithStats(nodeCtx, plDesc)

        if not brdfPlugin.isEmpty():
            # Material options plugins must be exported between the Node and MtlSingleBRDF plugins
            mtl = nodeCtx.rootObj
            return self._exportMtlOptions(nodeCtx, mtl, singleBrdfPlugin)

        return singleBrdfPlugin


    def _exportMtlOptions(self, nodeCtx, mtl, singleBRDFMtl):
        
        mtlNext = self._exportMtlOption(nodeCtx, mtl, singleBRDFMtl, 'MtlMaterialID', 'base_mtl')
        mtlNext = self._exportMtlOption(nodeCtx, mtl, mtlNext, 'MtlRenderStats', 'base_mtl')
        mtlNext = self._exportMtlOption(nodeCtx, mtl, mtlNext, 'MtlWrapper', 'base_material')
        mtlNext = self._exportMtlOption(nodeCtx, mtl, mtlNext, 'MtlRoundEdges', 'base_mtl')

        return mtlNext

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
        activeMtls = [mtl for mtl in bpy.data.materials if not isObjectOrphaned(mtl)]

        # Remove from VRay the materials whose node trees' topology has changed.They will be
        # fully re-exported during the current update cycle
        topologyUpdates = self._getTopologyUpdates()
        updatedMtls = [mtl for mtl in activeMtls if self.fullExport or (getObjTrackId(mtl) in topologyUpdates)]

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


    def _getTopologyUpdates(self) -> list[int]:
        """ Return the track IDs of the Material data objects whose node tree topology has changed """

        topologyUpdates = UpdateTracker.getUpdatesOfType(UpdateTarget.MATERIAL, UpdateFlags.TOPOLOGY)
        return [t[0] for t in topologyUpdates]


def run(ctx: ExporterContext):
    return MtlExporter(ctx).export()
