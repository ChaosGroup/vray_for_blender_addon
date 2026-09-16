# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

# VBLD-1270: the default of the LightIES 'soft_shadows' property changed from 'No' to 'Use light
# shape for Shadows', so that the Light Shape settings have an effect out of the box. A scene saved
# before this change only stores the property if it was ever assigned, so an IES light left at the
# old default would silently start using its shape for shadows and render differently. Pin those
# back to 'No'.

import bpy
from vray_blender.utils.upgrade_scene import scopedForUpgrade
from vray_blender.nodes.utils import getNodeByType


def _lightIesPropGroups():
    """ Yield the LightIES propGroups of every IES light, covering both the non-node storage
        (light.vray.LightIES) and the node storage (the plugin node in the light's node tree).
    """
    for light in scopedForUpgrade(bpy.data.lights):
        if light.vray.light_type != 'IES':
            continue

        yield light.vray.LightIES

        if node := getNodeByType(light.node_tree, 'VRayNodeLightIES'):
            yield node.LightIES


def run():
    for propGroup in _lightIesPropGroups():
        if not propGroup.is_property_set('soft_shadows'):
            propGroup.soft_shadows = '0'


def check():
    return any(not propGroup.is_property_set('soft_shadows') for propGroup in _lightIesPropGroups())
