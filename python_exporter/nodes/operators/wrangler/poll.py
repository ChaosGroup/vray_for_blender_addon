# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

""" Shared poll helpers for the V-Ray node-editor operators. """

import bpy


# The V-Ray node-editor dropdown registers a single user-facing editor type
# ('VRayNodeTreeEditor'). Object/fur/decal trees are rendered inside that same
# editor, so polling this single tree_type covers all V-Ray editing contexts.
VRAY_TREE_TYPE = 'VRayNodeTreeEditor'


def isVrayEditor(context) -> bool:
    """ True when the current node editor is V-Ray's. Rejects stock Blender
        tree types (ShaderNodeTree, CompositorNodeTree, etc.) so other node
        addons keep owning those editors.
    """
    space = context.space_data
    if not space or space.type != 'NODE_EDITOR':
        return False
    return getattr(space, 'tree_type', '') == VRAY_TREE_TYPE


def hasEditTree(context) -> bool:
    space = context.space_data
    return bool(space and getattr(space, 'edit_tree', None))


def hasSelection(context, minCount: int = 1) -> bool:
    return len(context.selected_nodes or []) >= minCount


def hasActive(context) -> bool:
    space = context.space_data
    ntree = space.edit_tree if space else None
    return bool(ntree and ntree.nodes.active)
