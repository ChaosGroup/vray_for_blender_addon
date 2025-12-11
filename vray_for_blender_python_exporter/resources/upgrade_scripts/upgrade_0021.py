import bpy

def run():
    for scene in bpy.data.scenes:
        scene.world.vray.RenderChannelDenoiser.name = 'Denoiser'


def check():
    for scene in bpy.data.scenes:
        if hasattr(scene.world, "vray") and scene.world.vray.RenderChannelDenoiser.name != 'Denoiser':
            return True
    return False
