from vray_blender.lib import color_utils
from vray_blender.nodes.utils import getUpdateCallbackPropertyContext


def onUpdateColor(src, pluginType, attrName):
    """ Handler for the color/color_temperature selection common to all lights """

    propContext = getUpdateCallbackPropertyContext(src, pluginType)

    match propContext.get("color_mode"):
        case '0': # Color
            if attrName == 'color_colortex':
                color = propContext.get('color_colortex')
                newTemperature = color_utils.RGBToKelvin(color)
                propContext.set('temperature',newTemperature )

        case '1': # Temperature
            if attrName == 'temperature':
                temperature = propContext.get('temperature')
                newColor = color_utils.kelvinToRGB(temperature)
                propContext.safeSetColor('color_colortex', newColor)