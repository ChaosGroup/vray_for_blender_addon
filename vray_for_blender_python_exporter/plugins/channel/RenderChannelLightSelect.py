from vray_blender.exporting import light_export
from vray_blender.exporting.tools import getInputSocketByAttr
from vray_blender.lib.defs import ExporterContext, PluginDesc, AttrPlugin
from vray_blender.lib import  export_utils, plugin_utils, lib_utils


plugin_utils.loadPluginOnModule(globals(), __name__)

def exportCustom(ctx: ExporterContext, pluginDesc: PluginDesc):
 
    # Property group will be empty if the LightSelect plugin is not backed by a node. 
    # In this case, there is no need to process the lights list as it will be
    # calculated and exported by the caller based on the specific purpose of the 
    # LightSelect.
    if propGroup := pluginDesc.vrayPropGroup:
        
        # If there is an active LightMix, user-created light select channels are only exported in 'manual' light mix mode
        if (lightMixNode := ctx.activeLightMixNode) and (lightMixNode.RenderChannelLightMix.mode != 'manual'):
            return
        
        selectedLights = []

        if (node := pluginDesc.node) and getInputSocketByAttr(node, 'lights').is_linked:
            # The LightSelect node is linked to a selector node. The selected lights have already
            # been collected in its 'lights' property by the node tree export procedure
            selectedLightPlugins = pluginDesc.getAttribute('lights')
            selectedLights = [p.auxData['object'] for p in selectedLightPlugins]
        else:
            # No Selector node is connected to the LightSelect node. Export LightSelect node's own data
            selectedLights = propGroup.light_selector.getSelectedItems(ctx.ctx, 'objects')
            
        for objLight in selectedLights:
            pluginName = light_export._getPluginName(objLight)
            ctx.linkPluginToRenderChannel(pluginName, 'channels_full', AttrPlugin(pluginDesc.name))

    return export_utils.exportPluginCommon(ctx, pluginDesc)