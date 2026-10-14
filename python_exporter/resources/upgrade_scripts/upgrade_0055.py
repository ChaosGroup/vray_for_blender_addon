# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

# Three changes to the Color Arithmetic node, all applied per node.
#
# 1. Which inputs it shows used to follow only 'mode', and only coarsely. Color B / Mult B were
#    shown for every mode but Result A. They now follow the inputs the node's channels actually
#    read, which is the one 'mode' routes to the Result output plus every output socket the user
#    has added. Socket state is stored per node, so nodes saved before version 55 keep the old
#    set until something touches them. The case that matters is a node in Result A mode with a
#    binary output (Product, Sum, ...) added. It was saved with no B inputs and cannot be edited.
#
#    The inputs are also ordered so each color sits next to its multiplier. Socket order is
#    stored per node too, so reorder the saved ones to match.
#
# 2. Color A and Color B now default to white instead of black, matching V-Ray for Houdini. Black
#    A made every operation produce black, so a fresh node did nothing. The socket proxies the
#    property group, which stores a property only once it is assigned, so a node left on the old
#    default would silently render with the new one. Pin those back.
#
# 3. Result Alpha now defaults to 1 instead of 0, so the result is opaque. Pinned back the same way.

import bpy

from vray_blender.utils.upgrade_scene import scopedForUpgrade

_NODE_TYPE = 'VRayNodeTexAColorOp'

# What color_a, color_b and result_alpha defaulted to before version 55.
_OLD_COLOR_DEFAULT = (0.0, 0.0, 0.0)
_OLD_ALPHA_DEFAULT = 0.0

# The order the input sockets are created in from version 55 on.
_INPUT_ORDER = ('color_a', 'mult_a', 'color_b', 'mult_b', 'result_alpha')

# Collections whose datablocks can own a V-Ray node tree.
_TREE_OWNERS = ('materials', 'worlds', 'objects', 'lights', 'cameras', 'node_groups')


def _trees(collections):
    for name in _TREE_OWNERS:
        for db in collections(getattr(bpy.data, name)):
            # A node group is itself the tree; the others own one.
            tree = db if isinstance(db, bpy.types.NodeTree) else getattr(db, 'node_tree', None)
            if tree is not None:
                yield tree


def _colorOpNodes(collections):
    for tree in _trees(collections):
        for node in tree.nodes:
            if node.bl_idname == _NODE_TYPE:
                yield node


def _reorderInputs(node):
    """ Put the input sockets in _INPUT_ORDER. Nodes saved before version 55 have Color B ahead
        of Mult A. move() invalidates socket references, so look each one up by index each time.
    """
    for target, attr in enumerate(_INPUT_ORDER):
        current = next((i for i, s in enumerate(node.inputs) if s.vray_attr == attr), None)

        if (current is not None) and (current != target):
            node.inputs.move(current, target)


def run():
    from vray_blender.plugins.texture.TexAColorOp import updateInputSockets

    for node in _colorOpNodes(scopedForUpgrade):
        propGroup = node.TexAColorOp

        for attr in ('color_a', 'color_b'):
            if not propGroup.is_property_set(attr):
                setattr(propGroup, attr, _OLD_COLOR_DEFAULT)

        if not propGroup.is_property_set('result_alpha'):
            propGroup.result_alpha = _OLD_ALPHA_DEFAULT

        _reorderInputs(node)
        updateInputSockets(node)


def check():
    from vray_blender.utils.upgrade_scene import scoped
    return next(_colorOpNodes(scoped), None) is not None
