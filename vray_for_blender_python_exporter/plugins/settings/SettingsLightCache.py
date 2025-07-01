
import bpy

from vray_blender.lib.defs import ExporterContext
from vray_blender import debug
from vray_blender.lib import export_utils, path_utils
from vray_blender.lib import plugin_utils


plugin_utils.loadPluginOnModule(globals(), __name__)


def exportCustom(ctx: ExporterContext, pluginDesc):
    scene = ctx.dg.scene
    vrayExporter = scene.vray.Exporter
    propGroup = pluginDesc.vrayPropGroup

    if propGroup.num_passes_auto:
        pluginDesc.setAttribute('num_passes', bpy.context.scene.render.threads)

    if getattr(propGroup, 'auto_save'):
        autosaveFile = getattr(propGroup, "auto_save_file")
        autosaveFile = path_utils.formatResourcePath(autosaveFile, allowRelative = ctx.exportOnly)
        path_utils.createDirectoryFromFilepath(autosaveFile)

    return export_utils.exportPluginCommon(ctx, pluginDesc)
