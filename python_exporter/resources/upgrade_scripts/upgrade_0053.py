# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

# scene.vray.SettingsOptions.mtl_stochasticBump was not exposed before version 53 and
# defaulted to False. It now defaults to True, which changes how bump maps are sampled,
# so force scenes saved on the old default back to False to keep their look. Pre-bump
# there was no UI for this property, so there can't be an intentional user override to
# clobber.
#
# The property was never written to older files, so on load it shows the new default and
# is indistinguishable by value from a brand new scene - which this chain also runs on
# (getSceneUpgradeNumber() reports 1 for an unstamped scene). The V-Ray version stamp of
# the active scene is what tells the two apart: a default or non-V-Ray scene has none and
# must keep the new default.

import bpy
from vray_blender.utils.upgrade_scene import scopedForUpgrade

def run():
    for scene in scopedForUpgrade(bpy.data.scenes):
        scene.vray.SettingsOptions.mtl_stochasticBump = False


def check():
    return any(s.vray.SettingsOptions.mtl_stochasticBump for s in scopedForUpgrade(bpy.data.scenes))
