# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy

# Group trees must match the parent tree type for Blender's C-level
# node_group_poll type check to pass.
# V-Ray groups are identified by vray.tree_type == 'GROUP' on the tree.
# Material/world/light trees are ShaderNodeTree; object trees use their own type.
VRAY_GROUP_TREE_TYPE = 'ShaderNodeTree'
VRAY_GROUP_NODE_TYPE = 'VRayNodeGroup'

# All V-Ray node editor tree types (space.tree_type values).
VRAY_EDITOR_TREE_TYPES = frozenset({
    'VRayNodeTreeEditor',
    'VRayNodeTreeObject',
    'VRayNodeTreeFur',
    'VRayNodeTreeDecal',
})

# Object-level tree types that need a matching group tree type.
VRAY_OBJECT_TREE_TYPES = frozenset({
    'VRayNodeTreeObject',
    'VRayNodeTreeFur',
    'VRayNodeTreeDecal',
})

# Nodes that must never be grouped: they are tree-level sinks/outputs whose
# position in the root tree is assumed by the export and lookup code.
NON_GROUPABLE_NODE_TYPES = frozenset({
    'NodeGroupInput',
    'NodeGroupOutput',
    VRAY_GROUP_NODE_TYPE,
    'ShaderNodeGroup',
    # Tree output/anchor nodes
    'VRayNodeWorldOutput',
    'VRayNodeOutputMaterial',
    'VRayNodeObjectOutput',
    'VRayNodeFurOutput',
    'VRayNodeDecalOutput',
})


def isGroupNodesEnabled():
    """ Check if the group nodes feature is enabled. Returns False when
        `bpy.context.scene` is not yet available (e.g. during addon load).
    """
    try:
        return bpy.context.scene.vray.Exporter.enable_group_nodes
    except AttributeError:
        return False
