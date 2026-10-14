# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

# VBLD-2887: the 'active' state of a node's input sockets is stored per node (VRaySocket.ui_enabled)
# and was only evaluated when a property from its condition changed, never when the node was
# created. A condition that is false for the default values therefore left the socket enabled.
# The one such condition is BRDFVRayMtl's Coat Anisotropy, which is disabled while Coat Amount is 0.
# Evaluate the conditions on the nodes saved before version 57.

import bpy

from vray_blender.utils.upgrade_scene import scopedForUpgrade

_PLUGIN_TYPE = 'BRDFVRayMtl'

# Collections whose datablocks can own a V-Ray node tree.
_TREE_OWNERS = ('materials', 'worlds', 'objects', 'lights', 'cameras', 'node_groups')


def _trees(collections):
    for name in _TREE_OWNERS:
        for db in collections(getattr(bpy.data, name)):
            # A node group is itself the tree; the others own one.
            tree = db if isinstance(db, bpy.types.NodeTree) else getattr(db, 'node_tree', None)
            if tree is not None:
                yield tree


def _vrayMtlNodes(collections):
    for tree in _trees(collections):
        for node in tree.nodes:
            if getattr(node, 'vray_plugin', '') == _PLUGIN_TYPE:
                yield node


def run():
    from vray_blender.nodes.utils import updateConditionalSockets
    from vray_blender.plugins import getPluginModule

    pluginModule = getPluginModule(_PLUGIN_TYPE)

    for node in _vrayMtlNodes(scopedForUpgrade):
        if (propGroup := getattr(node, _PLUGIN_TYPE, None)) is None:
            continue

        # Only the 'active' state. The visibility also depends on the rollouts' state and is
        # already correct.
        updateConditionalSockets(node, pluginModule, propGroup, updateVisibility=False)


def check():
    from vray_blender.utils.upgrade_scene import scoped
    return next(_vrayMtlNodes(scoped), None) is not None
