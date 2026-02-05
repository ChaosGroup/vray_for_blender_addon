import bpy
import os

from mathutils import Vector
import numpy as np

from vray_blender.ui.classes import pollEngine
from vray_blender import debug
from vray_blender.exporting.tools import isObjectVrayProxy
from vray_blender.operators import VRAY_OT_message_box_base

import vray_blender.vray_tools.vray_proxy as proxy

_SUCCESS = 0
_ERROR_FILE_MISSING = 1
_ERROR_IMPORT_FAILED = 2

def computeBasisMatrix(vertices: np.ndarray, boundingBox: np.ndarray, isFlatObject: bool):
    """
    Constructs a basis matrix from 4 non-coplanar vertices in the given [N,3] array. 
    
    Parameters:
        vertices: [N,3] array   
        boundingBox: (float,float,float)[8] the bounding box of the object
        isFlatObject: (bool) allow for vertices that are closer to each other
    Returns:
        Either:
        - The basis matrix of the coordinate system defined by the 4 points
        - The indices of the points in the vertices list
        - A list of any added vertices
        OR 
        - None
        - Error message
    """
    
    # Try to find vertices that are at significant distance from each other comapred to the 
    # object dimensions so that the error accumulated during the floating computations 
    # was not visually noticeable. The factor is empirically determined and may be 
    # adjusted in the future.
    avgDimension = np.mean(np.ptp(boundingBox, axis=0))
    minPtDistance = 0.0001 if isFlatObject else max(avgDimension / 100, 0.0001) 
    
    totalVerts = len(vertices)
    addedVertices = []
    
    if totalVerts < 3:
        raise Exception("Error: Mesh has fewer than 3 vertices.")
    

    def stridedWalk(total, step):
        # Yield indices from 0 to total-1 by walking with stride 'step'.
        for offset in range(step):
            if offset > step:
                # Step is larger than total
                break    
            yield from range(offset, total, step)

    # We aare searching for vertices that are sufficiently far apart. Vertices with adjacent indices
    # are likely to be close to each other. Walk the list with some stride in an attempt to improve 
    # searching times.
    indexGen = stridedWalk(totalVerts, 100)

    # First vertex
    v0 = Vector(vertices[0])
    vi = [0, -1, -1, -1]
    v1 = None

    # Second vertex
    while (i := next(indexGen, -1)) != -1:
        curr = Vector(vertices[i])
        if (curr - v0).length > minPtDistance: 
            v1 = curr
            vi[1] = i
            break
            
    if v1 is None:
        raise Exception("Error: All vertices are at the same location.")

    # Third vertex (Non-collinear)
    v2 = None
    vec01 = (v1 - v0)
    
    while (i := next(indexGen, -1)) != -1:
        curr = Vector(vertices[i])
        vec0Curr = (curr - v0)
        distToLine = vec0Curr.cross(vec01).length / vec01.length 

        if distToLine > minPtDistance:
            v2 = curr
            vi[2] = i
            break
            
    if v2 is None:
        # Couldn't find a point that is sufficiently far, invent one
        linePerpendicular = vec0Curr.cross(vec01).normalize()
        v2 = linePerpendicular * minPtDistance
        addedVertices.append(v2)

    # Fourth vertex (Non-coplanar)
    v3 = None
    planeNormal = (v1 - v0).cross(v2 - v0).normalized()
    
    while (i := next(indexGen, -1)) != -1:
        curr = Vector(vertices[i])
        distToPlane = abs(planeNormal.dot(curr - v0))
        
        if distToPlane > minPtDistance:
            v3 = curr
            vi[3] = i
            break

    if v3 is None:
        # Mesh is flat-ish. Create a new point that will be at a sufficient distance
        # from the plane defined by the other three
        v3 = planeNormal * minPtDistance
        addedVertices.append(tuple(v3))

    # Append any newly created vertices to the list
    if len(addedVertices) == 1:
        vi[3] = totalVerts 
    elif len(addedVertices) == 2:
        vi[2] = totalVerts
        vi[3] = totalVerts + 1

    matrix = proxy._basisMatrixFromVectors(v0, v1, v2, v3)
    
    return matrix, vi, addedVertices


def arrayFromMeshVertices(mesh: bpy.types.Mesh):
    count = len(mesh.vertices)

    # 1. Create a flat buffer (3 floats per vertex)
    coords = np.empty(count * 3, dtype=np.float32)

    # 2. Fast copy from C to Python
    mesh.vertices.foreach_get("co", coords)
    return coords.reshape(count, 3)


def upgradeProxy(obj: bpy.types.Object):
    from mathutils import Matrix
    import vray_blender.vray_tools.vray_proxy as proxy
    from vray_blender.exporting.tools import mat4x4ToTuple

    geomMeshFile = obj.data.vray.GeomMeshFile
    absFilePath = bpy.path.abspath(geomMeshFile.file) 
    
    if not os.path.exists(absFilePath):
        debug.reportError(f"Failed to upgrade V-Ray Proxy {obj.name}. Mesh file '{absFilePath}' is missing.")
        return _ERROR_FILE_MISSING
    
    # Regardless of whether this is a point preview, we need to compute the anchor transform 
    # from an actual mesh
    originalPreviewType = geomMeshFile.previewType
    geomMeshFile['previewType'] = 'Preview'
    meshData, boxVertices, err = proxy._constructPreview(geomMeshFile, absFilePath, isProxy=True)
    if err:
        return _ERROR_IMPORT_FAILED

    # For some old scenes computing the transform of the object from the saved GeomMeshFile properties does not place the object
    # in the exactly same position after the upgrade. Compute the basis matrix from actual points of the mesh. 
    basisMatrix, pointIndices, addedVertices = computeBasisMatrix(meshData['vertices'], boxVertices, isFlatObject=False)

    if len(addedVertices) > 0:
        # The object is too flat, try again with greater tolerance for flatness
        basisMatrix, pointIndices, addedVertices = computeBasisMatrix(meshData['vertices'], boxVertices, isFlatObject=True)
    
    geomMeshFileScale = Matrix.Scale(geomMeshFile.scale, 4)
    geomMeshFile['basis_matrix'] = mat4x4ToTuple(geomMeshFileScale @ basisMatrix)
    geomMeshFile['basis_vertex_indices'] = pointIndices
    geomMeshFile['previewType'] = originalPreviewType

    if len(addedVertices) > 0:
        # If any anchor vertices were added, set them to the mesh
        mesh: bpy.types.Mesh = obj.data
        vertices = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
        faces = np.empty(len(mesh.loop_triangles) * 3, dtype=np.int32)

        mesh.vertices.foreach_get('co', vertices)
        mesh.loop_triangles.foreach_get('vertices', faces)
        vertices = vertices.reshape((-1,3))

        allVertices = np.concatenate((vertices, addedVertices), axis=0)
        
        newMeshData = {
            'vertices': allVertices,
            'faces': faces.reshape((-1,3))
        }

        proxy._replaceObjMesh(obj, newMeshData)

    proxy.loadVRayProxyPreviewMesh(obj, geomMeshFile.file)
    return _SUCCESS

def run():
    
    missingFiles = False
    for obj in bpy.data.objects:
        if isObjectVrayProxy(obj):
            try:
                if err := upgradeProxy(obj):
                    if err == _ERROR_FILE_MISSING:
                        missingFiles = True
                        
            except Exception as e:
                debug.reportError(f"Failed to upgrade V-Ray Proxy {obj.name}. Error: {e} ")

    if missingFiles:

        msg = ( "Scene update failed for one or more V-Ray Proxy Objects\n"
                "because their mesh files are missing. Download all missing\n"
                "Comsos assets and then reload the scene to retry the update.")
        
        bpy.utils.register_class(VRAY_OT_prompt_missing_files)
        bpy.ops.vray.prompt_missing_files('INVOKE_DEFAULT', message=msg)


def check():
    return any(isObjectVrayProxy(obj) for obj in bpy.data.objects)


class VRAY_OT_prompt_missing_files(VRAY_OT_message_box_base):
    bl_idname  = "vray.prompt_missing_files"
    bl_label   = "Scene update"
    bl_options = {'INTERNAL'}

    message : bpy.props.StringProperty(
        name = "message",
        default = ""
    )
    
    @classmethod
    def poll(cls, context):
        return pollEngine(context)

    def execute(self, context):
        bpy.app.timers.register(lambda : bpy.utils.unregister_class(VRAY_OT_prompt_missing_files))
        return { 'FINISHED' }

    def invoke(self, context, event):
        self._centerDialog(context, event)
        return context.window_manager.invoke_props_dialog(self, width=350)

    def draw(self, context):
        self.layout.label(text='Missing V-Ray Proxy files', icon='ERROR')
        self.layout.separator()
        box = self.layout.box()
        lines = self.message.split('\n')
        for line in lines:
            box.label(text=line)
