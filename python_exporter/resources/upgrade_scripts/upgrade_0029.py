# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

from vray_blender.lib.mixin import VRayNodeBase
from vray_blender.utils.upgrade_scene import  UpgradeContext, upgradeScene, sceneNeedsUpgrade
from vray_blender.plugins.templates.common import cleanupObjectSelectorLists
from functools import partial

def copySun(nodeType: str, ctx: UpgradeContext, oldNode: VRayNodeBase, newNode: VRayNodeBase):
    if oldSun := getattr(oldNode, nodeType).sun:
        getattr(newNode, nodeType).sun_select.boundPropObjName = oldSun.name
        getattr(newNode, nodeType).sun_select.boundPropObj = oldSun


UPGRADE_INFO = {
    'nodes': {
        'VRayNodeTexSky': {
            'node_pre_copy': partial(copySun, 'TexSky')
        },
        'VRayNodeVolumeAerialPerspective': {
            'node_pre_copy': partial(copySun, 'VolumeAerialPerspective')
        }
    }
}

def run():
    upgradeScene(UPGRADE_INFO)
    cleanupObjectSelectorLists() #T his will assign the correct object type to the object selectors

def check():
    return sceneNeedsUpgrade(UPGRADE_INFO)