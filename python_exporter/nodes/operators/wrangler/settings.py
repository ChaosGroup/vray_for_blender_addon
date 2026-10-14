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
from vray_blender.lib.attribute_utils import (copyPropGroupValues, getBlenderDefault,
                                              getPreservedRefAttrs, resetPropGroupToDefaults)
from vray_blender.plugins import getPluginModule
from vray_blender.nodes.utils import (getPluginTypeOfNode, getPropGroupOfNode, getVRayPropGroupNames,
                                      isVrayNode)
from vray_blender.nodes.operators.wrangler.poll import isVrayEditor, hasEditTree


def _socketsByPlugin(node):
    """ The node's value-bearing input sockets as {pluginType: {attr: socket}}.

        Bucketed per plugin because a meta node carries the sockets of several plugins, and two
        plugins may declare the same attr name.
    """
    result = {}

    for sock in node.inputs:
        attrName = getattr(sock, 'vray_attr', '')
        if not (attrName and hasattr(sock, 'value')):
            continue
        fnPlugin = getattr(sock, 'getPluginName', None)
        pluginType = fnPlugin() if fnPlugin else getattr(node, 'vray_plugin', 'NONE')
        result.setdefault(pluginType, {})[attrName] = sock

    return result


def _resetPluginGroup(node, pluginType: str, pluginModule, sockByAttr: dict, skipAttrs: set):
    """ Reset one of the node's plugin property groups and the sockets backing its params.

        @param sockByAttr - {attr: socket} of this plugin only
        @param skipAttrs - attr names to leave untouched
    """
    # Params whose value lives on an input socket are reset on the socket below.
    if (propGroup := getattr(node, pluginType, None)) is not None:
        resetPropGroupToDefaults(propGroup, pluginModule, skipAttrs=set(sockByAttr.keys()) | skipAttrs)

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


def resetNode(node, skipAttrs=None):
    """ Reset every JSON-defined parameter to its default.

        Mirrors PropertyContext priority: if a param has an input socket with
        a .value attribute (matched by vray_attr), reset that; otherwise reset
        the PropGroup entry. TEMPLATE params are reset via the template's own
        resetToDefaults() (handled inside resetPropGroupToDefaults).

        All of the node's plugin property groups are reset, not just the one named by
        'vray_plugin': a meta node draws several plugins on the same property page.

        Dynamically-created sockets (no vray_attr, e.g. VRaySocketTexMulti) are not backed by a JSON
        param; their value-bearing properties are reset to registered defaults via property_unset.
        Links are preserved.

        @param skipAttrs - optional set of attr names to leave untouched, on top of each plugin's
            own preserved references (see getPreservedRefAttrs).
    """
    skipAttrs = set(skipAttrs or ())
    sockByPlugin = _socketsByPlugin(node)
    pluginTypes = [p for p in getVRayPropGroupNames(node) if p]

    for pluginType in pluginTypes:
        if pluginModule := getPluginModule(pluginType):
            _resetPluginGroup(node, pluginType, pluginModule, sockByPlugin.get(pluginType, {}),
                              skipAttrs | getPreservedRefAttrs(pluginType))

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
    mainPluginModule = getPluginModule(getattr(node, 'vray_plugin', ''))
    if mainPluginModule is not None and (fnReset := getattr(mainPluginModule, 'nodeReset', None)):
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
            if not getPluginTypeOfNode(node):
                continue
            resetNode(node)
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
