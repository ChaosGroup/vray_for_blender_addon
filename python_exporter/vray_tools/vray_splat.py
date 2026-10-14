# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later


import os
from io import BufferedReader
from pathlib import PurePath

import numpy as np
from mathutils import Vector

from vray_blender import debug
from vray_blender.lib import path_utils, sys_utils
from vray_blender.lib.sys_utils import getAppSdkLibPath
from vray_blender.vray_tools.vray_proxy import PreviewAction, binRead, runVRayTools


# Loading of preview data for V-Ray Gaussian splat objects.
#
# This mirrors the V-Ray Proxy/Scene preview pipeline in vray_proxy.py: the 'vraytools' utility
# dumps a simplified binary file from the .ply, which is then read into Blender-compatible data
# (point positions + colors + bounding box). The data is consumed by utils.splat_preview to draw
# the viewport point cloud. The actual render is produced by V-Ray, which reads the .ply directly.


def _dumpSplatFile(splatFile: str, binFile: str):
    """ Run vraytools utility to dump the preview points (positions and average colors) of a
        Gaussian splat file (.ply) into a simplified binary format which can then be easily
        loaded by the Python code.

    Args:
        splatFile (str): path to a .ply Gaussian splat file
        binFile   (str): path to the resulting binary file
    Returns:
        str | None: Error message on failure, None on success
    """

    vrayToolsApp = path_utils.getBinTool(sys_utils.getPlatformName("vraytools"))

    cmd = [vrayToolsApp]
    cmd.extend(['-vrayLib', getAppSdkLibPath()])
    cmd.extend(['-action', PreviewAction.GaussianPreview])
    cmd.extend(['-input', splatFile])
    cmd.extend(['-output', binFile])

    debug.printInfo(f"Running Gaussian splat preview tool: {' '.join(cmd)}")

    try:
        result = runVRayTools(cmd)
    except Exception as ex:
        return f"Failed to launch Gaussian preview tool '{vrayToolsApp}': {ex}"

    if result.returncode != 0:
        debug.printError(result.stdout)
        debug.printError(result.stderr)
        return f"Error generating Gaussian preview file: {result.returncode}"

    if not os.path.isfile(binFile):
        return "Error generating Gaussian preview file: file is missing"

    return None


def _readSplatObjectFromBinFile(file: BufferedReader):
    """ Read a single splat object (positions, colors and primitive count) from a .vrbin file.

    Returns:
        tuple(np.ndarray, np.ndarray, int): (N, 3) positions, (N, 4) RGBA colors, primitive count
        (0 if the file has no count channel).
    """
    def _readString(file):
        length = binRead(file, 'I', 1)
        return binRead(file, 's', length)

    _readString(file)                        # object name (unused)
    tocSize = binRead(file, "I", 1)          # uint32, number of channels

    chunks = []
    for _ in range(tocSize):
        itemType  = binRead(file, 'c', 1)    # char, channel type ('p', 'c' or 'n')
        itemCount = binRead(file, 'Q', 1)    # uint64, number of items
        binRead(file, 'Q', 1)                # uint64, data offset (channels are read in order)
        chunks.append((itemType, itemCount))

    coords = np.empty((0, 3), dtype=np.float32)
    colors = np.empty((0, 4), dtype=np.float32)
    primitives = 0

    def _readVectors(itemCount):
        floatArray = binRead(file, 'f', itemCount * 3) if itemCount else ()  # 3 floats per item
        return np.asarray(floatArray, dtype=np.float32).reshape(-1, 3)

    for itemType, itemCount in chunks:
        match itemType:
            case 'p':
                coords = _readVectors(itemCount)
            case 'c':
                arr = _readVectors(itemCount)
                colors = np.ones((arr.shape[0], 4), dtype=np.float32)  # opaque alpha
                colors[:, :3] = arr
            case 'n':
                primitives = binRead(file, 'Q', itemCount)
            case _:
                raise Exception(f"Unsupported Gaussian preview channel type '{itemType}'")

    return coords, colors, primitives


def readBinSplatFile(filePath: str):
    """ Read a .vrbin file produced by the vraytools 'DumpGaussian' action into
        (coords, colors, primitives). """
    assert os.path.isfile(filePath)

    with open(os.path.expanduser(filePath), "rb") as file:
        numObjects = binRead(file, 'I', 1)   # uint32
        assert numObjects == 1, "Expected a single object in the Gaussian preview file"
        return _readSplatObjectFromBinFile(file)


def loadVRaySplatPreview(filePath: str):
    """ Generate and read the preview data for a Gaussian splat .ply file.

        Mirrors loadVRayProxyPreviewMesh/loadVRayScenePreviewMesh: dumps a temporary .vrbin file
        with the vraytools utility, reads it back, and removes it. The result is derived data and
        is never stored in the .blend.

    Returns:
        dict | None: { 'coords': (N,3) float32, 'colors': (N,4) float32,
                       'bbox': (Vector, Vector) | None, 'primitives': int },
                     or None on failure. 'primitives' counts the model, not the preview points.
    """
    if not filePath or not os.path.isfile(filePath):
        return None

    binFile = str(PurePath(path_utils.getV4BTempDir(), PurePath(filePath).stem).with_suffix('.vrbin'))
    if err := _dumpSplatFile(filePath, binFile):
        debug.printError(err)
        return None

    try:
        coords, colors, primitives = readBinSplatFile(binFile)
    except Exception as ex:
        debug.printExceptionInfo(ex, f"Failed to read Gaussian preview file '{binFile}'")
        return None
    finally:
        if os.path.isfile(binFile):
            os.unlink(binFile)

    bbox = (Vector(coords.min(axis=0)), Vector(coords.max(axis=0))) if coords.shape[0] > 0 else None
    return {'coords': coords, 'colors': colors, 'bbox': bbox, 'primitives': primitives}
