# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later


from vray_blender.exporting.tools import isObjectVrayProxy
from vray_blender.lib import plugin_utils
from vray_blender.lib.draw_utils import UIPainter
from vray_blender.plugins import getPluginModule

plugin_utils.loadPluginOnModule(globals(), __name__)


def nodeDraw(context, layout, node):
    propGroup = node.UVWGenMayaPlace2dTexture
    ob = context.object
    
    col = layout.column()
    col.use_property_decorate = False

    # Only MESH obects have explicit UV maps.
    # UV maps are per-object, so if more than one object is selected hide the
    # selector to avoid confusion. Note that the active object might be
    # unselected, hence the < 2 check.
    # No label on either picker. prop_search takes no placeholder, and Blender's UV Map node
    # draws this bare too.
    if (len(context.selected_objects) < 2) and ob and (ob.type == 'MESH'):
        if isObjectVrayProxy(ob):
            if ob.data.uv_layers:
                # Proxy has named UV layers from the proxy file (e.g. vray_channel_id_0, vray_channel_id_1)
                col.prop_search(propGroup, 'uv_set_name', ob.data, 'uv_layers',
                                icon='GROUP_UVS', text="")
        else:
            col.prop_search(propGroup, 'uv_set_name', ob.data, 'uv_layers',
                            icon='GROUP_UVS', text="")
    
    pluginModule = getPluginModule('UVWGenMayaPlace2dTexture')
    painter = UIPainter(context, pluginModule, propGroup, node)
    painter.renderWidgets(layout, pluginModule.Node['widgets'], True)
