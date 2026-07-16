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
from vray_blender.lib.attribute_types import StructuralSocketProps
from vray_blender.lib.attribute_utils import copyPropGroupValues, getBlenderDefault, resetPropGroupToDefaults
from vray_blender.plugins import getPluginModule
from vray_blender.nodes.utils import getPluginTypeOfNode, getPropGroupOfNode, isVrayNode
from vray_blender.nodes.operators.wrangler.poll import isVrayEditor, hasEditTree


def resetNode(node, pluginModule, skipAttrs=None):
    """ Reset every JSON-defined parameter to its default.

        Mirrors PropertyContext priority: if a param has an input socket with
        a .value attribute (matched by vray_attr), reset that; otherwise reset
        the PropGroup entry. TEMPLATE params are reset via the template's own
        resetToDefaults() (handled inside resetPropGroupToDefaults).

        Dynamically-created sockets (no vray_attr, e.g. VRaySocketTexMulti) are not backed by a JSON
        param; their value-bearing properties are reset to registered defaults via property_unset.
        Links are preserved.

        @param skipAttrs - optional set of attr names to leave untouched (e.g. properties that
            reference an external file or another object, which the reset should preserve).
    """
    skipAttrs = skipAttrs or set()
    propGroup = getPropGroupOfNode(node) if node.vray_plugin else None
    sockByAttr = {getattr(s, 'vray_attr', ''): s for s in node.inputs
                  if getattr(s, 'vray_attr', '') and hasattr(s, 'value')}

    # Reset PropGroup entries, skipping params whose value lives on an input socket or that the
    # caller asked to preserve (the latter also covers preserved TEMPLATE params, e.g. object_selector).
    if propGroup:
        resetPropGroupToDefaults(propGroup, pluginModule, skipAttrs=set(sockByAttr.keys()) | skipAttrs)

    # Reset the socket-backed params on their sockets.
    for param in pluginModule.Parameters:
        if param['type'] == 'TEMPLATE':
            continue
        if param['attr'] in skipAttrs:
            continue
        sock = sockByAttr.get(param['attr'])
        if not sock:
            continue
        blDefault = getBlenderDefault(param)
        if blDefault is None:
            continue
        try:
            sock.value = blDefault
        except (TypeError, ValueError, AttributeError):
            pass

    # Dynamically-added sockets carry no vray_attr (not backed by a JSON param), so the loops above
    # never touch them. Reset their value-bearing properties to registered defaults. Values only:
    # property_unset resets a property's VALUE — it does not remove links (links live in the node
    # tree, not on the socket), and read-only built-ins (is_linked, link_limit, name) are skipped.
    for sock in node.inputs:
        if getattr(sock, 'vray_attr', ''):
            continue
        for prop in sock.bl_rna.properties:
            if (not prop.is_runtime) or prop.is_readonly or prop.identifier in StructuralSocketProps:
                continue
            try:
                sock.property_unset(prop.identifier)
            except (TypeError, RuntimeError, AttributeError):
                pass

    # Re-apply socket values that the node sets explicitly at creation (e.g. TexMulti ids + gradient).
    # The generic pass above reset them to property defaults, so this runs last to overlay the correct
    # values onto the existing sockets (count preserved). Mirrors the nodeInit/nodeCopy/nodeFree hooks:
    # a module-level nodeReset for generic plugin nodes, else a method on special node classes.
    if pluginModule is not None and (fnReset := getattr(pluginModule, 'nodeReset', None)):
        fnReset(node)
    elif fnReset := getattr(node, 'nodeReset', None):
        fnReset()


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
                resetNode(node, pluginModule)
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
