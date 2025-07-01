from vray_blender.exporting.mtl_export import MtlExporter
from vray_blender.lib.defs import ExporterContext, PluginDesc, AttrPlugin
from vray_blender.lib import  export_utils, plugin_utils


plugin_utils.loadPluginOnModule(globals(), __name__)

def exportCustom(ctx: ExporterContext, pluginDesc: PluginDesc):
    propGroup = pluginDesc.vrayPropGroup
    
    if not (selectedMtls := propGroup.selected.getSelectedItems(ctx.ctx, 'materials')):
        return AttrPlugin()
    
    # RenderChannelMaterialSelect is a varaiant of RenderChannelColor
    pluginDesc.type = 'RenderChannelColor'
    
    if not propGroup.name:
        pluginDesc.setAttribute('name',  'MaterialSelect')

    attrPlugin = export_utils.exportPluginCommon(ctx, pluginDesc)
    
    # Set link to the the render channel in all selected materials
    for mtl in selectedMtls:
        if mtlPlugin := ctx.exportedMtls.get(mtl):
            plugin_utils.updateValue(ctx.renderer, mtlPlugin.name, 'channels', [attrPlugin])
    
    return attrPlugin


