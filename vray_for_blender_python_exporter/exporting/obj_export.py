import bpy
import numpy as np
from collections import defaultdict

from vray_blender.exporting import tools
from vray_blender.exporting.smoke_export import SmokeExporter
from vray_blender.exporting.hair_export import HairExporter
from vray_blender.exporting.instancer_export import InstancerExporter
from vray_blender.exporting.node_export import exportNodePlugin
from vray_blender.exporting.node_exporters.geometry_node_export import exportVRayNodeDisplacement
from vray_blender.exporting.plugin_tracker import getObjTrackId, log as trackerLog
from vray_blender.exporting.update_tracker import UpdateFlags, UpdateTarget, UpdateTracker
from vray_blender.lib.blender_utils import geometryObjectIt
from vray_blender.lib.defs import AttrPlugin, DataArray, ExporterBase, ExporterContext, PluginDesc
from vray_blender.lib import plugin_utils, sys_utils, export_utils
from vray_blender.lib.names import Names
from vray_blender import debug
from vray_blender.bin import VRayBlenderLib as vray
from vray_blender.nodes import utils as NodesUtils
from vray_blender.plugins.geometry.GeomHair import getGeomHairPluginName

from vray_blender.exporting.node_export import *
from vray_blender.exporting.plugin_tracker import TrackObj

class Object(object):
    """ No-name object """
    pass


class MeshData:
    NORMALS_FACE    = 0
    NORMALS_POINT   = 1
    NORMALS_CORNER  = 2

    def __init__(self, name = ""):
        self.name           = name
        self.normalsDomain  = MeshData.NORMALS_FACE
        self.options        = vray.MeshExportOptions()
        self.vertices       : DataArray = None
        self.loopTris       : DataArray = None
        self.loops          : DataArray = None
        self.normals        : DataArray = None
        self.loopTriPolys   : DataArray = None
        self.polyMtlIndices : DataArray = None
        self.loopUVs        : list[DataArray] = []
        self.loopColors     : list[DataArray] = []

        self.subdiv = Object()
        self.subdiv.enabled     = False
        self.subdiv.level       = 0
        self.subdiv.type        = 0
        self.subdiv.useCreases = False

        self.options.mergeChannelVerts = False

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



class GeometryExporter(ExporterBase):
    """ Export all geometry objects in a depsgraph """

    def __init__(self, ctx: ExporterContext):
        super().__init__(ctx)

        self.instancers: list[bpy.types.ID] = []
        self.hairExporter   = HairExporter(self)
        self.objTracker     = ctx.objTrackers['OBJ']
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

    def export(self):
        if self.preview:
            self._exportPreview()
        else:
            # The list of updated light gizmos includes objects that have been updated and objects that were previously
            # used as gizmos but have been detached from the mesh lights
            disconnectedMeshLightGizmos = {p.gizmoObjTrackId for p in self.persistedState.activeMeshLightsInfo.difference(self.activeMeshLightsInfo)}
            self.updatedMeshLightGizmos = {p.gizmoObjTrackId for p in self.updatedMeshLightsInfo}.union(disconnectedMeshLightGizmos)

            disconnectedFurGizmos = {p.gizmoObjTrackId for p in self.persistedState.activeFurInfo.difference(self.activeFurInfo)}
            self.updatedFurGizmos = {p.gizmoObjTrackId for p in self.updatedFurInfo}.union(disconnectedFurGizmos)

            self._calculateGizmoStates()

            self._exportScene()


    def _meshFromObj(self, obj: bpy.types.Object) -> bpy.types.Mesh | None:
        # Get temporary mesh object from the object evaluated above. This mesh
        # will be destroyed once we finish the export of the object, so don't 
        # hang on to it
        # INVESTIGATE: what should the value of 'preserve_all_data_layers' be?
        #   Looks like color channel data is not available if False
        if (obj.type == 'MESH' and obj.mode != 'EDIT'):
            mesh = obj.data
        else:
            if not (mesh := obj.to_mesh(preserve_all_data_layers=True, depsgraph=self.dg)):
                return None
            self.objectsWithTempMeshes.append(obj)

        # VRay requires triangular faces
        self.ts.timeThis("calc_triangles", lambda :  mesh.calc_loop_triangles())

        return mesh


    def _fillMeshData(self, mesh: bpy.types.Mesh, name: str, isInstanced: bool):
        meshData = MeshData(name)

        meshData.vertices       = DataArray(mesh.vertices[0].as_pointer(), len(mesh.vertices))
        meshData.loops          = DataArray(mesh.loops[0].as_pointer(), len(mesh.loops))
        meshData.loopTris       = DataArray(mesh.loop_triangles[0].as_pointer(), len(mesh.loop_triangles))
        meshData.loopTriPolys   = DataArray(mesh.loop_triangle_polygons[0].as_pointer(), len(mesh.loop_triangle_polygons))

        # Blender adds 'material_index' attribute to the mesh when additional material slots are created.
        meshData.polyMtlIndices = DataArray.fromAttribute(mesh, 'material_index')

        match mesh.normals_domain:
            case 'FACE':
                meshData.normals    = DataArray(mesh.polygon_normals[0].as_pointer(), len(mesh.polygon_normals))
                meshData.normalsDomain = MeshData.NORMALS_FACE
            case 'POINT':
                meshData.normals    = DataArray(mesh.vertex_normals[0].as_pointer(), len(mesh.vertex_normals))
                meshData.normalsDomain = MeshData.NORMALS_POINT
            case 'CORNER':
                meshData.normals    = DataArray(mesh.corner_normals[0].as_pointer(), len(mesh.corner_normals))
                meshData.normalsDomain = MeshData.NORMALS_CORNER

        for layer in mesh.uv_layers:
            # layer may have empty data when object's mesh is in edit mode
            #  TODO: is this the correct check? Should we check the viewport mode instead?
            if len(layer.data) > 0:
                meshData.loopUVs.append(DataArray(layer.data[0].as_pointer(), len(layer.data), layer.name))

        for layer in mesh.color_attributes:
            meshData.loopColors.append(DataArray(layer.data[0].as_pointer(), len(layer.data), layer.name))

        meshData.options.forceDynamicGeometry = (sys_utils.isGPUEngine(self.ctx.scene) and self.interactive) or isInstanced
        meshData.options.useSubsurfToOSD = tools.vrayExporter(self.ctx).subsurf_to_osd
        meshData.options.mergeChannelVerts = False

        return meshData

    def _applyMeshModifiers(self, obj: bpy.types.Object, meshData: MeshData):
        """ Apply modifiers to the object's proper geometry """
        if len(obj.modifiers) == 0:
            return

        for modifier in obj.modifiers:
            match modifier.type:
                case "SUBSURF":  # Subdivided surface
                    meshData.options.mergeChannelVerts = True

                case _:
                    pass

        if tools.vrayExporter(self.ctx).subsurf_to_osd:
            lastMod = obj.modifiers[-1]
            if lastMod.type == "SUBSURF": # Subdivided surface
                meshData.subdiv.enabled     = True
                meshData.subdiv.level       = lastMod.levels if self.interactive else lastMod.render_levels
                meshData.subdiv.type        = 0 if lastMod.subdivision_type == "CATMULL_CLARK" else 1 
                meshData.subdiv.use_creases = lastMod.use_creases

    def _exportNonMeshModifiers(self, obj: bpy.types.Object, exportGeometry: bool, isVisible: bool):
        """ Apply modifiers that do not change object's proper geometry. """
        for modifier in obj.modifiers:
            match modifier.type:
                case 'FLUID':
                    if (modifier.fluid_type == 'DOMAIN') and (modifier.domain_settings.domain_type == 'GAS'):
                        SmokeExporter(self).exportFluidModifier(obj, exportGeometry, isVisible)
                case _:
                    pass

    def _exportParticleSystems(self, obj: bpy.types.Object, exportGeometry: bool):
        ####  Export modifiers  ###
        objTrackId = getObjTrackId(obj)

        for pmod in [m for m in obj.modifiers if m.type == 'PARTICLE_SYSTEM']:
            psys = pmod.particle_system
            if psys.settings.type == "HAIR":
                modVisible = isModifierVisible(self, pmod)
                # Export modifier if the parent object's geometry has changed or the parent object was made visible
                exportModifier = exportGeometry or self.objectsWithUpdatedVisibility.get(objTrackId, False)
                particleHairName = self.hairExporter.getParticleHairName(obj.original, psys)
                
                geomPlugin = None
                if exportModifier and modVisible:
                    fnExportParticles = lambda pmod = pmod: self.hairExporter.exportFromParticles(obj, pmod)
                    geomPlugin = self.ts.timeThis("collect_hair_particles_data", fnExportParticles )
                    if (not geomPlugin) and (Names.object(obj) in self.persistedState.objToGeomPluginName):
                        # Previously exported particle data is now invalid (likely due to user changes in particle settings).
                        # Force the node to reference an empty plugin to hide any outdated geometry until valid data is available again.
                        geomPlugin = AttrPlugin(forceUpdate=True)
                else:
                    geomPlugin = AttrPlugin(self.persistedState.objToGeomPluginName.get(Names.object(obj), ""))

                if not geomPlugin:
                    # Invalid particles data, nothing to export
                    return

                nodePlugin = exportNodePlugin(self, obj, geomPlugin, particleHairName, self.objTracker, isInstancer=False, visible=modVisible)
                self.objTracker.trackPlugin(objTrackId, nodePlugin.name)


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
            with nodeCtx.push(displacementNode):
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


    def _exportObjNodeTree(self, obj: bpy.types.Object, geomPlugin, isVisible = True, 
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
                
                geomPlugin = self._exportGeometrySockets(nodeCtx, nodeOutput, geomPlugin, objTrackId)
                self.persistedState.objToGeomPluginName[Names.object(obj, instance)] = geomPlugin.name

                nodePlugin = exportNodePlugin(self, obj, geomPlugin, Names.object(obj, instance), self.objTracker, instance=instance, visible=isVisible)
                export_utils.exportObjProperties(obj, nodeCtx, self.objTracker, nodeOutput, [nodePlugin.name])
        
        return nodePlugin


    def _exportMeshObject(self, obj: bpy.types.Object, exportGeometry: bool, isVisible: bool, instance: bpy.types.DepsgraphObjectInstance, asyncExport):
        evaluatedObj = obj.evaluated_get(self.dg)
        assert evaluatedObj.is_evaluated, f"Evaluated object expected: {evaluatedObj.name}"

        objectName = Names.object(evaluatedObj, instance)

        meshPluginName = None
        if exportGeometry:
            meshPluginName = self.ts.timeThis("export_mesh", lambda: self._exportMesh(evaluatedObj, instance, asyncExport))

        elif objectName in self.persistedState.objToGeomPluginName: # Check if the object has valid geometry already exported.
            # Here we want the base GeomStaticMesh plugin name, not the GeomDisplacedMesh or GeomStaticSmoothedMesh plugin name
            meshPluginName = Names.objectData(evaluatedObj, instance)
        
        # Non-mesh modifiers show geometry, but don't need a Node plugin to be exported for them.
        # The geometry associated with them may also not need exporting, so apply them regardless
        # of the return value of exportMesh()
        self._exportNonMeshModifiers(evaluatedObj, exportGeometry, isVisible)

        if meshPluginName is None:
            # Failed to export mesh. Nothing more we can do, skip exporting the Node plugin
            return False

        if isVisible:
            ####  Export modifiers  ###
            self._exportParticleSystems(evaluatedObj, exportGeometry)

        objTrackId = getObjTrackId(evaluatedObj)
        isMeshLightGizmo = objTrackId in [p.gizmoObjTrackId for p in self.activeMeshLightsInfo]
        isEnvFogGizmo = objTrackId in self.activeGizmos
        isGizmo = isMeshLightGizmo or isEnvFogGizmo

        # Export node
        nodePlugin = None

        if (not isGizmo) and (not self._isInstancerVisibilityDisabled(evaluatedObj)):
            baseGeomPlugin = AttrPlugin(meshPluginName) # Empty plugin attribute representing the object's geometry
            
            if evaluatedObj.vray.ntree and NodesUtils.getOutputNode(evaluatedObj.vray.ntree, 'OBJECT'):
                nodePlugin = self._exportObjNodeTree(evaluatedObj, baseGeomPlugin, isVisible, instance)
            else:
                nodePlugin = exportNodePlugin(
                    self, 
                    evaluatedObj,
                    baseGeomPlugin,
                    objectName,
                    self.objTracker,
                    instance = instance, 
                    visible = isVisible
                )
        
            if nodePlugin is not None:
                plugin_utils.updateValue(self.renderer, nodePlugin.name, "objectID", evaluatedObj.vray.VRayObjectProperties.objectID)

        else:
            # 'Node' plugin should not be exported for gizmos
            nodePluginName = Names.vrayNode(Names.object(evaluatedObj))
            vray.pluginRemove(self.renderer, nodePluginName)
            self.objTracker.forgetPlugin(objTrackId, nodePluginName)

        return True

    # Export a MESH object to GeomStaticMesh VRay plugin
    def _exportMesh(self, evaluatedObj: bpy.types.Object, instance: bpy.types.DepsgraphObjectInstance, asyncExport: bool):
        
        if (mesh := self._meshFromObj(evaluatedObj)) is None:
            debug.printError(f"Failed to convert object {evaluatedObj.name} to mesh")
            return None
            
        isInstanced = instance is not None
        meshPluginName = Names.objectData(evaluatedObj, instance)

        if (len(mesh.loops) == 0):
            if (evaluatedObj.mode == 'EDIT') and isInstanced:
                # While a GN instancer object's geometry is being edited, the instanced objects' meshes in the 
                # evaluated depsgraph are empty. We only need to re-export the instance data in this case.
                return meshPluginName
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
            return None
        
        meshData = self._fillMeshData(mesh, meshPluginName, isInstanced)
        self._applyMeshModifiers(evaluatedObj, meshData)

        vray.pluginCreate(self.renderer, meshPluginName, 'GeomStaticMesh')
        vray.exportGeometry(self.renderer, meshData, asyncExport)
        self.objTracker.trackPlugin(getObjTrackId(evaluatedObj), meshPluginName, isInstanced)

        self.persistedState.objToGeomPluginName[Names.object(evaluatedObj, instance)] = meshPluginName

        return meshPluginName


    # Export a MESH object to VRayScene plugin
    def _exportVrayScene(self, obj: bpy.types.Object, isVisible: bool):
        vrayScene = obj.data.vray.VRayScene
        pluginName = Names.pluginObject("vrayscene", Names.object(obj))
        pluginDesc = PluginDesc(pluginName, "VRayScene")
        pluginDesc.vrayPropGroup = vrayScene

        pluginDesc.setAttribute("filepath", vrayScene.filepath)
        pluginDesc.setAttribute("transform", obj.matrix_world)
        pluginDesc.setAttribute("mrs_visibility", isVisible)

        export_utils.exportPlugin(self, pluginDesc)
        # NOTE: The object is deliberately not added to the plugin tracker because
        # no changes are allowed to VRayScene objects during IPR.


    # Export a MESH object to GeomMeshFile plugin
    def _exportVrayProxy(self, obj: bpy.types.Object, exportGeometry: bool, isVisible: bool, instance: bpy.types.DepsgraphObjectInstance = None):
        objectName = Names.object(obj)
        pluginName = Names.pluginObject("vrayproxy", objectName)
        isInstanced = instance is not None

        if exportGeometry:
            geomMeshFile = obj.data.vray.GeomMeshFile

            pluginDesc = PluginDesc(pluginName, "GeomMeshFile")
            pluginDesc.vrayPropGroup = geomMeshFile

            export_utils.exportPlugin(self, pluginDesc)
            self.objTracker.trackPlugin(getObjTrackId(obj), pluginName, isInstanced=isInstanced)

        # Export node
        exportNodePlugin(self, obj, AttrPlugin(pluginName), Names.object(obj, instance), 
                               self.objTracker, instance=instance, isInstancer=False, visible=isVisible)


    def _exportCurves(self, obj: bpy.types.Object, exportGeometry: bool, isVisible: bool, instance: bpy.types.DepsgraphObjectInstance = None):
        obj = obj.evaluated_get(self.dg)
        assert obj.is_evaluated, f"Evaluated object expected: {obj.name}"

        geomPlugin = self.ts.timeThis("collect_hair_curves_data", lambda: self.hairExporter.exportFromCurves(obj, exportGeometry))
        
        # Export node
        exportNodePlugin(self, obj, geomPlugin, Names.object(obj, instance), 
                                self.objTracker, instance=instance, isInstancer=False, visible=isVisible)


    # Export a POINTCLOUD object to a GeomParticleSystem VRay plugin
    def _exportPointCloud(self, obj: bpy.types.Object, exportGeometry: bool, isVisible: bool, instance: bpy.types.DepsgraphObjectInstance = None):
        obj = obj.evaluated_get(self.dg)
        assert obj.is_evaluated, f"Evaluated object expected: {obj.name}"

        pluginName = Names.object(obj, instance) if obj.is_modified(self.ctx.scene, tools.evalMode(self.interactive)) \
                                        else Names.objectData(obj, instance)

        if not exportGeometry:
            # Export node as its visibility may have changed
            exportNodePlugin(self, obj, AttrPlugin(pluginName), Names.object(obj, instance), self.objTracker, 
                             instance=instance, isInstancer=False, visible=isVisible)
            return
        
        pc = obj.data.evaluated_get(self.dg)
        numPoints = len(pc.attributes["position"].data)

        data = PointCloudData(pluginName)
        data.renderType = PointCloudData.TYPE_SPHERES
        data.points = tools.foreachGetAttr(pc.attributes["position"].data, "vector", shape=(numPoints, 3), dtype=np.float32)
        data.radii  = tools.foreachGetAttr(pc.attributes["radius"].data, "value", shape=(numPoints, 1), dtype=np.float32)
        
        # TODO: Get the color from some real source ( if needed at all )
        redColor = np.array([1.0, 0.0, 0.0])
        data.colors = np.tile(redColor, (numPoints, 1))
        
        self.ts.timeThis("C++ export_point_cloud", lambda : vray.exportPointCloud(self.renderer, data))

        self.persistedState.objToGeomPluginName[Names.object(obj, instance)] = pluginName

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

        for obj in self.dg.objects:
            # Force geometry export
            self._exportObject(obj.original, exportGeometry=True, isVisible=True)


    def _exportScene(self):
        """ Export all geometry in the scene """
        # Geometry objects are exported in two passes - scene objects and instances.
        # This is necessary because the final render depsgraph does not contain
        # the instanced objects. This is different from the viewport rendering,
        # where all objects are contained in the depsgraph.
        self.ts.timeThis("Export non-instanced objects", lambda: self._exportObjects())
        self.ts.timeThis("Export instance data", lambda: self._exportInstances())
        
        self.hairExporter.exportFurObjects() # Export fur objects after all the geometry plugins have been exported

        self._hideInvisibleObjects()
        self._syncGizmos()


    def _exportObjects(self):
        def geometryForExport():
            # NOTE: In prod renders, some objects are not added to the depsgraph, e.g. objects 
            # instanced by legacy instancers. Cycles won't show them in final renders either.
            if self.commonSettings.useMotionBlur and self.isAnimation:
                yield from self.motionBlurBuilder.getGeometryForExport(self.dg.objects)
            else:
                yield from geometryObjectIt(self.dg.objects)

        updatedFurObjects = set(getObjTrackId(p.parentObj) for p in self.updatedFurInfo)

        # Export all scene objects
        for obj in geometryForExport():
            objTrackId = getObjTrackId(obj)

            isFirstMotionBlurFrame = (self.commonSettings.useMotionBlur and self.motionBlurBuilder.isFirstFrame(self.currentFrame))


            exportGeometry = self.fullExport \
                                or ( objTrackId in self.dgUpdates['geometry']) \
                                or isFirstMotionBlurFrame \
                                or (objTrackId not in self.persistedState.processedObjects)

            if obj.vray.isVRayFur:
                self.hairExporter.addFurObjectForExport(obj.original, (exportGeometry or (objTrackId in updatedFurObjects)))
                continue

            with self.objectContext.push(obj):
                # Node plugins' "visible" property for instanced objects should be set to
                # False if we only want to see the instances and not the original instanced object
                isVisible = objTrackId in self.visibleObjects
                self._exportObject(obj.original, exportGeometry, isVisible=isVisible)            

        # Block here, wait for all geoemtry to be exported and release all temp meshes before
        # iterating the scene for instance export. Not doing so will cause crashes.
        vray.finishExport(self.renderer, False)
        
        for obj in self.objectsWithTempMeshes:
            obj.to_mesh_clear()
        self.objectsWithTempMeshes.clear()

    def _exportObject(self,
                      obj: bpy.types.Object,
                      exportGeometry: bool,
                      isVisible: bool,
                      instance: bpy.types.DepsgraphObjectInstance = None,
                      asyncExport = True):

        assert isinstance(obj, bpy.types.Object), "Only Blender 'Object' type accepted"

        objTrackId = getObjTrackId(obj)

        # Mark the object as processed, even if it is not exported.
        # This ensures we can differentiate between objects already handled and new additions to the depsgraph.
        self.persistedState.processedObjects.add(objTrackId)

        if (not self.fullExport) \
                and not exportGeometry \
                and objTrackId not in self.dgUpdates['transform'] \
                and (objTrackId not in self.objectsWithUpdatedVisibility) \
                and (objTrackId not in self.updatedMeshLightGizmos) \
                and (objTrackId not in self.updatedFurGizmos) \
                and (UpdateFlags.NONE == UpdateTracker.getObjUpdate(UpdateTarget.OBJECT_MTL_OPTIONS, obj))\
                and (objTrackId not in self.addedGizmos) \
                and (objTrackId not in self.removedGizmos) \
                and instance is None:
            return False

        evaluatedObj = obj.evaluated_get(self.dg)
        
        if not evaluatedObj.is_evaluated:
            # Objects that are in the depsgraph but are not evaluated are not visible in the scene.
            # This is true e.g. for certain objects that are marked as 'Disabled in renders'.
            return False
        
        exported = True

        match obj.type:
            case 'MESH'| 'META' | 'SURFACE' | 'FONT' | 'CURVE':
                if tools.isObjectVrayScene(obj):
                    if self.fullExport:
                        # VRayScene does not handle correctly updates during IPR, this is why they are disabled
                        self._exportVrayScene(obj, isVisible)
                elif tools.isObjectVrayProxy(obj):
                    self._exportVrayProxy(obj, exportGeometry, isVisible, instance)
                elif not tools.isObjectNonMeshClipper(obj):
                    exported = self._exportMeshObject(obj, exportGeometry, isVisible, instance, asyncExport)
                else:
                    # This is a clipper object that should not be drawn
                    self.hiddenObjects.append(obj)

                # In addition to the exported mesh, export clipper settings for it
                self._exportClipper(obj)
                
            case 'POINTCLOUD':
                self._exportPointCloud(obj, exportGeometry, isVisible, instance)
            case "VOLUME":
                SmokeExporter(self).exportVolume(obj, exportGeometry, isVisible)
            case 'CURVES':
                if not obj.vray.isVRayFur:
                    self._exportCurves(obj, exportGeometry, isVisible, instance)
            case _:
                # print(f"Export of {evaluatedObj.type} not implemented")
                pass
        
        return exported

    def _exportInstances(self):
        instanceChanges = {}
        instancerChanges = {}
        exporter = self
        newInstancers = self.activeInstancers.difference(self.persistedState.activeInstancers)

        def hasInstanceChanged(objTrackId, instancerTrackId):
            """ Returns True if the instance has to be exported because either the instanced object or the 
                instancer have changed.
            """
            if (result := instanceChanges.get((objTrackId, instancerTrackId), None)) is not None:
                return result

            # Blender does not create instanced objects when the scene is rendered for the first time if
            # the instancer is invisible. This is why we need to check if the instancer has become visible
            # since the last export and process the instances if this is so.
            changed = exporter.objectsWithUpdatedVisibility.get(instancerTrackId, False) \
                        or (objTrackId in exporter.dgUpdates['geometry']) \
                        or (instancerTrackId in exporter.dgUpdates['geometry'])
            
            instanceChanges[(objTrackId, instancerTrackId)] = changed
            return changed

        def shouldExportGeometry(objTrackId, instancerTrackId):
            return exporter.fullExport \
                or exporter.objectsWithUpdatedVisibility.get(instancerTrackId, False) \
                or (objTrackId in exporter.dgUpdates['geometry']) \
                or (instancerTrackId in newInstancers)

        def hasInstancerChanged(objTrackId, instancerTrackId):
            if (result := instancerChanges.get((objTrackId, instancerTrackId), None)) is not None:
                return result
            
            changed = exporter.fullExport \
                        or exporter.objectsWithUpdatedVisibility.get(instancerTrackId, False) \
                        or objTrackId in exporter.dgUpdates['transform'] \
                        or instancerTrackId in exporter.dgUpdates['geometry'] \
                        or instancerTrackId in exporter.dgUpdates['transform'] \
                        or (instancerTrackId not in self.persistedState.activeInstancers) and (instancerTrackId in self.activeInstancers)
            
            instancerChanges[(objTrackId, instancerTrackId)] = changed
            return changed
        

        instancerExporter = InstancerExporter(self)

        # If object meshes are generated from a GN tree, they won't be assigned a unique vray IDs. 
        # Track the first exported instance of each object, matching the rest of the instances by 
        # the object's data pointer. 
        # NOTE: We rely on the order of the instances being always the same;
        # if not, an additional map rendom_id to id should be used.
        # NOTE: instance.random_id is stable but is not persisted to the scene.

        exportedGeometry    = {} # id(obj.data) => list[instance.random_id]
        exportedNodes       = {} # id(obj.data) => node_plugin_name

        exportedGeomHair = {} # mark fur objects that have been exported for

        # Pair of nodePluginName, furTrackId, furName for fur objects that have been exported for each instance
        exportedGeomHairNodes : dict[int, list[tuple[str, int, str]]] = {}
        

        updatedFurGizmoObjTrackIdSet = set(p.gizmoObjTrackId for p in self.updatedFurInfo) # Objects selected by fur objects that have been updated.

        # Maps track id of object selected by fur to a pair of ( name of the fur, track id of the fur)
        furGizmoObjTrackIdMap = defaultdict(list)
        for p in self.activeFurInfo:
            furGizmoObjTrackIdMap[p.gizmoObjTrackId].append((p.parentName, p.parentObjTrackId))


        for inst in self.dg.object_instances:
            if not inst.is_instance:
                continue

            obj       = inst.object
            instancer = inst.parent

            objTrackId       = getObjTrackId(obj)
            instancerTrackId = getObjTrackId(instancer)
            dataID           = id(obj.data)

            instanceChanged  = hasInstanceChanged(objTrackId, instancerTrackId)
            instancerChanged = hasInstancerChanged(objTrackId, instancerTrackId)

            if ((instanceChanged or instancerChanged) and (dataID not in exportedGeometry)):
    
                    # Export geometry + node plugins for the instance. 
                    exported = self._exportObject(
                        obj,
                        exportGeometry = shouldExportGeometry(objTrackId, instancerTrackId),
                        isVisible=False,
                        instance=inst,
                        asyncExport=False
                    )

                    if exported:
                        exportedNodes[dataID] = Names.vrayNode(Names.object(obj, inst))
                        exportedGeometry[dataID] = inst.random_id

                    # We need to clear the temp meshes that were created while the instance iterator is vallid.
                    for obj in self.objectsWithTempMeshes:
                        obj.to_mesh_clear()
                    self.objectsWithTempMeshes.clear()


            if instancerChanged and (nodePluginName := exportedNodes.get(dataID)):
                instancerExporter.addInstance(inst, nodePluginName)


            isFurInstanceChanged = (instancerTrackId in updatedFurGizmoObjTrackIdSet) or instancerChanged

            # Marking fur nodes to be added to instancer. They are already exported in the _exportObjects() method.
            if isFurInstanceChanged and (dataID not in exportedGeomHair):
                nodesList = exportedGeomHairNodes[dataID] = []
                exportedGeomHair[dataID] = inst.random_id
                
                for furName, furTrackId in furGizmoObjTrackIdMap[instancerTrackId]:
                    furNodeName = Names.vrayNode(getGeomHairPluginName(furName, Names.object(obj, inst)))
                    
                    # Create a placeholder node plugin for the instancer.
                    # Its properties will be set later in hairExporter.exportFurObjects().
                    vray.pluginCreate(self.renderer, furNodeName, 'Node')

                    nodesList.append((furNodeName, furTrackId, furName))

            # Adding fur nodes to instancer.
            if isFurInstanceChanged and (nodePluginNames := exportedGeomHairNodes.get(dataID)):
                for nodePluginName, furTrackId, furName in nodePluginNames:
                    # The properties of this plugin will be filled in the hairExporter.exportFurObjects() method.
                    instancerName = f"instancer@{getGeomHairPluginName(furName, Names.object(instancer))}"
                    instancerExporter.addInstance(inst, nodePluginName, furTrackId, instancerName)
        
        # Export the collected instancer data
        instancerExporter.export()


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
                vray.pluginCreate(self.renderer, clipMeshNodeName, 'Node')
                clipMeshPlugin = AttrPlugin(clipMeshNodeName)
            
            excluded = []
            if collExcluded := vrayClipper.exclusion_nodes_ptr:
                excluded =  [AttrPlugin(Names.vrayNode(Names.object(o))) for o in collExcluded.objects]
                
                for nodePlugin in excluded:
                    vray.pluginCreate(self.renderer, nodePlugin.name, 'Node')

            
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
        objectIds = { getObjTrackId(o) for o in self.allObjects if o.type not in ('LIGHT') }
        diff = self.objTracker.diff(objectIds)
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

        self.hairExporter.purgeFurInfo()


    # Sync object visibility in the viewport
    # This method will use the 'visible' property of plugins of type 'Node'
    # in order to switch on and off parts of the scene 
    def syncObjVisibility(self):
        from vray_blender.lib.blender_utils import getFirstAvailableView3D

        def isVisibleInLocalView(obj, dg, view3d):
            if not self.interactive:
                # There is no local view mode in production renders
                return True
            
            if obj.type not in GEOMETRY_OBJECT_TYPES:
                return True
            
            evalObj = obj.evaluated_get(dg)
            return evalObj.local_view_get(view3d)
        
        view3d = getFirstAvailableView3D()
        localView = view3d if (view3d and view3d.local_view) else None

        showObjects = {}
        for o in [obj for obj in self.sceneObjects if obj.type in tools.EXPORTED_OBJECT_TYPES or obj.is_instancer]:
            objTrackId = getObjTrackId(o)
            wasShown   = objTrackId in self.persistedState.visibleObjects
            isShown    = (objTrackId in self.visibleObjects) and ((not localView) or isVisibleInLocalView(o, self.dg, view3d))

            if (wasShown != isShown) or (self.fullExport):
                self.objectsWithUpdatedVisibility[objTrackId] = isShown

                showObjects[objTrackId] = isShown
                for pluginName in self.objTracker.getPlugins(objTrackId):
                    # TODO: Checking for names is error-prone, but we might not know the types of 
                    # all plugins if they were exported in C++. Make C++ return the types
                    # of the exported plugins and track plugin type as well, so that here
                    # we could search by plugin type  
                    isInstanced = self.objTracker.getPluginInstanced(pluginName)
                    if pluginName.startswith("node@"):
                        plugin_utils.updateValue(self.renderer, pluginName, "visible", isShown and not isInstanced)
                        trackerLog(f"{'SHOW' if isShown else 'HIDE'} : {objTrackId} => {pluginName}")
                    elif pluginName.endswith("@PhxShaderSim") or o.type == 'LIGHT':
                        plugin_utils.updateValue(self.renderer, pluginName, "enabled", isShown and not isInstanced)
                        trackerLog(f"{'SHOW' if isShown else 'HIDE'} : {objTrackId} => {pluginName}")
        
        # Some objects may be referenced by multiple users, that is why 
        # visibility in the tracker cannot be changed while syncing above 
        for objTrackId, isShown in showObjects.items():
            if isShown:
                self.visibleObjects.add(objTrackId)
            elif objTrackId in (self.visibleObjects):
                self.visibleObjects.remove(objTrackId)


    def _calculateGizmoStates(self):
        from vray_blender.exporting.tools import getInputSocketByAttr, getLinkedFromSocket
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

        for node in [n for n in worldNodeTree.nodes if hasattr(n, "vray_plugin") and n.vray_plugin == 'EnvironmentFog']:
            gizmoSock = getInputSocketByAttr(node, 'gizmos')
            selectedObjects = set()
            if selectorSocket := getLinkedFromSocket(gizmoSock):
                selectedObjects = {getObjTrackId(o) for o in getObjectsFromSelector(selectorSocket.node, self.ctx)}
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
        hasParticleSystemModifier = any([m for m in obj.modifiers if m.type == 'PARTICLE_SYSTEM'])
        
        if obj.is_instancer or hasParticleSystemModifier:
            return not (obj.show_instancer_for_viewport if self.interactive else obj.show_instancer_for_render)
        
        return False


# TODO: This function is just glue for the POC. Remove in final code
def run(ctx: ExporterContext):
    GeometryExporter(ctx).export()

