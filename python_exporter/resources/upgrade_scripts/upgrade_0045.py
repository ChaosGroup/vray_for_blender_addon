# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
from vray_blender.utils.upgrade_scene import scopedForUpgrade

from vray_blender.exporting.tools import isObjectVRayDecal
from vray_blender.nodes.utils import getNodeByType, treeHasNodes

# VRayDecal.displacement_multiplier default was decreased from 1.0 to 0.01.
# Preserve the old default for scenes that were saved without an explicit value
# so that their look does not change.
_OLD_DISPLACEMENT_MULTIPLIER = 1.0
_ATTR = 'displacement_multiplier'


def _decalPropGroups():
    """ Yield every VRayDecal property group in the scene (both the one on the
        mesh data and the one on a VRayNodeDecalOutput node, if present).
    """
    for obj in scopedForUpgrade(bpy.data.objects):
        if not isObjectVRayDecal(obj):
            continue

        yield obj.data.vray.VRayDecal

        if treeHasNodes(obj.vray.ntree) and (outputNode := getNodeByType(obj.vray.ntree, 'VRayNodeDecalOutput')):
            yield outputNode.VRayDecal


def run():
    for propGroup in _decalPropGroups():
        if not propGroup.is_property_set(_ATTR):
            setattr(propGroup, _ATTR, _OLD_DISPLACEMENT_MULTIPLIER)


def check():
    return any(not pg.is_property_set(_ATTR) for pg in _decalPropGroups())
