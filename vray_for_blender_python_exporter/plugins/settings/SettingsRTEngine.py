
from vray_blender.lib import export_utils, plugin_utils
from vray_blender.lib.defs import ExporterContext, AttrPlugin
from vray_blender.lib.sys_utils import isGPUEngine


plugin_utils.loadPluginOnModule(globals(), __name__)

def exportCustom(ctx: ExporterContext, pluginDesc):
    scene = ctx.dg.scene
    sampler = scene.vray.SettingsImageSampler
    settingsRTEngine = scene.vray.SettingsRTEngine

    # Some image sampler settings should be set in both SettingsImageSampler and in
    # SettingsRTEngine in order to work on both CPU and GPU.
    match sampler.type:
        case '1': # Bucket
            pluginDesc.setAttribute('noise_threshold', sampler.dmc_threshold)
        case '3': # Progressive
            pluginDesc.setAttribute('noise_threshold', sampler.progressive_threshold)

    pluginDesc.setAttribute('max_render_time', sampler.progressive_maxTime)

    # Similar to other DCC when OOC is enabled force full size textures.
    pluginDesc.setAttribute('opencl_resizeTextures', settingsRTEngine.opencl_resizeTextures if not settingsRTEngine.out_of_core_textures else '0')

    if not isGPUEngine(scene):
        pluginDesc.resetAttribute('max_sample_level')

    return export_utils.exportPluginCommon(ctx, pluginDesc)
