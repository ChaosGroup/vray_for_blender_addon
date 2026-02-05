import gpu
import bpy
import random
import struct
import numpy as np

from gpu_extras.batch import batch_for_shader
from mathutils import Vector, Matrix
from vray_blender.lib import blender_utils
from vray_blender.nodes.utils import getNodeByType, getObjectsFromSelector
from vray_blender.exporting.tools import getLinkedFromSocket
from vray_blender.lib.names import Names
from vray_blender.exporting.plugin_tracker import getObjTrackId


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

class HairRootsBuff:
    """ Class that stores in buffers the hair roots of fur """

    def _getRandomPointOnFace(self, face, mesh):
        """ Get a random point on a face of a mesh """

        if len(face.vertices) < 3:
            return None

        # Get the first three vertices
        i0, i1, i2 = face.vertices[0], face.vertices[1], face.vertices[2]
        v0, v1, v2 = mesh.vertices[i0].co, mesh.vertices[i1].co, mesh.vertices[i2].co

        # Generate two random numbers
        s = random.random()
        t = random.random()
        if s + t > 1.0:
            s = 1.0 - s
            t = 1.0 - t
            
        # Calculate the point using two edges of the triangle
        edge1 = v1 - v0
        edge2 = v2 - v0
        
        return v0 + s * edge1 + t * edge2

    def _getRandomTriIndices(self, mesh: bpy.types.Mesh):
        """ Get MAX_HAIRS random triangle indices from a mesh
            TODO: This function can be rewritten in c++;
        """
        triangles = mesh.loop_triangles
        trianglesCount = len(triangles)

        # If the number of triangles is greater than the threshold, use numpy random sampling
        # Using weighted selection is too slow and unnecessary for large meshes.
        if trianglesCount > MAX_TRIANGLE_SAMPLE:
            return np.random.randint(0, trianglesCount, size=MAX_HAIRS)

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
        r = np.random.random(MAX_HAIRS) * totalArea

        return np.searchsorted(cumulativeWeights, r)

    def _createBuffer(self, data):
        return gpu.types.GPUUniformBuf(data=struct.pack(f"{len(data)}f", *data))
    

    def __init__(self, mesh: bpy.types.Mesh):

        # Distributes points on a mesh surface.
        faces = [mesh.loop_triangles[i] for i in self._getRandomTriIndices(mesh)]
        points = [PADDING] * MAX_HAIRS * 4
        normals = [PADDING] * MAX_HAIRS * 4
        for i, f in enumerate(faces):
            p = self._getRandomPointOnFace(f, mesh)

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
            
            """
        )
        shaderInfo.uniform_buf(0, "FurParams", "furParams")
        shaderInfo.uniform_buf(1, f"vec3[{MAX_HAIRS}]", "hairRootsPos")
        shaderInfo.uniform_buf(2, f"vec3[{MAX_HAIRS}]", "hairRootsNormals")

        shaderInfo.vertex_source("""
            void main()
            {   
                vec3 position = hairRootsPos[posIndex];
                vec3 normal = hairRootsNormals[posIndex];
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


def _drawFurPreviewBatch(furObj: bpy.types.Object, matrix: Matrix, hairRoots: HairRootsBuff):
    previewShader = PreviewShader.get()
    previewShader.bind()
    previewShader.uniform_float('transformMatrix', matrix)
    previewShader.uniform_float('perspectiveMatrix', bpy.context.region_data.perspective_matrix)

    previewShader.uniform_block('hairRootsPos', hairRoots.posBuf)
    previewShader.uniform_block('hairRootsNormals', hairRoots.normalsBuf)
    
    furParamsBuf = _genFurParamsBuffer(furObj)
    previewShader.uniform_block('furParams', furParamsBuf)

    indexesCount = _getGeomHairPropGroup(furObj).preview_hair_count * HAIR_SEGMENTS* 2

    batch = batch_for_shader(previewShader, 'LINES', {
        'hairSegmentIndex': hairSegmentIndexes[:indexesCount], 'posIndex': hairPosIndexes[:indexesCount]
    })
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

    space3d = bpy.context.space_data

    # Ensure that this function is called only in a 3D viewport context.
    assert isinstance(space3d, bpy.types.SpaceView3D), "Space is not a 3D view"

    if space3d.shading.type != 'SOLID':
        return

    if bpy.context.active_object and bpy.context.active_object.mode != 'OBJECT':
        return

    gpu.state.depth_test_set('LESS_EQUAL')
    gpu.state.line_width_set(1.0)

    objectsWithFur  = {}
    for obj in bpy.context.scene.objects:
        if obj.vray.isVRayFur and \
            _getGeomHairPropGroup(obj).show_viewport_preview and \
            ((not space3d.local_view) or obj.local_view_get(space3d)) and \
            obj.visible_get():

            objectsWithFur[obj] = [getObjTrackId(o) for o in _getObjectsSelectedByFur(bpy.context, obj)]          

    iteratedInstGeom = {}
    depsgraph = bpy.context.evaluated_depsgraph_get()
    for inst in depsgraph.object_instances:
        if inst.object.type in blender_utils.NonGeometryTypes:
            continue

        obj = inst.object
        dataID = id(obj.data)
        if dataID not in iteratedInstGeom:
            iteratedInstGeom[dataID] = Names.objectData(obj, inst if inst.is_instance else None)
        objDataName = iteratedInstGeom[dataID]

        if space3d.local_view and (not obj.local_view_get(space3d)):
            # Don't draw fur preview for objects that are not visible in local view
            continue

        for furObj, selectedObjects in objectsWithFur.items():
            if ((not inst.is_instance) and (getObjTrackId(obj) in selectedObjects)) or \
                (inst.is_instance and (getObjTrackId(inst.parent) in selectedObjects)):

                if objDataName not in hairRootsBuffers:
                    mesh = obj.to_mesh(depsgraph=depsgraph)
                    if len(mesh.loop_triangles) == 0:
                        hairRootsBuffers[objDataName] = None # To indicate that the object has no valid geometry
                    else:
                        hairRootsBuffers[objDataName] = HairRootsBuff(mesh)

                if buffers := hairRootsBuffers[objDataName]:
                    _drawFurPreviewBatch(furObj, inst.matrix_world, buffers)


@bpy.app.handlers.persistent
def cleanObsoleteHairRoots(scene, depsgraph):
    """ Remove roots buffers of objects with changed geometry """

    updatedGeom = {
        getObjTrackId(u.id)
        for u in depsgraph.updates
            if isinstance(u.id, bpy.types.Object)
                and u.id.type not in blender_utils.NonGeometryTypes
                and u.is_updated_geometry
    }

    for inst in depsgraph.object_instances:
        if inst.object.type in blender_utils.NonGeometryTypes:
            continue

        obj = inst.object
        parentObj = inst.parent if inst.is_instance else obj

        if (getObjTrackId(parentObj) in updatedGeom):
            objDataName = Names.objectData(obj, inst if inst.is_instance else None)
            hairRootsBuffers.pop(objDataName, None)


@bpy.app.handlers.persistent
def resetHairRootsBuffers(e):
    hairRootsBuffers.clear()

_drawHandler = None

def register():
    global _drawHandler

    _drawHandler = bpy.types.SpaceView3D.draw_handler_add(drawFurPreview, (), 'WINDOW', 'POST_PIXEL')

    blender_utils.addEvent(bpy.app.handlers.depsgraph_update_post, cleanObsoleteHairRoots)
    blender_utils.addEvent(bpy.app.handlers.load_pre, resetHairRootsBuffers)


def unregister():
    global _drawHandler

    if _drawHandler:
        bpy.types.SpaceView3D.draw_handler_remove(_drawHandler, 'WINDOW')
        _drawHandler = None

    blender_utils.delEvent(bpy.app.handlers.depsgraph_update_post, cleanObsoleteHairRoots)
    blender_utils.delEvent(bpy.app.handlers.load_pre, resetHairRootsBuffers)
