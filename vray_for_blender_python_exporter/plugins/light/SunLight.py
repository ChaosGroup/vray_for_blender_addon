from vray_blender.lib import color_utils, plugin_utils
from vray_blender.nodes.utils import getUpdateCallbackPropertyContext
from vray_blender.lib.defs import  ExporterContext, PluginDesc
from vray_blender.lib import plugin_utils, export_utils


plugin_utils.loadPluginOnModule(globals(), __name__)


def onUpdateAttribute(src, context, attrName):
    propContext = getUpdateCallbackPropertyContext(src, "SunLight")

    match propContext.get("color_temp_mode"):
        case '0': # Color
            if attrName == 'filter_color':
                color = propContext.get('filter_color')
                newTemperature = color_utils.RGBToKelvin(color)
                propContext.set('temperature',newTemperature )

        case '1': # Temperature
            if attrName == 'temperature':
                temperature = propContext.get('temperature')
                newColor = color_utils.kelvinToRGB(temperature)
                propContext.safeSetColor('filter_color', newColor)



def exportCustom(ctx: ExporterContext, pluginDesc: PluginDesc):
    if ctx.commonSettings.isGpu and ctx.interactive:
        # During GPU IPR, changes made to cloud properties are not applied automatically.
        # Toggling SunLight.enabled forces these changes to apply.
        sunLightEnabled = pluginDesc.node.SunLight.enabled if pluginDesc.node else pluginDesc.vrayPropGroup.enabled
        plugin_utils.updateValue(ctx.renderer, pluginDesc.name, "enabled", not sunLightEnabled)

    return export_utils.exportPluginCommon(ctx, pluginDesc)