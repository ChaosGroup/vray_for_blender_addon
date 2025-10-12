
import bpy, os

from vray_blender.engine.renderer_ipr_viewport import VRayRendererIprViewport
from vray_blender.lib.defs import ExporterContext
from vray_blender.lib import export_utils
from vray_blender.lib import plugin_utils
from vray_blender.plugins import PLUGIN_MODULES

plugin_utils.loadPluginOnModule(globals(), __name__)


def exportCustom(ctx: ExporterContext, pluginDesc):
    scene = ctx.dg.scene
    vrayScene = scene.vray
    vrayDR    = vrayScene.VRayDR

    pluginDesc.setAttribute('misc_transferAssets', vrayDR.transferAssets)

    # TODO: The mtl_override processing has been removed from the C++ implementation,
    # do we still need it?
    pluginModule = PLUGIN_MODULES[pluginDesc.type]
    propGroup = getattr(vrayScene, pluginDesc.type)
    attributes = sorted(pluginModule.Parameters, key=lambda t: t['attr'])

    for attrDesc in attributes:
        key = attrDesc['attr']
        if key in propGroup and attrDesc['default'] != getattr(propGroup, key):
            pluginDesc.setAttribute(key, getattr(propGroup, key))

    if bpy.app.background:
        threadPriority = os.getenv("VRAY_BLENDER_HEADLESS_THREAD_PRIORITY", 0)
    else:
        threadPriority = 2 if bpy.context.scene.vray.Exporter.lower_thread_priority else 1
    pluginDesc.setAttribute("misc_lowThreadPriority", threadPriority)

    pluginDesc.removeAttribute('mtl_override')
    pluginDesc.vrayPropGroup = propGroup

    return export_utils.exportPluginCommon(ctx, pluginDesc)


def widgetDrawMaxMipmapResolution(context: bpy.types.Context, layout, propGroup, widgetAttr):
    layout.prop(context.scene.vray.SettingsTextureCache, "max_mipmap_resolution")
