# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
from vray_blender.utils.upgrade_scene import upgradeScene, sceneNeedsUpgrade
from vray_blender.exporting.tools import isObjectVrayProxy

UPGRADE_INFO = {
    "nodes": {
        "VRayNodeBRDFVRayMtl": None
    }
}

def run():
    for obj in bpy.data.objects:
        if isObjectVrayProxy(obj):
            for light in (c for c in obj.children if c.type == 'LIGHT'):
                light.data.vray.initial_proxy_light_pos = light.matrix_local.decompose()[0]
            
    upgradeScene(UPGRADE_INFO)

def check():
    return sceneNeedsUpgrade(UPGRADE_INFO) or \
        any(isObjectVrayProxy(obj) and any(c.type=="LIGHT" for c in obj.children) for obj in bpy.data.objects)
