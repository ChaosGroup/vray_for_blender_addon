# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

""" Copy Settings and Reset Selected.

    V-Ray nodes hold their value state either in a dynamic PropertyGroup at
    getattr(node, node.vray_plugin), or directly on input sockets (.value).
    Sockets take precedence (same as PropertyContext), so reset/copy must
    handle both storages.
"""

import bpy

from vray_blender.lib.mixin import VRayOperatorBase
from vray_blender.lib.attribute_utils import copyPropGroupValues, getBlenderDefault
from vray_blender.plugins import getPluginModule
from vray_blender.nodes.utils import getPluginTypeOfNode, getPropGroupOfNode, isVrayNode
from vray_blender.nodes.operators.wrangler.poll import isVrayEditor, hasEditTree


def _resetNode(node, pluginModule):
    """ Reset every JSON-defined parameter to its default.

        Mirrors PropertyContext priority: if a param has an input socket with
        a .value attribute (matched by vray_attr), reset that; otherwise reset
        the PropGroup entry. TEMPLATE attrs are skipped.

        Socket-local properties with no vray_attr (e.g. VRaySocketTexMulti.id)
        are intentionally left alone — they have no JSON default to reset to.
    """
    propGroup = getPropGroupOfNode(node) if node.vray_plugin else None
    sockByAttr = {getattr(s, 'vray_attr', ''): s for s in node.inputs if getattr(s, 'vray_attr', '')}

    for param in pluginModule.Parameters:
        if param['type'] == 'TEMPLATE':
            continue
        blDefault = getBlenderDefault(param)
        if blDefault is None:
            continue
        attrName = param['attr']
        sock = sockByAttr.get(attrName)
        if sock and hasattr(sock, 'value'):
            try:
                sock.value = blDefault
            except (TypeError, ValueError, AttributeError):
                pass
        elif propGroup:
            try:
                setattr(propGroup, attrName, blDefault)
            except (TypeError, ValueError, AttributeError):
                pass


class VRAY_OT_WR_copy_settings(VRayOperatorBase):
    """Copy settings from the active node to all other selected nodes of the same V-Ray plugin"""
    bl_idname = "vray.wr_copy_settings"
    bl_label = "Copy Settings to Selected"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        if not (isVrayEditor(context) and hasEditTree(context)):
            return False
        active = context.space_data.edit_tree.nodes.active
        return bool(active and isVrayNode(active) and getPluginTypeOfNode(active))

    def execute(self, context):
        ntree = context.space_data.edit_tree
        active = ntree.nodes.active
        plugin = getPluginTypeOfNode(active) if active and isVrayNode(active) else None
        srcPropGroup = getPropGroupOfNode(active) if plugin else None
        if not (plugin and srcPropGroup):
            self.report({'WARNING'}, "Active node is not a V-Ray node.")
            return {'CANCELLED'}

        pluginModule = getPluginModule(plugin)
        if pluginModule is None:
            self.report({'WARNING'}, f"No plugin module for '{plugin}'.")
            return {'CANCELLED'}

        count = 0
        for node in ntree.nodes:
            if node is active or not node.select or not isVrayNode(node):
                continue
            if getPluginTypeOfNode(node) != plugin:
                continue
            if destPropGroup := getPropGroupOfNode(node):
                copyPropGroupValues(srcPropGroup, destPropGroup, pluginModule)
                count += 1

        if count == 0:
            self.report({'INFO'}, "No matching selected nodes to copy to.")
            return {'CANCELLED'}
        self.report({'INFO'}, f"Copied settings to {count} node(s).")
        return {'FINISHED'}


class VRAY_OT_WR_reset_nodes(VRayOperatorBase):
    """Reset selected V-Ray nodes' settings to their plugin defaults"""
    bl_idname = "vray.wr_reset_nodes"
    bl_label = "Reset Selected Nodes"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return isVrayEditor(context) and hasEditTree(context)

    def execute(self, context):
        count = 0
        for node in context.space_data.edit_tree.nodes:
            if not node.select or not isVrayNode(node):
                continue
            plugin = getPluginTypeOfNode(node)
            if not plugin:
                continue
            if pluginModule := getPluginModule(plugin):
                _resetNode(node, pluginModule)
                count += 1

        if count == 0:
            self.report({'INFO'}, "No V-Ray nodes selected to reset.")
            return {'CANCELLED'}
        context.space_data.edit_tree.update_tag()
        self.report({'INFO'}, f"Reset {count} node(s) to defaults.")
        return {'FINISHED'}


def getRegClasses():
    return (
        VRAY_OT_WR_copy_settings,
        VRAY_OT_WR_reset_nodes,
    )


def register():
    for regClass in getRegClasses():
        bpy.utils.register_class(regClass)


def unregister():
    for regClass in reversed(getRegClasses()):
        bpy.utils.unregister_class(regClass)
