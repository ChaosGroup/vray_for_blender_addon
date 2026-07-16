# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
import os

from vray_blender.lib.blender_utils import updateShadowAttr
from vray_blender.plugins.material.MtlVRmat import _getMaterialNamesFromMtlFile
from vray_blender.utils.upgrade_scene import sceneNeedsUpgrade


UPGRADE_INFO = {
    'nodes': {
        'VRayNodeMtlVRmat': None
    }
}

def run():
    for mtl in (m for m in bpy.data.materials if hasattr(m, 'node_tree') and m.node_tree):
        for node in (n for n in mtl.node_tree.nodes if n.bl_idname == 'VRayNodeMtlVRmat'):
            propGroup = node.MtlVRmat
            
            if os.path.exists(bpy.path.abspath(propGroup.filename)):
                mtlList = _getMaterialNamesFromMtlFile(propGroup.filename)
                propGroup.materials_list = ';'.join(mtlList)

def check():
    return sceneNeedsUpgrade(UPGRADE_INFO)
