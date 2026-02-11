# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later


import bpy

from vray_blender.lib.defs import ExporterContext
from vray_blender.lib import export_utils
from vray_blender.lib import plugin_utils


plugin_utils.loadPluginOnModule(globals(), __name__)


def _updateSystemGamma(self, context):
    if self.sync_with_gamma:
        view_settings = context.scene.view_settings
        view_settings.gamma = 1.0 / self.gamma

# Inject update callback
for attrDesc in globals()['Parameters']:
    if attrDesc['attr'] in {'gamma', 'sync_with_gamma'}:
        attrDesc['update'] = "_updateSystemGamma"


def exportCustom(ctx: ExporterContext, pluginDesc):
    current_scene_cm = ctx.dg.scene.vray.SettingsColorMapping

    if ctx.preview and current_scene_cm.preview_use_scene_cm:
        # Use color mapping settings from current scene not from preview scene
        propGroup = current_scene_cm

    return export_utils.exportPluginCommon(ctx, pluginDesc)
