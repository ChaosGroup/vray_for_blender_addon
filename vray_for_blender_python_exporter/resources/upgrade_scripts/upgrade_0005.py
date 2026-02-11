# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

from vray_blender.utils.upgrade_scene import upgradeScene, sceneNeedsUpgrade

UPGRADE_INFO = {
    "nodes": {
        "VRayNodeBRDFBump": None,
        "VRayNodeMtl2Sided": None,
        "VRayNodeMtlRenderStats": None,
        "VRayNodeMtlRoundEdges": None,
        "VRayNodeMtlWrapper": None,
        "VRayNodeMtlMaterialID": None,
        "VRayNodeMtlOverride": None
    }
}

def run():
    upgradeScene(UPGRADE_INFO)

def check():
    return sceneNeedsUpgrade(UPGRADE_INFO)
