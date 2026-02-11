# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
from vray_blender.utils.upgrade_scene import sceneNeedsUpgrade
UPGRADE_INFO = {
    "nodes": {
        "VRayNodeTexLayeredMax": None
    }
}
def run():
    for mtl in [m for m in bpy.data.materials if m.use_nodes]:
        for node in [n for n in mtl.node_tree.nodes if n.bl_idname == 'VRayNodeTexLayeredMax']:
            node.outputs['Output'].name = 'Color'

def check():
    return sceneNeedsUpgrade(UPGRADE_INFO)