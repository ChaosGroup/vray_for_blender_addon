from vray_blender.lib.defs import ExporterContext, PluginDesc
from vray_blender.lib import  export_utils, plugin_utils


plugin_utils.loadPluginOnModule(globals(), __name__)

def exportCustom(ctx: ExporterContext, pluginDesc: PluginDesc):
    pluginDesc.setAttribute("color_mode", int(pluginDesc.vrayPropGroup.color_mode_enum))
    
    return export_utils.exportPluginCommon(ctx, pluginDesc)