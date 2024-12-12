from vray_blender.lib.defs import  ExporterContext, PluginDesc
from vray_blender.lib import plugin_utils, export_utils

plugin_utils.loadPluginOnModule(globals(), __name__)


def exportCustom(ctx: ExporterContext, pluginDesc: PluginDesc):
    attrPlugin = export_utils.exportPluginCommon(ctx, pluginDesc)
    
    # Suppress the export of the actual output socket name as we are using the
    # mode parameter to tell V-Ray which output socket to use.
    attrPlugin.useDefaultOutput()

    return attrPlugin