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
    col.use_property_split = True
    col.use_property_decorate = False

    # Only MESH obects have explicit UV maps.
    # UV maps are per-object, so if more than one object is selected hide the
    # selector to avoid confusion. Note that the active object might be
    # unselected, hence the < 2 check.
    if (len(context.selected_objects) < 2) and ob and (ob.type == 'MESH'):
        if isObjectVrayProxy(ob):
            # Show the selector if multiple objects of a different type
            col.prop(propGroup, 'uvw_channel', text="UVW Channel")
        else:
            col.prop_search(propGroup, 'uv_set_name', ob.data, 'uv_layers', text="UV Layer")
    
    pluginModule = getPluginModule('UVWGenMayaPlace2dTexture')
    painter = UIPainter(context, pluginModule, propGroup, node)
    painter.renderWidgets(layout, pluginModule.Node['widgets'], True)
