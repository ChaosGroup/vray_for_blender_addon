# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import gpu
import bpy
import random
import struct
import numpy as np

from gpu_extras.batch import batch_for_shader
from mathutils import Vector, Matrix
from vray_blender.lib import blender_utils
from vray_blender.nodes.utils import getNodeByType, getObjectsFromSelector
from vray_blender.exporting.tools import getLinkedFromSocket, MESH_OBJECT_TYPES
from vray_blender.lib.names import Names
from vray_blender.exporting.plugin_tracker import getObjTrackId
from collections import defaultdict


PADDING = 0.0 # Padding to make the data 16-byte aligned for GPUUniformBuf usage

# Max number of hairs to draw. Limited by the UBO size.
# If for some reason in the future we need to draw more hairs,
# consider storing their root positions and normals in python lists and passing them as vertex input attributes instead of UBOs.
# example:
    # use:
    #   shaderInfo.vertex_in(1, 'VEC3', "rootPos")
    #   shaderInfo.vertex_in(2, 'VEC3', "rootNormal")
    # instead of:
    #   shaderInfo.vertex_in(1, 'INT', "posIndex")
MAX_HAIRS = 1024

HAIR_SEGMENTS = 10

# If the number of triangles is greater than this value, use numpy random sampling instead of roulette wheel selection
MAX_TRIANGLE_SAMPLE = 100000

hairSegmentIndexes = [segmentIdx + i for posIdx in range(MAX_HAIRS) for segmentIdx in range(HAIR_SEGMENTS-1) for i in (0, 1)]
hairPosIndexes = [posIdx for posIdx in range(MAX_HAIRS) for segmentIdx in range(HAIR_SEGMENTS-1) for _ in (0, 1)]

hairRootsBuffers = {}
meshDataByObjId = defaultdict(set) # Matches meshdata name to the object id of its user or instance parent.
furDataForDrawing: set[tuple[str, str, Matrix]] = set() # Data needed for drawing fur preview.
_furDataValid = False
_furTargets = None # (fur name, target names) for the fur objects with a visible preview. None means rescan.
_furTargetUids = set()
_furDeformingUids = set()
_furHasInstancers = False
_furFrame = None

class HairRootsBuff:
    """ Class that stores in buffers the hair roots of fur """

    def _getRandomPointOnFace(self, face, mesh, rng):
        """ Get a random point on a face of a mesh """

        if len(face.vertices) < 3:
            return None

        # Get the first three vertices
        i0, i1, i2 = face.vertices[0], face.vertices[1], face.vertices[2]
        v0, v1, v2 = mesh.vertices[i0].co, mesh.vertices[i1].co, mesh.vertices[i2].co

        # Generate two random numbers
        s = rng.random()
        t = rng.random()
        if s + t > 1.0:
            s = 1.0 - s
            t = 1.0 - t

        # Calculate the point using two edges of the triangle
        edge1 = v1 - v0
        edge2 = v2 - v0

        return v0 + s * edge1 + t * edge2

    def _getRandomTriIndices(self, mesh: bpy.types.Mesh, npRng):
        """ Get MAX_HAIRS random triangle indices from a mesh
            TODO: This function can be rewritten in c++;
        """
        triangles = mesh.loop_triangles
        trianglesCount = len(triangles)

        # If the number of triangles is greater than the threshold, use numpy random sampling
        # Using weighted selection is too slow and unnecessary for large meshes.
        if trianglesCount > MAX_TRIANGLE_SAMPLE:
            return npRng.integers(0, trianglesCount, size=MAX_HAIRS)

        # Get triangle vertex indices fast
        triVertIdx = np.empty((trianglesCount, 3), dtype=np.int32)
        triangles.foreach_get("vertices", triVertIdx.ravel())

        # Get vertex coords fast
        verts = np.empty((len(mesh.vertices), 3), dtype=np.float32)
        mesh.vertices.foreach_get("co", verts.ravel())

        v0 = verts[triVertIdx[:, 0]]
        v1 = verts[triVertIdx[:, 1]]
        v2 = verts[triVertIdx[:, 2]]

        # Its actually faster to calculate the area of the triangles using numpy
        # than using the area property of the triangles (areas =[tri.area for tri in mesh.loop_triangles] )
        areas = 0.5 * np.linalg.norm(np.cross(v1 - v0, v2 - v0), axis=1)

        # Roulette wheel selection with the areas as weights.
        cumulativeWeights = np.cumsum(areas)
        totalArea = cumulativeWeights[-1]
        r = npRng.random(MAX_HAIRS) * totalArea

        return np.searchsorted(cumulativeWeights, r)

    def _createBuffer(self, data):
        return gpu.types.GPUUniformBuf(data=struct.pack(f"{len(data)}f", *data))


    def __init__(self, mesh: bpy.types.Mesh):
        # Use a fixed arbitrarily chosen seed for more deterministic fur previews
        seed = 195
        rng = random.Random(seed)
        npRng = np.random.default_rng(seed)

        # Distributes points on a mesh surface.
        faces = [mesh.loop_triangles[i] for i in self._getRandomTriIndices(mesh, npRng)]
        points = [PADDING] * MAX_HAIRS * 4
        normals = [PADDING] * MAX_HAIRS * 4
        for i, f in enumerate(faces):
            p = self._getRandomPointOnFace(f, mesh, rng)

            i = i * 4
            points[i] = p.x
            points[i+1] = p.y
            points[i+2] = p.z

            normals[i] = f.normal.x
            normals[i+1] = f.normal.y
            normals[i+2] = f.normal.z

        self.posBuf = self._createBuffer(points) # Positions of the hair roots
        self.normalsBuf = self._createBuffer(normals) # Normals of the hair roots


class PreviewShader:
    """ Class that represents the shader used to draw the fur preview """
    _shader = None

    @staticmethod
    def _init():
        """ Initializes the '_shader' variable """

        shaderInfo = gpu.types.GPUShaderCreateInfo()
        shaderInfo.push_constant('MAT4', "transformMatrix")
        shaderInfo.push_constant('MAT4', "perspectiveMatrix")

        shaderInfo.vertex_in(0, 'INT', "hairSegmentIndex")
        shaderInfo.vertex_in(1, 'INT', "posIndex")
        shaderInfo.fragment_out(0, 'VEC4', "outColor")

        shaderInfo.typedef_source(
            f"""
            // Parameter that controls the fur appearance.
            // They are not passed to the shader as push constants to
            // avoid "Push constants have a minimum supported size of 128 bytes, however the constants added so far already reach ..." error.
            struct FurParams {{
                float bend;
                float step;
                vec3 gravity;
                vec4 lineColor;
            }};

            struct HairRootsPos {{
                vec4 pos[{MAX_HAIRS}];
            }};

            struct HairRootsNormals {{
                vec4 normal[{MAX_HAIRS}];
            }};

            """
        )
        shaderInfo.uniform_buf(0, "FurParams", "furParams")
        shaderInfo.uniform_buf(1, "HairRootsPos", "hairRootsPos")
        shaderInfo.uniform_buf(2, "HairRootsNormals", "hairRootsNormals")

        shaderInfo.vertex_source("""
            void main()
            {
                vec3 position = hairRootsPos.pos[posIndex].xyz;
                vec3 normal = hairRootsNormals.normal[posIndex].xyz;
                vec3 p = (transformMatrix * vec4(position, 1.0)).xyz;        // current position
                vec3 d = transpose(inverse(mat3(transformMatrix))) * normal;   // current direction

                // Simplified version of V-Ray's code for generation of hair segments.
                // Unfortunately, every segment point requires the previous point to be calculated,
                // which leads to redundant calculations.
                for (int i = 0; i < hairSegmentIndex; i++)
                {
                    vec3 nd = normalize(d + furParams.gravity * furParams.bend);

                    p += nd * furParams.step;
                    d = nd;
                }

                gl_Position = perspectiveMatrix * vec4(p, 1.0);
            }
        """)

        shaderInfo.fragment_source(
            "void main()"
            "{"
                "outColor = furParams.lineColor;"
            "}"
        )

        PreviewShader._shader = gpu.shader.create_from_info(shaderInfo)

    @staticmethod
    def get():
        if PreviewShader._shader is None:
            PreviewShader._init()
        return PreviewShader._shader




def _genFurParamsBuffer(furObj: bpy.types.Object):
    """ Generates buffer with the necessary parameters of the fur object """
    geomHair = _getGeomHairPropGroup(furObj)

    gravity = (Vector(geomHair.gravity_vector) * geomHair.gravity_base) / HAIR_SEGMENTS
    bend = geomHair.bend
    step = geomHair.length_base/HAIR_SEGMENTS

    uiTheme   = bpy.context.preferences.themes[0]
    color = uiTheme.view_3d.object_active[:] + (1.0,) if furObj == bpy.context.active_object else (0.0, 0.0, 0.0, 1.0)

    data = struct.pack("12f", bend, step, PADDING, PADDING, *gravity, PADDING, *color)

    return gpu.types.GPUUniformBuf(data=data)


def _getFurDrawParams(furObj: bpy.types.Object, previewShader: gpu.types.GPUShader):
    """ Return the params buffer and line batch shared by all preview draws of a fur object. """
    indexesCount = _getGeomHairPropGroup(furObj).preview_hair_count * HAIR_SEGMENTS* 2

    batch = batch_for_shader(previewShader, 'LINES', {
        'hairSegmentIndex': hairSegmentIndexes[:indexesCount], 'posIndex': hairPosIndexes[:indexesCount]
    })
    return _genFurParamsBuffer(furObj), batch


def _drawFurPreviewBatch(previewShader: gpu.types.GPUShader, matrix: Matrix, hairRoots: HairRootsBuff, furParamsBuf: gpu.types.GPUUniformBuf, batch: gpu.types.GPUBatch):
    previewShader.uniform_float('transformMatrix', matrix)

    previewShader.uniform_block('hairRootsPos', hairRoots.posBuf)
    previewShader.uniform_block('hairRootsNormals', hairRoots.normalsBuf)
    previewShader.uniform_block('furParams', furParamsBuf)

    batch.draw(previewShader)


def _getGeomHairPropGroup(furObj: bpy.types.Object):
    if (ntree := furObj.vray.ntree) and (outputNode := getNodeByType(ntree, "VRayNodeFurOutput")):
        return outputNode.GeomHair

    return furObj.data.vray.GeomHair

def _getObjectsSelectedByFur(ctx: bpy.types.Context, furObj: bpy.types.Object):
    outputNode = None
    if (ntree := furObj.vray.ntree) and (outputNode := getNodeByType(ntree, "VRayNodeFurOutput")):

        if(socketLink := getLinkedFromSocket(outputNode.inputs['Mesh'])):
            if socketLink.node.bl_idname in ('VRayNodeSelectObject', 'VRayNodeMultiSelect'):
                return getObjectsFromSelector(socketLink.node, ctx)
            return []

    return _getGeomHairPropGroup(furObj).object_selector.getSelectedItems(ctx, 'objects')


def drawFurPreview():
    """ Draws viewport preview hairs for fur objects """
    global _furDataValid

    space3d = bpy.context.space_data

    # Ensure that this function is called only in a 3D viewport context.
    assert isinstance(space3d, bpy.types.SpaceView3D), "Space is not a 3D view"

    if bpy.context.engine != 'VRAY_RENDER_RT':
        return

    if space3d.shading.type != 'SOLID':
        return

    if not space3d.show_object_viewport_curves:
        return

    # Don't draw fur preview if the user is in edit mode.
    if bpy.context.active_object and bpy.context.active_object.mode != 'OBJECT':
        return

    if not _furDataValid:
        _generateHairRootsBuffers(bpy.context.scene, bpy.context.evaluated_depsgraph_get())
        _furDataValid = True

    prevDepthTest = gpu.state.depth_test_get()
    gpu.state.depth_test_set('LESS_EQUAL')
    gpu.state.line_width_set(1.0)

    previewShader = PreviewShader.get()
    previewShader.bind()
    previewShader.uniform_float('perspectiveMatrix', bpy.context.region_data.perspective_matrix)

    furDrawParams = {}
    for furObjName, objDataName, matrix in furDataForDrawing:
        if not (buffers := hairRootsBuffers.get(objDataName)):
            continue
        if furObjName not in furDrawParams:
            furObj = bpy.data.objects.get(furObjName)
            # Don't draw fur preview for objects that are not visible in local view
            visible = furObj and not (space3d.local_view and (not furObj.local_view_get(space3d)))
            furDrawParams[furObjName] = _getFurDrawParams(furObj, previewShader) if visible else None
        if params := furDrawParams[furObjName]:
            _drawFurPreviewBatch(previewShader, matrix, buffers, *params)

    gpu.state.depth_test_set(prevDepthTest)


def _generateHairRootsBuffers(scene: bpy.types.Scene, depsgraph: bpy.types.Depsgraph):
    """ Generates the hair roots buffers for the scene """
    global _furTargets, _furHasInstancers
    furDataForDrawing.clear()

    if _furTargets is None:
        _furTargets = []
        if bpy.data.hair_curves:
            _furTargets = [(obj.name, [o.name for o in _getObjectsSelectedByFur(bpy.context, obj)]) for obj in scene.objects
                          if obj.type == 'CURVES' and obj.vray.isVRayFur and _getGeomHairPropGroup(obj).show_viewport_preview and obj.visible_get()]
        _furTargetUids.clear()
        _furTargetUids.update(o.session_uid for _, names in _furTargets for n in names if (o := bpy.data.objects.get(n)))

    furByTarget = defaultdict(list)
    for furName, targetNames in _furTargets:
        for targetName in targetNames:
            furByTarget[targetName].append(furName)

    _furDeformingUids.clear()
    instancers = set()
    for targetName, furNames in furByTarget.items():
        if (target := bpy.data.objects.get(targetName)) is None:
            continue
        # Blender exposes the evaluated mesh of these objects as a self instance,
        # while evaluated_get() still returns their original non-mesh data.
        if (target.is_instancer
                or (target.type in MESH_OBJECT_TYPES and target.type != 'MESH')
                or any(m.type == 'NODES' for m in target.modifiers)):
            instancers.add(targetName)
        if target.modifiers or getattr(target.data, 'shape_keys', None) or (target.data and target.data.animation_data):
            _furDeformingUids.add(target.session_uid)
        if target.visible_get():
            _addFurDrawData(furNames, target.evaluated_get(depsgraph), None)

    _furHasInstancers = bool(instancers)
    if instancers:
        for inst in depsgraph.object_instances:
            if inst.is_instance and (parentName := inst.parent.name) in instancers:
                _addFurDrawData(furByTarget[parentName], inst.object, inst)

    usedData = {objDataName for _, objDataName, _ in furDataForDrawing}
    for objTrackId in [k for k, names in meshDataByObjId.items() if not (names & usedData)]:
        for objDataName in meshDataByObjId.pop(objTrackId):
            hairRootsBuffers.pop(objDataName, None)


def _addFurDrawData(furNames: list[str], obj: bpy.types.Object, inst: bpy.types.DepsgraphObjectInstance | None):
    if not isinstance(obj.data, bpy.types.Mesh):
        return

    objDataName = Names.objectData(obj, inst)
    if objDataName not in hairRootsBuffers:
        mesh = obj.data
        if len(mesh.loop_triangles) == 0:
            hairRootsBuffers[objDataName] = None # To indicate that the object has no valid geometry
        else:
            meshDataByObjId[getObjTrackId(obj)].add(objDataName)
            if inst:
                meshDataByObjId[getObjTrackId(inst.parent)].add(objDataName)
            hairRootsBuffers[objDataName] = HairRootsBuff(mesh)

    matrixCopy = (inst.matrix_world if inst else obj.matrix_world).copy()
    matrixCopy.freeze()
    for furName in furNames:
        furDataForDrawing.add((furName, objDataName, matrixCopy))


def _syncFurCaches(scene: bpy.types.Scene, depsgraph: bpy.types.Depsgraph, structural: bool):
    """ Drop the fur draw data and hair roots invalidated by the update. """
    global _furDataValid, _furTargets, _furFrame

    frameChanged = scene.frame_current != _furFrame
    _furFrame = scene.frame_current

    if scene.render.engine != 'VRAY_RENDER_RT':
        resetHairRootsBuffers(None)
        return

    tracked = bool(_furTargetUids or meshDataByObjId)
    if tracked and not (structural or _furHasInstancers):
        for objTrackId in _furDeformingUids & meshDataByObjId.keys():
            for objDataName in meshDataByObjId.pop(objTrackId):
                hairRootsBuffers.pop(objDataName, None)
        _furDataValid = False
        return

    structural = structural and bool(bpy.data.hair_curves)
    if not (tracked or structural):
        return

    for u in depsgraph.updates:
        idBlock = u.id
        if isinstance(idBlock, bpy.types.Object):
            if tracked:
                objTrackId = getObjTrackId(idBlock)
                if (objTrackId in meshDataByObjId) and u.is_updated_geometry:
                    for objDataName in meshDataByObjId.pop(objTrackId):
                        hairRootsBuffers.pop(objDataName, None)
                    _furDataValid = False
                if objTrackId in _furTargetUids:
                    _furDataValid = False
                    continue
            if structural and (idBlock.type == 'CURVES') and idBlock.vray.isVRayFur:
                _furTargets = None
        elif structural and (isinstance(idBlock, (bpy.types.Collection, bpy.types.Curves)) or
                             (isinstance(idBlock, bpy.types.Scene) and not frameChanged) or
                             (isinstance(idBlock, bpy.types.NodeTree) and idBlock.bl_idname == 'VRayNodeTreeFur')):
            _furTargets = None

    if _furTargets is None:
        _furDataValid = False


@bpy.app.handlers.persistent
def cleanObsoleteHairRoots(scene, depsgraph):
    """ Clear the hair roots buffers for the objects that have been updated. """
    _syncFurCaches(scene, depsgraph, structural=False)


@bpy.app.handlers.persistent
def _onDepsgraphUpdatePost(scene, depsgraph):
    _syncFurCaches(scene, depsgraph, structural=True)


@bpy.app.handlers.persistent
def resetHairRootsBuffers(e):
    global _furDataValid, _furTargets
    hairRootsBuffers.clear()
    meshDataByObjId.clear()
    furDataForDrawing.clear()
    _furTargets = None
    _furTargetUids.clear()
    _furDeformingUids.clear()
    _furDataValid = False


_drawHandler = None

def register():
    global _drawHandler

    _drawHandler = bpy.types.SpaceView3D.draw_handler_add(drawFurPreview, (), 'WINDOW', 'POST_PIXEL')

    blender_utils.addEvent(bpy.app.handlers.depsgraph_update_post, _onDepsgraphUpdatePost)
    blender_utils.addEvent(bpy.app.handlers.frame_change_post, cleanObsoleteHairRoots)
    blender_utils.addEvent(bpy.app.handlers.load_pre, resetHairRootsBuffers)
    blender_utils.addEvent(bpy.app.handlers.undo_post, resetHairRootsBuffers)
    blender_utils.addEvent(bpy.app.handlers.redo_post, resetHairRootsBuffers)


def unregister():
    global _drawHandler

    if _drawHandler:
        bpy.types.SpaceView3D.draw_handler_remove(_drawHandler, 'WINDOW')
        _drawHandler = None

    blender_utils.delEvent(bpy.app.handlers.depsgraph_update_post, _onDepsgraphUpdatePost)
    blender_utils.delEvent(bpy.app.handlers.frame_change_post, cleanObsoleteHairRoots)
    blender_utils.delEvent(bpy.app.handlers.load_pre, resetHairRootsBuffers)
    blender_utils.delEvent(bpy.app.handlers.undo_post, resetHairRootsBuffers)
    blender_utils.delEvent(bpy.app.handlers.redo_post, resetHairRootsBuffers)
