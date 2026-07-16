# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy

from vray_blender.exporting.tools import removeOutputSocketLinks, getOutputSocketByAttr
from vray_blender.lib import plugin_utils
from vray_blender.lib.mixin import VRayOperatorBase

plugin_utils.loadPluginOnModule(globals(), __name__)


# Number of columns to spread the (long) channel list over in the Add/Remove menus.
_MENU_COLUMNS = 3


def _outputChannels(node):
    """ (identifier, name, description) for every selectable output channel. """
    return [(i.identifier, i.name, i.description)
            for i in node.TexFloatOp.bl_rna.properties['outputs'].enum_items]


def _outputItems(node, wantShown):
    """ Output channels filtered by whether their socket is currently shown.
        wantShown=False -> channels that can be added, True -> channels that can be removed.
    """
    items = []
    if node is not None and hasattr(node, 'TexFloatOp'):
        for identifier, name, desc in _outputChannels(node):
            sock = getOutputSocketByAttr(node, identifier)
            if bool(sock and sock.enabled) == wantShown:
                items.append((identifier, name, desc))
    return items


def _drawChannelMenu(menu, context, wantShown, operatorId):
    node = getattr(context, 'node', None) or getattr(context, 'active_node', None)
    items = _outputItems(node, wantShown)

    # Lay out as explicit side-by-side columns so the popup sizes to its content
    # instead of stretching to fill the screen width (as column_flow does).
    row = menu.layout.row()
    perColumn = (len(items) + _MENU_COLUMNS - 1) // _MENU_COLUMNS
    for start in range(0, len(items), perColumn or 1):
        column = row.column(align=True)
        for identifier, name, _desc in items[start:start + perColumn]:
            column.operator(operatorId, text=name).channel = identifier


class VRAY_MT_node_texfloatop_add(bpy.types.Menu):
    bl_idname = 'VRAY_MT_node_texfloatop_add'
    bl_label  = "Add Output"

    def draw(self, context):
        _drawChannelMenu(self, context, wantShown=False, operatorId='vray.node_texfloatop_add_output')


class VRAY_MT_node_texfloatop_remove(bpy.types.Menu):
    bl_idname = 'VRAY_MT_node_texfloatop_remove'
    bl_label  = "Remove Output"

    def draw(self, context):
        _drawChannelMenu(self, context, wantShown=True, operatorId='vray.node_texfloatop_remove_output')


class VRAY_OT_node_texfloatop_add_output(VRayOperatorBase):
    bl_idname      = 'vray.node_texfloatop_add_output'
    bl_label       = "Add Output"
    bl_description = "Show an output socket for the selected channel"
    bl_options     = {'INTERNAL', 'UNDO'}

    channel: bpy.props.StringProperty()

    def execute(self, context):
        outSock = getOutputSocketByAttr(context.node, self.channel)
        outSock.hide    = False
        outSock.enabled = True
        return {'FINISHED'}


class VRAY_OT_node_texfloatop_remove_output(VRayOperatorBase):
    bl_idname      = 'vray.node_texfloatop_remove_output'
    bl_label       = "Remove Output"
    bl_description = "Hide the selected output socket"
    bl_options     = {'INTERNAL', 'UNDO'}

    channel: bpy.props.StringProperty()

    def execute(self, context):
        outSock = getOutputSocketByAttr(context.node, self.channel)
        outSock.enabled = False
        outSock.hide    = True
        removeOutputSocketLinks(outSock)
        return {'FINISHED'}


def nodeDraw(context, layout, node):
    propGroup = node.TexFloatOp

    channels = _outputChannels(node)
    shown = sum(1 for ident, _, _ in channels
                if (s := getOutputSocketByAttr(node, ident)) and s.enabled)
    total = len(channels)

    col = layout.column()
    col.use_property_decorate = False
    col.context_pointer_set('node', node)

    # [ mode | + | - ] on a single row.
    row = col.row(align=True)
    row.prop(propGroup, 'mode', text="")

    addCell = row.row(align=True)
    addCell.enabled = shown < total
    addCell.menu('VRAY_MT_node_texfloatop_add', text="", icon='ADD')

    removeCell = row.row(align=True)
    removeCell.enabled = shown > 0
    removeCell.menu('VRAY_MT_node_texfloatop_remove', text="", icon='REMOVE')


def getRegClasses():
    return (
        VRAY_OT_node_texfloatop_add_output,
        VRAY_OT_node_texfloatop_remove_output,
        VRAY_MT_node_texfloatop_add,
        VRAY_MT_node_texfloatop_remove,
    )


def register():
    for regClass in getRegClasses():
        bpy.utils.register_class(regClass)


def unregister():
    for regClass in getRegClasses():
        bpy.utils.unregister_class(regClass)
