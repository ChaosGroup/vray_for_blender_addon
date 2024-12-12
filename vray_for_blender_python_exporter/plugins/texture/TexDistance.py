from vray_blender.exporting.tools import GEOMETRY_OBJECT_TYPES
from vray_blender.lib.defs import ExporterContext, PluginDesc, AttrPlugin
from vray_blender.lib import  export_utils, plugin_utils


plugin_utils.loadPluginOnModule(globals(), __name__)

def exportCustom1(ctx: ExporterContext, pluginDesc: PluginDesc):
    
    propGroup = pluginDesc.vrayPropGroup
    if not (selectedObjects := propGroup.object_selector.getSelectedItems(ctx.ctx, 'objects')):
        return AttrPlugin()
    
    pluginDesc.setAttributes({
        'objects': selectedObjects
    })

    return export_utils.exportPluginCommon(ctx, pluginDesc)