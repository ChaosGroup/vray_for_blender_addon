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
    'ObjectList': 4,
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


def _dumpMeshFile(meshFile: str, binFile: str, previewType: int, previewFaces: int, flipAxis: int,
                  visibility: tuple[list[str], int] = None):
    """ Run vraytools utility to dump the requested data from a mesh file (.vrmesh, .abc etc) into a simplified
        binary format which can that be easily loaded by the Python code.

    Args:
        meshFile    (str): path to a file in format compatible wiht VRayProxy
        binFile     (str): path to the resulting binary file
        previewType (int): one of the _PTEVIEW_TYPES values
        previewFaces(int): upper limit for the number of faces in the preview. Only valid
                            if isPreview is True.
        flipAxis    (int): one of the GeomMeshFile.flip_axis values
        visibility  (tuple[list[str], int], optional): the full paths in the Object Hierarchy's
                            visibility list and the list type (0 Exclude, 1 Include). The dump then
                            holds only the visible objects. None dumps them all.
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

    # The paths go in a file rather than on the command line: a proxy can hold thousands of objects
    # and their full paths are long, which would run into the command-line length limit.
    visibilityFile = None

    if visibility is not None:
        names, listType = visibility
        visibilityFile = str(PurePath(binFile).with_suffix('.vislist'))

        try:
            with open(visibilityFile, 'w', encoding='utf-8') as f:
                f.write('\n'.join(names))
        except OSError as ex:
            # Not fatal - an unfiltered preview is a better outcome than no preview at all.
            debug.printError(f"Could not write the proxy visibility list: {ex}")
            visibilityFile = None

        if visibilityFile:
            cmd.extend(['-visibilityList', visibilityFile])
            cmd.extend(['-visibilityType', str(listType)])

    try:
        result = runVRayTools(cmd)

        debug.printInfo(f"Running mesh preview tool: {' '.join(cmd)}")

        if result.returncode != 0:
            debug.printError(result.stdout)
            debug.printError(result.stderr)
            return f"Error generating bin file: {result.returncode}"

        if not os.path.isfile(binFile):
            return "Error generating bin file: file is missing"
    finally:
        if visibilityFile and os.path.isfile(visibilityFile):
            os.unlink(visibilityFile)

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


def _getObjectNames(objects: dict):
    """ The proxy object names out of a .vrbin, or None when it carries no object info at all.

        Matched by content rather than by name: the geometry entry is called 'object' and the one
        holding the object infos 'objects', which is too easy to mix up. An empty list is a valid
        answer - a file with no object info channel - and is not the same as None.
    """
    for meshData in objects.values():
        if (objectInfos := meshData.get('object_infos')) is not None:
            return [o['name'] for o in objectInfos]

    return None


def _getShaders(objects: dict):
    """ The shader sets out of a .vrbin, or None when it carries no shader info at all.

        Matched by content, like _getObjectNames(): the geometry entry has always carried them, and
        the object-infos entry does too, so that the dumps which write no geometry ('Boxes',
        'ObjectList') can report them as well. An empty list is a valid answer - a file with no
        shader sets at all - and is not the same as None.
    """
    for meshData in objects.values():
        if (shaders := meshData.get('shaders')) is not None:
            return shaders

    return None


def _generateProxyPreview(filePath: str, previewType: str, previewFaces: int, flipAxis: int,
                          outExtras: dict = None, visibility: tuple[list[str], int] = None):
    """ Get mesh data from a .vrmesh or .abc file, or a file compatible with VRayScene.

    Args:
        meshFile    (str): path to a file in format compatible with VRayProxy
        binFile     (str): path to the resulting binary file
        previewType (int): one of the _PTEVIEW_TYPES values
        previewFaces(int): upper limit for the number of faces in the preview. Only valid
                            if isPreview is True.
        flipAxis    (int): one of the GeomMeshFile.flip_axis values
        outExtras   (dict, optional): filled with whatever else the same dump produced -
                            'boxes' (the bounding box mesh), 'bounds' (the single box covering the
                            whole file), 'objectNames' and 'shaders'. One read of the file yields
                            them all, so asking for them here is what spares the caller a second and
                            a third run of the tool. Any of them may be missing: an older
                            'vraytools' writes only the geometry.
        visibility  (tuple[list[str], int], optional): see _dumpMeshFile(). When given, the objects
                            the list hides are left out of the dumped geometry.
    Returns:
        tuple(dict[str,list], str) | tuple(None, str):
            tuple of mesh data (vertices, faces) and empty string on success, or None and an error message on failure.

    """
    binMeshFile = str(PurePath(path_utils.getV4BTempDir(), PurePath(filePath).stem).with_suffix('.vrbin'))

    if err := _dumpMeshFile(filePath, binMeshFile, _PREVIEW_TYPES[previewType], previewFaces, flipAxis,
                            visibility):
        return None, err

    objects = vray_proxy.readBinMeshFile(binMeshFile)

    os.unlink(binMeshFile)

    # 'Boxes' has no separate geometry - the boxes are the preview.
    meshData = objects.get('object') or objects.get('boxes')

    assert meshData is not None
    if outExtras is not None:
        if (boxes := objects.get('boxes')) is not None:
            outExtras['boxes'] = boxes
        if (bounds := objects.get('bounds')) is not None:
            outExtras['bounds'] = bounds
        if (objectNames := _getObjectNames(objects)) is not None:
            outExtras['objectNames'] = objectNames
        if (shaders := _getShaders(objects)) is not None:
            outExtras['shaders'] = shaders

    return meshData, ""


def readProxyObjectNames(filePath: str, outShaders: list = None):
    """ Read the names of the objects contained in a .vrmesh or .abc file.

        The names are full '/'-separated paths, so the caller can reconstruct the object hierarchy.
        NOTE: This spawns the 'vraytools' process, do not call it from a draw() procedure.

    Args:
        filePath (str): path to a file in format compatible with VRayProxy. Must be an absolute path.
        outShaders (list, optional): filled with the shader sets of the file, which the same dump
            returns. Left untouched by an older 'vraytools', which reports only the names.

    Returns:
        tuple(list[str], str): the object names in file order and an empty string on success,
            or an empty list and an error message on failure.
    """
    binMeshFile = str(PurePath(path_utils.getV4BTempDir(), PurePath(filePath).stem).with_suffix('.vrbin'))

    if err := _dumpMeshFile(filePath, binMeshFile, _PREVIEW_TYPES['ObjectList'], 0, 0):
        return [], err

    objects = vray_proxy.readBinMeshFile(binMeshFile)

    os.unlink(binMeshFile)

    if outShaders is not None and (shaders := _getShaders(objects)) is not None:
        outShaders[:] = shaders

    # A file without an object info channel in its preview voxel yields an empty list, which is
    # not an error.
    return _getObjectNames(objects) or [], ""


def _generatePreview(filePath: str, previewType: str, previewFaces: int, flipAxis: int, isProxy: bool,
                     outExtras: dict = None, visibility: tuple[list[str], int] = None):
    if isProxy:
        return _generateProxyPreview(filePath, previewType, previewFaces, flipAxis, outExtras, visibility)
    else:
        # A VRayScene dump carries the geometry alone - its bounding boxes come from a separate
        # ScenePreview built with usePreview=false, so they cannot ride along.
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


def _sanitizeBBox(bbox: np.ndarray, vertices: np.ndarray):
    """ Return a bounding box that could plausibly be the box of this geometry.

        The box comes from the voxel info table of the proxy file, and the preview voxel of a
        '.vrmesh' is not guaranteed to carry a meaningful one - several test assets report
        uninitialised values there, from a near-zero box up to 7.9e34. Using such a box is not
        harmless: mathutils works in single precision, so squaring a coordinate of 7.9e34 in
        Vector.length overflows to inf, the anchor points come out NaN, and every transform derived
        from them - including the one on the exported Node, which then renders nothing - follows.

        The test is deliberately loose. A box may legitimately be bigger than the preview geometry,
        or slightly smaller than it, since the preview is a decimation of the whole file; it only
        has to be separated from one that is wrong by orders of magnitude.

        'vraytools' runs the same check over the box it writes, but only for the proxy dumps. This
        one is what covers the rest of the callers: the VRayScene preview, whose box comes from a
        different dump entirely (ScenePreview, '-action 2'), and the frozen upgrade scripts, which
        call _constructPreview() directly.
    """
    if (bbox is None) or (len(bbox) < 8) or (vertices is None) or (len(vertices) == 0):
        return bbox

    box = np.asarray(bbox[:8], dtype=np.float64)
    verts = np.asarray(vertices, dtype=np.float64)
    lo, hi = verts.min(axis=0), verts.max(axis=0)
    geomExtent = float((hi - lo).max())

    if np.all(np.isfinite(box)):
        boxLo, boxHi = box.min(axis=0), box.max(axis=0)
        boxExtent = float((boxHi - boxLo).max())

        # Nothing to measure against, or an order of magnitude either way in size and no more than
        # one extent away in position - all plausible
        if (geomExtent <= 0.0) or (
                (geomExtent * 0.125 <= boxExtent <= geomExtent * 8.0)
                and float(np.abs((boxLo + boxHi) - (lo + hi)).max()) * 0.5 <= geomExtent):
            return bbox

    debug.printWarning("V-Ray Proxy: the bounding box in the file does not match its geometry, "
                       "using the geometry's own box instead")

    # The same 8 corners in the same order as the tool writes them, so the callers that index into
    # the box - the basis anchors, the 'Boxes' preview - keep working unchanged.
    return np.array((
        (lo[0], lo[1], lo[2]), (lo[0], lo[1], hi[2]), (hi[0], lo[1], hi[2]), (hi[0], lo[1], lo[2]),
        (lo[0], hi[1], lo[2]), (lo[0], hi[1], hi[2]), (hi[0], hi[1], hi[2]), (hi[0], hi[1], lo[2]),
    ), dtype=np.float32)


def _computeBasisMatrix(vertices: np.ndarray, bbox: np.ndarray, sanitizeBBox=True):

    """
    Constructs a basis matrix from 4 non-coplanar vertices that are added to the initial array.
    The new vertices are placed at the cemter of the object so that they did not go outside the
    bounding box if the object is not flat.

    Parameters:
        vertices: [N,3]array
        bbox: [N,3]array [8] the bounding box of the object
        sanitizeBBox: False when the box has already been validated and covers more than the
            geometry given here on purpose - a preview filtered by the visibility list is a subset
            of the file the box bounds, and measuring one against the other would reject it.
    Returns:
        - The basis matrix of the coordinate system defined by the 4 points
        - The indices of the points in the vertices list
        - The vertex list with the new vertices added to the initial list
    """
    if sanitizeBBox:
        bbox = _sanitizeBBox(bbox, vertices)

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


def _anchorBox(bboxData: dict, extras: dict, outInfo: dict = None):
    """ The 8 corners the preview object's anchor points are placed on.

        The 'bounds' entry when the dump carries one, and it is the only correct answer once the
        boxes can be filtered: 'boxes' then holds one box per *visible* object, so its first box is
        the first surviving object's, and anchoring to that would move the proxy in the viewport
        every time something was hidden. 'bounds' always covers the whole file.

        Falling back to the first box keeps a VRayScene dump and an older 'vraytools' working, and
        for those the box is the whole-file one anyway.

        outInfo, when given, records whether the box needs no further validation. 'vraytools' has
        already run the same check over the box it writes, and re-running it here against filtered
        geometry would reject a perfectly good box for being larger than what is left of the mesh.
    """
    if (bounds := extras.get('bounds')) is not None and len(bounds['vertices']) >= 8:
        if outInfo is not None:
            outInfo['boxTrusted'] = True
        return bounds['vertices'][:8]

    # There may be more then 1 bounding boxes, get the first one. We only need it in order
    # to position the added vertices for the basis, so this should be fine.
    assert len(bboxData['vertices']) >= 8
    return bboxData['vertices'][:8]


def _constructPointPreview(proxyPropGroup: dict, meshFilePath: str, isProxy: bool, outObjectNames: list = None,
                           outShaders: list = None, visibility: tuple[list[str], int] = None,
                           outInfo: dict = None):
    """ Fill mesh data for a point preview """
    proxyPropGroup['num_preview_faces'] = 0

    extras = {}
    bboxData, err = _generatePreview(meshFilePath, 'Boxes', 0, int(proxyPropGroup.flip_axis), isProxy, extras,
                                     visibility)

    if not bboxData:
        return None, None, err

    if outObjectNames is not None and 'objectNames' in extras:
        outObjectNames[:] = extras['objectNames']

    if outShaders is not None and 'shaders' in extras:
        outShaders[:] = extras['shaders']

    boxVertices = _anchorBox(bboxData, extras, outInfo)

    # In Point mode we need to have at least 4 vertices in order to be able to compute
    # the applied transforms on the preview object. We select them from the bounding box points
    # but if the mesh has no faces, the vertices will be shown in the selection outline of the object.
    # If the mesh has any faces however, Blender will outline them and not outline the
    # free vertices. Construct a very small cube with proper faces to 'capture'
    # the selection and look as a point.
    #
    # One cube per box, so a file whose objects the dump reported separately gets a point at each of
    # them rather than a single point for the whole proxy - and the hidden ones are already absent
    # from the boxes.
    #
    # Only when the dump wrote a 'bounds' entry, which is what says its 'boxes' are per-object. A
    # VRayScene dump and an older 'vraytools' report one box for everything, and that one has always
    # produced the single point this used to draw.
    boxCorners = bboxData['vertices'] if ('bounds' in extras) else bboxData['vertices'][:8]
    cubeVertexArrays = []
    cubeFaceArrays = []

    for first in range(0, (len(boxCorners) // 8) * 8, 8):
        center = Vector(np.mean(boxCorners[first:first + 8], axis=0))
        cubeVertices, cubeFaces = _constructCube(0.00001, center)

        # Rebase this cube's faces onto the vertices already collected.
        cubeFaceArrays.append(np.asarray(cubeFaces) + sum(len(v) for v in cubeVertexArrays))
        cubeVertexArrays.append(np.asarray(cubeVertices))

    if cubeVertexArrays:
        allCubeVertices = np.concatenate(cubeVertexArrays, axis=0)
        allCubeFaces = np.concatenate(cubeFaceArrays, axis=0)
    else:
        # Everything is hidden. The anchor points below are all that is left, and they are what
        # keeps the proxy object where it was.
        allCubeVertices = np.zeros((0, 3), dtype=np.float32)
        allCubeFaces = np.zeros((0, 3), dtype=np.int32)

    bboxData['vertices'] = np.concatenate((allCubeVertices, boxVertices), axis=0)
    bboxData['faces'] = allCubeFaces

    return bboxData, boxVertices, None


def _constructPreview(proxyPropGroup: dict, meshFilePath: str, isProxy: bool, resetNumFaces=False,
                      outObjectNames: list = None, outShaders: list = None,
                      visibility: tuple[list[str], int] = None, outInfo: dict = None):
    """ Fill mesh data for a non-point preview """

    numFaces = proxyPropGroup.num_preview_faces if not resetNumFaces else getPropertyDefaultValue(proxyPropGroup, 'num_preview_faces')

    extras = {}
    meshData, err = _generatePreview(meshFilePath, proxyPropGroup.previewType, numFaces,
                                     int(proxyPropGroup.flip_axis), isProxy, extras, visibility)
    if err:
        return None, None, err

    if len(meshData['faces']) == 0 and visibility is None:
        # With a visibility list in play an empty mesh is a legitimate answer - everything in the
        # file is hidden - and the preview becomes the anchor points alone.
        return None, None, 'V-RayProxy import failed - mesh has no faces'

    if outObjectNames is not None and 'objectNames' in extras:
        outObjectNames[:] = extras['objectNames']

    if outShaders is not None and 'shaders' in extras:
        outShaders[:] = extras['shaders']

    if proxyPropGroup.previewType == 'Boxes':
        bboxData = meshData
    elif 'bounds' in extras:
        # The dump already carried the only box the anchor needs, so there is nothing more to read.
        # This is what keeps 'Full' to a single read of the file: its dump writes no per-object
        # boxes, and asking for them would now mean a second *full* read.
        bboxData = extras['bounds']
    elif (bboxData := extras.get('boxes')) is None:
        # The dump did not carry the boxes: a VRayScene, or an older 'vraytools' that writes only
        # the geometry. Read them separately, as this always used to.
        bboxExtras = {}
        bboxData, err = _generatePreview(meshFilePath, 'Boxes', 0, int(proxyPropGroup.flip_axis), isProxy,
                                         bboxExtras, visibility)
        if err:
            return None, None, err

        if (bounds := bboxExtras.get('bounds')) is not None:
            extras['bounds'] = bounds

    return meshData, _anchorBox(bboxData, extras, outInfo), None



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
            # The slot index is the shader ID itself, not the shader's position in the list:
            # _addMaterialSlotsForProxyShaders() fills the gaps with empty slots, and the exporter
            # numbers the MtlMulti 'ids_list' by slot index. Compacting the IDs here instead would
            # show a face with a different material than it renders with, whenever they have gaps.
            matArray = np.clip(materialIDs, 0, numSlots - 1)
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

    # The same dump that produces the preview also carries the names of the objects in the file and
    # its shader sets, so the object hierarchy can be filled from it instead of running the tool a
    # second time.
    objectNames = []
    shaders = []

    # Leave out the objects the Object Hierarchy hides. Only once the rows belong to the file being
    # loaded: on a new file they have not been read yet, and filtering with the previous file's list
    # would hide objects by a name collision. setObjectNames() below is what fills them in, after
    # which every later reload of this file is filtered.
    visibility = None
    hierarchy = getattr(geomMeshFile, 'object_hierarchy', None)
    if (hierarchy is not None) and (not hierarchy.needsRebuildFor(absFilePath)):
        visibility = hierarchy.getVisibilityList()

    previewInfo = {}

    meshData, boxVertices, err = _constructPointPreview(geomMeshFile, absFilePath, isProxy=True,
                                                        outObjectNames=objectNames, outShaders=shaders,
                                                        visibility=visibility, outInfo=previewInfo) \
                                    if geomMeshFile.previewType == 'Point' \
                                    else _constructPreview(geomMeshFile, absFilePath, isProxy=True,
                                                           resetNumFaces=isNewMesh,
                                                           outObjectNames=objectNames, outShaders=shaders,
                                                           visibility=visibility, outInfo=previewInfo)

    if err:
        return err

    if len(meshData['vertices']) == 0 and visibility is None:
        # An empty preview is expected when the visibility list hides everything in the file. Only
        # an unfiltered one that comes back empty says something is wrong with the file.
        return f"V-Ray Proxy object has no vertices. Loaded from {absFilePath}"

    # Done here as well as inside _computeBasisMatrix(), because the geometry's centre is taken
    # from the same box a few lines below. Skipped for a box the tool has already vouched for -
    # see _anchorBox().
    trustBox = previewInfo.get('boxTrusted', False)
    if not trustBox:
        boxVertices = _sanitizeBBox(boxVertices, meshData['vertices'])

    # The new preview object's anchor points may not be the same as the old ones.
    # Bring them to the coordinate system of the original object
    baseMatrix, pointIndices, newVertices = _computeBasisMatrix(meshData['vertices'], boxVertices,
                                                                sanitizeBBox=not trustBox)
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

    # Fill the Object Hierarchy from the names this dump already returned. An older 'vraytools'
    # does not write them, in which case the list is left alone and read on demand, as before.
    #
    # Only when the list has not been read from this file yet. This runs on every preview reload -
    # a preview type or face count change, not just a new file - and setObjectNames() rebuilds the
    # rows from scratch, so doing it unconditionally would throw away the check state and the
    # per-object material overrides the user has set. Asked with the path being loaded rather than
    # the one on the property, since the import operators load the preview before assigning it.
    if objectNames and geomMeshFile.object_hierarchy.needsRebuildFor(absFilePath):
        geomMeshFile.object_hierarchy.setObjectNames(objectNames, shaders, absFilePath)

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


def createVRaySceneObject(context, absFilePath: str, useRelPath: bool):
    """ Create a V-Ray Scene object referencing absFilePath.

        Returns (object, None) on success, (None, error message) on failure.
    """
    name = f"VRayScene@{PurePath(absFilePath).stem}"

    mesh = bpy.data.meshes.new(name)
    obj = bpy.data.objects.new(name, mesh)
    obj.location = context.scene.cursor.location
    context.scene.collection.objects.link(obj)
    obj.vray.VRayAsset.assetType = blender_utils.VRAY_ASSET_TYPE["Scene"]

    vrayScene = obj.data.vray.VRayScene

    if err := loadVRayScenePreviewMesh(vrayScene, absFilePath):
        bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.meshes.remove(mesh)
        return None, err

    filePath = absFilePath

    if useRelPath and not blender_utils.isDefaultScene():
        if relFilePath := path_utils.tryGetRelativePath(absFilePath):
            filePath = relFilePath
        else:
            debug.report('INFO', "Cannot import V-Ray Scene with relative path, using absolute path instead")

    vrayScene['filepath'] = filePath
    blender_utils.selectObject(obj)
    return obj, None


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

        Each entry is [id][name length][name]

    Returns:
        list[dict]: Each entry has 'name' (str) and 'id' (int).
    """
    shaders = []
    offset = 0
    count = struct.unpack_from('I', rawData, offset)[0]
    offset += 4

    for _ in range(count):
        objectId = struct.unpack_from('i', rawData, offset)[0]
        offset += 4
        nameLen = struct.unpack_from('I', rawData, offset)[0]
        offset += 4
        name = rawData[offset:offset + nameLen].decode('utf-8')
        offset += nameLen
        shaders.append({'name': name, 'id': objectId})

    return shaders


def _parseObjectInfoBlock(rawData: bytes):
    """ Parse the 'o' channel byte block into a list of object descriptors.

    Each entry is [id][name length][name]

    Returns:
        list[dict]: Each entry has 'name' (str, the full '/'-separated path) and 'id' (int).
    """
    objectInfos = []
    offset = 0
    count = struct.unpack_from('I', rawData, offset)[0]
    offset += 4

    for _ in range(count):
        objectId = struct.unpack_from('i', rawData, offset)[0]
        offset += 4
        nameLen = struct.unpack_from('I', rawData, offset)[0]
        offset += 4
        name = rawData[offset:offset + nameLen].decode('utf-8')
        offset += nameLen
        objectInfos.append({'name': name, 'id': objectId})

    return objectInfos


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
    objectInfos = None

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
            case 'o':
                rawData = file.read(itemCount)
                objectInfos = _parseObjectInfoBlock(rawData)
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
        'object_infos'      : objectInfos,
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
