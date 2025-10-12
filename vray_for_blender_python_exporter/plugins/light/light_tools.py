from vray_blender.lib import color_utils
from vray_blender.nodes.utils import getUpdateCallbackPropertyContext


def onUpdateColorTemperature(src, pluginType, attrName, colorModeAttrName='color_mode', colorAttrName='color', combinedColorAttrName='color_colortex'):
    """ Handler for the color/color_temperature selection common to all lights """

    assert attrName in (colorModeAttrName, 'temperature')

    propContext = getUpdateCallbackPropertyContext(src, pluginType)

    if propContext.get(colorModeAttrName) == '1': # Temperature
        temperature = propContext.get('temperature')
        newColor = color_utils.kelvinToRGB(temperature)
        oldColor = propContext.get(colorAttrName)

        if newColor != oldColor:
            propContext.safeSetColor(combinedColorAttrName, newColor)