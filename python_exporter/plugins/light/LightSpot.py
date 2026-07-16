# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy

from vray_blender.lib import plugin_utils
from vray_blender.nodes.utils import getUpdateCallbackPropertyContext
from vray_blender.exporting.light_export import ANGLE_EPSILON

plugin_utils.loadPluginOnModule(globals(), __name__)


def nodeUpdate(node: bpy.types.Node):
    if node.mute:
        node.mute = False


def _spotLightData(src) -> bpy.types.Light:
    """ Resolve the Light datablock that owns the propgroup/socket that fired the update.

        The update may come from a non-node light's property group (light.vray.LightSpot),
        whose id_data is the Light itself, or from a node property group, whose id_data is
        the light's node tree.
    """
    owner = src.id_data
    if isinstance(owner, bpy.types.Light):
        return owner
    return next((l for l in bpy.data.lights if l.node_tree == owner), None)


def onUpdateUnits(src, context, attrName):
    from vray_blender.plugins.light.light_tools import convertIntensityForUnitChange
    propGroup = getUpdateCallbackPropertyContext(src, 'LightSpot').propGroup
    convertIntensityForUnitChange(propGroup, 'LightSpot', context)


def onUpdateAttribute(src, context: bpy.types.Context, attrName: str):
    # The light is resolved from the update source, not from context.active_object: the
    # callback also fires from fixSceneLights() in the depsgraph-post handler, where there
    # is no active object (or it is a different light than the one being updated).
    if (light := _spotLightData(src)) is None or light.type != 'SPOT':
        return

    propContext = getUpdateCallbackPropertyContext(src, 'LightSpot')

    # This function will be called when the user changes the vaues of the light properties directly
    # in the property pages or through a script, and when the change occurs due to a manipulation of
    # the light gizmo or through the native Blender light properties. In the latter case, an infinite
    # recursion may occur with the fixSceneLights() function in light_export.py if care is not taken
    # to only set the properties when their values have changed.
    match attrName:
        case 'coneAngle':
            newConeAngle = propContext.get('coneAngle')
            if abs(newConeAngle - light.spot_size) > ANGLE_EPSILON:
                light.spot_size = newConeAngle
                light.spot_blend = max(-propContext.get('penumbraAngle') / newConeAngle, 0)

        case 'penumbraAngle':
            newPenumbraAngle = -propContext.get('penumbraAngle')
            coneAngle = propContext.get('coneAngle')
            newBlend = max(newPenumbraAngle / coneAngle, 0)
            if abs(newBlend - light.spot_blend) > ANGLE_EPSILON:
                light.spot_blend = newBlend

        case 'show_cone':
            newShow = propContext.get('show_cone')
            if newShow != light.show_cone:
                light.show_cone = newShow
