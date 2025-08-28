from vray_blender.exporting import light_export
from vray_blender.exporting.tools import getInputSocketByAttr
from vray_blender.lib.defs import ExporterContext, PluginDesc, AttrPlugin
from vray_blender.lib import  export_utils, plugin_utils
from vray_blender.nodes.tools import isInputSocketLinked

plugin_utils.loadPluginOnModule(globals(), __name__)

# Map from light select mode to the name of the channel property that
# has to be exported for Light plugins
_CHANNELS_PROPERTY_MAP = {
    '0':  'channels',             # direct diffuse and specula
    '1':  'channels_raw',         # direct raw
    '2':  'channels_diffuse',     # direct diffuse
    '3':  'channels_specular',    # direct specular
    '4':  'channels_full',        # full
    '5':  'channels',             # indirect diffuse and specular
    '6':  'channels',             # indirect diffuse
    '7':  'channels',             # indirect specular
    '8':  'channels',             # subsurface
    '9':  'channels',             # light path expression
    '12': 'channels',             # direct diffuse shadow
    '13': 'channels'              # direct specular shadow
}


def exportCustom(ctx: ExporterContext, pluginDesc: PluginDesc):
    
    if not (ctx.production or ctx.iprVFB):
        return AttrPlugin()
    
    # This method may be called to export LightSelect world-tree nodes or light select
    # channels created to support the various light select modes.
    propGroup = pluginDesc.vrayPropGroup
    isNode = bool(propGroup)
    
    if isNode: 
        # Plugins are exported for RenderChannelLightSelect nodes only if light mix
        # is active and the light select mode is set to 'manual'
        if ctx.activeLightMixNode and (ctx.activeLightMixNode.RenderChannelLightMix.mode != 'manual'):
            return AttrPlugin()
        
        # Get a list of lights that should be included in this light select render channel 
        includedLights = []

        if (node := pluginDesc.node) and isInputSocketLinked(getInputSocketByAttr(node, 'lights')):
            # The LightSelect node is linked to a selector node. The selected lights have already
            # been collected in its 'lights' property by the node tree export procedure
            selectedLightPlugins = pluginDesc.getAttribute('lights')
            includedLights = [p.auxData['object'] for p in selectedLightPlugins]
        else:
            # No Selector node is connected to the LightSelect node. Export LightSelect node's own data
            includedLights = propGroup.light_selector.getSelectedItems(ctx.ctx, 'objects')
            
        # Reference the LightSelect in the lights affecter by it
        channelsPropName = _CHANNELS_PROPERTY_MAP[propGroup.light_select_mode]
        lsPlugin = export_utils.exportPluginCommon(ctx, pluginDesc)

        for objLight in includedLights:
            light_export.linkLightToRenderChannel(ctx, objLight, channelsPropName, lsPlugin)

    else:
        # RenderChannelLightSelect without a node. The calling code has the responsibility
        # to create the links between light objects and light select render channels.
        lsPlugin = export_utils.exportPluginCommon(ctx, pluginDesc)
    
    return lsPlugin