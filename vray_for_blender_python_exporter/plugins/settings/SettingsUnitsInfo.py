
import bpy

from vray_blender.lib.defs import ExporterContext
from vray_blender.lib import export_utils
from vray_blender.lib import plugin_utils


plugin_utils.loadPluginOnModule(globals(), __name__)


def exportCustom(ctx: ExporterContext, pluginDesc):
    unit_settings = bpy.context.scene.unit_settings

    if unit_settings.system != 'NONE':
        pluginDesc.setAttribute('meters_scale', unit_settings.scale_length)
        photometricScale = pluginDesc.vrayPropGroup.photometric_scale * unit_settings.scale_length ** 2
        pluginDesc.setAttribute('photometric_scale', photometricScale)


    sceneFps = bpy.context.scene.render.fps / bpy.context.scene.render.fps_base
    pluginDesc.setAttribute('frames_scale', sceneFps)
    pluginDesc.setAttribute('seconds_scale', 1.0 / sceneFps)

    return export_utils.exportPluginCommon(ctx, pluginDesc)
