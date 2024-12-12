
from vray_blender.lib.defs import ExporterContext
from vray_blender.lib import export_utils
from vray_blender.lib import plugin_utils


plugin_utils.loadPluginOnModule(globals(), __name__)

ENGINE_SPHERICAL_HARMONICS = '4'

def exportCustom(ctx: ExporterContext, pluginDesc):
    vrayScene = ctx.dg.scene.vray
    vrayExporter  = vrayScene.Exporter

    if vrayScene.SettingsGI.primary_engine != ENGINE_SPHERICAL_HARMONICS:
        return
    if vrayExporter.spherical_harmonics != 'RENDER':
        return
    
    return export_utils.exportPluginCommon(ctx, pluginDesc)
