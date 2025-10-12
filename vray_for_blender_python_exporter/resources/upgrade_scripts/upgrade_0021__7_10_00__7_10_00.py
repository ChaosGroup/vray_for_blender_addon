import bpy
from vray_blender.exporting.tools import isObjectVrayProxy


def run():
    bpy.context.scene.world.vray.RenderChannelDenoiser.name = 'Denoiser'


def check():
    return hasattr(bpy.context.scene.world, "vray") and bpy.context.scene.world.vray.RenderChannelDenoiser.name != 'Denoiser'
