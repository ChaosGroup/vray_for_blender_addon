
from vray_blender.lib.defs import ExporterContext
from vray_blender.lib import export_utils
from vray_blender.lib import plugin_utils


plugin_utils.loadPluginOnModule(globals(), __name__)


def exportCustom(ctx: ExporterContext, pluginDesc):
    propGroup = pluginDesc.vrayPropGroup

        # Set bucket height equal to its width
    pluginDesc.setAttribute('yc', propGroup.xc)
    return export_utils.exportPluginCommon(ctx, pluginDesc)
