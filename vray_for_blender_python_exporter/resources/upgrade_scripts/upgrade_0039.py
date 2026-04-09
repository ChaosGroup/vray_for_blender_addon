# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
from vray_blender.nodes.sockets import moveExtendSocketToBottom
from vray_blender.utils.upgrade_scene import sceneNeedsUpgrade
from vray_blender.nodes.specials.effects import addEffectsExtendSocket
from vray_blender.nodes.specials.renderchannels import addRenderChannelsExtendSocket
from vray_blender.nodes.specials.texture import addTexLayeredExtendSocket
from vray_blender.plugins.texture.TexMulti import addTexMultiExtendSocket
from vray_blender.plugins.BRDF.BRDFLayered import addBRDFLayeredExtendSocket

UPGRADE_INFO = {
    'nodes': {
        'VRayNodeTexLayeredMax': addTexLayeredExtendSocket,
        'VRayNodeTexMulti': addTexMultiExtendSocket,
        'VRayNodeBRDFLayered': addBRDFLayeredExtendSocket,
        'VRayNodeEffectsHolder': addEffectsExtendSocket,
        'VRayNodeRenderChannels': addRenderChannelsExtendSocket,
    }
}

def _upgradeTree(ntree: bpy.types.NodeTree):
    if not ntree:
        return
    for node in ntree.nodes:
        if fnAddExtendSock := UPGRADE_INFO['nodes'].get(node.bl_idname, None):
            # If another script re-creates the node before this one it will end up with
            # multiple extend sockets.
            if any(s.bl_idname == 'VRaySocketExtend' for s in node.inputs):
                moveExtendSocketToBottom(node)
                continue
            fnAddExtendSock(node)

def run():
    for material in bpy.data.materials:
        _upgradeTree(material.node_tree)

    for object in bpy.data.objects:
        _upgradeTree(object.vray.ntree)

    for world in bpy.data.worlds:
        _upgradeTree(world.node_tree)

def check():
    return sceneNeedsUpgrade(UPGRADE_INFO)
