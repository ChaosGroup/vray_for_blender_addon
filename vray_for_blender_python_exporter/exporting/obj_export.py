import bpy
import bmesh
import numpy as np

from vray_blender.exporting import tools
from vray_blender.exporting.smoke_export import SmokeExporter
from vray_blender.exporting.hair_export import HairExporter
from vray_blender.exporting.instancer_export import InstancerExporter
from vray_blender.exporting.node_export import exportNodePlugin
from vray_blender.exporting.node_exporters.geometry_node_export import exportVRayNodeDisplacement
from vray_blender.exporting.plugin_tracker import getObjTrackId, log as trackerLog
from vray_blender.exporting.update_tracker import UpdateFlags, UpdateTarget, UpdateTracker
from vray_blender.lib.blender_utils import frameInRange, geometryObjectIt
from vray_blender.lib.defs import AttrPlugin, DataArray, ExporterBase, ExporterContext, PluginDesc
from vray_blender.lib import plugin_utils, sys_utils, export_utils
from vray_blender.lib.names import Names
from vray_blender import debug
from vray_blender.bin import VRayBlenderLib as vray
from vray_blender.nodes import utils as NodesUtils

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
        self.loopTriPolys   : np.ndarray = None
        self.polyMtlIndices : np.ndarray = None
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
        self.exported: set                  = set()  # Keep track of what has been exported ( unique names )
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
        
        # A list of all objects for which the visibility has changed since the last export.
        # {objectTrackId: is_visible}
        self.objectsWithUpdatedVisibility = {} 
        
        # Gizmos for the Environment fog effect
        self.addedGizmos  = set()  # Added from the previous update
        self.removedGizmos = set()  # Removed from the previous update

    def export(self):
        if self.preview:
            self._exportPreview()
        else:
            # The list of updated light gizmos includes objects that have been updated and objects that were previously
            # used as gizmos but have been detached from the mesh lights
            disconnectedMeshLightGizmos = {p.gizmoObjTrackId for p in self.cachedMeshLightsInfo.difference(self.activeMeshLightsInfo)}
            self.updatedMeshLightGizmos = {p.gizmoObjTrackId for p in self.updatedMeshLightsInfo}.union(disconnectedMeshLightGizmos)

            self._calculateGizmoStates()

            self._exportScene()


    def _meshFromObj(self, obj: bpy.types.Object) -> bpy.types.Mesh | None:

        if obj.mode == 'EDIT':
            # While an object is in edit mode, evaluated copy won't change. Set a reference
            # to the original object from which the edit-mode data could be obtained.
            evaluatedObject = obj
        else:
            # In order to apply any modifiers / geometry nodes, we need to 'evaluate' the object
            # The returned object is distinct from the original object
            evaluatedObject = obj.evaluated_get(self.dg)

        if evaluatedObject is None:
            return None

        # Get temporary mesh object from the object evaluated above. This mesh
        # will be destroyed once we finish the export of the object, so don't 
        # hang on to it
        # INVESTIGATE: what should the value of 'preserve_all_data_layers' be?
        #   Looks like color channel data is not available if False
        mesh = evaluatedObject.data if evaluatedObject.type == 'MESH' \
                    else evaluatedObject.to_mesh(preserve_all_data_layers=True, depsgraph=self.dg)
        
        if mesh is None:
            debug.printError(f"Cannot convert object {evaluatedObject.name} to mesh")
            return None
        
        # VRay requires triangular faces
        self.ts.timeThis("calc_triangles", lambda :  mesh.calc_loop_triangles())

        if len(mesh.loops) == 0:
            # We don't export non-polygonal meshes at this point
            # TODO: Add support for point clouds represented as meshes with non-connected vertices
            debug.printDebug(f"Object {evaluatedObject.name} has a non-polygonal mesh, skipping export")
            return None

        return mesh

    
    def _fillMeshData(self, mesh: bpy.types.Mesh, name: str, isInstanced: bool, isEditMode: bool):
        if isEditMode:
            mesh = self._getEditDataFromMesh(mesh)
            
        meshData = MeshData(name)

        meshData.vertices       = DataArray(mesh.vertices[0].as_pointer(), len(mesh.vertices))
        meshData.loops          = DataArray(mesh.loops[0].as_pointer(), len(mesh.loops))
        meshData.loopTris       = DataArray(mesh.loop_triangles[0].as_pointer(), len(mesh.loop_triangles))
        meshData.polyMtlIndices = tools.foreachGetAttr(mesh.polygons, "material_index", shape=(len(mesh.polygons), 1), dtype=np.int32)
        meshData.loopTriPolys   = tools.foreachGetAttr(mesh.loop_triangles, "polygon_index", shape=(len(mesh.loop_triangles), 1), dtype=np.int32)
        
        
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

    
    def _getEditDataFromMesh(self, mesh:bpy.types.Mesh):
        """ Return a mesh which is a copy of the 'edit' state of the input mesh.

        Args:
            mesh (bpy.types.Mesh): 

        Returns:
            A new mesh into which the current state of the edited data is copied.
        """

        bm = bmesh.from_edit_mesh(mesh)
        
        # Create a temporary mesh object into which the current state of the edited mesh will be copied
        tempName = f"{mesh.name}_TMP_EDIT"
        tempMesh = bpy.data.meshes.new(tempName)
        tmpObj = bpy.data.objects.new(tempName, tempMesh)
        
        # Save the temp obect reference. It will be exported asynchronously in BlenderLib 
        # and will be deleted after the export is finished.
        self.tempObjects.append(tmpObj)

        # Copy the data from the edit state to the newly created mesh.
        bm.to_mesh(tmpObj.data)
        bm.free()

        return tmpObj.data


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
                
                if modVisible and exportModifier:
                    fnExportParticles = lambda pmod = pmod: self.hairExporter.exportFromParticles(obj, pmod)
                    geomPlugin = self.ts.timeThis("collect_hair_particles_data", fnExportParticles )
                    nodePlugin = self._exportNodePlugin(obj, geomPlugin, geomPlugin.name, isInstancer=False, visible=modVisible)
                    self.objTracker.trackPlugin(objTrackId, nodePlugin.name)


    def _setMeshAttrOfGeom(self, mainGeomPlugin, geomPlugin):
        plugin_utils.updateValue(self.renderer, mainGeomPlugin.name, "mesh", geomPlugin)


    def _getNodeLinkToNode(self, nodeOutput, sockName, nodeType):
        # Returns node link connecting socket to node with specific type
        if sock := getInputSocketByName(nodeOutput, sockName):
            # The output node of the tree is not connected to anything
            nodeLink = getNodeLink(sock)
            if nodeLink and nodeLink.from_node.bl_idname == nodeType:
                return nodeLink            
        return None


    def _exportGeometrySockets(self, nodeCtx, nodeOutput, geomPlugin, trackId):
        advancedGeomPlugin = AttrPlugin()
        displacementNodeLink = self._getNodeLinkToNode(nodeOutput, "Displacement", "VRayNodeDisplacement")
        subdivNodeLink = self._getNodeLinkToNode(nodeOutput,  "Subdivision", "VRayNodeGeomStaticSmoothedMesh")

        if (not displacementNodeLink) and subdivNodeLink:
            advancedGeomPlugin = exportVRayNode(nodeCtx, subdivNodeLink)

        elif displacementNodeLink:
            displacementNode = displacementNodeLink.from_node
            with nodeCtx.push(displacementNode):
                subdivPropGroup = None
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
                
                with TrackNode(nodeCtx.nodeTracker, nodeId):
                    nodeCtx.exportedNodes.add(nodeId)
                    advancedGeomPlugin = exportVRayNodeDisplacement(nodeCtx, subdivPropGroup)
                    # In some cases, the V-Ray node of an object may remain linked to the removed plugin 
                    # associated with the "track" identified by "nodeIdForRemoval".
                    advancedGeomPlugin.forceUpdate = forceUpdateGeomPlugin

        if advancedGeomPlugin and not advancedGeomPlugin.isEmpty():
            # The newly created "advancedGeomPlugin" is used as geometry of the object
            # and the staticGeom(the GeomStaticMesh plugin representing the objects's mesh)
            # is linked to the displacement plugin's mesh attribute.
            self._setMeshAttrOfGeom(advancedGeomPlugin, geomPlugin)
            return advancedGeomPlugin

        return geomPlugin


    def _exportObjProperties(self, obj: bpy.types.Object, nodeCtx: NodeContext, nodeOutput, nodePluginName):
        """ Exports the nodes connected in the "Matte", "Surface" and "Visibility" sockets of 
            object output node
        """
        pluginType = "VRayObjectProperties"
        objProps = obj.vray.VRayObjectProperties
        objPropsPluginName = Names.pluginObject(pluginType, Names.object(obj))
        objPropsPlDesc = PluginDesc( objPropsPluginName, pluginType)
        objPropsPlDesc.vrayPropGroup = objProps

        pluginModule = getPluginModule(pluginType)
    
        # Reset all the attributes to their default value instead of re-exporting a new plugin on every update
        for attrDesc in pluginModule.Parameters:
            objPropsPlDesc.setAttribute(attrDesc['attr'], attrDesc.get('default', None))

        for objProp in ("Matte", "Surface", "Visibility"):
            nodeLink = self._getNodeLinkToNode(nodeOutput, objProp, f"VRayNodeObject{objProp}Props")
            if nodeLink:
                # Only the attributes of connected object property nodes should be exported
                node = nodeLink.from_node
                for attr in node.visibleAttrs:
                    objPropsPlDesc.setAttribute(attr, getattr(objProps, attr))

                if objProp == "Visibility":
                    node.fillReflectAndRefractLists(nodeCtx.exporterCtx, objPropsPlDesc)

        objId = getObjTrackId(obj)
        # During GPU rendering, the first export of ObjectProperties does not trigger an image update.
        # This is a quick hack to work around the issue.
        if objPropsPluginName not in self.objTracker.getPlugins(objId):
            obj.update_tag()

        objPropsAttrPlugin = export_utils.exportPlugin(nodeCtx.exporterCtx, objPropsPlDesc)

        self.objTracker.trackPlugin(objId, objPropsPluginName)

        plugin_utils.updateValue(self.renderer, nodePluginName, "object_properties", objPropsAttrPlugin)


    def _exportObjNodeTree(self, obj: bpy.types.Object, geomPlugin, isVisible = True):
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
                nodePlugin = self._exportNodePlugin(obj, geomPlugin, Names.object(obj), visible=isVisible)
                self._exportObjProperties(obj, nodeCtx, nodeOutput, nodePlugin.name)
        
        return nodePlugin


    def _exportMeshObject(self, obj: bpy.types.Object, exportGeometry: bool, isInstanced: bool, isVisible: bool):
        obj = obj.evaluated_get(self.dg)
        assert obj.is_evaluated, f"Evaluated object expected: {obj.name}"
        
        # Export main object
        exported = (not exportGeometry) or self.ts.timeThis("export_mesh", lambda: self._exportMesh(obj, isInstanced))
        
        # Non-mesh modifiers show geometry, but don't need a Node plugin to be exported for them.
        # The geometry associated with them may also not need exporting, so apply them regardless
        # of the return value of exportMesh()
        self._exportNonMeshModifiers(obj, exportGeometry, isVisible)
        
        if not exported:
            return

        objTrackId = getObjTrackId(obj)
        
        if isVisible:
            ####  Export modifiers  ###
            self._exportParticleSystems(obj, exportGeometry)

        isMeshLightGizmo = objTrackId in [p.gizmoObjTrackId for p in self.activeMeshLightsInfo]
        isEnvFogGizmo = objTrackId in self.activeGizmos
        isGizmo = isMeshLightGizmo or isEnvFogGizmo

        nodePlugin = None

        if (not isGizmo) and (not self._isInstancerVisibilityDisabled(obj)):
            baseGeomPlugin = AttrPlugin(Names.objectData(obj)) # Empty plugin attribute representing the object's geometry
            if obj.vray.ntree and NodesUtils.getOutputNode(obj.vray.ntree, 'OBJECT'):
                nodePlugin = self._exportObjNodeTree(obj, baseGeomPlugin, isVisible)
            else:
                nodePlugin = self._exportNodePlugin(obj, baseGeomPlugin, Names.object(obj), visible=isVisible)

            if nodePlugin is not None:
                plugin_utils.updateValue(self.renderer, nodePlugin.name, "objectID", obj.vray.VRayObjectProperties.objectID)
                self.objTracker.trackPlugin(objTrackId, nodePlugin.name)

        else:
            # 'Node' plugin should not be exported for gizmos
            nodePluginName = Names.vrayNode(Names.object(obj))
            vray.pluginRemove(self.renderer, nodePluginName)
            self.objTracker.forgetPlugin(objTrackId, nodePluginName)


    # Export a MESH object to GeomStaticMesh VRay plugin
    def _exportMesh(self, obj: bpy.types.Object, isInstanced):
        if (mesh := self._meshFromObj(obj)) is None:
            return False

        uniqueName = Names.objectData(obj) if mesh else ""
        if uniqueName in self.exported:
            # The mesh is shared between multiple objects and has already been exported
            return True
        
        # TODO: Check if we could have FLUID modifier along with mesh modifiers    
        if "FLUID" not in [m.type for m in obj.modifiers]:
            meshData = self._fillMeshData(mesh, uniqueName, isInstanced, isEditMode = (obj.mode == 'EDIT'))
            self._applyMeshModifiers(obj, meshData)
            
            vray.pluginCreate(self.renderer, uniqueName, 'GeomStaticMesh')
            vray.exportGeometry(self.renderer, meshData)
            self.objTracker.trackPlugin(getObjTrackId(obj), uniqueName)
            self.exported.add(uniqueName)
            return True
        
        return False


    # Export a MESH object to VRayScene plugin
    def _exportVrayScene(self, obj, isVisible):
        vrayScene = obj.vray.VRayAsset
        pluginName = Names.pluginObject("vrayscene", Names.object(obj))
        pluginDesc = PluginDesc(pluginName, "VRayScene")
        pluginDesc.vrayPropGroup = vrayScene

        pluginDesc.setAttribute("filepath", vrayScene.filePath)
        pluginDesc.setAttribute("transform", obj.matrix_world)
        pluginDesc.setAttribute("mrs_visibility", isVisible)

        export_utils.exportPlugin(self, pluginDesc)
        self.objTracker.trackPlugin(getObjTrackId(obj), obj.name)


    # Export a MESH object to GeomMeshFile plugin
    def _exportVrayProxy(self, obj, exportGeometry, isVisible):
        pluginName = Names.pluginObject("vrayproxy", Names.object(obj))
        if exportGeometry:
            geomMeshFile = obj.data.vray.GeomMeshFile
            pluginDesc = PluginDesc(pluginName, "GeomMeshFile")
            pluginDesc.vrayPropGroup = geomMeshFile

            export_utils.exportPlugin(self, pluginDesc)
            self.objTracker.trackPlugin(getObjTrackId(obj), pluginName)

        nodePlugin = self._exportNodePlugin(obj, AttrPlugin(pluginName), Names.object(obj), isInstancer=False, visible=isVisible)
        self.objTracker.trackPlugin(getObjTrackId(obj), nodePlugin.name)


    def _exportCurves(self, obj: bpy.types.Object, exportGeometry: bool, isVisible):
        obj = obj.evaluated_get(self.dg)
        assert obj.is_evaluated, f"Evaluated object expected: {obj.name}"

        geomPlugin = self.ts.timeThis("collect_hair_curves_data", lambda: self.hairExporter.exportFromCurves(obj, exportGeometry))
        nodePlugin = self._exportNodePlugin(obj, geomPlugin, Names.object(obj), isInstancer=False, visible=isVisible)
        self.objTracker.trackPlugin(getObjTrackId(obj), nodePlugin.name)


    # Export a POINTCLOUD object to a GeomParticleSystem VRay plugin
    def _exportPointCloud(self, obj: bpy.types.Object, exportGeometry: bool, isVisible):
        obj = obj.evaluated_get(self.dg)
        assert obj.is_evaluated, f"Evaluated object expected: {obj.name}"

        pluginName = self._getPointCloudName(obj)

        if (not exportGeometry) or (pluginName in self.exported):
            # Export node as its visibility may have changed
            self._exportNodePlugin(obj, AttrPlugin(pluginName), Names.object(obj), isInstancer=False, visible=isVisible)
            return
        
        pc = obj.data.evaluated_get(self.dg)
        numPoints = len(pc.attributes["position"].data)

        data = PointCloudData(pluginName)
        data.renderType = PointCloudData.TYPE_SPHERES
        data.points = tools.foreachGetAttr(pc.attributes["position"].data, "vector", shape=(numPoints, 3), dtype=np.float32)
        data.uvs    = tools.foreachGetAttr(pc.attributes["UVMap"].data, "vector", shape=(numPoints, 2), dtype=np.float32)
        data.radii  = tools.foreachGetAttr(pc.attributes["radius"].data, "value", shape=(numPoints, 1), dtype=np.float32)
        
        # TODO: Get the color from some real source ( if needed at all )
        redColor = np.array([1.0, 0.0, 0.0])
        data.colors = np.tile(redColor, (numPoints, 1))
        
        self.ts.timeThis("C++ export_point_cloud", lambda : vray.exportPointCloud(self.renderer, data))
        self.exported.add(pluginName)        

        nodePlugin = self._exportNodePlugin(obj, AttrPlugin(pluginName), Names.object(obj))
        self.objTracker.trackPlugin(getObjTrackId(obj), nodePlugin.name)
        

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

         # In contrast to normal scenes, here we get the list of objects from dg.objects instead of dg.scene.objects
         for obj in self.dg.objects:
            # Force geometry export
            updates = {obj.original: True}
            self._exportObject(obj.original, updates, isInstanced=False, isVisible=True)


    def _isGeometryAnimated(self, obj: bpy.types.Object):
        """ Return whether an export of object's geometry is needed for the current animation frame 
            because of changes to the shape of geometry.
        """
        if not self.isAnimation:
            return False
        
        animInfo = self.commonSettings.animation
            
        # Always export animated objects on the first frame of the animation
        # because is serves as a keyframe in V-Ray
        if animInfo.frameCurrent == animInfo.frameStart:
            return True

        if animRange := self.geomAnimationRanges.get(obj.data.session_uid, None):
            return  frameInRange(self.currentFrame, animRange)


    def _exportScene(self):
        """ Export all geometry in the scene """
        geometryUpdates = {u.id.original.session_uid: u.is_updated_geometry for u in self.dg.updates}

        # Geometry objects are exported in two passes - scene objects and instances. 
        # This is necessary because the final render depsgraph does not contain
        # the instanced objects. This is different from the viewport rendering, 
        # where all objects are contained in the depsgraph.
        
        instancedObjects = set([i.object.original for i in self.dg.object_instances if i.is_instance])
        
        def geometryForExport():
            if self.commonSettings.useMotionBlur and self.isAnimation:
                yield from self.motionBlurBuilder.getGeometryForExport(self.dg.scene.objects)
            else:
                yield from geometryObjectIt(self.dg.scene.objects)

        # Export all scene objects
        for obj in geometryForExport():
            # Node plugins' "visible" property for instanced objects should be set to 
            # False if we only want to see the instances and not the original instanced object 
            isInstanced  = obj in instancedObjects
            isVisible    = getObjTrackId(obj.original) in self.visibleObjects

            
            # Explicitly handle animations which change object's geometry (shape). Geometry data export
            # is a performance bottleneck so we need to skip export for frames where there are no changes.
            # The same potentially could be done for the rest of the animated properties but the procedure
            # to collect the info is complicated and still not well-understood. Besides, changes to non-geometry 
            # properties are tracked by ZmqServer and attempts to set the same value to a parameter will be filtered out.
            if self._isGeometryAnimated(obj):
                geometryUpdates[obj.session_uid] = True
            
            with self.objectContext.push(obj):
                self._exportObject(obj.original, geometryUpdates, isInstanced, isVisible)

        # Export instance data (no geometry, just instances' atributes)
        self.ts.timeThis("Export instance data", lambda: self._exportInstances(geometryUpdates))

        self._hideInvisibleObjects()
        self._syncGizmos()


    def _exportObject(self, 
                      obj: bpy.types.Object, 
                      geometryUpdates: dict[int, bool], 
                      isInstanced: bool,
                      isVisible: bool):
        
        assert isinstance(obj, bpy.types.Object), "Only Blender 'Object' type accepted"
        

        objTrackId = getObjTrackId(obj)

        
        if (not self.fullExport) \
                and (objTrackId not in geometryUpdates) \
                and (objTrackId not in self.objectsWithUpdatedVisibility) \
                and (objTrackId not in self.updatedMeshLightGizmos) \
                and (UpdateFlags.NONE == UpdateTracker.getObjUpdate(UpdateTarget.OBJECT_MTL_OPTIONS, obj))\
                and (objTrackId not in self.addedGizmos) \
                and (objTrackId not in self.removedGizmos):
            return

        isFirstMotionBlureFrame = (self.commonSettings.useMotionBlur and self.motionBlurBuilder.isFirstFrame(self.currentFrame))
        
        # If exportGeometry is False, no geometry data will be exported. The other properties of the objects will
        # still be exported. 
        exportGeometry = (self.interactive and self.fullExport) \
                            or self.preview \
                            or (objTrackId in geometryUpdates) \
                            or isFirstMotionBlureFrame
        
        if not obj.evaluated_get(self.dg).is_evaluated:
            # Objects that are in the depsgraph but are not evaluated are not visible in the scene.
            # This is true e.g. for certain objects that are marked as 'Disabled in renders'.
            return

        match obj.type:
            case 'MESH'| 'META' | 'SURFACE' | 'FONT' | 'CURVE':
                if tools.isObjectVrayScene(obj):
                    self._exportVrayScene(obj, isVisible)
                elif tools.isObjectVrayProxy(obj):
                    self._exportVrayProxy(obj, exportGeometry, isVisible)
                elif not tools.isObjectNonMeshClipper(obj):
                    self._exportMeshObject(obj, exportGeometry, isInstanced, isVisible)
                else:
                    # This is a clipper object that should not be drawn
                    self.hiddenObjects.append(obj)

                # In addition to the exported mesh, export clipper settings for it
                self._exportClipper(obj)
                
            case 'POINTCLOUD':
                self._exportPointCloud(obj, exportGeometry, isVisible)
            case "VOLUME":
                SmokeExporter(self).exportVolume(obj, exportGeometry, isVisible)
            case 'CURVES':
                self._exportCurves(obj, exportGeometry, isVisible)
            case _:
                # print(f"Export of {evaluatedObj.type} not implemented")
                pass
        
    
    def _exportInstances(self, geometryUpdates: dict[int, bool]):
        instancerChanges = {}

        def hasInstanceChanged(obj, instancer):
            """ Returns True if the instance has to be exported because either the instanced object or the 
                instancer have changed.
            """
            if (result := instancerChanges.get((instancer, obj), None)) is not None:
                return result
            
            objTrackId = getObjTrackId(obj)
            instancerTrackId = getObjTrackId(instancer)
            
            # Blender does not create instanced objects when the scene is rendered for the first time if
            # the instancer is invisible. This is why we need to check if the instancer has become visible
            # since the last export and process the instances if this is so.
            changed = self.objectsWithUpdatedVisibility.get(instancerTrackId, False) or \
                (objTrackId in geometryUpdates) or (instancerTrackId in geometryUpdates)
            instancerChanges[(obj, instancer)] = changed
            return changed
                
        instancerExporter = InstancerExporter(self)

        # NOTE: If the instanced object is invisible, the interactive depsgraph will include have the instances
        # but the prod depsgraph will not. This leads to differences between the interactive and production renders.
        for inst in self.dg.object_instances:
            if inst.is_instance and (self.fullExport or hasInstanceChanged(inst.instance_object, inst.parent)):
                self._collectInstanceData(instancerExporter, inst, geometryUpdates)

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


    def _exportNodePlugin(self, 
                     obj: bpy.types.Object, 
                     geomPlugin: AttrPlugin,
                     objectName: str,
                     isInstancer = False,
                     visible = True):
        nodePlugin = exportNodePlugin(self, obj, geomPlugin, objectName, isInstancer, visible)

        return nodePlugin


    def _collectInstanceData(self, instancerExporter: InstancerExporter, 
                                    inst: bpy.types.DepsgraphObjectInstance, 
                                    updates: dict[int, bool]):
        instancerObj = inst.parent
        
        if getObjTrackId(instancerObj.original) not in self.activeInstancers:
            # Instancer's visibility has been switched off in the UI, do not export objects instanced from it
            return
        
        instancerExporter.addInstance(inst)
    

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
        objectIds = [getObjTrackId(o) for o in self.sceneObjects if o.type not in ('LIGHT')]

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


    # Sync object visibility in the viewport
    # This method will use the 'visible' property of plugins of type 'Node'
    # in order to switch on and off parts of the scene 
    def syncObjVisibility(self):
        assert(self.interactive)

        from vray_blender.lib.blender_utils import getFirstAvailableView3D

        def isVisibleInLocalView(obj, dg, view3d):
            if obj.type not in GEOMETRY_OBJECT_TYPES:
                return True
            
            evalObj = obj.evaluated_get(dg)
            return evalObj.local_view_get(view3d)
        
        view3d = getFirstAvailableView3D()
        localView = view3d if (view3d and view3d.local_view) else None

        showObjects = {}
        for o in [obj for obj in self.sceneObjects if obj.type in tools.EXPORTED_OBJECT_TYPES]:
            objTrackId = getObjTrackId(o)
            wasShown   = objTrackId in self.visibleObjects
            isShown    = o.visible_get() and ((not localView) or isVisibleInLocalView(o, self.dg, view3d))
            
            if (wasShown != isShown) or (self.fullExport):
                self.objectsWithUpdatedVisibility[objTrackId] = isShown

                showObjects[objTrackId] = isShown
                for pluginName in self.objTracker.getPlugins(objTrackId):
                    # TODO: Checking for names is error-prone, but we might not know the types of 
                    # all plugins if they were exported in C++. Make C++ return the types
                    # of the exported plugins and track plugin type as well, so that here
                    # we could search by plugin type  
                    if pluginName.startswith("node@"):
                        plugin_utils.updateValue(self.renderer, pluginName, "visible", isShown)
                        trackerLog(f"{'SHOW' if isShown else 'HIDE'} : {objTrackId} => {pluginName}")
                    elif pluginName.endswith("@PhxShaderSim") or o.type == 'LIGHT':
                        plugin_utils.updateValue(self.renderer, pluginName, "enabled", isShown)
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
            if selectorSocket := getLinkedFromSocket(gizmoSock):
                activeGizmos.update({getObjTrackId(o) for o in getObjectsFromSelector(selectorSocket.node, self.ctx)})
            else:
                activeGizmos.update({getObjTrackId(o) for o in node.EnvironmentFog.gizmo_selector.getSelectedItems(self.ctx, 'objects')})

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


    def _getPointCloudName(self, obj):
        return Names.object(obj) if obj.is_modified(self.ctx.scene, tools.evalMode(self.interactive)) \
                                        else Names.objectData(obj)


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

