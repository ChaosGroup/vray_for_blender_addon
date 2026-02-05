import bpy
import os
from mathutils import Vector
import numpy as np

from vray_blender import debug
from vray_blender.exporting.tools import isObjectVrayProxy
from vray_blender.vray_tools.vray_proxy import _generateProxyPreview


def _getCenterOfProxyPreview(filePath, geomMeshFile):
    """ Get the center of the bounding box of the proxy mesh preview.
    """
    meshData, err = _generateProxyPreview(filePath, 'Boxes', 0, int(geomMeshFile.flip_axis))
    if err:
        debug.printError(f"Proxy preview mesh center calculation error: {err}")
        return Vector((0.0, 0.0, 0.0))

    return Vector(np.mean(meshData['vertices'], axis=0)) * geomMeshFile.scale

def run():
    for obj in bpy.data.objects:
        if isObjectVrayProxy(obj):
            geomMeshFile = obj.data.vray.GeomMeshFile
            filePath = bpy.path.abspath(geomMeshFile.file)
            if os.path.exists(filePath):
                obj.data.vray.GeomMeshFile["initial_preview_mesh_pos"] = _getCenterOfProxyPreview(filePath, geomMeshFile)


def check():
    return any(isObjectVrayProxy(obj) for obj in bpy.data.objects)
