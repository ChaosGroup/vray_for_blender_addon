# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later


import struct
from pathlib import PurePath
import os
from io import BufferedReader

import bpy
import numpy as np
from mathutils import Vector, Matrix

from vray_blender import debug
from vray_blender.exporting.tools import isObjectVrayProxy, isObjectVrayScene, matrixLayoutToMatrix, mat4x4ToTuple
from vray_blender.lib import blender_utils, sys_utils, path_utils
from vray_blender.lib.blender_utils import hasShadowedAttrChanged, updateShadowAttr, getPropertyDefaultValue
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


def _dumpMeshFile(meshFile: str, binFile: str, previewType: int, previewFaces: int, flipAxis: int):
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
    cmd.extend(['-input', meshFile])
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


def _dumpVrSceneFile(sceneFile: str, binFile: str, previewType: int, previewFaces: int, flipAxis: int):
    """ Run vraytools utility to dump the requested data from a scene file (.vrscene, .usd) into a simplified 
        binary format which can be easily loaded by the Python code.

    Args:
        sceneFile   (str):  path to the .vrscene file from which to generate the preview
        binFile     (str):  path to the resulting binary file
        previewType (int):  one of the values in _PRWVIEW_TYPES
        previewFaces(int):  upper limit for the number of faces in the preview. Only valid 
                            if isPreview is True.
        flipAxis    (int): one of the GeomMeshFile.flip_axis values

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
    cmd.extend(['-flipAxis', str(flipAxis)])

    debug.printInfo(f"Calling: {' '.join(cmd)}")

    result = run(cmd, stdout=PIPE, stderr=PIPE, universal_newlines=True)

    debug.printInfo(f"Running scene preview tool: {' '.join(cmd)}")
    
    if result.returncode != 0:
        debug.printError(result.stdout)
        debug.printError(result.stderr)
        return "Error generating scene preview file!"
    
    return None


def _generateScenePreview(filePath: str, previewType: str, previewFaces: int, flipAxis: int):
    binMeshFile = str(PurePath(path_utils.getV4BTempDir(), PurePath(filePath).stem).with_suffix('.vrbin'))

    if err := _dumpVrSceneFile(filePath, binMeshFile, _PREVIEW_TYPES[previewType], previewFaces, flipAxis):
        return None, err
    
    objects = vray_proxy.readBinMeshFile(binMeshFile)

    os.unlink(binMeshFile)

    assert len(objects) == 1
    meshData = list(objects.values())[0]

    assert meshData is not None
    return meshData, ""


def _generateProxyPreview(filePath: str, previewType: str, previewFaces: int, flipAxis: int):
    """ Get mesh data from a .vrmesh or .abc file, or a file compatible with VRayScene.
    
    Args:
        meshFile    (str): path to a file in format compatible with VRayProxy
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


def _generatePreview(filePath: str, previewType: str, previewFaces: int, flipAxis: int, isProxy: bool):
    if isProxy:
        return _generateProxyPreview(filePath, previewType, previewFaces, flipAxis)
    else:
        return _generateScenePreview(filePath, previewType, previewFaces, flipAxis)


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
    
    anchorVertices = _applyTransformToVertexArray(anchorVertices, matTranslate @ matScale)

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


def _constructPointPreview(proxyPropGroup: dict, meshFilePath: str, isProxy: bool):
    """ Fill mesh data for a point preview """
    proxyPropGroup['num_preview_faces'] = 0

    bboxData, err = _generatePreview(meshFilePath, 'Boxes', 0, int(proxyPropGroup.flip_axis), isProxy)
    
    if not bboxData:
        return None, None, err
    
    # There may be more then 1 bounding boxes, get the first one. We only need it in order
    # to position the added vertices for the basis, so this should be fine. 
    assert len(bboxData['vertices']) >= 8
    boxVertices = bboxData['vertices'][:8]

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


def _constructPreview(proxyPropGroup: dict, meshFilePath: str, isProxy: bool, resetNumFaces=False):
    """ Fill mesh data for a non-point preview """

    numFaces = proxyPropGroup.num_preview_faces if not resetNumFaces else getPropertyDefaultValue(proxyPropGroup, 'num_preview_faces')

    meshData, err = _generatePreview(meshFilePath, proxyPropGroup.previewType, numFaces, int(proxyPropGroup.flip_axis), isProxy)
    if err:
        return None, None, err
    
    if len(meshData['faces']) == 0:
        return None, None, 'V-RayProxy import failed - mesh has no faces'

    if proxyPropGroup.previewType == 'Boxes':
        bboxData = meshData
    else:
        bboxData, err = _generatePreview(meshFilePath, 'Boxes', 0, int(proxyPropGroup.flip_axis), isProxy)
        if err:
            return None, None, err
        
    # There may be more then 1 bounding boxes, get the first one. We only need it in order
    # to position the added vertices for the basis, so this should be fine. 
    assert len(bboxData['vertices']) >= 8
    return meshData, bboxData['vertices'][:8], None


def _getAnchorTransform(previewMesh: bpy.types.Mesh, isProxy: bool):
    """ Return a matrix constructed from the 4 anchor points """
    
    if isProxy:
        proxyPropGroup = previewMesh.vray.GeomMeshFile
    else:
        proxyPropGroup = previewMesh.vray.VRayScene

    pointIndices = proxyPropGroup.basis_vertex_indices

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
    isNewMesh = hasShadowedAttrChanged(geomMeshFile, 'file')

    absFilePath = bpy.path.abspath(filePath)
    if ( fileExt:= PurePath(absFilePath).suffix) not in ('.vrmesh', '.abc'):
        return f"File format {fileExt} is not supported by V-Ray Proxy"
    
    meshData, boxVertices, err = _constructPointPreview(geomMeshFile, absFilePath, isProxy=True) if geomMeshFile.previewType == 'Point' \
                                    else _constructPreview(geomMeshFile, absFilePath, isProxy=True, resetNumFaces=isNewMesh)
    
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
    
    
def loadVRayScenePreviewMesh(previewObj: bpy.types.Object, absFilePath: str):
    """ Load preview from a file format compatible with VRayScene.
    
        Args:
            previewObj      (Object): scene object whose mesh will be replaced by the preview mesh
            sceneFilepath   (str)   : path to a file compatible with VRayScene

        Returns: 
            None on success, error message on error.
    """
    fileExt = PurePath(absFilePath).suffix
    if fileExt != '.vrscene' and not fileExt.startswith(".usd"):
        return f"File format {fileExt} is not supported by V-Ray Scene"
    
    absFilePath = bpy.path.abspath(absFilePath)
    if not os.path.exists(absFilePath):
        return "Scene file doesn't exist!"

    vrayScene = previewObj.data.vray.VRayScene
    isNewScene = hasShadowedAttrChanged(vrayScene, 'filepath')

    meshData, boxVertices, err = _constructPointPreview(vrayScene, absFilePath, isProxy=False) \
                            if vrayScene.previewType == 'Point' \
                                else _constructPreview(vrayScene, absFilePath, isProxy=False,  resetNumFaces = isNewScene)

    if err:
        return err
        
    if len(meshData['vertices']) == 0:
        return f"V-Ray Scene object has no vertices. Loaded from {absFilePath}"
    
    # The new preview object's anchor points may not be the same as the old ones.
    # Bring them to the coordinate system of the original object
    baseMatrix, pointIndices, newVertices = _computeBasisMatrix(meshData['vertices'] , boxVertices)
    meshData['vertices'] = newVertices

    if vrayScene.filepath:
        # The proxy is being reimported because one of its properties has changed (incl. the mesh file).
        # In addition to the scale, we also apply the transform of the previous proxy so that the new
        # object appeared at the same position
        appliedTransform = getProxyPreviewAppliedTransform(previewObj)
        meshData['vertices'] = _applyTransformToVertexArray(meshData['vertices'], appliedTransform)
    
    vrayScene['basis_matrix'] = mat4x4ToTuple(baseMatrix)
    vrayScene['basis_vertex_indices'] = pointIndices

    _replaceObjMesh(previewObj, meshData)
    
    if vrayScene.previewType == 'Preview':
        # The preview-generation procedure uses the requested number of preview faces as a guideline only.
        # Write back to the UI the number of actual preview faces
        vrayScene['num_preview_faces'] = len(meshData['faces'])

    updateShadowAttr(vrayScene, 'filepath')
    
    
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


def getProxyPreviewAppliedTransform(obj: bpy.types.Object, fromOriginal=True):
    """ Return the cumulative transformation to the obeject's mesh which has been applied
        after the proxy object was imported for the first time. This incliudes changing 
        the object's origin point and using 'Apply transform' on the object.
    """
    assert isObjectVrayProxy(obj) or isObjectVrayScene(obj)

    # When a transformation is applied to an object, Blender transforms the vertices of its mesh.
    # From the current locations (in object local coordinates) of the 4 vertices that serve as 
    # the 'anchor' basis matrix, we can compute the transformation applued to the object.
    
    isProxy = isObjectVrayProxy(obj)

    # If in Edit mode, non-original obj will have no geometry data
    mesh = obj.original.data if fromOriginal else obj.data
    
    propGroup = mesh.vray.GeomMeshFile if isProxy else mesh.vray.VRayScene
    basePosMatrix = matrixLayoutToMatrix(propGroup.basis_matrix)
    currPosMatrix = _getAnchorTransform(mesh, isProxy) 
        
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
        


