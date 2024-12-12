from vray_blender.lib import plugin_utils
from vray_blender.plugins.light.light_tools import onUpdateColor

plugin_utils.loadPluginOnModule(globals(), __name__)


def onUpdateAttribute(src, context, attrName):
    onUpdateColor(src, 'LightSphere', attrName)


