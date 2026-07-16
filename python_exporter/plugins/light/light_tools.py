# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import math

import bpy

from vray_blender.exporting.tools import getInputSocketByAttr
from vray_blender.lib.blender_utils import getShadowAttr, updateShadowAttr
from vray_blender.nodes.utils import getLightOutputNode, isAutoConnectEnabled

# Luminous efficacy of radiation at peak photopic sensitivity (lm/W at 555 nm).
_WATTS_TO_LUMENS = 683.0


def _clamp(x, lo, hi):
    return max(lo, min(hi, x))


def _colorIntensity(color):
    """ Average of the RGB components - mirrors V-Ray's Color::intensity(). """
    avg = (color[0] + color[1] + color[2]) / 3.0
    return avg if avg > 1e-6 else 1.0


def _resolveLight(propGroup) -> bpy.types.Light:
    """ Light datablock owning the propGroup, resolved from the update source rather than
        context.active_object (mirrors LightSpot._spotLightData). Non-node: id_data is the
        Light; node: id_data is the light's node tree.
    """
    owner = propGroup.id_data
    if isinstance(owner, bpy.types.Light):
        return owner
    return next((l for l in bpy.data.lights if l.node_tree == owner), None)


def _unitPower(propGroup, pluginType, light, lightObj):
    """ V-Ray SDK getUnitPower() for each light type: the solid-angle / area factor
        the renderer divides by for the flux units (Lumens, Watts).
    """
    match pluginType:
        case 'LightRectangle':
            # The renderer's u_size/v_size are taken from the Blender light dimensions
            # (see _setLightRectLightAttrs), NOT from the propGroup, which the UI does not
            # keep in sync. Read the live dimensions so the area matches what is rendered.
            if light is not None:
                sizeX = light.size / 2.0
                isSquare = light.shape in ('SQUARE', 'DISK') or propGroup.is_disc
                sizeY = sizeX if isSquare else light.size_y / 2.0
            else:
                sizeX, sizeY = propGroup.u_size, propGroup.v_size
            # The core folds the object's transform scale into u_size/v_size in frameBegin
            # (u_size *= length(tm.m[0]), v_size *= length(tm.m[1])); mirror it so the area
            # matches what is rendered for scaled light objects.
            if lightObj is not None:
                m3 = lightObj.matrix_world.to_3x3()
                sizeX *= m3.col[0].length
                sizeY *= m3.col[1].length
            # Full area = (2*sizeX)*(2*sizeY), integrated over a hemisphere (factor pi).
            area = 4.0 * sizeX * sizeY
            return area * math.pi if area > 1e-12 else 4.0 * math.pi ** 2
        case 'LightSphere':
            r = propGroup.radius
            # The core scales the radius by the average transform scale (getAvgTmScale).
            if lightObj is not None:
                m3 = lightObj.matrix_world.to_3x3()
                r *= (m3.col[0].length + m3.col[1].length + m3.col[2].length) / 3.0
            return 4.0 * math.pi ** 2 * r ** 2 if r > 1e-6 else 4.0 * math.pi ** 2
        case 'LightSpot':
            # Solid angle of the spot cone (mirrors SpotLight::getUnitPower). Angles are radians.
            cone, pen = propGroup.coneAngle, propGroup.penumbraAngle
            cs1 = math.cos(_clamp(cone + pen, 0.0, math.pi) * 0.5)
            cs2 = math.cos(_clamp(cone - pen, 0.0, math.pi) * 0.5)
            return (1.0 - (cs1 + cs2) * 0.5) * 2.0 * math.pi ** 2
        case _:
            # LightOmni: entire sphere (4pi sr) * hemisphere factor (pi).
            return 4.0 * math.pi ** 2


def _intensityCoeff(units, colorIntensity, meterScale, photometricScale, unitPower):
    """ Rendering coefficient per unit type, mirroring PhotoLight::calculateLightMult.

        photometricScale is applied to all photometric units (1-4) but NOT to Default (0),
        so it does not cancel when converting to/from Default and must be included.

        units: '0' Default, '1' Lumens, '2' lm/m/m/sr, '3' Watts, '4' W/m/m/sr
    """
    match units:
        case '1':   # Lumens (flux)
            return photometricScale / (colorIntensity * meterScale ** 2 * unitPower)
        case '2':   # lm/m/m/sr (radiance)
            return photometricScale / colorIntensity
        case '3':   # Watts (flux)
            return _WATTS_TO_LUMENS * photometricScale / (colorIntensity * meterScale ** 2 * unitPower)
        case '4':   # W/m/m/sr (radiance)
            return _WATTS_TO_LUMENS * photometricScale / colorIntensity
        case _:     # '0' Default - intensity is used as-is
            return 1.0


def convertIntensityForUnitChange(propGroup, pluginType, context):
    """ Rescale 'intensity' to preserve the rendered output when 'units' changes.

        Reads the previous unit from the shadow attribute, computes the ratio of
        rendering coefficients for old vs new units, and scales intensity by that
        ratio so the light's output stays constant.
    """
    newUnits = propGroup.units
    oldUnits = getShadowAttr(propGroup, 'units')
    if oldUnits == newUnits:
        return

    # Programmatic unit changes - Cosmos/asset import, .vrscene import, native-light
    # conversion, scene upgrades - set 'units' and 'intensity' together to stored values;
    # the intensity is already expressed in the new units, so it must NOT be rescaled.
    # These bulk operations run with auto-connect disabled. Still sync the shadow so the
    # next user-driven change in the UI converts from the correct previous unit.
    if not isAutoConnectEnabled():
        updateShadowAttr(propGroup, 'units')
        return

    # Mirror SettingsUnitsInfo.exportCustom so the coefficients match what the renderer uses.
    unitSettings = context.scene.unit_settings
    photometricScale = context.scene.vray.SettingsUnitsInfo.photometric_scale
    meterScale = 1.0
    if unitSettings.system != 'NONE':
        meterScale = unitSettings.scale_length
        photometricScale *= meterScale ** 2

    # Resolve the light - and an object using it, for the world-space transform scale - from
    # the update source rather than context.active_object (which is wrong for pinned panels,
    # the node editor, scripting, or when fixSceneLights() drives the change).
    light = _resolveLight(propGroup)
    lightObj = next((o for o in bpy.data.objects if o.data is light), None) if light else None

    colorIntensity = _colorIntensity(propGroup.color)
    power = _unitPower(propGroup, pluginType, light, lightObj)

    oldCoeff = _intensityCoeff(oldUnits, colorIntensity, meterScale, photometricScale, power)
    newCoeff = _intensityCoeff(newUnits, colorIntensity, meterScale, photometricScale, power)

    if abs(newCoeff) > 1e-30:
        propGroup.intensity = propGroup.intensity * oldCoeff / newCoeff

    updateShadowAttr(propGroup, 'units')


class VRAY_OT_set_light_color_from_temperature(bpy.types.Operator):
    bl_idname = "vray.set_light_color_from_temperature"
    bl_label = "Set as color"
    bl_description = "Set light color from temperature"
    bl_options = {'INTERNAL', 'UNDO'}

    color:       bpy.props.FloatVectorProperty()
    light_name:  bpy.props.StringProperty()
    plugin_type: bpy.props.StringProperty()
    color_attr_name: bpy.props.StringProperty()

    def execute(self, context):
        light = bpy.data.lights.get(self.light_name)
        if not light:
            return {'CANCELLED'}

        # The lights can have or not have nodes. This will change starting with Blender 5.1
        # where they are always created with a node tree.
        if (nodeTree := getattr(light, 'node_tree', None)) and nodeTree.nodes and \
            (node := getLightOutputNode(nodeTree)):
                colorSock = getInputSocketByAttr(node, self.color_attr_name)
                colorSock.value = self.color
        elif hasattr(light, "vray"):
            propGroup = getattr(light.vray, self.plugin_type)
            setattr(propGroup, self.color_attr_name, self.color)

        return {'FINISHED'}


def register():
    if not hasattr(bpy.types, "VRAY_OT_set_light_color_from_temperature"):
        bpy.utils.register_class(VRAY_OT_set_light_color_from_temperature)


def unregister():
    if hasattr(bpy.types, "VRAY_OT_set_light_color_from_temperature"):
        bpy.utils.unregister_class(VRAY_OT_set_light_color_from_temperature)
