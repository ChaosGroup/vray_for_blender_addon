# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later


import bpy

from vray_blender.lib.defs import ExporterContext
from vray_blender.lib import export_utils
from vray_blender.lib import plugin_utils


plugin_utils.loadPluginOnModule(globals(), __name__)


def exportCustom(ctx: ExporterContext, pluginDesc):
    unit_settings = bpy.context.scene.unit_settings

    if unit_settings.system != 'NONE':
        pluginDesc.setAttribute('meters_scale', unit_settings.scale_length)
        # Not scaled by meters_scale^2: V-Ray already divides flux-unit lights by it, so the
        # same physical scene renders the same at any scale_length (see upgrade_0058.py).
        pluginDesc.setAttribute('photometric_scale', pluginDesc.vrayPropGroup.photometric_scale)


    sceneFps = bpy.context.scene.render.fps / bpy.context.scene.render.fps_base
    pluginDesc.setAttribute('frames_scale', sceneFps)
    pluginDesc.setAttribute('seconds_scale', 1.0 / sceneFps)

    return export_utils.exportPluginCommon(ctx, pluginDesc)
