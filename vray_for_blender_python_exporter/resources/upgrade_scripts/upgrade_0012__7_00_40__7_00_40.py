import bpy
import os
from vray_blender.exporting.tools import isObjectVrayProxy
from vray_blender.vray_tools.vray_proxy import _getDimsOfProxyPreview


def run():
    for obj in bpy.data.objects:
        if isObjectVrayProxy(obj):
            geomMeshFile = obj.data.vray.GeomMeshFile
            filePath = bpy.path.abspath(geomMeshFile.file)

            if os.path.exists(filePath):
                # _getDimsOfProxyPreview overcomes the issue with proxy objects with previewType=None
                geomMeshFile["initial_preview_dims"] = _getDimsOfProxyPreview(filePath, geomMeshFile)
            else:
                geomMeshFile["initial_preview_dims"] = obj.dimensions # Handling the case where the proxy needs re-linking

            for light in (c for c in obj.children if c.type == 'LIGHT'):
                light.data.vray.initial_proxy_light_pos = light.matrix_local.decompose()[0]


def check():
    return any(isObjectVrayProxy(obj) for obj in bpy.data.objects)
