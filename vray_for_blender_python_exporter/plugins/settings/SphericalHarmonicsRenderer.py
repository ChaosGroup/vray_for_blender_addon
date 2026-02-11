# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later


from vray_blender.lib import export_utils, plugin_utils
from vray_blender.lib.defs import ExporterContext, PluginDesc, AttrPlugin


plugin_utils.loadPluginOnModule(globals(), __name__)

ENGINE_SPHERICAL_HARMONICS = '4'

def exportCustom(ctx: ExporterContext, pluginDesc: PluginDesc):
    vrayScene = ctx.dg.scene.vray
    vrayExporter  = vrayScene.Exporter

    if vrayScene.SettingsGI.primary_engine != ENGINE_SPHERICAL_HARMONICS:
        return AttrPlugin()
    if vrayExporter.spherical_harmonics != 'RENDER':
        return AttrPlugin()
    
    return export_utils.exportPluginCommon(ctx, pluginDesc)
