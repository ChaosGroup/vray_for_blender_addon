
from vray_blender.lib.defs import ExporterContext, PluginDesc
from vray_blender.lib import export_utils
from vray_blender.lib import plugin_utils


plugin_utils.loadPluginOnModule(globals(), __name__)


def exportCustom(ctx: ExporterContext, pluginDesc: PluginDesc):
    assert ctx.interactive, "SettingsCurrentFrame plugin should only be exported for interactive rendering"
    pluginDesc.setAttribute("current_frame", ctx.currentFrame)

    return export_utils.exportPluginCommon(ctx, pluginDesc)
