# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
from vray_blender.utils.upgrade_scene import sceneNeedsUpgrade

# Node types and their distance-based bump_amount attribute names
# (from BRDFAlSurface, BRDFCarPaint2, BRDFToonMtl, BRDFVRayMtl custom JSONs)
BRDF_BUMP_ATTRS = {
    "VRayNodeBRDFVRayMtl": ("bump_amount", "coat_bump_amount"),
    "VRayNodeBRDFToonMtl": ("bump_amount", "coat_bump_amount"),
    "VRayNodeBRDFAlSurface": (
        "bump_amount",
        "diffuse_bump_amount",
        "reflect1_bump_amount",
        "reflect2_bump_amount",
    ),
    "VRayNodeBRDFCarPaint2": ("base_bump_amount", "coat_bump_amount"),
}

OLD_DEFAULT = 1.0 # default bump amount before the changes in the custom JSONs

UPGRADE_INFO = {
    "nodes": {bl_idname: None for bl_idname in BRDF_BUMP_ATTRS},
}

def _mtlNodeTrees():
    """Yield all relevant node trees (materials)."""
    for mtl in bpy.data.materials:
        if getattr(mtl, "node_tree", None) and mtl.node_tree:
            yield mtl.node_tree

def run():
    for ntree in _mtlNodeTrees():
        for node in ntree.nodes:
            if node.bl_idname not in BRDF_BUMP_ATTRS:
                continue
            for attr in BRDF_BUMP_ATTRS[node.bl_idname]:
                propGroup = getattr(node, node.vray_plugin, None)
                if not propGroup.is_property_set(attr):
                    setattr(propGroup, attr, OLD_DEFAULT)

def check():
    return sceneNeedsUpgrade(UPGRADE_INFO)
