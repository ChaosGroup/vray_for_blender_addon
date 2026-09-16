# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
import numpy as np

from vray_blender.engine.renderer_ipr_viewport import VRayRendererIprViewport
from vray_blender.engine.renderer_ipr_vfb import VRayRendererIprVfb
from vray_blender.exporting import tools
from vray_blender.exporting.smoke_export import SmokeExporter
from vray_blender.exporting.hair_export import HairExporter
from vray_blender.exporting.fur_export import FurExporter
from vray_blender.exporting.node_export import exportNodePlugin, fillNodePluginDesc
from vray_blender.exporting.node_exporters.geometry_node_export import exportVRayNodeDisplacement
from vray_blender.exporting.plugin_tracker import getObjTrackId, log as trackerLog
from vray_blender.exporting.update_tracker import UpdateFlags, UpdateTarget, UpdateTracker
from vray_blender.lib.blender_utils import geometryObjectIt, isNonGeometryExportedAsGeometry, TestBreak, isMaterialAssignedToObject
from vray_blender.lib.defs import AttrPlugin, ExporterBase, ExporterContext, MeshData, PluginDesc
from vray_blender.lib import plugin_utils, export_utils
from vray_blender.lib.names import Names
from vray_blender import debug
from vray_blender.bin import VRayBlenderLib as vray
from vray_blender.nodes import utils as NodesUtils
from vray_blender.nodes.specials.selector import resolveSelectorNode
from vray_blender.plugins.geometry.VRayDecal import isPluginVRayDecal, getVRayDecalPluginName

from vray_blender.exporting.node_export import *
from vray_blender.exporting.plugin_tracker import TrackObj


DYNAMIC_GEOMETRY_TRI_THRESHOLD = 100000

class PointCloudData:
    TYPE_MULTIPOINTS = 3
    TYPE_MULTISTREAK = 4
    TYPE_POINTS     = 6
    TYPE_SPHERES    = 7
    TYPE_SPRITES    = 8
    TYPE_STREAK     = 9

    def __init__(self, name):
        self.name = name
        self.points     = np.empty(shape=(0,3), dtype=np.float32)
        self.uvs        = np.empty(shape=(0,2), dtype=np.float32)
        self.radii      = np.empty(shape=(0,1), dtype=np.float32)
        self.colors     = np.empty(shape=(0,3), dtype=np.float32)
        self.renderType = self.TYPE_SPHERES



def _needsObjPropertiesExport(obj: bpy.types.Object, viewLayerEval: bpy.types.ViewLayer) -> bool:
    """ Return True if the Blender Holdout or Indirect Only toggle is set and therefore
        requires VRayObjectProperties to be exported.
    """
    # See the NOTE in export_utils.exportObjProperties() re. view_layer_eval.
    return obj.holdout_get(view_layer=viewLayerEval) or obj.indirect_only_get(view_layer=viewLayerEval)


class GeometryExporter(ExporterBase):
    """ Export all geometry objects in a depsgraph """

    def __init__(self, ctx: ExporterContext):
        super().__init__(ctx)

        self.instancers: list[bpy.types.ID] = []
        self.hairExporter   = HairExporter(self)
        self.furExporter    = FurExporter(self)
        self.objTracker     = ctx.objTrackers['OBJ']
        self.modTracker     = ctx.objTrackers['MODIFIER']
        self.objMtlTracker  = ctx.objTrackers['OBJ_MTL']
        self.instTracker    = ctx.objTrackers['INSTANCER']

        self.nodeTracker = ctx.nodeTrackers['OBJ']

        # Some objects (e.g. non-mesh clipper) need not be drawn, but this is figured out
        # during the export. Store them so that the nodes exported for them could be hidden
        self.hiddenObjects: list[bpy.types.Object]   = []

        # A list of gizmo objects for mesh lights which need to be exported
        self.updatedMeshLightGizmos = set()

        # Gizmos for the Environment fog effect
        self.addedGizmos  = set()  # Added from the previous update
        self.removedGizmos = set()  # Removed from the previous update

        self.updatedFurGizmos = set()

        # Chaos Scatter target lists and curve tessellations, resolved in one prepass before the
        # object loop so no to_mesh() happens while a temp mesh is in flight (see
        # scatter_export.prepassResolveTargets). Empty on paths that never run that prepass.
        self.scatterTargetCache: dict = {}
        self.scatterPolylineCache: dict = {}

    def _initInstanceState(self):
        """ Populate the gizmo-related state used by `_exportObject` for instances.
        """
        # The list of updated light gizmos includes objects that have been updated and objects that were previously
        # used as gizmos but have been detached from the mesh lights
        disconnectedMeshLightGizmos = {p.gizmoObjTrackId for p in self.persistedState.activeMeshLightsInfo.difference(self.activeMeshLightsInfo)}
        self.updatedMeshLightGizmos = {p.gizmoObjTrackId for p in self.updatedMeshLightsInfo}.union(disconnectedMeshLightGizmos)

        disconnectedFurGizmos = {p.gizmoObjTrackId for p in self.persistedState.activeFurInfo.difference(self.activeFurInfo)}
        self.updatedFurGizmos = {p.gizmoObjTrackId for p in self.updatedFurInfo}.union(disconnectedFurGizmos)

    def export(self):
        if self.preview:
            self._exportPreview()
        else:
            self._initInstanceState()
            self._calculateGizmoStates()

            self._exportScene()

    def _meshFromObj(self, obj: bpy.types.Object) -> bpy.types.Mesh | None:
        # Get temporary mesh object from the object evaluated above. This mesh
        # will be destroyed once we finish the export of the object, so don't
        # hang on to it
        if (obj.type == 'MESH' and obj.mode != 'EDIT'):
            mesh = obj.data
        else:
            if not (mesh := obj.to_mesh(depsgraph=self.dg)):
                return None
            self.objectsWithTempMeshes.append(obj)

        # VRay requires triangular faces
        self.ts.timeThis("calc_triangles", lambda :  mesh.calc_loop_triangles())

        return mesh


    def _fillMeshData(self, obj: bpy.types.Object, mesh: bpy.types.Mesh, name: str, isInstanced: bool):
        # The pointer plumbing is shared with the Chaos Scatter preview (MeshData.fromMesh); only the
        # attribute filter and the export options below are specific to the object pass. Attributes
        # become map channels, which V-Ray resolves by name, so one nothing references is pure payload.
        # The UV layers are not filtered - uvw_channel picks them by index, so dropping any would
        # renumber the rest. Proxy export has no materials, so it keeps all.
        meshData = MeshData.fromMesh(mesh, name, None if self.isProxyExport else self.referencedAttrNames)

        dynamicGeometry = (len(mesh.loop_triangles) > DYNAMIC_GEOMETRY_TRI_THRESHOLD) or isInstanced or (self.interactive and not self.fullExport)

        meshData.options.forceDynamicGeometry = dynamicGeometry
        meshData.options.useSubsurfToOSD = tools.vrayExporter(self.ctx).subsurf_to_osd
        meshData.options.mergeChannelVerts = False
        meshData.options.exportEdgeVisibility = True

        return meshData


    def _applyMeshModifiers(self, obj: bpy.types.Object, meshData: MeshData):
        """ Apply modifiers to the object's proper geometry """
        if len(obj.modifiers) == 0:
            return

        for modifier in obj.modifiers:
            match modifier.type:
                case "SUBSURF":  # Subdivided surface
                    # Merging on big meshes can be quite slow, so for now it's only enabled for
                    # production renders mostly for faster time to first pixel. It will probably
                    # be fully disabled in the future and replaced with V-Ray's OSD.
                    meshData.options.mergeChannelVerts = self.production

                case _:
                    pass

        if tools.vrayExporter(self.ctx).subsurf_to_osd:
            lastMod = obj.modifiers[-1]
            if lastMod.type == "SUBSURF": # Subdivided surface
                meshData.subdiv.enabled     = True
                meshData.subdiv.level       = lastMod.levels if self.interactive else lastMod.render_levels
                meshData.subdiv.type        = 0 if lastMod.subdivision_type == "CATMULL_CLARK" else 1
                meshData.subdiv.useCreases = lastMod.use_creases

    def _applyProxyMaterialSlotOffset(self, obj: bpy.types.Object, meshData: MeshData):
        """Proxy export: apply precomputed slot offset on meshData."""
        pe = self.proxyExportSettings
        meshData.mtlIdOffset = pe.proxyMaterialSlotOffsets.get(meshData.name, 0)

    def _exportNonMeshModifiers(self, obj: bpy.types.Object, exportGeometry: bool, isVisible: bool):
        """ Apply modifiers that do not change object's proper geometry. """
        for modifier in obj.modifiers:
            match modifier.type:
                case 'FLUID':
                    if (modifier.fluid_type == 'DOMAIN') and (modifier.domain_settings.domain_type == 'GAS'):
                        SmokeExporter(self).exportFluidModifier(obj, exportGeometry, isVisible)
                case _:
                    pass

    def _exportParticleSystems(self, obj: bpy.types.Object, exportMesh: bool):
        ####  Export modifiers  ###
        objTrackId = getObjTrackId(obj)

        for pmod in [m for m in obj.modifiers if m.type == 'PARTICLE_SYSTEM']:
            psys: bpy.types.ParticleSystem = pmod.particle_system
            psysTrackId = getObjTrackId(psys.settings)
            if psys.settings.type == "HAIR" and not psys.settings.render_type in ('OBJECT', 'NONE', 'COLLECTION'):
                modVisible = isModifierVisible(self, pmod)
                particleHairName = self.hairExporter.getParticleHairName(obj.original, psys)

                geomPlugin = None
                dataName = Names.objectData(obj)
                # ParticleSystemSettings has a completely valid session_uid so we use it for tacking. The obj.original.data part is
                # for edit mode. If you enter edit mode and the mesh changes we don't get a particle hair update after exiting edit mode.
                exportModifier = export_utils.isObjectGeomUpdated(self, psysTrackId) or getObjTrackId(obj.original.data) in self.dgUpdates['geometry']
                if exportModifier and modVisible:
                    fnExportParticles = lambda pmod = pmod: self.hairExporter.exportFromParticles(obj, pmod)
                    geomPlugin = self.ts.timeThis("collect_hair_particles_data", fnExportParticles )
                    if (not geomPlugin) and (self.persistedState.objDataTracker.particleDataExported(dataName, psys.name)):
                        # Previously exported particle data is now invalid (likely due to user changes in particle settings).
                        # Force the node to reference an empty plugin to hide any outdated geometry until valid data is available again.
                        geomPlugin = AttrPlugin(forceUpdate=True)
                else:
                    particlePluginName = self.persistedState.objDataTracker.getParticlePluginName(dataName, psys.name)
                    if particlePluginName is None:
                        continue
                    geomPlugin = AttrPlugin(particlePluginName)

                if not geomPlugin:
                    # Invalid particles data, nothing to export
                    return

                if exportMesh:
                    geomPlugin.forceUpdate = True
                nodePlugin = exportNodePlugin(self, obj, geomPlugin, particleHairName, self.objTracker, isInstancer=False, visible=modVisible)
                self.modTracker.trackPlugin(psysTrackId, nodePlugin.name)
                self.persistedState.processedObjects.add(psysTrackId)


    def _setMeshAttrOfGeom(self, mainGeomPlugin, geomPlugin):
        assert mainGeomPlugin.name != geomPlugin.name, "Can't set mesh attribute of the same plugin"
        plugin_utils.updateValue(self.renderer, mainGeomPlugin.name, "mesh", geomPlugin)


    def _exportGeometrySockets(self, nodeCtx: NodeContext, nodeOutput, geomPlugin, trackId):
        advancedGeomPlugin = AttrPlugin()
        displacementNodeLink = tools.getNodeLinkToNode(nodeOutput, "Displacement", "VRayNodeDisplacement")
        subdivNodeLink = tools.getNodeLinkToNode(nodeOutput,  "Subdivision", "VRayNodeGeomStaticSmoothedMesh")

        subdivPropGroup = None
        if (not displacementNodeLink) and subdivNodeLink:
            advancedGeomPlugin = exportVRayNode(nodeCtx, subdivNodeLink)
            subdivPropGroup = getattr(subdivNodeLink.from_node, "GeomStaticSmoothedMesh")
        elif displacementNodeLink:
            displacementNode = displacementNodeLink.from_node
            with nodeCtx.push(displacementNode), nodeCtx.pushGroupPath(displacementNodeLink.groupPath):
                nodeId = getNodeTrackId(displacementNode)
                nodeIdForRemoval = f'{nodeId}@Subdiv'

                # If both VRayNodeGeomStaticSmoothedMesh and VRayNodeDisplacement are attached,
                # a single plugin, GeomStaticSmoothedMesh, will be exported, combining the properties of both nodes.
                # Otherwise the nodes will be exported normally using exportVRayNode
                if subdivNodeLink:
                    nodeIdForRemoval = nodeId

                    # If both displacement and subdivision are used,
                    # an additional identifier ("@Subdiv") is added to the node ID for the node tracker.
                    # This is necessary to differentiate it from the node ID of the displacement node without subdivision.
                    nodeId += '@Subdiv'
                    subdivPropGroup = getattr(subdivNodeLink.from_node, "GeomStaticSmoothedMesh")

                # In interactive mode, remove VRayNodeGeomStaticSmoothedMesh if only displacement is connected,
                # otherwise remove GeomDisplacedMesh if only subdivision is connected."
                forceUpdateGeomPlugin = False
                for pluginName in self.nodeTracker.getNodePlugins(trackId, nodeIdForRemoval):
                    vray.pluginRemove(self.renderer, pluginName)
                    forceUpdateGeomPlugin = True  # Force update only on topology changes
                self.nodeTracker.forgetNode(trackId, nodeIdForRemoval)

                if (advancedGeomPlugin := nodeCtx.getCachedNodePlugin(displacementNode)) is None:
                    with TrackNode(nodeCtx.nodeTracker, nodeId):
                        advancedGeomPlugin = exportVRayNodeDisplacement(nodeCtx, subdivPropGroup)
                        # In some cases, the V-Ray node of an object may remain linked to the removed plugin
                        # associated with the "track" identified by "nodeIdForRemoval".
                        advancedGeomPlugin.forceUpdate = forceUpdateGeomPlugin
                        nodeCtx.cacheNodePlugin(displacementNode, advancedGeomPlugin)

        if advancedGeomPlugin and not advancedGeomPlugin.isEmpty():
            # The newly created "advancedGeomPlugin" is used as geometry of the object
            # and the staticGeom(the GeomStaticMesh plugin representing the objects's mesh)
            # is linked to the displacement plugin's mesh attribute.
            self._setMeshAttrOfGeom(advancedGeomPlugin, geomPlugin)
            # The subdivision plugin itself doesn't have a smooth uv parameter, the parameter should be
            # set on the mesh i.e. GeomMeshFile/GeomStaticMesh.
            if subdivPropGroup:
                plugin_utils.updateValue(self.renderer, geomPlugin.name, "smooth_uv", subdivPropGroup.subdivide_uvs)
                if not subdivPropGroup.subdivide_uvs:
                    plugin_utils.updateValue(nodeCtx.renderer, advancedGeomPlugin.name, "preserve_map_borders", -1)
            return advancedGeomPlugin

        return geomPlugin


    def _exportObjNodeTree(self, obj: bpy.types.Object, baseGeomPlugin: AttrPlugin, isVisible = True,
                           instance: bpy.types.DepsgraphObjectInstance = None):
        """ Export of geometry and object property nodes from OBJECT node tree
        """
        nodeOutput = NodesUtils.getOutputNode(obj.vray.ntree, 'OBJECT')
        nodeCtx = NodeContext(self, obj, self.ctx.scene, self.renderer)
        nodeCtx.rootObj     = obj
        nodeCtx.nodeTracker = self.nodeTracker
        nodeCtx.ntree       = obj.vray.ntree

        objTrackId = getObjTrackId(obj)

        with nodeCtx:
            with TrackObj(self.nodeTracker, objTrackId):

                geomPlugin = self._exportGeometrySockets(nodeCtx, nodeOutput, baseGeomPlugin, objTrackId)
                self.persistedState.objDataTracker.trackPluginOfData(Names.objectData(obj, instance), geomPlugin.name)

                nodePlugin = exportNodePlugin(self, obj, geomPlugin, Names.object(obj, instance), self.objTracker, instance=instance, visible=isVisible)
                export_utils.exportObjProperties(obj, nodeCtx.exporterCtx, nodeCtx.renderer, self.objTracker, nodeOutput, [nodePlugin.name])

        return nodePlugin


    def _exportMeshObject(self, evaluatedObj: bpy.types.Object, exportGeometry: bool, isVisible: bool, instance: bpy.types.DepsgraphObjectInstance, asyncExport, force=False):
        assert evaluatedObj.is_evaluated or force, f"Evaluated object expected: {evaluatedObj.name}"

        objectName = Names.object(evaluatedObj, instance)

        baseGeomPlugin = None # Empty plugin attribute representing the object's geometry

        meshDataName = Names.objectData(evaluatedObj, instance)
        dataExported = self.persistedState.objDataTracker.dataExported(meshDataName)
        exportMesh = exportGeometry and (not self.fullExport or not dataExported)
        # Check if the object has valid geometry already exported.
        if exportMesh:
            baseGeomPlugin = self._exportMesh(evaluatedObj, instance, meshDataName, asyncExport)
        else:
            # Here we want the base GeomStaticMesh plugin name, not the GeomDisplacedMesh or GeomStaticSmoothedMesh plugin name
            baseGeomPlugin = AttrPlugin(meshDataName)

        # Non-mesh modifiers show geometry, but don't need a Node plugin to be exported for them.
        # The geometry associated with them may also not need exporting, so apply them regardless
        # of the return value of exportMesh()
        self._exportNonMeshModifiers(evaluatedObj, exportGeometry, isVisible)

        if baseGeomPlugin is None:
            # Failed to export mesh. Nothing more we can do, skip exporting the Node plugin
            return False

        if isVisible:
            ####  Export modifiers  ###
            self._exportParticleSystems(evaluatedObj, exportMesh)

        objTrackId = getObjTrackId(evaluatedObj)
        isMeshLightGizmo = objTrackId in {p.gizmoObjTrackId for p in self.activeMeshLightsInfo}
        isEnvFogGizmo = objTrackId in self.activeGizmos
        isGizmo = isMeshLightGizmo or isEnvFogGizmo

        # Export node
        nodePlugin = None

        if (not isGizmo) and (not self._isInstancerVisibilityDisabled(evaluatedObj)):
            if evaluatedObj.vray.ntree and NodesUtils.getOutputNode(evaluatedObj.vray.ntree, 'OBJECT') and (self.fullExport or objTrackId in self.dgUpdates["shading"]):
                nodePlugin = self._exportObjNodeTree(evaluatedObj, baseGeomPlugin, isVisible, instance)
            else:
                # There could be a GeomDisplacedMesh or GeomStaticSmoothedMesh wrapping the base geometry.
                # Note: The plugin name could be None if the object geometry gets removed.
                geomPlugin = AttrPlugin(self.persistedState.objDataTracker.getPluginName(meshDataName) or '')

                nodePlugin = exportNodePlugin(
                    self,
                    evaluatedObj,
                    geomPlugin,
                    objectName,
                    self.objTracker,
                    instance = instance,
                    visible = isVisible
                )

                # Export VRayObjectProperties when any Blender visibility/holdout flag deviates
                # from its defaults, OR when the plugin was previously exported (so that disabling
                # holdout resets the plugin back to defaults rather than leaving matte_surface=True).
                # This branch also runs for objects that DO have an OBJECT node tree on updates that
                # don't set the 'shading' flag (e.g. transform-only IPR edits), when the node-tree
                # path above is skipped. Pass the output node so node-driven object properties
                # (e.g. the 'visibility' percentage) are re-derived instead of reset to defaults.
                if nodePlugin is not None:
                    objPropsPluginName = Names.pluginObject("VRayObjectProperties", Names.object(evaluatedObj))
                    wasExported = objPropsPluginName in self.objTracker.getPlugins(objTrackId)
                    if wasExported or _needsObjPropertiesExport(evaluatedObj, self.dg.view_layer_eval):
                        objOutputNode = NodesUtils.getOutputNode(evaluatedObj.vray.ntree, 'OBJECT') if evaluatedObj.vray.ntree else None
                        export_utils.exportObjProperties(evaluatedObj, self, self.renderer, self.objTracker,
                                                         objOutputNode, [nodePlugin.name])

            if nodePlugin is not None:
                plugin_utils.updateValue(self.renderer, nodePlugin.name, "objectID", evaluatedObj.vray.VRayObjectProperties.objectID)

        else:
            # 'Node' plugin should not be exported for gizmos
            nodePluginName = Names.vrayNode(Names.object(evaluatedObj))
            vray.pluginRemove(self.renderer, nodePluginName)
            self.objTracker.forgetPlugin(objTrackId, nodePluginName)

        return True

    # Export a MESH object to GeomStaticMesh VRay plugin
    def _exportMesh(self, evaluatedObj: bpy.types.Object, instance: bpy.types.DepsgraphObjectInstance, meshDataName: str, asyncExport: bool):

        if (mesh := self._meshFromObj(evaluatedObj)) is None:
            debug.printDebug(f"Object {evaluatedObj.name} can't be converted to mesh")
            return None

        isInstanced = instance is not None

        if (len(mesh.loops) == 0):
            if (evaluatedObj.mode == 'EDIT') and isInstanced:
                # While a GN instancer object's geometry is being edited, the instanced objects' meshes in the
                # evaluated depsgraph are empty. We only need to re-export the instance data in this case.
                return AttrPlugin(meshDataName)
            elif self.persistedState.objDataTracker.popData(meshDataName):
                # Geometry plugin previously existed for this object but is now invalid,
                # so return an empty plugin to clear the geometry from the V-Ray Node plugin,
                # effectively hiding any old geometry.
                return AttrPlugin()
            else:
                # Mesh data could not be obtained for
                debug.printDebug(f"Object {evaluatedObj.name} has a non-polygonal or empty mesh, skipping export")
                return None

        # Fluid modifier may be exported as mesh or as particles. The mesh is exported below,
        # the particle mode is handles by SmokeExporter.
        fluidMod = next((m for m in evaluatedObj.modifiers if m.type == 'FLUID'), None)
        fluidAsMesh = False
        if fluidMod and (fluidMod.fluid_type == 'DOMAIN') and (fluidData := fluidMod.domain_settings):
            fluidAsMesh = (fluidData.domain_type == 'LIQUID') and fluidData.use_mesh

        if fluidMod and (not fluidAsMesh):
            return AttrPlugin()

        meshData = self._fillMeshData(evaluatedObj, mesh, meshDataName, isInstanced)
        self._applyMeshModifiers(evaluatedObj, meshData)
        if self.isProxyExport:
            self._applyProxyMaterialSlotOffset(evaluatedObj, meshData)

        plugin_utils.createPlugin(self, meshDataName, "GeomStaticMesh")
        vray.exportGeometry(self.renderer, meshData, asyncExport)
        self.objTracker.trackPlugin(getObjTrackId(evaluatedObj), meshDataName, isInstanced)

        self.persistedState.objDataTracker.trackPluginOfData(meshDataName, meshDataName)

        return AttrPlugin(meshDataName)


    # Export a MESH object to VRayScene plugin
    def _exportVrayScene(self, obj: bpy.types.Object, isVisible: bool, instance, force=False):
        assert obj.is_evaluated or force, f"Evaluated object expected: {obj.name}"
        pluginName = Names.pluginObject("vrayscene", Names.object(obj))

        isInstanced = instance is not None
        appliedTransform = getProxyPreviewAppliedTransform(obj, fromOriginal=not isInstanced)
        transform = obj.matrix_world @ appliedTransform

        if self.fullExport:
            vrayScene = obj.data.vray.VRayScene
            pluginDesc = PluginDesc(pluginName, "VRayScene")
            pluginDesc.vrayPropGroup = vrayScene

            pluginDesc.setAttribute("filepath", vrayScene.filepath)
            pluginDesc.setAttribute("transform", transform)
            pluginDesc.setAttribute("mrs_visibility", isVisible)

            export_utils.exportPlugin(self, pluginDesc)
            # NOTE: The object is deliberately not added to the plugin tracker because
            # no changes are allowed to VRayScene objects during IPR.
        elif getObjTrackId(obj) in self.dgUpdates["transform"]:
            plugin_utils.updateValue(self.renderer, pluginName, "transform", transform)


    # Export a MESH object to GeomMeshFile plugin
    def _exportVrayProxy(self, obj: bpy.types.Object, exportGeometry: bool, isVisible: bool, instance: bpy.types.DepsgraphObjectInstance = None, force=False):
        assert obj.is_evaluated or force
        objectName = Names.object(obj)
        pluginName = Names.pluginObject("vrayproxy", objectName)
        isInstanced = instance is not None
        dataName = Names.objectData(obj, instance)

        # The proxy preview should always have vertices (at least the anchor vertices).
        # Having no vertices means the geometry tree is either instancing a proxy object or has no geometry connected to the output.
        if len(obj.data.vertices) < 4:
            if self.persistedState.objDataTracker.popData(dataName):
                # The object had exported geometry, but it has been disconnected.
                pluginName = ""
            else:
                return

        elif exportGeometry:
            geomMeshFile = obj.original.data.vray.GeomMeshFile

            pluginDesc = PluginDesc(pluginName, "GeomMeshFile")
            pluginDesc.vrayPropGroup = geomMeshFile

            export_utils.exportPlugin(self, pluginDesc)
            self.objTracker.trackPlugin(getObjTrackId(obj), pluginName, isInstanced=isInstanced)
            self.persistedState.objDataTracker.trackPluginOfData(dataName, pluginName)

        # Export node
        if obj.vray.ntree and NodesUtils.getOutputNode(obj.vray.ntree, 'OBJECT') and (self.fullExport or getObjTrackId(obj) in self.dgUpdates["shading"]):
            self._exportObjNodeTree(obj, AttrPlugin(pluginName), isVisible, instance)
        else:
            geomPlugin = AttrPlugin(self.persistedState.objDataTracker.getPluginName(dataName) or '')
            exportNodePlugin(self, obj, geomPlugin, Names.object(obj, instance),
                               self.objTracker, instance=instance, isInstancer=False, visible=isVisible)

    # Export a V-Ray Gaussian splat object (an Empty) to a GeomGaussians plugin
    def _exportVRaySplat(self, obj: bpy.types.Object, isVisible: bool, force=False):
        assert obj.is_evaluated or force, f"Evaluated object expected: {obj.name}"

        geomName = Names.pluginObject("vraysplat", Names.object(obj))
        geomDesc = PluginDesc(geomName, "GeomGaussians")
        gaussians = obj.original.vray.GeomGaussians
        geomDesc.vrayPropGroup = gaussians

        # Object-based clipping mask: generate a TexDistance from the chosen object and use it as
        # the clipping_mask. We only support object clipping for splats (no texture graphs).
        if clipObj := gaussians.clip_object.boundPropObj:
            geomDesc.setAttribute("clipping_mask", self._exportSplatClipMask(obj, clipObj, gaussians.clip_distance))

        export_utils.exportPlugin(self, geomDesc)

        # Wrap the geometry in a Node so it picks up the object's transform and visibility.
        exportNodePlugin(self, obj, AttrPlugin(geomName), Names.object(obj), self.objTracker, visible=isVisible)
        self.objTracker.trackPlugin(getObjTrackId(obj), geomName)

    def _exportEmptyGeometry(self, obj: bpy.types.Object, pluginType: str, isVisible: bool, force=False):
        """ Export an Empty that stands for a procedural V-Ray geometry plugin (GeomPlane,
            GeomPerfectSphere). The plugin's parameters live on obj.vray.<pluginType> because an
            Empty has no data block; the Empty's transform places and orients the geometry.
        """
        assert obj.is_evaluated or force, f"Evaluated object expected: {obj.name}"

        geomName = Names.pluginObject(pluginType.lower(), Names.object(obj))
        geomDesc = PluginDesc(geomName, pluginType)
        geomDesc.vrayPropGroup = getattr(obj.original.vray, pluginType)
        export_utils.exportPlugin(self, geomDesc)

        # Wrap the geometry in a Node so it picks up the object's transform and visibility.
        exportNodePlugin(self, obj, AttrPlugin(geomName), Names.object(obj), self.objTracker, visible=isVisible)
        self.objTracker.trackPlugin(getObjTrackId(obj), geomName)

    def _exportSplatClipMask(self, obj: bpy.types.Object, clipObj: bpy.types.Object, distance: float) -> AttrPlugin:
        """ Export a TexDistance plugin that measures the distance to clipObj, used as the
            clipping mask of a Gaussian splat (mirrors the V-Ray for Maya object-clipping). """
        texName = Names.pluginObject("vraysplatclip", Names.object(obj))
        texDesc = PluginDesc(texName, "TexDistance")
        texDesc.setAttribute("distance", distance)
        texDesc.setAttribute("inside_separate", True)
        texDesc.setAttribute("inside_solid", True)

        # The clip mask references the clip object's scene Node. Register it as a referenced object so
        # it is exported even when it is invisible / disabled in renders (VBLD-2516), and pre-create
        # the Node so the reference is valid if it is exported after this plugin.
        self.registerReferencedObject(clipObj)
        clipNodeName = Names.vrayNode(Names.object(clipObj))
        plugin_utils.forwardDeclarePlugin(self, clipNodeName, 'Node')
        texDesc.setAttribute("objects", [AttrPlugin(clipNodeName)])
        export_utils.exportPlugin(self, texDesc)
        self.objTracker.trackPlugin(getObjTrackId(obj), texName)
        return AttrPlugin(texName)

    def exportVRayDecal(self, obj: bpy.types.Object, isVisible: bool, instance: bpy.types.DepsgraphObjectInstance = None):
        """ Export a MESH object marked as a decal to a VRayDecal plugin.

            'instance' is set when the decal is part of an instanced collection. GeomInstancer does
            not instance VRayDecal correctly, so a decal cannot be collected into the parent's
            instancer the way meshes and lights are. Each instance gets its own decal plugin
            instead, placed at the instance's world transform.

            Returns the name of the exported plugin.
        """
        pluginName = getVRayDecalPluginName(obj, instance)
        pluginDesc = PluginDesc(pluginName, "VRayDecal")

        transform = instance.matrix_world if instance is not None else obj.matrix_world
        fillNodePluginDesc(self, obj, pluginDesc, transform, self.objTracker)
        pluginDesc.setAttribute("enabled", isVisible)

        objTrackId = getObjTrackId(obj)
        if NodesUtils.treeHasNodes(obj.vray.ntree) and (decalNode := NodesUtils.getOutputNode(obj.vray.ntree)):
            pluginDesc.vrayPropGroup = decalNode.VRayDecal
            pluginDesc.node = decalNode

            nodeCtx = NodeContext(self, obj, self.ctx.scene, self.renderer)
            nodeCtx.rootObj     = obj
            nodeCtx.nodeTracker = self.nodeTracker
            nodeCtx.ntree       = obj.vray.ntree

            with nodeCtx, nodeCtx.push(decalNode):
                if nodeCtx.getCachedNodePlugin(decalNode) is None:
                    nodeCtx.cacheNodePlugin(decalNode)
                    with TrackObj(self.nodeTracker, objTrackId):
                        with TrackNode(self.nodeTracker, getNodeTrackId(decalNode)):
                            # The plugin doesn't have a dedicated displacement multiplier so we do it manually.
                            displacementSocket = getInputSocketByAttr(decalNode, "displacement_tex_color")
                            displacementPlugin = exportLinkedSocket(nodeCtx, displacementSocket)
                            if displacementPlugin and decalNode.VRayDecal.displacement_multiplier != 1.0:
                                multTex = PluginDesc(pluginName + "@displ", 'TexAColorOp')
                                multTex.setAttribute("color_a", displacementPlugin)
                                multTex.setAttribute("mult_a", decalNode.VRayDecal.displacement_multiplier)
                                displacementPlugin = exportPluginWithStats(nodeCtx, multTex)

                            # Wrap the displacement and mask similarly to how we do it for plugin lists.
                            if displacementPlugin and displacementPlugin.isOutputSet():
                                displacementPlugin = export_utils.wrapAsTexture(nodeCtx, displacementPlugin)
                            pluginDesc.setAttribute("displacement_tex_color", displacementPlugin)

                            maskSocket = getInputSocketByAttr(decalNode, "mask")
                            maskPlugin = exportLinkedSocket(nodeCtx, maskSocket)
                            if not decalNode.VRayDecal.enable_mask:
                                maskPlugin = AttrPlugin()
                            elif maskPlugin and maskPlugin.isOutputSet():
                                maskPlugin = export_utils.wrapAsTexture(nodeCtx, maskPlugin)
                            pluginDesc.setAttribute("mask", maskPlugin)

                            exportNodeTree(nodeCtx, pluginDesc, skippedSockets=[ "mask", "material", "enabled", "transform", "displacement_tex_color" ])
                            export_utils.exportPlugin(self, pluginDesc)
        else:
            pluginDesc.vrayPropGroup = obj.data.vray.VRayDecal
            export_utils.exportPlugin(self, pluginDesc)

        self.objTracker.trackPlugin(objTrackId, pluginName)
        return pluginName

    def _exportCurves(self, obj: bpy.types.Object, exportGeometry: bool, isVisible: bool, instance: bpy.types.DepsgraphObjectInstance = None, force=False):
        assert obj.is_evaluated or force, f"Evaluated object expected: {obj.name}"

        geomPlugin = self.ts.timeThis("collect_hair_curves_data", lambda: self.hairExporter.exportFromCurves(obj, exportGeometry))

        # Export node
        exportNodePlugin(self, obj, geomPlugin, Names.object(obj, instance),
                                self.objTracker, instance=instance, isInstancer=False, visible=isVisible)


    # Export a POINTCLOUD object to a GeomParticleSystem VRay plugin
    def _exportPointCloud(self, obj: bpy.types.Object, exportGeometry: bool, isVisible: bool, instance: bpy.types.DepsgraphObjectInstance = None, force=False):
        assert obj.is_evaluated or force, f"Evaluated object expected: {obj.name}"

        pluginName = Names.objectData(obj, instance)

        if not exportGeometry:
            # Export node as its visibility may have changed
            exportNodePlugin(self, obj, AttrPlugin(pluginName), Names.object(obj, instance), self.objTracker,
                             instance=instance, isInstancer=False, visible=isVisible)
            return

        attributes = obj.data.attributes
        if "position" not in attributes or "radius" not in attributes:
            return

        numPoints = len(attributes["position"].data)
        if numPoints == 0:
            return

        data = PointCloudData(pluginName)
        data.renderType = PointCloudData.TYPE_SPHERES
        data.points = tools.foreachGetAttr(attributes["position"].data, "vector", shape=(numPoints, 3), dtype=np.float32)
        data.radii  = tools.foreachGetAttr(attributes["radius"].data, "value", shape=(numPoints,), dtype=np.float32)

        # TODO: Get the color from some real source ( if needed at all )
        redColor = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        data.colors = np.tile(redColor, (numPoints, 1))

        plugin_utils.createPlugin(self, pluginName, "GeomParticleSystem")
        self.ts.timeThis("C++ export_point_cloud", lambda : vray.exportPointCloud(self.renderer, data, asyncExport = instance is None))

        self.persistedState.objDataTracker.trackPluginOfData(Names.objectData(obj, instance), pluginName)

        # Export node
        exportNodePlugin(self, obj, AttrPlugin(pluginName), Names.object(obj, instance),
                                self.objTracker, instance=instance, isInstancer=False, visible=isVisible)


    def _hideInvisibleObjects(self):
        """ Hides the objects that should be exported because their geometry is needed, but are not rendered.
            These may be e.g. instancers.
        """
        for obj in self.hiddenObjects:
            vrayNodeName = Names.vrayNode(Names.object(obj))
            plugin_utils.updateValue(self.renderer, vrayNodeName, "visible", False)


    def _exportPreview(self):
        """ Dedicated path for exporting preview scenes
            It is necessary because the depsgraph for the preview is created differently
            than the one for normal render, and besides the export is simpler (e.g. no instancing etc.)
        """

        # Same prepass the scene path runs, and for the same reason (see _exportObjects). A preview
        # scene can contain a Chaos Scatter carrier, and without this a 1D spline scatter exports no
        # spline_vertices at all - splines have no inline fallback, meshes do.
        from vray_blender.exporting import scatter_export
        (self.scatterTargetCache,
         self.scatterPolylineCache) = scatter_export.prepassResolveTargets(self)

        for obj in self.dg.objects:
            # Force geometry export
            self.exportObject(obj, exportGeometry=True, isVisible=True)


    def _exportScene(self):
        """ Export all geometry in the scene.

            The instance pass (`InstancerExporter.exportInstances`) runs separately, after
            both the geometry and light passes have completed, so it can also collect light
            instances into the same per-parent `instancer@<parent>` `GeomInstancer` plugins.
        """

        # Add the fur objects before exporting the scene objects,
        # because they rely on the geometry plugins exported from the scene objects.
        self.furExporter.addFurObjectsForExport()

        # Final render depsgraphs don't contain the instanced objects (only the instancers),
        # so the per-instance export happens later, in the instancer pass triggered by
        # the renderer after lights have been emitted.
        self.ts.timeThis("Export non-instanced objects", lambda: self._exportObjects())

        self._hideInvisibleObjects()
        self._syncGizmos()


    def _exportObjects(self):
        # The dg.objects pass owns these; a referenced object NOT here is hidden and gets force-exported.
        dgObjectIds = {getObjTrackId(o) for o in self.dg.objects}

        def geometryForExport():
            if self.isProxyExport:
                for obj in self.dg.objects:
                    if self.proxyExportSettings.exportOnlySelected and not obj.original.select_get():
                        continue
                    if tools.isProxyConvertibleGeometryType(obj):
                        yield obj
                return

            # NOTE: In prod renders, some objects are not added to the depsgraph, e.g. objects
            # instanced by legacy instancers. Cycles won't show them in final renders either.
            if self.commonSettings.exportMotionData and self.isAnimation:
                yield from self.motionBlurBuilder.getGeometryForExport(self.dg.objects)
            else:
                yield from geometryObjectIt(self.dg.objects)

            # Referenced (by a selector etc.) but hidden objects the pass above never yields; without
            # this their references resolve to empty plugins. The prepass already discovered them.
            # Snapshot the dict: a forced export may re-register refs (a no-op) and must not mutate it
            # mid-iteration. Lights are LightExporter._exportScene's to force-export, not this pass's.
            for refTrackId, refObj in list(self.referencedObjects.items()):
                if (refTrackId not in dgObjectIds) and (refObj.type != 'LIGHT'):
                    yield refObj.evaluated_get(self.dg)

        # Must precede the loop: resolving tessellates curve targets, and to_mesh() frees the
        # object's previous temp mesh on entry - mid-loop that would pull the rug out from under
        # an exportGeometry(asyncExport=True) still being read on a worker thread.
        from vray_blender.exporting import scatter_export
        (self.scatterTargetCache,
         self.scatterPolylineCache) = scatter_export.prepassResolveTargets(self)

        # Export all scene objects
        for obj in geometryForExport():
            if obj.vray.isVRayFur:
                continue

            with self.objectContext.push(obj):
                # Node plugins' "visible" property for instanced objects should be set to
                # False if we only want to see the instances and not the original instanced object
                objTrackId = getObjTrackId(obj)
                forced = objTrackId not in dgObjectIds
                isVisible = objTrackId in self.visibleObjects
                exportGeometry = forced or export_utils.isObjectGeomUpdated(self, objTrackId)
                self.exportObject(obj, exportGeometry, isVisible=isVisible, force=forced)

            self.furExporter.exportFursOfObject(obj)
            self.exportProgress.update(self.engine)

        # Block here, wait for all geoemtry to be exported and release all temp meshes before
        # iterating the scene for instance export. Not doing so will cause crashes.
        vray.finishExport(self.renderer, False)

        for obj in self.objectsWithTempMeshes:
            obj.to_mesh_clear()
        self.objectsWithTempMeshes.clear()

    def exportObject(self,
                      evaluatedObj: bpy.types.Object,
                      exportGeometry: bool,
                      isVisible: bool,
                      instance: bpy.types.DepsgraphObjectInstance = None,
                      asyncExport = True,
                      force = False):
        """ Export an object based on its type.

            force=True is for a referenced object the dg.objects pass does not yield (hidden / disabled
            in renders): it skips the is_evaluated guard but keeps change-tracking, exporting it hidden.
        """
        assert isinstance(evaluatedObj, bpy.types.Object), "Only Blender 'Object' type accepted"

        objTrackId = getObjTrackId(evaluatedObj)

        # Mark the object as processed, even if it is not exported.
        # This ensures we can differentiate between objects already handled and new additions to the depsgraph.
        alreadyProcessed = objTrackId in self.persistedState.processedObjects
        self.persistedState.processedObjects.add(objTrackId)

        if force:
            # Re-export when full, first-seen, or changed this cycle (dg.updates includes hidden
            # objects, so moving e.g. a splat clip object updates the render in IPR). is_evaluated is
            # intentionally not checked - a hide_render object still yields its base geometry.
            if (not self.fullExport) and alreadyProcessed and (objTrackId not in self.dgUpdates['all']):
                return False
        else:
            if not exportGeometry and (
                    isMaterialAssignedToObject(self.updatedMtlWithDisplacement, evaluatedObj)
                    or isMaterialAssignedToObject(self.updatedMtlWithQuickCaustics, evaluatedObj)):
                # The geometry must be recompiled without a full geometry re-export in two cases:
                #  - the object's material has displacement (otherwise the change wont affect the geometry);
                #  - a quick caustics parameter changed (the caustic beam generators are registered
                #    during geometry compilation, so the geometry must be recompiled to (un)register them).
                vrayNodeName = Names.vrayNode(Names.object(evaluatedObj))
                vray.pluginReCreateAttr(self.renderer, vrayNodeName, "geometry")

            if (not self.fullExport) \
                    and not exportGeometry \
                    and objTrackId not in self.dgUpdates['transform'] \
                    and not export_utils.isObjectTreeUpdated(self, evaluatedObj) \
                    and (objTrackId not in self.objectsWithUpdatedVisibility) \
                    and (objTrackId not in self.objectsWithUpdatedHoldout) \
                    and (objTrackId not in self.updatedMeshLightGizmos) \
                    and (objTrackId not in self.updatedFurGizmos) \
                    and (UpdateFlags.NONE == UpdateTracker.getObjUpdate(UpdateTarget.OBJECT_MTL_OPTIONS, evaluatedObj)) \
                    and (objTrackId not in self.addedGizmos) \
                    and (objTrackId not in self.removedGizmos) \
                    and not (isNonGeometryExportedAsGeometry(evaluatedObj) and (objTrackId in self.dgUpdates['all'])) \
                    and not (tools.isObjectChaosScatter(evaluatedObj) and self._chaosScatterNeedsUpdate(evaluatedObj, objTrackId)) \
                    and instance is None:
                return False

            if not evaluatedObj.is_evaluated:
                # Objects that are in the depsgraph but are not evaluated are not visible in the scene.
                # This is true e.g. for certain objects that are marked as 'Disabled in renders'.
                return False

        # Past every skip guard, so this object is really being re-exported on this update. Counted
        # here and not at the call sites because exportObject() is entered both from the scene pass
        # and from the instance pass - 'objs' counts exports, 'uniqueObjs' counts objects.
        self.sceneStats.addObject(objTrackId)

        exported = True

        match evaluatedObj.type:
            case 'MESH'| 'META' | 'SURFACE' | 'FONT' | 'CURVE':
                if tools.isObjectChaosScatter(evaluatedObj):
                    # Pre-5.1 the scatter carrier is a vertices-only Mesh (a PointCloud cannot
                    # be sized from Python there); route it like the POINTCLOUD case below.
                    from vray_blender.exporting import scatter_export
                    scatter_export.exportChaosScatter(self, evaluatedObj, isVisible, force)
                elif tools.isObjectVrayScene(evaluatedObj):
                    # VRayScene does not handle correctly updates during IPR, this is why they are disabled
                    self._exportVrayScene(evaluatedObj, isVisible, instance, force)
                elif tools.isObjectVrayProxy(evaluatedObj):
                    self._exportVrayProxy(evaluatedObj, exportGeometry, isVisible, instance, force)
                elif tools.isObjectVRayDecal(evaluatedObj):
                    self.exportVRayDecal(evaluatedObj, isVisible, instance)
                    # A decal is exported as a VRayDecal plugin and has no Node of its own.
                    # Reporting it as exported would make the instance pass reference a Node
                    # plugin that is never created.
                    exported = False
                elif not tools.isObjectNonMeshClipper(evaluatedObj):
                    exported = self._exportMeshObject(evaluatedObj, exportGeometry, isVisible, instance, asyncExport, force)
                else:
                    # This is a clipper object that should not be drawn - it has no Node plugin either
                    self.hiddenObjects.append(evaluatedObj)
                    exported = False

                # In addition to the exported mesh, export clipper settings for it
                self._exportClipper(evaluatedObj)

            case 'POINTCLOUD':
                if tools.isObjectChaosScatter(evaluatedObj):
                    from vray_blender.exporting import scatter_export
                    scatter_export.exportChaosScatter(self, evaluatedObj, isVisible, force)
                else:
                    self._exportPointCloud(evaluatedObj, exportGeometry, isVisible, instance, force)
            case "VOLUME":
                SmokeExporter(self).exportVolume(evaluatedObj, exportGeometry, isVisible)
            case 'CURVES':
                if not evaluatedObj.vray.isVRayFur:
                    self._exportCurves(evaluatedObj, exportGeometry, isVisible, instance, force)
                else:
                    # A V-Ray Fur object has no Node plugin of its own - its hair geometry is
                    # exported separately by the FurExporter, into its own instancer. Reporting it
                    # as exported would make the instance pass reference a Node plugin that is
                    # never created.
                    exported = False
            case 'EMPTY':
                if tools.isObjectVRayGaussian(evaluatedObj):
                    self._exportVRaySplat(evaluatedObj, isVisible, force)
                elif tools.isObjectVRayInfinitePlane(evaluatedObj):
                    self._exportEmptyGeometry(evaluatedObj, "GeomPlane", isVisible, force)
                elif tools.isObjectVRayPerfectSphere(evaluatedObj):
                    self._exportEmptyGeometry(evaluatedObj, "GeomPerfectSphere", isVisible, force)
            case _:
                # print(f"Export of {evaluatedObj.type} not implemented")
                pass

        TestBreak.check(self)
        return exported


    def _chaosScatterNeedsUpdate(self, obj: bpy.types.Object, objTrackId: int):
        """ True when the scatter carrier itself was re-evaluated, or when any object it references
            (target/model/spline/camera) changed this cycle - either way the GeomScatter chain must
            re-export even though none of the other change signals fired.
        """
        from vray_blender.exporting import scatter_export
        return (objTrackId in self.dgUpdates['all']) or scatter_export.scatterNeedsUpdate(self, obj)


    def _exportClipper(self, clipperObj: bpy.types.Object):
        pluginName = Names.pluginObject("clipper", Names.object(clipperObj))
        plDesc = PluginDesc(pluginName, "VRayClipper")
        vrayClipper = clipperObj.vray.VRayClipper
        plDesc.vrayPropGroup = vrayClipper

        if vrayClipper.enabled:
            mtlPlugin = AttrPlugin()

            if (not vrayClipper.use_obj_mtl) and vrayClipper.material:
                mtlName = Names.object(bpy.data.materials[vrayClipper.material])
                mtlPlugin = AttrPlugin(mtlName)

            clipMeshPlugin = AttrPlugin()
            if vrayClipper.use_obj_mesh:
                clipMeshNodeName = Names.vrayNode(Names.object(clipperObj))
                plugin_utils.forwardDeclarePlugin(self, clipMeshNodeName, 'Node')
                clipMeshPlugin = AttrPlugin(clipMeshNodeName)

            excluded = []
            if collExcluded := vrayClipper.exclusion_nodes_ptr:
                excluded = [AttrPlugin(Names.vrayNode(Names.object(o))) for o in collExcluded.objects]

                for nodePlugin in excluded:
                    plugin_utils.forwardDeclarePlugin(self, nodePlugin.name, 'Node')


            plDesc.setAttributes({
                "clip_mesh"         : clipMeshPlugin,
                "transform"         : clipperObj.matrix_world,
                "material"          : mtlPlugin,
                "exclusion_nodes"   : excluded
            })

            export_utils.exportPlugin(self, plDesc)
            self.objTracker.trackPlugin(getObjTrackId(clipperObj), pluginName)
        elif self.interactive:
            # Plugins are only tracked  in interactive render mode
            objPluigns = self.objTracker.getPlugins(getObjTrackId(clipperObj))

            for pluginName in [name for name in objPluigns if name.startswith("clipper@")]:
                vray.pluginRemove(self.renderer, pluginName)


    # Remove plugins associated with object node tree
    def _forgetObjNodes(self, objId):
        nodeIds = self.nodeTracker.getOwnedNodes(objId)
        for nodeId in nodeIds:
            for pluginName in self.nodeTracker.getNodePlugins(objId, nodeId):
                vray.pluginRemove(self.renderer, pluginName)
                trackerLog(f"REMOVE OBJECT NODE PLUGIN: {pluginName}")

        self.nodeTracker.forgetObj(objId)


    # Remove the plugins associated with deleted objects
    # Use only for the interactive viewport
    def prunePlugins(self):
        assert(self.interactive)

        # LIGHT objects are tracked by the LightExporter
        objectIds = set()
        psysIds = set()
        for obj in self.allObjects:
            if obj.type != 'LIGHT':
                objectIds.add(getObjTrackId(obj))

                for psys in obj.particle_systems:
                    psysIds.add(getObjTrackId(psys.settings))

        # An instance source is not necessarily in the scene - see instancedObjectTrackIds.
        objectIds.update(self.instancedObjectTrackIds)

        diff = set(self.objTracker.diff(objectIds))

        # Local import like every other scatter_export use here (soft dependency).
        from vray_blender.exporting import scatter_export

        # A live GeomScatter still lists the Nodes about to be pruned. See the TODO there.
        scatter_export.tearDownScattersReferencing(self, diff)

        for objTrackId in diff:
            self._forgetObjNodes(objTrackId)

            for pluginName in self.objTracker.getOwnedPlugins(objTrackId):
                vray.pluginRemove(self.renderer, pluginName)
                trackerLog(f"REMOVE: {objTrackId} => {pluginName}")
            self.objTracker.forget(objTrackId)

            for pluginName in self.instTracker.getOwnedPlugins(objTrackId):
                vray.pluginRemove(self.renderer, pluginName)
                trackerLog(f"REMOVE: {objTrackId} => {pluginName}")
            self.instTracker.forget(objTrackId)

        # Remove the material options plugins associated with the updated objects
        mtlOptionsUpdates = UpdateTracker.getUpdatesOfType(UpdateTarget.OBJECT_MTL_OPTIONS, UpdateFlags.TOPOLOGY)

        for u in mtlOptionsUpdates:
            objTrackId = u[0]
            objMtlPlugins = self.objMtlTracker.getOwnedPlugins(objTrackId)
            for pluginName in objMtlPlugins:
                vray.pluginRemove(self.renderer, pluginName)
                trackerLog(f"REMOVE: {objTrackId} => {pluginName}")

            self.objMtlTracker.forget(objTrackId)

        self.furExporter.purgeFurInfo()

        diff = self.modTracker.diff(psysIds)
        for psysTrackId in diff:
            for pluginName in self.modTracker.getOwnedPlugins(psysTrackId):
                vray.pluginRemove(self.renderer, pluginName)
                trackerLog(f"REMOVE: {psysTrackId} => {pluginName}")
            self.modTracker.forget(psysTrackId)
            self.objTracker.forget(psysTrackId)


    # Sync object visibility in the viewport
    # This method will use the 'visible' property of plugins of type 'Node'
    # in order to switch on and off parts of the scene
    def syncObjVisibility(self):
        """ Blender reports no per-object depsgraph update when visibility changes - hiding an object
            produces a single flagless Scene entry and does not mention the object at all (see
            ExporterContext.structuralUpdate). So the whole scene is rescanned whenever that signal
            appears, and on every other interactive update only the objects the depsgraph actually
            reported are re-checked.

            NOTE: visible_get() / holdout_get() / indirect_only_get() return False rather than raising
            for objects that are not in the view layer (excluded collection, another scene, unlinked),
            so no guard is needed for the objects dg.updates may report.
        """

        # Both collections describe what changed during *this* sync, so they have to start empty.
        # In production renders the ExporterContext is created once per job and reused for every
        # animation frame (engine/renderer_prod.py), so without this reset they accumulate for the
        # whole job: the first frame is a full export and fills them with every object in the scene,
        # after which the change guard in exportObject() never fires again and every object is
        # re-exported on every frame. In IPR the context is recreated per update cycle, so there
        # the reset is a no-op.
        self.objectsWithUpdatedVisibility.clear()
        self.objectsWithUpdatedHoldout.clear()

        def isVisibleInLocalView(obj, view3d):
            if not view3d:
                return True
            if obj.type not in EXPORTED_OBJECT_TYPES:
                return True

            evalObj = obj.evaluated_get(self.dg)
            return evalObj.local_view_get(view3d)

        view3d = self.uiRegionContext.view3d if self.interactive else None
        localView = view3d if (view3d and view3d.local_view) else None

        # Local view is per-viewport state, so the depsgraph can only ever half-describe it. Measured
        # on Blender 5.0.1: entering local view, leaving it, and adding or removing an object while
        # it stays on all raise exactly one flagless Scene entry and never name the objects involved.
        # That is enough for structuralUpdate to force the full path below - which is what catches a
        # membership change, since the incremental path would see no updated object at all - but it
        # is byte-identical to a selection and says nothing about WHICH View3D is driving the export.
        # Diffing a token of our own covers what the depsgraph cannot report: the export moving to a
        # different viewport, which is not a depsgraph event.
        localViewToken = localView.as_pointer() if localView else None
        localViewChanged = localViewToken != self.persistedState.localViewToken

        def isShownNow(obj):
            return isObjectVisible(self, obj) and isVisibleInLocalView(obj, localView)

        def needsSync(obj):
            return obj.type in tools.EXPORTED_OBJECT_TYPES or obj.is_instancer or tools.isObjectVRayGaussian(obj)

        def syncHoldout(obj, objTrackId):
            # Diff Holdout / Indirect Only here too, alongside visibility (see holdoutState).
            holdoutState = (obj.holdout_get(), obj.indirect_only_get())
            if holdoutState != self.persistedState.holdoutState.get(objTrackId):
                self.objectsWithUpdatedHoldout.add(objTrackId)
                self.persistedState.holdoutState[objTrackId] = holdoutState

        def syncVisibility(obj, objTrackId, isShown):
            self.objectsWithUpdatedVisibility[objTrackId] = isShown

            if obj.type == 'LIGHT': # Light objects are synced by the LightExporter
                return

            self.furExporter.syncVisibility(obj, isShown)

            for pluginName in self.objTracker.getPlugins(objTrackId):
                # TODO: Checking for names is error-prone, but we might not know the types of
                # all plugins if they were exported in C++. Make C++ return the types
                # of the exported plugins and track plugin type as well, so that here
                # we could search by plugin type
                isInstanced = self.objTracker.getPluginInstanced(pluginName)
                if pluginName.startswith("node@"):
                    plugin_utils.updateValue(self.renderer, pluginName, "visible", isShown and not isInstanced)
                    trackerLog(f"{'SHOW' if isShown else 'HIDE'} : {objTrackId} => {pluginName}")
                elif pluginName.endswith("@PhxShaderSim") or isPluginVRayDecal(pluginName):
                    plugin_utils.updateValue(self.renderer, pluginName, "enabled", isShown and not isInstanced)
                    trackerLog(f"{'SHOW' if isShown else 'HIDE'} : {objTrackId} => {pluginName}")

        if self.fullExport or self.structuralUpdate or localViewChanged:
            # The depsgraph does not include collections, so the scene is the source of truth here.
            # One pass computes the new visible set and syncs the plugins; wasShown is compared
            # against the previous cycle's set, so building the new one as we go is safe.
            prevVisibleObjects = self.persistedState.visibleObjects
            currentVisibleObjects = set()

            for obj in self.ctx.scene.objects:
                objTrackId = getObjTrackId(obj)
                isShown = isShownNow(obj)

                if isShown:
                    currentVisibleObjects.add(objTrackId)

                if not needsSync(obj):
                    continue

                syncHoldout(obj, objTrackId)

                if (objTrackId in prevVisibleObjects) != isShown or self.fullExport:
                    syncVisibility(obj, objTrackId, isShown)

            # Some objects may be referenced by multiple users, that is why
            # visibility in the tracker cannot be changed while syncing above
            self.persistedState.visibleObjects = currentVisibleObjects

            # Committed only now that the rescan has actually run. Written before the loop, an
            # exception raised inside it would leave the token looking rescanned and the rescan
            # would never be retried. Only this branch needs to write it: reaching the incremental
            # path below means localViewChanged was False, i.e. the token is already current.
            self.persistedState.localViewToken = localViewToken
            return

        # Incremental: only an object the depsgraph reported can have changed. The new states are
        # collected and applied after the loop rather than written straight into the persisted set,
        # for the same reason the full path above swaps its set in only at the end: the set is the
        # previous cycle's snapshot and is still being read for wasShown while the loop runs, so an
        # object sharing a track id with one already visited would otherwise see the other one's
        # write instead of the snapshot.
        visibleObjects = self.persistedState.visibleObjects
        newVisibility = {}

        for obj in self.dgUpdatedObjects:
            objTrackId = getObjTrackId(obj)
            wasShown = objTrackId in visibleObjects
            isShown = isShownNow(obj)

            newVisibility[objTrackId] = isShown

            if not needsSync(obj):
                continue

            syncHoldout(obj, objTrackId)

            if wasShown != isShown:
                syncVisibility(obj, objTrackId, isShown)

        for objTrackId, isShown in newVisibility.items():
            if isShown:
                visibleObjects.add(objTrackId)
            else:
                visibleObjects.discard(objTrackId)


    def _calculateGizmoStates(self):
        from vray_blender.exporting.tools import getInputSocketByAttr
        from vray_blender.nodes.utils import getObjectsFromSelector
        from vray_blender.nodes.tools import isVrayNodeTree

        if not hasattr(self.ctx.scene.world, 'node_tree'):
            # Scene has no world node tree
            return

        worldNodeTree = self.ctx.scene.world.node_tree
        if not isVrayNodeTree(worldNodeTree, 'WORLD'):
            # This may be a Blender's nodetree
            return

        activeGizmos = set()

        if worldOutput := NodesUtils.getOutputNode(worldNodeTree, 'WORLD'):
            for node in [n for n in worldNodeTree.nodes if hasattr(n, "vray_plugin") and n.vray_plugin == 'EnvironmentFog']:
                if not NodesUtils.areNodesInterconnected(node, worldOutput):
                    continue

                gizmoSock = getInputSocketByAttr(node, 'gizmos')
                selectedObjects = set()

                if selectorNode := resolveSelectorNode(gizmoSock):
                    selectedObjects = {getObjTrackId(o) for o in getObjectsFromSelector(selectorNode, self.ctx)}
                else:
                    selectedObjects = {getObjTrackId(o) for o in node.EnvironmentFog.gizmo_selector.getSelectedItems(self.ctx, 'objects') }

                activeGizmos.update([oid for oid in selectedObjects if oid in self.visibleObjects])

        self.addedGizmos = activeGizmos.difference(self.activeGizmos)
        self.removedGizmos = self.activeGizmos.difference(activeGizmos)
        self.activeGizmos.clear()
        self.activeGizmos.update(activeGizmos)


    def _syncGizmos(self):
        # Remove plugins associated with removed gizmo objects
        gizmoTracker = self.objTrackers['GIZMO']

        for objTrackId in self.removedGizmos:
            for nodePluginName in gizmoTracker.getOwnedPlugins(objTrackId):
                vray.pluginRemove(self.renderer, nodePluginName)
                trackerLog(f"REMOVE: {objTrackId} => {nodePluginName}")

            gizmoTracker.forget(objTrackId)


    def _isInstancerVisibilityDisabled(self, obj: bpy.types.Object):
        """ Return whether the object is an instancer that is explicitly hidden as instancer.

            The notion of instancer visibility is different from the object visibility. Object visibility
            determines whether the object and all of its dependent objects will be visible in the scene.
            Instancer visibility determines whether the instancer object itself will be rendered in the scene.

            Args:
                obj (bpy.types.Object): any object

            Returns:
                bool :  True if the object is an instancer with its instancer visibility disabled.
                        False is the object is not an instancer or is an instancer with instancer visibility
                            enabled.
        """
        hasParticleSystemModifier = any(m.type == 'PARTICLE_SYSTEM' for m in obj.modifiers)

        if obj.is_instancer or hasParticleSystemModifier:
            return not (obj.show_instancer_for_viewport if self.interactive else obj.show_instancer_for_render)

        return False


def run(ctx: ExporterContext):
    exporter = GeometryExporter(ctx)
    exporter.export()
    return exporter
