# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
from vray_blender.nodes.sockets import moveExtendSocketToBottom
from vray_blender.utils.upgrade_scene import sceneNeedsUpgrade, scopedForUpgrade
from vray_blender.nodes.specials.material import addMtlMultiExtendSocket

UPGRADE_INFO = {
    'nodes': {
        'VRayNodeMtlMulti': addMtlMultiExtendSocket,
        'VRayNodeTexDirt': None,
        'VRayNodeTexGradRamp': None,
    }
}

_OLD_DIRT_RADIUS = 50.0
_OLD_GRAD_RAMP_TYPE = '0'  # Four corner

def _upgradeTree(ntree: bpy.types.NodeTree):
    if not ntree:
        return
    for node in ntree.nodes:
        if fnAddExtendSock := UPGRADE_INFO['nodes'].get(node.bl_idname, None):
            if any(s.bl_idname == 'VRaySocketExtend' for s in node.inputs):
                moveExtendSocketToBottom(node)
                continue
            fnAddExtendSock(node)


def _upgradeTexDirtRadius(ntree: bpy.types.NodeTree):
    if not ntree:
        return
    for node in ntree.nodes:
        if node.bl_idname != 'VRayNodeTexDirt':
            continue
        propGroup = getattr(node, node.vray_plugin, None)
        if propGroup and not propGroup.is_property_set('radius'):
            propGroup.radius = _OLD_DIRT_RADIUS


def _upgradeTexGradRampType(ntree: bpy.types.NodeTree):
    if not ntree:
        return
    for node in ntree.nodes:
        if node.bl_idname != 'VRayNodeTexGradRamp':
            continue
        propGroup = getattr(node, node.vray_plugin, None)
        if propGroup and not propGroup.is_property_set('gradient_type'):
            propGroup.gradient_type = _OLD_GRAD_RAMP_TYPE


def run():
    for material in scopedForUpgrade(bpy.data.materials):
        _upgradeTree(material.node_tree)
        _upgradeTexDirtRadius(material.node_tree)
        _upgradeTexGradRampType(material.node_tree)

    for world in scopedForUpgrade(bpy.data.worlds):
        if getattr(world, 'use_nodes', False):
            _upgradeTexDirtRadius(world.node_tree)
            _upgradeTexGradRampType(world.node_tree)

    for group in scopedForUpgrade(bpy.data.node_groups):
        if hasattr(group, 'vray') and group.vray.tree_type == 'OBJECT':
            _upgradeTexDirtRadius(group)
            _upgradeTexGradRampType(group)


def check():
    return sceneNeedsUpgrade(UPGRADE_INFO)
