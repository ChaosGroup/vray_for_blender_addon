
from vray_blender.lib import plugin_utils
from vray_blender.lib.draw_utils import UIPainter
from vray_blender.plugins import getPluginModule

plugin_utils.loadPluginOnModule(globals(), __name__)


def nodeDraw(context, layout, node):
    propGroup = node.UVWGenMayaPlace2dTexture
    ob = context.object

    split = layout.split(factor=0.3)
    split.label(text="UV Layer:")
    if ob and ob.type == 'MESH':
        split.prop_search(propGroup, 'uv_set_name', ob.data, 'uv_layers', text="")
    else:
        split.prop(propGroup, 'uv_set_name', text="")

    pluginModule = getPluginModule('UVWGenMayaPlace2dTexture')
    painter = UIPainter(context, pluginModule, propGroup, node)
    painter.renderWidgets(layout, pluginModule.Node['widgets'])
