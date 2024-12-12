import bpy

from vray_blender.lib import plugin_utils


plugin_utils.loadPluginOnModule(globals(), __name__)


def widgetDrawMinHairWidth(context: bpy.types.Context, layout, propGroup, widgetAttr):
    layout.prop(context.scene.vray.SettingsHair, "min_hair_width")