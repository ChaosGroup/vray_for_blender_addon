# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy

from vray_blender.lib import plugin_utils
from vray_blender.lib.blender_utils import getShadowAttr, updateShadowAttr
from vray_blender.nodes.utils import isAutoConnectEnabled

plugin_utils.loadPluginOnModule(globals(), __name__)

_IES_UNITS_FEET = 1
_FEET_TO_METERS = 0.3048


def nodeUpdate(node: bpy.types.Node):
    if node.mute:
        node.mute = False


def _readIesOpeningSize(filePath: str):
    """ Return the (width, length, height) of the luminous opening in metres as written in an IES
        profile, or None if the profile cannot be parsed. The LM-63 signs are preserved: a round
        opening is written as a negative dimension.
    """
    try:
        with open(filePath, 'r', errors='replace') as iesFile:
            _, sep, afterTilt = iesFile.read().partition('TILT=')

        if not sep:
            return None

        tiltMode, _, photometricData = afterTilt.partition('\n')
        # Some profiles separate the values with commas instead of whitespace.
        values = photometricData.replace(',', ' ').split()

        if tiltMode.strip().upper() == 'INCLUDE':
            # Skip the inlined tilt block: orientation, pair count, then that many angles and factors.
            values = values[2 + (2 * int(float(values[1]))):]

        # The photometric block starts with lamp count, lumens, candela multiplier, vertical angle
        # count, horizontal angle count, photometric type, units type, width, length, height.
        units, width, length, height = (float(values[i]) for i in (6, 7, 8, 9))
    except (OSError, ValueError, IndexError):
        return None

    scale = _FEET_TO_METERS if int(units) == _IES_UNITS_FEET else 1.0
    return width * scale, length * scale, height * scale


def onUpdateIesFile(src, context: bpy.types.Context, attrName: str):
    """ Seed the light shape size from the newly selected profile, the way V-Ray for Maya does.
        The V-Ray defaults for the dimensions are 0, and a shape of size 0 renders as a point
        light, so without this every shape picked in the Light Shape rollout is a no-op (VBLD-1270).
    """
    assert attrName == 'ies_file'

    if not isAutoConnectEnabled():
        return

    if getShadowAttr(src, 'ies_file') == src.ies_file:
        return

    updateShadowAttr(src, 'ies_file')

    if (size := _readIesOpeningSize(bpy.path.abspath(src.ies_file))) is None:
        return

    width, length, height = size

    # A round opening is encoded as a negative dimension - the width for circles, spheres and
    # vertical cylinders, the height for the horizontal ones.
    if width < 0.0:
        diameter = -width
    elif height < 0.0:
        diameter = -height
    else:
        diameter = max(abs(width), abs(length))

    src.ies_light_width    = abs(width)
    src.ies_light_length   = abs(length)
    src.ies_light_height   = abs(height)
    src.ies_light_diameter = diameter


# The shapes which use each of the light shape dimensions, mirrored from the shape UI of the 3ds Max
# VRayIES light. That is the 'enable=' UI guides of the LightIES plugin (which the descriptor
# generator does not carry over), plus the height on the two ellipse shapes, which Max enables and
# the guides do not. Shapes -1 (From IES File) and 0 (Point) use no dimension at all.
_SHAPES_USING_WIDTH    = {'1', '6', '7', '8', '9', '10', '11', '12', '13', '14', '15', '16'}
_SHAPES_USING_LENGTH   = {'1', '5', '7', '8', '9', '10', '11', '12', '13', '16'}
_SHAPES_USING_HEIGHT   = {'4', '7', '8', '9', '10', '11', '12', '13', '15', '16'}
_SHAPES_USING_DIAMETER = {'2', '3', '4', '5', '6'}


def isShapeWidthActive(propGroup, node):
    return propGroup.ies_light_shape in _SHAPES_USING_WIDTH

def isShapeLengthActive(propGroup, node):
    return propGroup.ies_light_shape in _SHAPES_USING_LENGTH

def isShapeHeightActive(propGroup, node):
    return propGroup.ies_light_shape in _SHAPES_USING_HEIGHT

def isShapeDiameterActive(propGroup, node):
    return propGroup.ies_light_shape in _SHAPES_USING_DIAMETER
