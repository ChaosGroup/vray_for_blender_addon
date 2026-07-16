# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
from vray_blender.nodes.sockets import addInput


def _treeNeedsUpgrade(ntree: bpy.types.NodeTree) -> bool:
    if not ntree:
        return False
    return any(
        node.bl_idname == 'VRayNodeOutputMaterial'
        and not node.inputs.get('Outlines')
        for node in ntree.nodes
    )


def _upgradeTree(ntree: bpy.types.NodeTree):
    if not ntree:
        return
    for node in ntree.nodes:
        if node.bl_idname == 'VRayNodeOutputMaterial' and not node.inputs.get('Outlines'):
            addInput(node, 'VRaySocketBRDF', 'Outlines', 'outlines')


def run():
    for material in bpy.data.materials:
        _upgradeTree(material.node_tree)


def check():
    return any(
        _treeNeedsUpgrade(material.node_tree)
        for material in bpy.data.materials
    )
