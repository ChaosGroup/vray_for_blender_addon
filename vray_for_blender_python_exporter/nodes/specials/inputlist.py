# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later


import bpy

from ..importing import _getInputSocketNameByAttr
from ..sockets import addInput, addOutput
from ..operators import sockets as SocketOperators
from vray_blender.lib.mixin import VRayNodeBase, VRayOperatorBase


class VRAY_OT_node_list_plugin_add(SocketOperators.VRayNodeAddCustomSocket, VRayOperatorBase):
    bl_idname      = 'vray.node_list_plugin_add'
    bl_label       = "Add Plugin Socket"
    bl_description = "Adds Plugin sockets"
    bl_options     = {'INTERNAL', 'UNDO'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.vray_socket_type = 'VRaySocketObject'
        self.vray_socket_name = "Plugin"


class VRAY_OT_node_list_plugin_del(SocketOperators.VRayNodeDelCustomSocket, VRayOperatorBase):
    bl_idname      = 'vray.node_list_plugin_del'
    bl_label       = "Remove Plugin Socket"
    bl_description = "Removes Plugin socket (only not linked sockets will be removed)"
    bl_options     = {'INTERNAL', 'UNDO'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.vray_socket_type = 'VRaySocketObject'
        self.vray_socket_name = "Plugin"


class VRayPluginListHolder(VRayNodeBase):
    """ Representation of V-Ray Plugin List """
    bl_idname = 'VRayPluginListHolder'
    bl_label  = 'V-Ray Plugin List'
    bl_icon   = 'PRESET'

    vray_type   = 'NONE'
    vray_plugin = 'NONE'

    def init(self, context):
        addInput(self, 'VRaySocketObject', "Plugin")
        addOutput(self, 'VRaySocketObjectList', "List")

    def draw_buttons(self, context, layout):
        split = layout.split()
        row = split.row(align=True)
        row.operator('vray.node_list_plugin_add', icon="ADD", text="Add")
        row.operator('vray.node_list_plugin_del', icon="REMOVE", text="")


def getRegClasses():
    return (
        VRAY_OT_node_list_plugin_del,
        VRAY_OT_node_list_plugin_add,
        VRayPluginListHolder
    )


def register():
    for regClass in getRegClasses():
        bpy.utils.register_class(regClass)


def unregister():
    for regClass in getRegClasses():
        bpy.utils.unregister_class(regClass)
