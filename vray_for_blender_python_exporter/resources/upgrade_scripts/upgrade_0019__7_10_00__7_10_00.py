import bpy

def run():
    denoiser = bpy.context.scene.world.vray.RenderChannelDenoiser
    nodeDenoiser = bpy.context.scene.world.vray.VRayRenderChannels.VRayNodeRenderChannelDenoiser
    denoiser.viewport_enabled = nodeDenoiser.enabled and denoiser.enabled
    denoiser.viewport_engine = denoiser.engine

def check():
    return hasattr(bpy.context.scene.world, "vray") and bpy.context.scene.world.vray.VRayRenderChannels.VRayNodeRenderChannelDenoiser.enabled
