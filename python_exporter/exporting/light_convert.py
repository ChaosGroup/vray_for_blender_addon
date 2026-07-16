# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

""" Conversion of native Blender lights to V-Ray lights.

    This is the 'export' half of the light conversion (mirroring how cycles_export turns a
    Cycles material into V-Ray plugin descriptions): it reads a native Blender light datablock
    and produces a single V-Ray light plugin description, in the same {ID, Name, Attributes} dict
    shape that the importer (nodes.importing) consumes.
"""

from math import radians, cos

from mathutils import Color

from vray_blender.lib import lib_utils, color_utils


# Angular diameter of the reference sun used by V-Ray's SunLight (size_multiplier == 1.0).
# Blender's default sun angle is the same ~0.526 degrees.
_REFERENCE_SUN_ANGLE = radians(0.526)

# Intensity calibration factors, measured by rendering single lights in both engines and
# matching the V-Ray result to Cycles (V-Ray default sky/world; difference-isolated, linear).
#
# SunLight: V-Ray's physical sky-sun at intensity_multiplier == 1.0 is ~90x brighter than a
# Blender sun of energy 1.0 (W/m^2), so the converted sun must be scaled down by ~1/90.
_VRAY_SUN_ENERGY_SCALE = 1.0 / 90.0
#
# LightOmni / LightRectangle: V-Ray radiant-power ('Watts') lights render ~1.46x dimmer than
# the same-wattage Cycles light, so they need a small boost.
_VRAY_RADIANT_SCALE = 1.46
#
# LightSpot: V-Ray concentrates the spot power into the cone, so its on-axis peak scales as
# ~1/cone-solid-angle, while Cycles keeps the spot's peak constant (= a point of that wattage).
# A single factor can't reconcile them, but a cone-angle-aware scale matches the peak (which is
# what otherwise blows out narrow spots). Measured fit: scale = K * (1 - cos(half_cone_angle)).
_VRAY_SPOT_CONE_K = 0.733


def _convertedColor(light):
    """ Light color, tinted by the blackbody temperature when enabled (matches Cycles). """
    col = Color(light.color[:3])

    if getattr(light, 'use_temperature', False):
        # kelvinToRGB only supports the 800 - 12000 K range; clamp to it.
        kelvin = min(max(int(light.temperature), 800), 12000)
        bb = color_utils.kelvinToRGB(kelvin)
        col = Color((col.r * bb.r, col.g * bb.g, col.b * bb.b))

    return col


def exportBlenderLight(light):
    """ Convert a native Blender light datablock into a V-Ray light plugin description.

        Returns a dict {'ID': pluginType, 'Name': light.name, 'Attributes': {...}} or None if
        the light type is not supported.
    """
    if light.type not in lib_utils.BlenderToVrayLightType:
        return None

    vrayType   = lib_utils.BlenderToVrayLightType[light.type]
    pluginType = lib_utils.LightTypeToPlugin[vrayType]

    col = _convertedColor(light)

    # Radiant power in Watts. Blender's 'exposure' is an additional power scale in stops.
    # NOTE: this assumes light.normalize == True (the default), where 'energy' is the total
    # radiant power and maps 1:1 to V-Ray's 'Watts' units. With normalize == False Blender
    # skips the per-area division while V-Ray always normalizes by area, so the match is only
    # approximate in that case.
    power = light.energy * (2.0 ** getattr(light, 'exposure', 0.0))

    attrs = {
        'enabled': True,
        'shadows': light.use_shadow,
    }

    if pluginType == 'SunLight':
        # V-Ray's SunLight is a physical sky model; calibrate its multiplier to Blender's
        # irradiance (see _VRAY_SUN_ENERGY_SCALE). The match is still approximate as it
        # assumes V-Ray's default sky model / turbidity.
        attrs['filter_color']         = col
        attrs['intensity_multiplier'] = power * _VRAY_SUN_ENERGY_SCALE
        # Match the visible sun-disc size to Blender's angular diameter.
        attrs['size_multiplier']      = light.angle / _REFERENCE_SUN_ANGLE
        # A Blender sun has no camera-visible disc; hide V-Ray's so it matches.
        attrs['invisible']            = True
    else:
        # The light's color is driven by the 'color_colortex' meta property, not the raw
        # 'color' attribute - the latter is suppressed from export (it backs color_colortex).
        # In node mode the value also has to reach the node's color socket, which it only does
        # when set through color_colortex. See nodes.utils.getNonExportablePluginProperties.
        attrs['color_colortex'] = (col.r, col.g, col.b)
        attrs['units']          = '3'    # Radiant power (W)
        # Omni/Rectangle get the radiant-power boost; LightSpot is overridden below with a
        # cone-angle-aware scale (its 1.0 here is just a placeholder).
        intensityScale = _VRAY_RADIANT_SCALE if pluginType in ('LightOmni', 'LightRectangle') else 1.0
        attrs['intensity']      = power * intensityScale

        if pluginType == 'LightOmni':
            attrs['shadowRadius'] = light.shadow_soft_size

        elif pluginType == 'LightSpot':
            attrs['shadowRadius']  = light.shadow_soft_size
            attrs['coneAngle']     = light.spot_size
            # Inverse of the spot_blend <-> penumbraAngle relation used in light_export._fixBlenderSpotLight.
            attrs['penumbraAngle'] = -(light.spot_size * light.spot_blend)
            # Match V-Ray's cone-concentrated peak to Cycles' constant spot peak (see _VRAY_SPOT_CONE_K).
            attrs['intensity']     = power * _VRAY_SPOT_CONE_K * (1.0 - cos(light.spot_size / 2.0))

        elif pluginType == 'LightRectangle':
            # V-Ray expects half-sizes. Square/disc shapes use a single dimension.
            isSquare = light.shape in ('SQUARE', 'DISK')
            attrs['u_size']  = light.size / 2.0
            attrs['v_size']  = (light.size if isSquare else light.size_y) / 2.0
            attrs['is_disc'] = light.shape in ('DISK', 'ELLIPSE')
            # V-Ray renders the rectangle shape to camera by default; facing away it shows as a
            # black backside (Blender area lights don't). Hide it from the camera to match.
            attrs['invisible'] = True

    return {
        'ID'         : pluginType,
        'Name'       : light.name,
        'Attributes' : attrs,
    }
