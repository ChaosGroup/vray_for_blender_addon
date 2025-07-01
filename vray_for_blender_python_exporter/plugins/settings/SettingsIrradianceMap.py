
from vray_blender.lib.defs import ExporterContext
from vray_blender.lib import export_utils, path_utils, plugin_utils, blender_utils
from vray_blender import debug

plugin_utils.loadPluginOnModule(globals(), __name__)


def exportCustom(ctx: ExporterContext, pluginDesc):
    scene = ctx.dg.scene
    vrayExporter = scene.vray.Exporter
    propGroup = pluginDesc.vrayPropGroup

    if propGroup.min_rate > propGroup.max_rate:
        debug.printInfo('Irradiance Map "Min. Rate" is more then "Max. Rate"!')

        pluginDesc.setAttribute('min_rate', propGroup.max_rate)
        pluginDesc.setAttribute('max_rate', propGroup.min_rate)

    if getattr(propGroup, 'auto_save'):
        autosaveFile = getattr(propGroup, "auto_save_file")
        autosaveFile = path_utils.formatResourcePath(autosaveFile, allowRelativePaths = ctx.exportOnly)
        path_utils.createDirectoryFromFilepath(autosaveFile)

    return export_utils.exportPluginCommon(ctx, pluginDesc)
