
from vray_blender.lib.defs import ExporterContext
from vray_blender.lib import export_utils
from vray_blender.lib import plugin_utils


plugin_utils.loadPluginOnModule(globals(), __name__)


def exportCustom(ctx: ExporterContext, pluginDesc):
    vrayScene = ctx.dg.scene.vray
    vrayExporter  = vrayScene.Exporter

    if vrayScene.SettingsGI.primary_engine != '4': # EngineSphericalharmonics
        return
    if vrayExporter.spherical_harmonics != 'BAKE':
        return
    
    return export_utils.exportPluginCommon(ctx, pluginDesc)
