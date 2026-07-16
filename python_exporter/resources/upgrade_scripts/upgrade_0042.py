# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
from vray_blender.nodes.utils import getPropGroupOfNode
from vray_blender.utils.upgrade_scene import sceneNeedsUpgrade

UPGRADE_INFO = {
    'nodes': {
        'VRayNodeLightAmbient': None,
        'VRayNodeLightDome': None,
        'VRayNodeLightIES': None,
        'VRayNodeLightMesh': None,
        'VRayNodeLightOmni': None,
        'VRayNodeLightRectangle': None,
        'VRayNodeLightSphere': None,
        'VRayNodeLightSpot': None,
        'VRayNodeMayaLightDirect': None,
        'VRayNodeSunLight': None,
    }
}

def _upgradeTree(ntree: bpy.types.NodeTree):
    if not ntree:
        return
    for node in ntree.nodes:
        if node.bl_idname in UPGRADE_INFO['nodes']:
            propGroup = getPropGroupOfNode(node)
            # Set to the current value as we only need to trigger an update
            # in order to update the socket state.
            propGroup.color_mode = propGroup.color_mode

def run():
    for light in bpy.data.lights:
        _upgradeTree(light.node_tree)

def check():
    return sceneNeedsUpgrade(UPGRADE_INFO)
