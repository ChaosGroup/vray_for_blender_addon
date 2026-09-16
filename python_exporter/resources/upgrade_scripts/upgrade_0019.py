# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
from vray_blender.utils.upgrade_scene import scoped

def run():
    for scene in scoped(bpy.data.scenes):
        if not scene.world:
            continue
        denoiser = scene.world.vray.RenderChannelDenoiser
        # Only Intel and NVIDIA denoisers are supported with Intel(1) being the default
        scene.vray.Exporter.viewport_denoiser_engine = '2' if denoiser.engine == '2' else '1'


def check():
    for scene in scoped(bpy.data.scenes):
        if hasattr(scene.world, "vray") and scene.world.vray.VRayRenderChannels.VRayNodeRenderChannelDenoiser.enabled:
            return True
    return False
