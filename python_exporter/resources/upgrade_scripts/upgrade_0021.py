# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
from vray_blender.utils.upgrade_scene import scopedForUpgrade

def run():
    for scene in scopedForUpgrade(bpy.data.scenes):
        scene.world.vray.RenderChannelDenoiser.name = 'Denoiser'


def check():
    for scene in scopedForUpgrade(bpy.data.scenes):
        if hasattr(scene.world, "vray") and scene.world.vray.RenderChannelDenoiser.name != 'Denoiser':
            return True
    return False
