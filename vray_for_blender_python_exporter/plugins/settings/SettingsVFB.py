# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later


from vray_blender.engine.vfb_event_handler import VfbEventHandler
from vray_blender.lib.defs import ExporterContext, PluginDesc
from vray_blender.lib import export_utils, plugin_utils


plugin_utils.loadPluginOnModule(globals(), __name__)


def exportCustom(ctx: ExporterContext, pluginDesc: PluginDesc):
    scene = ctx.dg.scene

    pluginDesc.setAttribute("vfb2_layers", VfbEventHandler.getVfbLayers())

    if scene.vray.SettingsVFB.use:
        return export_utils.exportPluginCommon(ctx, pluginDesc)
