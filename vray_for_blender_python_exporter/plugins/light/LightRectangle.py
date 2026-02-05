import bpy

from vray_blender.lib import plugin_utils
from vray_blender.plugins.light.light_tools import onUpdateColorTemperature

plugin_utils.loadPluginOnModule(globals(), __name__)


def nodeUpdate(node: bpy.types.Node):
    if node.mute:
        node.mute = False


def onUpdateAttribute(src, context: bpy.types.Context, attrName: str):
    onUpdateColorTemperature(src, 'LightRectangle', attrName)


def onUpdateWidth(src, context, attrName):
    propGroup = src
    
    if propGroup.is_disc:
        propGroup.v_size = propGroup.u_size

def widgetDrawSize(context, layout, propGroup, widgetAttr):
    light = context.active_object
    attrName = widgetAttr['name']
    
    attr = 'size' if attrName == 'u_size' else  'size_y'
    layout.prop(light.data, attr, text=widgetAttr.get('label', attrName))
