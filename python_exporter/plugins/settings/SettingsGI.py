# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later


import bpy

from vray_blender.lib.defs import ExporterContext
from vray_blender.lib import export_utils, plugin_utils, settings_defs

plugin_utils.loadPluginOnModule(globals(), __name__)

def exportCustom(ctx: ExporterContext, pluginDesc):

    if ctx.interactive and (not getattr(pluginDesc.vrayPropGroup, 'use_light_cache_for_interactive')):
        pluginDesc.setAttribute('secondary_engine', settings_defs.GIEngine.BruteForce)

    return export_utils.exportPluginCommon(ctx, pluginDesc)
