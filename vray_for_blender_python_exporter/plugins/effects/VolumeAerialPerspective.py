
from vray_blender.lib import export_utils, plugin_utils
from vray_blender.lib.defs import AttrPlugin, ExporterContext, PluginDesc
from vray_blender.lib.names import Names

plugin_utils.loadPluginOnModule(globals(), __name__)

from vray_blender.bin import VRayBlenderLib as vray

def exportCustom(ctx: ExporterContext, pluginDesc: PluginDesc):
    attrPlugin = AttrPlugin()
    
    if sun := pluginDesc.vrayPropGroup.sun:
        pluginName = Names.objectData(sun)
        attrPlugin = AttrPlugin(pluginName)

        # Export empty plugin because the plugin could be created later
        vray.pluginCreate(ctx.renderer, pluginName, 'SunLight')
    
    pluginDesc.setAttributes({
        'sun': attrPlugin,
    })

    return export_utils.exportPluginCommon(ctx, pluginDesc)