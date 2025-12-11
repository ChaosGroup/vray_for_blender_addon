
import struct
from pathlib import PurePath
import os
from io import BufferedReader

import bpy
import numpy as np
from mathutils import Vector, Matrix

from vray_blender import debug
from vray_blender.exporting.tools import isObjectVrayProxy, matrixLayoutToMatrix, mat4x4ToTuple
from vray_blender.lib import blender_utils, sys_utils, path_utils
from vray_blender.lib.blender_utils import updateShadowAttr
from vray_blender.lib.sys_utils import getAppSdkLibPath
from vray_blender.vray_tools import vray_proxy


# Types of actions that can be performed for generating data for VRayProxy or VRayScene supported formats
# using the vraytools utility.
class PreviewAction:
    MeshPreview    = '1'      # Geometry from a VRayProxy-compatible files
    ScenePreview   = '2'      # Geometry from a VRayScene-compatible files
    ScannedPreset  = '3'      # Scanned material preset info


_PREVIEW_TYPES = {
    'Full':    0,
    'Preview': 1,
    'Boxes' :  2
}


def _dumpMeshFile(vrmeshFile: str, binFile: str, previewType: int, previewFaces: int, flipAxis: int):
    """ Run vraytools utility to dump the requested data from a mesh file (.vrmesh, .abc etc) into a simplified 
        binary format which can that be easily loaded by the Python code.

    Args:
        meshFile    (str): path to a file in format compatible wiht VRayProxy
        binFile     (str): path to the resulting binary file
        previewType (int): one of the _PTEVIEW_TYPES values
        previewFaces(int): upper limit for the number of faces in the preview. Only valid 
                            if isPreview is True.
        flipAxis    (int): one of the GeomMeshFile.flip_axis values
    Returns:
        str | None: Error message on failure, None on success
    """
    from subprocess import PIPE, run

    vrayToolsApp = path_utils.getBinTool(sys_utils.getPlatformName("vraytools"))

    cmd = [vrayToolsApp]
    cmd.extend(['-vrayLib', getAppSdkLibPath()])
    cmd.extend(['-action', PreviewAction.MeshPreview])
    cmd.extend(['-input', vrmeshFile])
    cmd.extend(['-output', binFile])
    cmd.extend(['-previewType', str(previewType)])
    cmd.extend(['-previewFaces', str(previewFaces)])
    cmd.extend(['-flipAxis', str(flipAxis)])

    result = run(cmd, stdout=PIPE, stderr=PIPE, universal_newlines=True)

    debug.printInfo(f"Running mesh preview tool: {' '.join(cmd)}")
    
    if result.returncode != 0:
        debug.printError(result.stdout)
        debug.printError(result.stderr)
        return f"Error generating bin file: {result.returncode}"
    
    if not os.path.isfile(binFile):
       return "Error generating bin file: file is missing"
     
    return None


def _generateScenePreview(sceneFile: str, binFile: str, previewType: int, previewFaces: int):
    """ Run vraytools utility to dump the requested data from a scene file (.vrscene, .usd) into a simplified 
        binary format which can be easily loaded by the Python code.

    Args:
        vrsceneFile (str):  path to the .vrscene file from which to generate the preview
        binFile     (str):  path to the resulting binary file
        previewType (int):  one of the values in _PRWVIEW_TYPES
        previewFaces(int):  upper limit for the number of faces in the preview. Only valid 
                            if isPreview is True.

    Returns:
        str | None: Error message on failure, None on success
    """
    from subprocess import PIPE, run

    vrayToolsApp = path_utils.getBinTool(sys_utils.getPlatformName("vraytools"))

    cmd = [vrayToolsApp]
    cmd.extend(['-vrayLib', getAppSdkLibPath()])
    cmd.extend(['-action', PreviewAction.ScenePreview])
    cmd.extend(['-input', sceneFile])
    cmd.extend(['-output', binFile])
    cmd.extend(['-previewType', str(previewType)])
    cmd.extend(['-previewFaces', str(previewFaces)])

    debug.printInfo(f"Calling: {' '.join(cmd)}")

    result = run(cmd, stdout=PIPE, stderr=PIPE, universal_newlines=True)

    debug.printInfo(f"Running scene preview tool: {' '.join(cmd)}")
    
    if result.returncode != 0:
        debug.printError(result.stdout)
        debug.printError(result.stderr)
        return "Error generating scene preview file!"
    
    return None


def _getDataFromMeshFile(filePath: str, previewType: str, previewFaces: int, flipAxis: int):
    """ Get mesh data from a .vrmesh or .abc file, or a file compatible with VRayScene.
    
    Args:
        meshFile    (str): path to a file in format compatible wiht VRayProxy
        binFile     (str): path to the resulting binary file
        previewType (int): one of the _PTEVIEW_TYPES values
        previewFaces(int): upper limit for the number of faces in the preview. Only valid 
                            if isPreview is True.
        flipAxis    (int): one of the GeomMeshFile.flip_axis values
    Returns:
        tuple(dict[str,list], str) | tuple(None, str):
            tuple of mesh data (vertices, faces) and empty string on success, or None and an error message on failure.

    """
    binMeshFile = str(PurePath(path_utils.getV4BTempDir(), PurePath(filePath).stem).with_suffix('.vrbin'))

    if err := _dumpMeshFile(filePath, binMeshFile, _PREVIEW_TYPES[previewType], previewFaces, flipAxis):
        return None, err
    
    objects = vray_proxy.readBinMeshFile(binMeshFile)

    os.unlink(binMeshFile)

    assert len(objects) == 1
    meshData = list(objects.values())[0]

    assert meshData is not None
    return meshData, ""



def _basisMatrixFromVectors(v0, v1, v2, v3):
    return Matrix((
            (v0.x, v1.x, v2.x, v3.x),
            (v0.y, v1.y, v2.y, v3.y),
            (v0.z, v1.z, v2.z, v3.z),
            (1.0,  1.0,  1.0,  1.0)
        ))


def _computeBasisMatrix(vertices: np.ndarray, bbox: np.ndarray):
    
    """
    Constructs a basis matrix from 4 non-coplanar vertices that are added to the initial array.
    The new vertices are placed at the cemter of the object so that they did not go outside the
    bounding box if the object is not flat. 
    
    Parameters:
        vertices: [N,3]array   
        bbox: [N,3]array [8] the bounding box of the object
    Returns:
        - The basis matrix of the coordinate system defined by the 4 points
        - The indices of the points in the vertices list
        - The vertex list with the new vertices added to the initial list
    """

    # Tetrahedron centered at 0,0,0
    h = abs((Vector(bbox[0]) - Vector(bbox[4])).length)
    w = abs((Vector(bbox[0]) - Vector(bbox[1])).length)
    d = abs((Vector(bbox[0]) - Vector(bbox[3])).length)

    # Compute the minimum dimension size based on the size of the object.
    # For large objects, picking the anchor points too close together will
    # result in imprecise transformations. This also solves the problem with
    # flat objects.
    avgDimension = (w + h + d) / 3
    minResolution = max(avgDimension / 100, 0.0001)

    ah = max(h / 100, minResolution)
    aw = max(w / 100, minResolution)
    ad = max(d / 100, minResolution)
    
    center = (Vector(bbox[0]) + Vector(bbox[6])) / 2

    matScale = Matrix.LocRotScale(None, None, Vector((aw, ad, ah)))
    matTranslate = Matrix.Translation(center)

    anchorVertices = np.array((
        (1, 1, 1),
        (1, -1, -1),
        (-1, 1, -1),
        (-1, -1, 1)
    ))
    
    anchorVertices = _applyTransformToVertexArray(anchorVertices, matScale @ matTranslate)

    matrix = _basisMatrixFromVectors(Vector(anchorVertices[0]), 
                             Vector(anchorVertices[1]), 
                             Vector(anchorVertices[2]), 
                             Vector(anchorVertices[3]))

    anchorIndices = list(range(len(vertices), len(vertices) + 4))
    resultVertices = np.concatenate((vertices, anchorVertices), axis=0)
    return matrix, anchorIndices, resultVertices


def _constructCube(size: float, center=(0, 0, 0)):
    """
    Returns the vertices and triangular faces of a cube centered at the given point.
    
    Parameters:
        size (float): The length of one edge of the cube.
        center (tuple): A tuple (x, y, z) representing the center of the cube.
    
    Returns:
        - vertices: List of 8 (x, y, z) coordinates.
        - faces: List of 12 tuples, where each tuple contains 3 integer indices
            pointing to the 'vertices' list.
    """
    halfSize = size / 2.0
    cx, cy, cz = center
    
    vertices = np.array((
        (cx - halfSize, cy - halfSize, cz - halfSize),
        (cx + halfSize, cy - halfSize, cz - halfSize),
        (cx + halfSize, cy + halfSize, cz - halfSize),
        (cx - halfSize, cy + halfSize, cz - halfSize),
        (cx - halfSize, cy - halfSize, cz + halfSize),
        (cx + halfSize, cy - halfSize, cz + halfSize),
        (cx + halfSize, cy + halfSize, cz + halfSize),
        (cx - halfSize, cy + halfSize, cz + halfSize) 
    ))
    
    faces = np.array((
        (0, 2, 1), (0, 3, 2),
        (4, 5, 6), (4, 6, 7),
        (0, 1, 5), (0, 5, 4),
        (1, 2, 6), (1, 6, 5),
        (2, 3, 7), (2, 7, 6),
        (3, 0, 4), (3, 4, 7)
    ))
    
    return vertices, faces


def _constructPointPreview(geomMeshFile: dict, meshFilePath: str):
    """ Fill mesh data for a point preview """
    geomMeshFile['num_preview_faces'] = 0
    bboxData, err = _getDataFromMeshFile(meshFilePath, 'Boxes', 8, int(geomMeshFile.flip_axis))

    if not bboxData:
        return None, None, err
    
    boxVertices = bboxData['vertices']
    assert len(boxVertices) == 8

    # In Point mode we need to have at least 4 vertices in order to be able to compute
    # the applied transforms on the preview object. We select them from the bounding box points
    # but if the mesh has no faces, the vertices will be shown in the selection outline of the object. 
    # If the mesh has any faces however, Blender will outline them and not outline the 
    # free vertices. Construct a very small cube with proper faces to 'capture' 
    # the selection and look as a point.
    geometryCenter = Vector(np.mean(boxVertices, axis=0))
    smallBoxVertices, smallBoxFaces = _constructCube(0.00001, geometryCenter)
    
    bboxData['vertices'] = np.concatenate((smallBoxVertices, boxVertices), axis=0)
    bboxData['faces'] = smallBoxFaces

    return bboxData, boxVertices, None


def _constructPreview(geomMeshFile: dict, meshFilePath: str):
    """ Fill mesh data for a non-point preview """
    meshData, err = _getDataFromMeshFile(meshFilePath, geomMeshFile.previewType,
                                         geomMeshFile.num_preview_faces, int(geomMeshFile.flip_axis))
    if err:
        return None, None, err
    
    bboxData, err = _getDataFromMeshFile(meshFilePath, 'Boxes',
                                        geomMeshFile.num_preview_faces, int(geomMeshFile.flip_axis))
    if err:
        return None, None, err
    
    assert len(bboxData['vertices']) == 8
    return meshData, bboxData['vertices'], None


def _getAnchorTransform(previewMesh: bpy.types.Mesh):
    """ Return a matrix constructed from the 4 anchor points """
    geomMeshFile = previewMesh.vray.GeomMeshFile
    pointIndices = geomMeshFile.basis_vertex_indices

    p0 = previewMesh.vertices[pointIndices[0]].co
    p1 = previewMesh.vertices[pointIndices[1]].co
    p2 = previewMesh.vertices[pointIndices[2]].co
    p3 = previewMesh.vertices[pointIndices[3]].co

    return _basisMatrixFromVectors(p0, p1, p2, p3)


def _applyTransformToVertexArray(vertices: np.ndarray, transform: Matrix):
    npMat = np.array(transform)

    rotScale    = npMat[:3, :3]
    translation = npMat[:3, 3]
    return vertices @ rotScale.T + translation


def _applyTransformToVertex(vertex: Vector, mat: Matrix):
    return vertex @ mat.to_3x3().transposed() + mat.to_translation()


def _replaceObjMesh(previewObj, meshData):
     # Replace object's mesh
    mesh = bpy.data.meshes.new("VRayProxyPreviewTemporary")
    mesh.from_pydata(meshData['vertices'], [], meshData['faces'])
    mesh.update()
    
    blender_utils.replaceObjectMesh(previewObj, mesh)
    bpy.data.meshes.remove(mesh)


def loadVRayProxyPreviewMesh(previewObj: bpy.types.Object, filePath: str, animFrame = 0):
    """ Load the preview voxel from a .vrmesh file, if any.
    
        Returns: None on success, error message on error.
    """
    assert previewObj is not None, "Proxy object must have been created"
    
    # Work with the GeomMeshFile field of the original object, or the changes to it
    # might be lost.
    geomMeshFile = previewObj.original.data.vray.GeomMeshFile
    
    absFilePath = bpy.path.abspath(filePath)
    if ( fileExt:= PurePath(absFilePath).suffix) not in ('.vrmesh', '.abc'):
        return f"File format {fileExt} is not supported by V-Ray Proxy"
    
    meshData, boxVertices, err = _constructPointPreview(geomMeshFile, absFilePath) \
                                if geomMeshFile.previewType == 'Point' \
                                    else _constructPreview(geomMeshFile, absFilePath)
    
    if err:
        return err
        
    if len(meshData['vertices']) == 0:
        return f"V-Ray Proxy object has no vertices. Loaded from {absFilePath}"
    
    # The new preview object's anchor points may not be the same as the old ones.
    # Bring them to the coordinate system of the original object
    baseMatrix, pointIndices, newVertices = _computeBasisMatrix(meshData['vertices'] , boxVertices)
    meshData['vertices'] = newVertices
    geomMeshFileScale = Matrix.Scale(geomMeshFile.scale, 4)
    meshData['vertices'] = _applyTransformToVertexArray(meshData['vertices'], geomMeshFileScale)
    geometryCenter = Vector(np.mean(boxVertices, axis=0))

    if geomMeshFile.file:
        # The proxy is being reimported because one of its properties has changed (incl. the mesh file).
        # In addition to the scale, we also apply the transform of the previous proxy so that the new
        # object appeared at the same position
        appliedTransform = getProxyPreviewAppliedTransform(previewObj)
        meshData['vertices'] = _applyTransformToVertexArray(meshData['vertices'], appliedTransform)
        _positionProxyLights(previewObj, Vector(geomMeshFile['initial_preview_mesh_pos']))
    else:
        geomMeshFile['initial_preview_mesh_pos'] = geometryCenter

    geomMeshFile['basis_matrix'] = mat4x4ToTuple(geomMeshFileScale @ baseMatrix)
    geomMeshFile['basis_vertex_indices'] = pointIndices

    _replaceObjMesh(previewObj, meshData)
    
    if geomMeshFile.previewType == 'Preview':
        # The preview-generation procedure uses the requested number of preview faces as a guideline only.
        # Write back to the UI the number of actual preview faces
        geomMeshFile['num_preview_faces'] = len(meshData['faces'])

    updateShadowAttr(geomMeshFile, 'file')
    
    
def loadVRayScenePreviewMesh(sceneFilepath, ob: bpy.types.Object):
    """ Load preview from a file format compatible with VRayScene.
    
        Args:
            sceneFilepath   (str)   : path to a file compatible with VRayScene
            ob              (Object): scene object whose mesh will be replaced by the preview mesh

        Returns: 
            None on success, error message on error.
    """
    fileExt = PurePath(sceneFilepath).suffix
    if fileExt != '.vrscene' and not fileExt.startswith(".usd"):
        return f"File format {fileExt} is not supported by V-Ray Scene"
    
    sceneFilepath = bpy.path.abspath(sceneFilepath)
    if not os.path.exists(sceneFilepath):
        return "Scene file doesn't exists!"

    vrayScene = ob.data.vray.VRayScene

    if vrayScene.previewType == 'None':
        # Replace with empty mesh
        mesh = bpy.data.meshes.new("VRayScenePreviewTemporary")
        blender_utils.replaceObjectMesh(ob, mesh)
        bpy.data.meshes.remove(mesh)
        vrayScene['num_preview_faces'] = 0
        return None

    isPreview   = (vrayScene.previewType == 'Preview')
    binMeshFile = str(PurePath(sceneFilepath).with_suffix('.vrbin'))

    if err := _generateScenePreview(sceneFilepath, binMeshFile, _PREVIEW_TYPES[vrayScene.previewType], vrayScene.num_preview_faces):
        return err

    objects = readBinMeshFile(binMeshFile)
    assert len(objects) == 1
    meshData = list(objects.values())[0]

    mesh = bpy.data.meshes.new("VRayScenePreviewTemporary")
    mesh.from_pydata(meshData['vertices'], [], meshData['faces'])
    
    if vrayScene.flip_axis in ('2', '3'):
        _flipYZAxes(mesh, yToZ=(vrayScene.flip_axis == '3'))
    mesh.update()

    if isPreview:
        # Set the actual number of preview faces to be shown to the user
        vrayScene['num_preview_faces'] = len(meshData['faces'])

    blender_utils.replaceObjectMesh(ob, mesh)
    bpy.data.meshes.remove(mesh)

    blender_utils.selectObject(ob)

    os.unlink(binMeshFile)

    return None


def isAlembicFile(filePath: str):
    # Cannot use functions from pathlib here because the path may be in Blender relative format.
    return filePath.endswith('.abc')


def _binRead(file: BufferedReader, dataType: str, numItems: int):
    """ Read typed data items from a binary file.

    Args:
        file (BufferedReader): input file object
        dataType (str): the type of the data item, one of the format specifiers defined for struct.unpack()
        numItems (int): the number of items to read

    Returns:
        The requested data in a compatible format.
    """

    DATA_SIZES = {
        'I': 4,
        'Q': 8,
        'c': 1,
        'f': 4,
        's': 1
    }
    rawData = file.read(numItems * DATA_SIZES[dataType])
    format = f"{numItems}{dataType}"
    buffer = struct.unpack(format, rawData)

    data = buffer[0] if len(buffer) == 1 else buffer
    if (dataType == 'c') and (len(buffer) == 1) :
        return data.decode()
    elif dataType == 's':
        return data.decode('utf-8')
    
    return data


def readBinMeshFile(filePath: str):
    """ Read a .vrbin file produced by vraytools utility into Blender-compatible fomat """
    assert os.path.isfile(filePath)

    objects = {} # name -> meshData

    with open(os.path.expanduser(filePath), "rb") as file:
        numObjects = _binRead(file, 'I', 1) # uint32

        for i in range(numObjects):
            meshData = _readObjectFromBinFile(file)
            objects[meshData['name']] = meshData
    
    return objects


def _readObjectFromBinFile(file: BufferedReader):  
    """ Read a single object from a binary mesh data stream.

    Args:
        file (BufferedReader): an open reader for the binary file

    Raises:
        Exception: 

    Returns:
        dict(str, meshData): a map of object name to mesh data for the object
    """
    def _readString(file):
        length = _binRead(file, 'I', 1)
        return _binRead(file, 's', length)
    
    chunks = []
    objName = _readString(file)
    tocSize = _binRead(file, "I", 1)         # uint32

    for _ in range(tocSize):
        itemType  = _binRead(file, 'c', 1)   # char
        itemCount = _binRead(file, 'Q', 1)   # uint64
        offset    = _binRead(file, 'Q', 1)   # uint64
        chunks.append((itemType, offset, itemCount))

    vertices = []
    faces = []

    for itemType, offset, itemCount in chunks:    
        match itemType:
            case 'v':
                floatArray  = _binRead(file, 'f', itemCount * 3) # 3 float coords per vertex
                vertices = np.reshape(floatArray, (-1, 3))
            case 'f':
                intArray  = _binRead(file, 'I', itemCount * 3) # 3 int indices per face
                faces = np.reshape(intArray, (-1,3))
            case _:
                raise Exception(f"Unsupported channel type '{itemType}'")

    return {
        'name'      : objName,
        'vertices'  : vertices,
        'faces'     : faces,
    }


def _flipYZAxes(mesh: bpy.types.Mesh, yToZ: bool):
    """ Change mesh orientation by flipping the Y and Z axes in a VRayScene object.

    Args:
        mesh (bpy.types.Mesh)   : The mesh to rotate.
        yToZ (bool)             : True - Y to Z, False - Z to Y
    """
    from mathutils import Matrix
    
    flipMatrix = Matrix()
    flipMatrix[1][1] = 0.0
    flipMatrix[2][2] = 0.0
    # 1) 90 degrees rotation around x axis (Z-up to Y-up: transform maya to max coordinate system)
    # 2) -90 degrees rotation around x axis (Y-up to Z-up: transform max to maya coordinate system)
    flipMatrix[1][2] = -1.0 if yToZ else 1.0
    flipMatrix[2][1] = 1.0 if yToZ else -1.0

    mesh.transform(flipMatrix)


def getProxyPreviewAppliedTransform(obj: bpy.types.Object, fromOriginal=True):
    """ Return the cumulative transformation to the obeject's mesh which has been applied
        after the proxy object was imported for the first time. This incliudes changing 
        the object's origin point and using 'Apply transform' on the object.
    """
    assert isObjectVrayProxy(obj)

    # When a transformation is applied to an object, Blender transforms the vertices of its mesh.
    # From the current locations (in object local coordinates) of the 4 vertices that serve as 
    # the 'anchor' basis matrix, we can compute the transformation applued to the object.
    
    # If in Edit mode, non-original obj will have no geometry data
    mesh = obj.original.data if fromOriginal else obj.data
    
    geomMeshFile = mesh.vray.GeomMeshFile
    basePosMatrix = matrixLayoutToMatrix(geomMeshFile.basis_matrix)
    currPosMatrix = _getAnchorTransform(mesh) 
        
    return currPosMatrix @ basePosMatrix.inverted()


def _positionProxyLights(previewObj: bpy.types.Object, parentObjPos: Vector):
    """ Set the position of lights attached to a VRayProxy object when the mesh is updated.
    
        This is necessary because the lights are positioned relative to the initial position of the mesh,
        and if the mesh origin point is moved, the lights need to be updated accordingly.
    """
    geomMeshFile = previewObj.data.vray.GeomMeshFile
    appliedTransform = getProxyPreviewAppliedTransform(previewObj) 
    
    for lightObj in (c for c in previewObj.children if c.type == 'LIGHT'):
        light = lightObj.data
        
        offsetFromParent = (Vector(light.vray.initial_proxy_light_pos) + parentObjPos) * geomMeshFile.scale
        offsetFromParent = _applyTransformToVertex(offsetFromParent, appliedTransform)

        lightScale = Vector((1.0, 1.0, 1.0)) * geomMeshFile.scale / light.vray.initial_proxy_light_scale

        lightObj.matrix_local.translation = offsetFromParent
        lightObj.scale = lightScale
        

#########################################
########### OBSOLETE FUNCTIONS ##########

## Used only for the upgrade scripts

def _getCenterOfProxyPreview(filePath, geomMeshFile):
    """ Get the center of the bounding box of the proxy mesh preview.
    """
    meshData, err = _getDataFromMeshFile(filePath, 'Boxes', 0, int(geomMeshFile.flip_axis))
    if err:
        debug.printError(f"Proxy preview mesh center calculation error: {err}")
        return Vector((0.0, 0.0, 0.0))

    return Vector(np.mean(meshData['vertices'], axis=0)) * geomMeshFile.scale

def _getDimsOfProxyPreview(filePath, geomMeshFile):
    """ Get the dimensions of the bounding box of the proxy mesh preview.
    """
    meshData, err = _getDataFromMeshFile(filePath, 'Boxes', 0, int(geomMeshFile.flip_axis))
    if err:
        debug.printError(f"Proxy preview mesh dimensions calculation error: {err}")
        return Vector((0.0, 0.0, 0.0))

    maxVec = Vector(np.max(meshData['vertices'], axis=0))
    minVec = Vector(np.min(meshData['vertices'], axis=0))
    return (maxVec - minVec) * geomMeshFile.scale