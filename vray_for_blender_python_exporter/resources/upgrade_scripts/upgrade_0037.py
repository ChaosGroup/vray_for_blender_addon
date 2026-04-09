# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
from vray_blender.utils.upgrade_scene import sceneNeedsUpgrade, upgradeScene

UPGRADE_INFO = {
    'nodes': {
        'VRayNodeBRDFScanned': None
    }
}

def run():
     upgradeScene(UPGRADE_INFO)

def check():
    return sceneNeedsUpgrade(UPGRADE_INFO)
