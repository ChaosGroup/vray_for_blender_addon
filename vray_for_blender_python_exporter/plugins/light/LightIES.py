import bpy

from vray_blender.lib import plugin_utils
from vray_blender.plugins.light.light_tools import onUpdateColorTemperature

plugin_utils.loadPluginOnModule(globals(), __name__)


def nodeUpdate(node: bpy.types.Node):
    if node.mute:
        node.mute = False


def onUpdateAttribute(src, context: bpy.types.Context, attrName: str):
    onUpdateColorTemperature(src, 'LightIES', attrName)
