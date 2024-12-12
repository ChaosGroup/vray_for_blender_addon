
from vray_blender.lib.defs import ExporterContext
from vray_blender.lib import export_utils
from vray_blender.lib import plugin_utils


plugin_utils.loadPluginOnModule(globals(), __name__)

def exportCustom(ctx: ExporterContext, pluginDesc):
    sampler = ctx.dg.scene.vray.SettingsImageSampler

    # Setting the corresponding noise threshold in SettingsImageSampler is not enough
    # for V-Ray. We need to set the same threshold in SettingsRTEngine for it to work.
    match sampler.type:
        case '1': # Bucket
            pluginDesc.setAttribute('noise_threshold', sampler.dmc_threshold)
        case '3': # Progressive
            pluginDesc.setAttribute('noise_threshold', sampler.progressive_threshold)

    return export_utils.exportPluginCommon(ctx, pluginDesc)
