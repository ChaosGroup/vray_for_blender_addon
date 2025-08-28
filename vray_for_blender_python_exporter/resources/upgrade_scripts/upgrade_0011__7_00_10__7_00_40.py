import bpy
from vray_blender.exporting.tools import isObjectVrayProxy


def run():
    for obj in bpy.data.objects:
        if isObjectVrayProxy(obj):
            for light in (c for c in obj.children if c.type == 'LIGHT'):
                light.data.vray.initial_proxy_light_pos = light.matrix_local.decompose()[0]


def check():
    return any(isObjectVrayProxy(obj) and any(c.type=="LIGHT" for c in obj.children) for obj in bpy.data.objects)
