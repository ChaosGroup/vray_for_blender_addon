# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

""" Conversion of native Blender lights to V-Ray lights.

    This is the 'export' half of the light conversion (mirroring how cycles_export turns a
    Cycles material into V-Ray plugin descriptions): it reads a native Blender light datablock
    and produces a single V-Ray light plugin description, in the same {ID, Name, Attributes} dict
    shape that the importer (nodes.importing) consumes.
"""

from math import radians, cos, pi

from mathutils import Color

from vray_blender import debug
from vray_blender.lib import lib_utils


# Angular diameter of the reference sun used by V-Ray's SunLight (size_multiplier == 1.0).
# Blender's default sun angle is the same ~0.526 degrees.
_REFERENCE_SUN_ANGLE = radians(0.526)

# Intensity calibration factors, measured by rendering single lights in both engines and
# matching the V-Ray result to Cycles (V-Ray default sky/world; difference-isolated, linear).
#
# SunLight: in 'Direct' colour mode the radiance has no atmospheric term, so one constant converts
# Blender's irradiance to it. Measured 1/3.1418 across elevation, strength and blackbody colour -
# the Lambertian 1/pi.
_VRAY_SUN_ENERGY_SCALE = 1.0 / pi
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


# Cycles' own blackbody fit (svm_math_blackbody_color_rec709). color_utils.kelvinToRGB cannot
# stand in: it is peak-normalized, and for a light the lost magnitude is part of the intensity.
_BLACKBODY_BREAKS = (965.0, 1167.0, 1449.0, 1902.0, 3315.0, 6365.0)
_BLACKBODY_R = ((1.61919106e+03, -2.05010916e-03, 5.02995757e+00),
                (2.48845471e+03, -1.11330907e-03, 3.22621544e+00),
                (3.34143193e+03, -4.86551192e-04, 1.76486769e+00),
                (4.09461742e+03, -1.27446582e-04, 7.25731635e-01),
                (4.67028036e+03,  2.91258199e-05, 1.26703442e-01),
                (4.59509185e+03,  2.87495649e-05, 1.50345020e-01),
                (3.78717450e+03,  9.35907826e-06, 3.99075871e-01))
_BLACKBODY_G = ((-4.88999748e+02, 6.04330754e-04, -7.55807526e-02),
                (-7.55994277e+02, 3.16730098e-04,  4.78306139e-01),
                (-1.02363977e+03, 1.20223470e-04,  9.36662319e-01),
                (-1.26571316e+03, 4.87340896e-06,  1.27054498e+00),
                (-1.42529332e+03, -4.01150431e-05, 1.43972784e+00),
                (-1.17554822e+03, -2.16378048e-05, 1.30408023e+00),
                (-5.00799571e+02, -4.59832026e-06, 1.09098763e+00))
_BLACKBODY_B = ((5.96945309e-11, -4.85742887e-08, -9.70622247e-05, -4.07936148e-03),
                (2.40430366e-11,  5.55021075e-08, -1.98503712e-04,  2.89312858e-02),
                (-1.40949732e-11, 1.89878968e-07, -3.56632824e-04,  9.10767778e-02),
                (-3.61460868e-11, 2.84822009e-07, -4.93211319e-04,  1.56723440e-01),
                (-1.97075738e-11, 1.75359352e-07, -2.50542825e-04, -2.22783266e-02),
                (-1.61997957e-13, -1.64216008e-08, 3.86216271e-04, -7.38077418e-01),
                (6.72650283e-13, -2.73078809e-08,  4.24098264e-04, -7.52335691e-01))


def _cyclesBlackbody(kelvin: float) -> Color:
    if kelvin >= 12000.0:
        return Color((0.8262954810464208, 0.9945080501520986, 1.566307710274283))
    if kelvin < 800.0:
        return Color((5.413294490189271, 0.0, 0.0))

    i = sum(1 for b in _BLACKBODY_BREAKS if kelvin >= b)
    r, g, b = _BLACKBODY_R[i], _BLACKBODY_G[i], _BLACKBODY_B[i]
    tInv = 1.0 / kelvin
    return Color((max(0.0, r[0] * tInv + r[1] * kelvin + r[2]), max(0.0, g[0] * tInv + g[1] * kelvin + g[2]),
                  max(0.0, ((b[0] * kelvin + b[1]) * kelvin + b[2]) * kelvin + b[3])))


def _followLink(socket):
    """ The node feeding 'socket', skipping reroutes; None if unlinked. """
    while socket is not None and socket.is_linked:
        node = socket.links[0].from_node
        if node.bl_idname != 'NodeReroute':
            return node
        socket = node.inputs[0]
    return None


def _nodeTreeEmission(light):
    """ (colour, strength) of a Cycles light node tree, both multiplied into the light's own
        colour and power the way Cycles does. Returns (white, 1.0) when there is no tree.

        Only the constant part of the tree is convertible - a V-Ray light takes a single colour
        and intensity, not a shader graph - so a textured Colour/Strength is reported and skipped.
    """
    neutral = (Color((1.0, 1.0, 1.0)), 1.0)

    if not getattr(light, 'use_nodes', False) or light.node_tree is None:
        return neutral

    output = next((n for n in light.node_tree.nodes if n.bl_idname == 'ShaderNodeOutputLight' and n.is_active_output), None)
    if output is None:
        return neutral

    emission = _followLink(output.inputs.get('Surface'))
    if emission is None:
        return neutral
    if emission.bl_idname != 'ShaderNodeEmission':
        debug.report(severity='WARNING', msg=f"Light '{light.name}': only an Emission node tree is convertible, "
                         f"'{emission.bl_idname}' is ignored")
        return neutral

    strengthSocket = emission.inputs['Strength']
    if strengthSocket.is_linked:
        debug.report(severity='WARNING', msg=f"Light '{light.name}': a textured Emission 'Strength' is not supported, " "using 1.0")
        strength = 1.0
    else:
        strength = strengthSocket.default_value

    colorSocket = emission.inputs['Color']
    colorNode = _followLink(colorSocket)
    if colorNode is None:
        color = Color(colorSocket.default_value[:3])
    elif colorNode.bl_idname == 'ShaderNodeBlackbody' and not colorNode.inputs['Temperature'].is_linked:
        color = _cyclesBlackbody(colorNode.inputs['Temperature'].default_value)
    elif colorNode.bl_idname == 'ShaderNodeRGB':
        color = Color(colorNode.outputs['Color'].default_value[:3])
    else:
        debug.report(severity='WARNING', msg=f"Light '{light.name}': a textured Emission 'Color' is not supported, " "using white")
        color = Color((1.0, 1.0, 1.0))

    return color, strength


def _convertedColor(light):
    """ Light color, tinted by the blackbody temperature when enabled (matches Cycles). """
    col = Color(light.color[:3])

    if getattr(light, 'use_temperature', False):
        # 'temperature_color' is what Cycles itself multiplies in (blender/light.cpp), and it
        # carries the emitted magnitude - 2.52 red at 2000 K. color_utils.kelvinToRGB is
        # peak-normalised, so it would lose that and convert the light 2.5x too dim.
        bb = light.temperature_color
        col = Color((col.r * bb[0], col.g * bb[1], col.b * bb[2]))

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

    # In Cycles a light node tree multiplies into the datablock's colour and power, so fold it in.
    nodeColor, nodeStrength = _nodeTreeEmission(light)
    col = Color((col.r * nodeColor.r, col.g * nodeColor.g, col.b * nodeColor.b))

    # Radiant power in Watts. Blender's 'exposure' is an additional power scale in stops.
    # NOTE: this assumes light.normalize == True (the default), where 'energy' is the total
    # radiant power and maps 1:1 to V-Ray's 'Watts' units. With normalize == False Blender
    # skips the per-area division while V-Ray always normalizes by area, so the match is only
    # approximate in that case.
    power = light.energy * nodeStrength * (2.0 ** getattr(light, 'exposure', 0.0))

    # Both branches below need the colour before it is clamped into the prop's [0, 1] range.
    colMean = (col.r + col.g + col.b) / 3.0
    peak    = max(col.r, col.g, col.b, 1.0)
    if peak > 1.0:
        # A blackbody colour is not in [0, 1] (2.52 red at 2000 K).
        col = Color((col.r / peak, col.g / peak, col.b / peak))

    attrs = {
        'enabled': True,
        'shadows': light.use_shadow,
    }

    if pluginType == 'SunLight':
        # A Blender sun is directional, but V-Ray's is a physical sky-sun: in the default 'Filter'
        # mode it adds an atmospheric tint and falls off towards the horizon. 'Direct' drops both,
        # leaving filter_color and intensity_multiplier in full control.
        attrs['color_mode']           = '1'   # Direct
        attrs['filter_color']         = col
        # filter_color and intensity_multiplier multiply per channel, so the peak taken out of the
        # colour above goes straight back into the multiplier.
        attrs['intensity_multiplier'] = power * peak * _VRAY_SUN_ENERGY_SCALE
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
        # A radiant-power light divides its colour by the colour's MEAN, so the wattage is the
        # total regardless of tint - Cycles instead multiplies the colour straight into the
        # strength. Without this factor a saturated light converts 1/mean too bright: measured
        # 2.50x for (1, 0.15, 0.05) and 2.37x for a 2000 K blackbody. The mean is the pre-clamp
        # one, which also cancels the peak divided out of the colour above.
        power *= colMean
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
