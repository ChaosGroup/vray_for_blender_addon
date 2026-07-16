# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
from vray_blender.lib.blender_utils import updateShadowAttr
from vray_blender.utils.upgrade_scene import sceneNeedsUpgrade

UPGRADE_INFO = {
    'nodes': {
        'VRayNodeBRDFVRayMtl': None
    }
}

def run():
    for mtl in (m for m in bpy.data.materials if hasattr(m, 'node_tree') and m.node_tree):
        for node in (n for n in mtl.node_tree.nodes if n.bl_idname == 'VRayNodeBRDFVRayMtl'):
            updateShadowAttr(node.BRDFVRayMtl, 'option_use_roughness')

def check():
    return sceneNeedsUpgrade(UPGRADE_INFO)
