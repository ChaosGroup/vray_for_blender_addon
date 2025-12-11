import bpy
import os
from vray_blender.exporting.tools import isObjectVrayProxy
from vray_blender.vray_tools.vray_proxy import _getCenterOfProxyPreview


def run():
    for obj in bpy.data.objects:
        if isObjectVrayProxy(obj):
            geomMeshFile = obj.data.vray.GeomMeshFile
            filePath = bpy.path.abspath(geomMeshFile.file)
            if os.path.exists(filePath):
                obj.data.vray.GeomMeshFile["initial_preview_mesh_pos"] = _getCenterOfProxyPreview(filePath, geomMeshFile)


def check():
    return any(isObjectVrayProxy(obj) for obj in bpy.data.objects)
