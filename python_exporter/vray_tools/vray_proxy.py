# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later


import math
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
from vray_blender.lib.blender_utils import hasShadowedAttrChanged, updateShadowAttr, getShadowAttr, getPropertyDefaultValue
from vray_blender.lib.sys_utils import getAppSdkLibPath
from vray_blender.vray_tools import vray_proxy



# Types of actions that can be performed for generating data for VRayProxy or VRayScene supported formats
# using the vraytools utility.
class PreviewAction:
    MeshPreview     = '1'      # Geometry from a VRayProxy-compatible files
    ScenePreview    = '2'      # Geometry from a VRayScene-compatible files
    ScannedPreset   = '3'      # Scanned material preset info
    MacOSInternal   = '4'      # Internal action used on macOS; not exposed in this module
    GaussianPreview = '5'      # Preview points (positions + colors) of a Gaussian splat file (.ply)


_PREVIEW_TYPES = {
    'Full':    0,
    'Preview': 1,
    'Boxes' :  2,
    'FullWithMetadata': 3,
}


def runVRayTools(cmd: list[str]):
    """ Run a 'vraytools' command line and return the CompletedProcess.

        Shared by all callers of the tool so that the logic is reused.
    """
    from subprocess import PIPE, run

    # On Windows the tool had a load-time dependency on vray.dll, now the code below is left just
    # in case future shared library dependency appears.
    appSdkPath = sys_utils.getAppSdkPath()
    cwd = appSdkPath if os.path.isdir(appSdkPath) else None

    return run(cmd, cwd=cwd, stdout=PIPE, stderr=PIPE, universal_newlines=True)


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

    vrayToolsApp = path_utils.getBinTool(sys_utils.getPlatformName("vraytools"))

    cmd = [vrayToolsApp]
    cmd.extend(['-vrayLib', getAppSdkLibPath()])
    cmd.extend(['-action', PreviewAction.MeshPreview])
    cmd.extend(['-input', meshFile])
    cmd.extend(['-output', binFile])
    cmd.extend(['-previewType', str(previewType)])
    cmd.extend(['-previewFaces', str(previewFaces)])
    cmd.extend(['-flipAxis', str(flipAxis)])

    result = runVRayTools(cmd)

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

    result = runVRayTools(cmd)

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


def _flipAxisMatrix(flipAxis: str) -> Matrix:
    """ Return the rotation that the vraytools '-flipAxis' option bakes into the preview
        vertices, so the same reorientation can be applied in Blender without regenerating
        the preview.
    """
    # GeomMeshFile.flip_axis exposes 0/1/2, VRayScene.flip_axis exposes 0/2/3. The '2' meaning
    # is the same in both; Y-Up->Z-Up is '1' for the proxy and '3' for the scene.
    match flipAxis:
        case '1' | '3': # Y-Up->Z-Up (Maya->Max)
            return Matrix.Rotation(math.radians(90.0), 4, 'X')
        case '2': # Z-Up->Y-Up (Max->Maya)
            return Matrix.Rotation(math.radians(-90.0), 4, 'X')
        case _: # as-is
            return Matrix.Identity(4)


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



def _applyTransformToVertexArray(vertices: np.ndarray, transform: Matrix):
    npMat = np.array(transform)

    rotScale    = npMat[:3, :3]
    translation = npMat[:3, 3]
    return vertices @ rotScale.T + translation


def _applyTransformToVertex(vertex: Vector, mat: Matrix):
    return vertex @ mat.to_3x3().transposed() + mat.to_translation()


def _replaceObjMesh(mesh: bpy.types.Mesh, meshData):
    # Replace object's mesh using fast foreach_set path
    faces = meshData['faces']
    numFaces = len(faces)

    from vray_blender.lib.mesh_build_utils import buildTriMeshBase
    tempMesh = buildTriMeshBase("VRayProxyPreviewTemporary", meshData['vertices'], faces)
    tempMesh.update()

    blender_utils.replaceObjectMesh(mesh, tempMesh)
    bpy.data.meshes.remove(tempMesh)

    shaders = meshData.get('shaders')
    materialIDs = meshData.get('material_ids')
    if shaders and materialIDs is not None and len(materialIDs) == numFaces:
        numSlots = len(mesh.materials)
        if numSlots > 0:
            sortedShaders = sorted(shaders, key=lambda s: s['id'])
            shaderIdToSlotIndex = {s['id']: i for i, s in enumerate(sortedShaders)}
            maxID = int(max(shaderIdToSlotIndex))
            lut = np.zeros(maxID + 1, dtype=np.int32)
            for sid, idx in shaderIdToSlotIndex.items():
                lut[sid] = idx
            matArray = lut[np.clip(materialIDs, 0, maxID)]
            np.clip(matArray, 0, numSlots - 1, out=matArray)
            attr = mesh.attributes.get("material_index") or \
                   mesh.attributes.new("material_index", 'INT', 'FACE')
            attr.data.foreach_set("value", matArray)
            mesh.update()


def loadVRayProxyPreviewMesh(geomMeshFile, filePath: str, animFrame = 0, outMetadata: dict = None):
    """ Load the preview voxel from a .vrmesh file, if any.

        Returns: None on success, error message on error.
        If outMetadata is provided, it will be populated with shader and UV channel info.
    """
    mesh = geomMeshFile.id_data
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
        appliedTransform = _computeAppliedTransform(geomMeshFile, mesh)
        # Capture the applied transform while the basis is still valid. If the scale is later
        # dragged through 0, the anchors collapse onto a single point and rotation/scale can no
        # longer be derived from the mesh; _computeAppliedTransform reuses this to restore the
        # full rotation + translation when the scale is raised again.
        geomMeshFile['applied_transform_backup'] = mat4x4ToTuple(appliedTransform)
        meshData['vertices'] = _applyTransformToVertexArray(meshData['vertices'], appliedTransform)
        parentObjPos = Vector(geomMeshFile['initial_preview_mesh_pos'])
        for proxyObj in (o for o in bpy.data.objects if o.data is mesh):
            _positionProxyLights(proxyObj, parentObjPos)
    else:
        geomMeshFile['initial_preview_mesh_pos'] = geometryCenter

    geomMeshFile['basis_matrix'] = mat4x4ToTuple(geomMeshFileScale @ baseMatrix)
    geomMeshFile['basis_vertex_indices'] = pointIndices

    _replaceObjMesh(mesh, meshData)

    # Create empty UV layers named after the proxy's UV channel indices.
    # This allows MayaPlace2D UVWGen to select UVW channels by name.
    if (uvChannelIndices := meshData.get('uv_channel_indices')) is not None:
        for channelIdx in uvChannelIndices:
            layerName = f"vray_channel_id_{channelIdx}"
            if layerName not in mesh.uv_layers:
                mesh.uv_layers.new(name=layerName)

    if outMetadata is not None:
        outMetadata['shaders'] = meshData.get('shaders')

    if geomMeshFile.previewType == 'Preview':
        # The preview-generation procedure uses the requested number of preview faces as a guideline only.
        # Write back to the UI the number of actual preview faces
        geomMeshFile['num_preview_faces'] = len(meshData['faces'])

    updateShadowAttr(geomMeshFile, 'file')


def applyProxyPreviewTransform(geomMeshFile, context):
    """ Fast path for 'scale'/'flip_axis' changes: transform the already-loaded preview mesh in
        place with a delta matrix instead of regenerating it through the external vraytools process.
    """
    mesh = geomMeshFile.id_data

    prevScale = getShadowAttr(geomMeshFile, 'scale')
    prevFlip  = getShadowAttr(geomMeshFile, 'flip_axis')
    newScale  = geomMeshFile.scale
    newFlip   = geomMeshFile.flip_axis

    # Nothing loaded yet (no file, or an empty mesh).
    if (not geomMeshFile.file) or (len(mesh.vertices) == 0):
        updateShadowAttr(geomMeshFile, 'scale')
        updateShadowAttr(geomMeshFile, 'flip_axis')
        return

    if (prevScale == newScale) and (prevFlip == newFlip):
        return

    if prevScale == 0.0:
        if err := loadVRayProxyPreviewMesh(geomMeshFile, geomMeshFile.file, context.scene.frame_current):
            debug.reportError(err)
    else:
        # Tool-space delta from the previously baked (scale, flip) to the new one.
        flipDelta  = _flipAxisMatrix(newFlip) @ _flipAxisMatrix(prevFlip).inverted()
        scaleRatio = newScale / prevScale
        delta = Matrix.Scale(scaleRatio, 4) @ flipDelta
        appliedTransform = _computeAppliedTransform(geomMeshFile, mesh)
        localDelta = appliedTransform @ delta @ appliedTransform.inverted()
        geomMeshFile['applied_transform_backup'] = mat4x4ToTuple(appliedTransform)

        # Update the preview mesh.
        mesh.transform(localDelta)
        mesh.update()

        # Keep basis_matrix consistent with the moved anchor verts.
        basisMatrix = matrixLayoutToMatrix(geomMeshFile.basis_matrix)
        geomMeshFile['basis_matrix'] = mat4x4ToTuple(delta @ basisMatrix)

        # Reposition any lights attached to the proxy, exactly as the full load does.
        parentObjPos = Vector(geomMeshFile.initial_preview_mesh_pos)
        for proxyObj in (o for o in bpy.data.objects if o.data is mesh):
            _positionProxyLights(proxyObj, parentObjPos)

    updateShadowAttr(geomMeshFile, 'scale')
    updateShadowAttr(geomMeshFile, 'flip_axis')


def loadVRayScenePreviewMesh(vrayScene, absFilePath: str):
    """ Load preview from a file format compatible with VRayScene.

        Args:
            vrayScene       (PropertyGroup): VRayScene propgroup of the scene object
            absFilePath     (str)          : path to a file compatible with VRayScene

        Returns:
            None on success, error message on error.
    """
    fileExt = PurePath(absFilePath).suffix
    if fileExt != '.vrscene' and not fileExt.startswith(".usd"):
        return f"File format {fileExt} is not supported by V-Ray Scene"

    absFilePath = bpy.path.abspath(absFilePath)
    if not os.path.exists(absFilePath):
        return "Scene file doesn't exist!"

    mesh = vrayScene.id_data
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
        appliedTransform = _computeAppliedTransform(vrayScene, mesh)
        meshData['vertices'] = _applyTransformToVertexArray(meshData['vertices'], appliedTransform)

    vrayScene['basis_matrix'] = mat4x4ToTuple(baseMatrix)
    vrayScene['basis_vertex_indices'] = pointIndices

    _replaceObjMesh(mesh, meshData)

    if vrayScene.previewType == 'Preview':
        # The preview-generation procedure uses the requested number of preview faces as a guideline only.
        # Write back to the UI the number of actual preview faces
        vrayScene['num_preview_faces'] = len(meshData['faces'])

    updateShadowAttr(vrayScene, 'filepath')


def isAlembicFile(filePath: str):
    # Cannot use functions from pathlib here because the path may be in Blender relative format.
    return filePath.endswith('.abc')


def binRead(file: BufferedReader, dataType: str, numItems: int):
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
        numObjects = binRead(file, 'I', 1) # uint32

        for i in range(numObjects):
            meshData = _readObjectFromBinFile(file)
            objects[meshData['name']] = meshData

    return objects


def _parseShaderInfoBlock(rawData: bytes):
    """ Parse the 's' channel byte block into a list of shader descriptors.

    Returns:
        list[dict]: Each entry has 'name' (str) and 'id' (int).
    """
    shaders = []
    offset = 0
    count = struct.unpack_from('I', rawData, offset)[0]
    offset += 4

    for _ in range(count):
        nameLen = struct.unpack_from('I', rawData, offset)[0]
        offset += 4
        name = rawData[offset:offset + nameLen].decode('utf-8')
        offset += nameLen
        shaderId = struct.unpack_from('i', rawData, offset)[0]
        offset += 4
        shaders.append({'name': name, 'id': shaderId})

    return shaders


def _parseUVChannelDataBlock(rawData: bytes):
    """ Parse the 'c' channel byte block into a list of UV channel data.

    Returns:
        list[dict]: Each entry has 'original_index' (int), 'uv_coords' (ndarray Nx2),
                    'uv_face_indices' (ndarray of int).
    """
    uvChannels = []
    offset = 0
    count = struct.unpack_from('I', rawData, offset)[0]
    offset += 4

    for _ in range(count):
        origIndex = struct.unpack_from('i', rawData, offset)[0]
        offset += 4

        numCoords = struct.unpack_from('Q', rawData, offset)[0]
        offset += 8

        # UV coords are stored as Vector3 (x,y,z) — extract only U,V (x,y)
        coordsBytes = numCoords * 3 * 4  # 3 floats per Vector3, 4 bytes per float
        uvCoords = np.frombuffer(rawData, dtype=np.float32, count=numCoords * 3, offset=offset).reshape(-1, 3)[:, :2].copy()
        offset += coordsBytes

        numIndices = struct.unpack_from('Q', rawData, offset)[0]
        offset += 8

        indicesBytes = numIndices * 4
        uvIndices = np.frombuffer(rawData, dtype=np.uint32, count=numIndices, offset=offset).copy()
        offset += indicesBytes

        uvChannels.append({
            'original_index': origIndex,
            'uv_coords': uvCoords,
            'uv_face_indices': uvIndices,
        })

    return uvChannels


def _readObjectFromBinFile(file: BufferedReader):
    """ Read a single object from a binary mesh data stream.

    Args:
        file (BufferedReader): an open reader for the binary file

    Returns:
        dict(str, meshData): a map of object name to mesh data for the object
    """
    def _readString(file):
        length = binRead(file, 'I', 1)
        return binRead(file, 's', length)

    chunks = []
    objName = _readString(file)
    tocSize = binRead(file, "I", 1)         # uint32

    for _ in range(tocSize):
        itemType  = binRead(file, 'c', 1)   # char
        itemCount = binRead(file, 'Q', 1)   # uint64
        offset    = binRead(file, 'Q', 1)   # uint64
        chunks.append((itemType, offset, itemCount))

    vertices = []
    faces = []
    normals = None
    normalIndices = None
    materialIDs = None
    shaders = None
    uvChannelIndices = None
    uvChannels = None

    for itemType, offset, itemCount in chunks:
        match itemType:
            case 'v':
                vertices = np.frombuffer(file.read(itemCount * 3 * 4), dtype=np.float32).reshape(-1, 3)
            case 'f':
                faces = np.frombuffer(file.read(itemCount * 3 * 4), dtype=np.int32).reshape(-1, 3)
            case 'n':
                normals = np.frombuffer(file.read(itemCount * 3 * 4), dtype=np.float32).reshape(-1, 3)
            case 'x':
                normalIndices = np.frombuffer(file.read(itemCount * 4), dtype=np.int32)
            case 'm':
                materialIDs = np.frombuffer(file.read(itemCount * 4), dtype=np.int32)
            case 's':
                rawData = file.read(itemCount)
                shaders = _parseShaderInfoBlock(rawData)
            case 'i':
                uvChannelIndices = np.frombuffer(file.read(itemCount * 4), dtype=np.int32)
            case 'c':
                rawData = file.read(itemCount)
                uvChannels = _parseUVChannelDataBlock(rawData)
            case _:
                raise Exception(f"Unsupported channel type '{itemType}'")

    return {
        'name'              : objName,
        'vertices'          : vertices,
        'faces'             : faces,
        'normals'           : normals,
        'normal_indices'    : normalIndices,
        'material_ids'      : materialIDs,
        'shaders'           : shaders,
        'uv_channel_indices': uvChannelIndices,
        'uv_channels'       : uvChannels,
    }


def _computeAppliedTransform(propGroup, mesh: bpy.types.Mesh):
    """ Compute the cumulative transform applied to the mesh since first import.
        Works from the propgroup and mesh data directly, without needing the Object.
    """
    basePosMatrix = matrixLayoutToMatrix(propGroup.basis_matrix)

    if basePosMatrix == Matrix():
        return Matrix()

    basePts = [basePosMatrix.col[i].xyz for i in range(4)]

    anchorIndices = propGroup.basis_vertex_indices
    currPts = [mesh.vertices[anchorIndices[i]].co for i in range(4)]

    baseDiffVecs = [basePts[i + 1] - basePts[0] for i in range(3)]
    currDiffVecs = [currPts[i + 1] - currPts[0] for i in range(3)]
    baseDiffMatrix = Matrix(tuple(zip(*baseDiffVecs)))
    currDiffMatrix = Matrix(tuple(zip(*currDiffVecs)))

    # The anchors collapse onto a single point - so rotation/scale can no longer be derived and
    # inverted_safe() below would yield a near-zero matrix that collapses the reloaded geometry to
    # a point (invisible preview) - in two cases:
    #   * GeomMeshFile.scale == 0 collapses the stored reference (baseDiffMatrix). The collapse
    #     point still marks the object's position, so keep the translation recovered from it.
    #   * The mesh itself is scaled to 0 in Edit Mode, collapsing the live anchors
    #     (currDiffMatrix). Their position is the Edit Mode pivot and carries no meaning, so keep
    #     the full backup transform to restore the proxy where it was.
    # In both cases take the rotation/scale from the transform captured by loadVRayProxyPreviewMesh
    # while the basis was still valid. Returning the backup (instead of a degenerate matrix) also
    # keeps callers from writing a corrupted value back into 'applied_transform_backup'.
    baseSingular = baseDiffMatrix.determinant() == 0.0
    if baseSingular or (currDiffMatrix.determinant() == 0.0):
        result = matrixLayoutToMatrix(propGroup.applied_transform_backup)
        if baseSingular:
            result.translation = currPts[0] - basePts[0]
        return result

    rotScaleMatrix = currDiffMatrix @ baseDiffMatrix.inverted_safe()

    result = rotScaleMatrix.to_4x4()
    result.translation = currPts[0] - rotScaleMatrix @ basePts[0]
    return result


def getProxyPreviewAppliedTransform(obj: bpy.types.Object, fromOriginal=True):
    """ Return the cumulative transformation to the object's mesh which has been applied
        after the proxy object was imported for the first time. This includes changing
        the object's origin point and using 'Apply transform' on the object.
    """
    assert isObjectVrayProxy(obj) or isObjectVrayScene(obj)

    # If in Edit mode, non-original obj will have no geometry data
    mesh = obj.original.data if fromOriginal else obj.data
    propGroup = mesh.vray.GeomMeshFile if isObjectVrayProxy(obj) else mesh.vray.VRayScene
    return _computeAppliedTransform(propGroup, mesh)


def _positionProxyLights(previewObj: bpy.types.Object, parentObjPos: Vector):
    """ Set the position of lights attached to a VRayProxy object when the mesh is updated.

        This is necessary because the lights are positioned relative to the initial position of the mesh,
        and if the mesh origin point is moved, the lights need to be updated accordingly.
    """
    geomMeshFile = previewObj.data.vray.GeomMeshFile
    appliedTransform = _computeAppliedTransform(geomMeshFile, previewObj.data)

    for lightObj in (c for c in previewObj.children if c.type == 'LIGHT'):
        light = lightObj.data

        offsetFromParent = (Vector(light.vray.initial_proxy_light_pos) + parentObjPos) * geomMeshFile.scale
        offsetFromParent = _applyTransformToVertex(offsetFromParent, appliedTransform)

        lightScale = Vector((1.0, 1.0, 1.0)) * geomMeshFile.scale / light.vray.initial_proxy_light_scale

        lightObj.matrix_local.translation = offsetFromParent
        lightObj.scale = lightScale
