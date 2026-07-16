# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
from vray_blender.lib import plugin_utils

plugin_utils.loadPluginOnModule(globals(), __name__)

def nodeDraw(context: bpy.types.Context, layout: bpy.types.UILayout, node):
    row = layout.row(align=True)
    row.label(text='Mode')
    row.prop(node.TexUserColor, 'mode', text='')

    row = layout.row(align=True)
    row.label(text='Attribute')
    if node.TexUserColor.mode == '0':
        row.prop(node.TexUserColor, 'user_attribute', text='')
    else:
        obj = context.active_object
        if obj and obj.type == 'MESH' and (dg := context.evaluated_depsgraph_get()):
            obj = obj.evaluated_get(dg)
            row.prop_search(node.TexUserColor, 'user_attribute', obj.data, 'color_attributes', icon='GROUP_VCOL', text='')
        else:
            row.prop(node.TexUserColor, 'user_attribute', icon='GROUP_VCOL', text='')
            layout.label(text='No mesh in active object', icon='ERROR')

def nodeDrawSide(context: bpy.types.Context, layout: bpy.types.UILayout, node):
    nodeDraw(context, layout, node)

    row = layout.row(align=True)
    row.label(text='Default Color')
    row.prop(node.TexUserColor, 'default_color', text='')