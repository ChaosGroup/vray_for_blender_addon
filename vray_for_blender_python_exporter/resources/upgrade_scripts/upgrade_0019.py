# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy

def run():
    for scene in bpy.data.scenes:
        denoiser = scene.world.vray.RenderChannelDenoiser
        nodeDenoiser = scene.world.vray.VRayRenderChannels.VRayNodeRenderChannelDenoiser
        denoiser.viewport_enabled = nodeDenoiser.enabled and denoiser.enabled
        denoiser.viewport_engine = denoiser.engine

def check():
    for scene in bpy.data.scenes:
        if hasattr(scene.world, "vray") and scene.world.vray.VRayRenderChannels.VRayNodeRenderChannelDenoiser.enabled:
            return True
    return False
