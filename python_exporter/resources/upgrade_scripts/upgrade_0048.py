# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy

# The 'mode' property of the LightMix render channel gained a new 'instanced' value, and its
# default was changed from the legacy empty string to 'instanced'. A scene created before this
# change could never have deliberately selected 'instanced' (the value did not exist), and the
# legacy default was stored as the empty string. In both cases the node would now fall back to
# the new 'instanced' default, silently changing the LightMix output. Remap those to 'individual',
# which is how a LightMix with the legacy default behaved before this change.
_LIGHTMIX_NODE = 'VRayNodeRenderChannelLightMix'


def _lightMixPropGroups():
    """ Yield every LightMix render-channel node in the file. LightMix lives in world node trees,
        but may also sit inside a V-Ray group, so scan the shared node groups as well.
    """
    trees = [w.node_tree for w in bpy.data.worlds if getattr(w, 'node_tree', None)]
    trees.extend(bpy.data.node_groups)

    for ntree in trees:
        for node in ntree.nodes:
            if node.bl_idname == 'VRayNodeRenderChannelLightMix':
                yield node.RenderChannelLightMix


def run():
    for propGroup in _lightMixPropGroups():
        if not propGroup.is_property_set('mode'):
            propGroup.mode = 'individual'


def check():
    return any(not propGroup.is_property_set('mode') for propGroup in _lightMixPropGroups())
