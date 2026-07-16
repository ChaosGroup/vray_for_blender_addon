# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

# VBLD-656: the 'units' attribute of the area/point lights became 'shadowed' so that
# changing the intensity units can rescale 'intensity' to preserve the rendered output.
# The shadow attribute that stores the previous unit does not exist in scenes saved
# before this change, so it is created with its default value. Sync it to the saved
# 'units' value here, otherwise the first unit change after load would convert from the
# wrong (default) unit. Lights that never left the default unit need no action - their
# shadow already matches.

import bpy

from vray_blender.lib.blender_utils import hasShadowedAttrChanged, updateShadowAttr
from vray_blender.nodes.utils import getNodeByType

# The light plugins whose 'units' attribute was made shadowed.
_SHADOWED_LIGHT_TYPES = ('LightOmni', 'LightRectangle', 'LightSphere', 'LightSpot')


def _unitsPropGroups():
    """ Yield every 'units'-bearing propGroup of the affected lights, covering both the
        non-node storage (light.vray.<Plugin>) and the node storage (the plugin node in
        the light's node tree).
    """
    for light in bpy.data.lights:
        for pluginType in _SHADOWED_LIGHT_TYPES:
            yield getattr(light.vray, pluginType)
            if node := getNodeByType(light.node_tree, f'VRayNode{pluginType}'):
                yield getattr(node, pluginType)


def run():
    for propGroup in _unitsPropGroups():
        updateShadowAttr(propGroup, 'units')


def check():
    return any(hasShadowedAttrChanged(pg, 'units') for pg in _unitsPropGroups())
