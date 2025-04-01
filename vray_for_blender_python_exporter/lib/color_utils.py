
import mathutils
import math

from vray_blender import debug
from vray_blender.lib.defs import AColor
from vray_blender.lib.lookup_tables import KELVIN_TO_COLOR_LUT


# Converts kelvin temperature to color
#
def kelvinToRGB(temperature):
    assert 800 <= temperature and temperature <= 12000
    return mathutils.Color(KELVIN_TO_COLOR_LUT[int(temperature)])



def toRGB(color):
    """ Convert a 3 or 4-element array-like object to mathutlils.Color """
    assert len(color) in (3,4)
    return mathutils.Color(color[:3])


def toRGBA(color):
    """ Convert a 3 or 4-element array-like object to AColor """
    assert len(color) in (3,4)
    return AColor((color[0], color[1], color[2]) + (color[3] if len(color) == 4 else 1.0,))


def opacityToTransparency(clr):
    """ Convert per-color opacity to per-color transparency in the same format (RGB or RGBA) """
    match len(clr):
        case 4:
            return AColor((1.0 - clr[0], 1.0 - clr[1], 1.0 - clr[2], 1.0 - clr[3]))
        case 3: 
            return mathutils.Color((1.0 - clr[0], 1.0 - clr[1], 1.0 - clr[2]))
        case _:
            debug.printError(f"opacityToTransparency: Invalid color size: {len(clr)}")

