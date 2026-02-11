# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

from vray_blender.lib.defs import ExporterContext
from vray_blender.lib import export_utils, path_utils
from vray_blender.lib import plugin_utils
from vray_blender.engine.renderer_ipr_viewport import VRayRendererIprViewport
from vray_blender.engine.renderer_ipr_vfb import VRayRendererIprVfb
from vray_blender import debug


plugin_utils.loadPluginOnModule(globals(), __name__)

def onCausticsEnabled(SettingsCaustics, context, attrName):
    # SettingsCaustics creates channel on enablement and needs reset to be properly visualized
    for iprRenderer in (VRayRendererIprViewport, VRayRendererIprVfb):
        if iprRenderer.isActive() and SettingsCaustics.on:
            iprRenderer.reset()
            debug.report("WARNING", "Caustics will be forced to 'Progressive' mode during interactive rendering")
            return


def exportCustom(ctx: ExporterContext, pluginDesc):
    if getattr(pluginDesc.vrayPropGroup, 'auto_save'):
        autosaveFile = getattr(pluginDesc.vrayPropGroup, "auto_save_file")
        autosaveFile = path_utils.formatResourcePath(autosaveFile, alllowRelativePaths = ctx.allowRelativePaths)
        path_utils.createDirectoryFromFilepath(autosaveFile)

    # Only 'Progressive' mode is available during IPR
    if ctx.interactive:
        pluginDesc.setAttribute("mode", "2")

    return export_utils.exportPluginCommon(ctx, pluginDesc)
