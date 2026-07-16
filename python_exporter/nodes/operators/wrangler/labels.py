# SPDX-FileCopyrightText: Blender Foundation
# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Based on Blender's Node Wrangler add-on (GPL-2.0-or-later).

""" Node-label manipulation operators: copy label, clear labels,
    prepend/append/replace labels.
"""

import bpy

from vray_blender.lib.mixin import VRayOperatorBase
from vray_blender.nodes.operators.wrangler.poll import isVrayEditor, hasEditTree, hasSelection


class VRAY_OT_WR_clear_label(VRayOperatorBase):
    bl_idname = "vray.wr_clear_label"
    bl_label = "Clear Label"
    bl_description = "Clear labels on selected nodes"
    bl_options = {'REGISTER', 'UNDO'}

    option: bpy.props.BoolProperty(default=False)

    @classmethod
    def poll(cls, context):
        return isVrayEditor(context) and hasEditTree(context) and hasSelection(context)

    def execute(self, context):
        for node in context.selected_nodes:
            node.label = ''
        return {'FINISHED'}

    def invoke(self, context, event):
        if self.option:
            return self.execute(context)
        return context.window_manager.invoke_confirm(self, event)


class VRAY_OT_WR_modify_labels(VRayOperatorBase):
    """Modify labels of all selected nodes"""
    bl_idname = "vray.wr_modify_labels"
    bl_label = "Modify Labels"
    bl_options = {'REGISTER', 'UNDO'}

    prepend: bpy.props.StringProperty(name="Add to Beginning")
    append: bpy.props.StringProperty(name="Add to End")
    replace_from: bpy.props.StringProperty(name="Text to Replace")
    replace_to: bpy.props.StringProperty(name="Replace with")

    @classmethod
    def poll(cls, context):
        return isVrayEditor(context) and hasEditTree(context) and hasSelection(context)

    def execute(self, context):
        for node in context.selected_nodes:
            # node.label is empty for fresh nodes - fall back to bl_label so
            # prepend/append/replace operate on the visible name.
            base = node.label or node.bl_label
            node.label = self.prepend + base.replace(self.replace_from, self.replace_to) + self.append
        return {'FINISHED'}

    def invoke(self, context, event):
        self.prepend = ""
        self.append = ""
        return context.window_manager.invoke_props_dialog(self)


class VRAY_OT_WR_copy_label(VRayOperatorBase):
    bl_idname = "vray.wr_copy_label"
    bl_label = "Copy Label"
    bl_description = "Copy label from active to selected nodes"
    bl_options = {'REGISTER', 'UNDO'}

    option: bpy.props.EnumProperty(
        name="Option",
        description="Source of label",
        items=(
            ('FROM_ACTIVE', 'From Active', 'From active node'),
            ('FROM_NODE', 'From Node', 'From node linked to selected node'),
            ('FROM_SOCKET', 'From Socket', 'From socket linked to selected node'),
        ),
    )

    @classmethod
    def poll(cls, context):
        return (isVrayEditor(context)
                and hasEditTree(context)
                and hasSelection(context, minCount=2))

    def execute(self, context):
        nodes = context.space_data.edit_tree.nodes
        active = nodes.active
        if self.option == 'FROM_ACTIVE':
            if not active:
                return {'CANCELLED'}
            sourceLabel = active.label
            for node in context.selected_nodes:
                if node is not active:
                    node.label = sourceLabel
        elif self.option == 'FROM_NODE':
            for node in context.selected_nodes:
                for input in node.inputs:
                    if input.links:
                        node.label = input.links[0].from_node.label
                        break
        elif self.option == 'FROM_SOCKET':
            for node in context.selected_nodes:
                for input in node.inputs:
                    if input.links:
                        node.label = input.links[0].from_socket.name
                        break
        return {'FINISHED'}


def getRegClasses():
    return (
        VRAY_OT_WR_clear_label,
        VRAY_OT_WR_modify_labels,
        VRAY_OT_WR_copy_label,
    )


def register():
    for regClass in getRegClasses():
        bpy.utils.register_class(regClass)


def unregister():
    for regClass in reversed(getRegClasses()):
        bpy.utils.unregister_class(regClass)
