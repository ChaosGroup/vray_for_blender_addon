from vray_blender.lib.defs import NodeContext, PluginDesc
from vray_blender.lib import export_utils, plugin_utils
from vray_blender.plugins.light.light_tools import onUpdateColorTemperature

plugin_utils.loadPluginOnModule(globals(), __name__)


def onUpdateAttribute(src, context, attrName):
    onUpdateColorTemperature(src, 'LightDome', attrName)
