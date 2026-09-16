# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later


import bpy

from vray_blender.exporting import node_export as commonNodesExport
from vray_blender.exporting.tools import getInputSocketByAttr
from vray_blender.lib import plugin_utils
from vray_blender.lib.defs import  PluginDesc, NodeContext
from vray_blender.lib.names import Names
from vray_blender.nodes.utils import getNodeOfPropGroup
from vray_blender.plugins import getPluginAttr, getPluginModule
from vray_blender.nodes.specials.gradient_ramp import VRaySocketColorRamp, createColorRampNode

plugin_utils.loadPluginOnModule(globals(), __name__)


# TexRamp is an import-only plugin (hidden from the node-add menu); it mirrors TexGradRamp's
# color-ramp handling. See plugins/skipped_plugins.py (HIDDEN_PLUGINS).
_PLUGIN_TYPE = 'TexRamp'


def nodeInit(node: bpy.types.Node):
    """ Create the gradient ramp node and attach it to the TexRamp node. """
    rampSocket = next(sock for sock in node.inputs if sock.bl_idname == VRaySocketColorRamp.bl_idname)
    createColorRampNode(node, rampSocket)

    node.id_data.nodes.active = node


def widgetDrawRamp(context, layout: bpy.types.UILayout, propGroup, widgetAttr):
    node = getNodeOfPropGroup(propGroup)
    sock = getInputSocketByAttr(node, widgetAttr.get("name", ""))

    if sock is None or not sock.is_linked:
        return

    for link in sock.links:
        ramp_node = link.from_node
        layout.separator()
        box = layout.box()
        box.label(text=sock.identifier)
        if not ramp_node.texture:
            continue
        box.template_color_ramp(ramp_node.texture, 'color_ramp')

        for sock in ramp_node.inputs:
            box.prop(sock, 'value', text=sock.identifier)


def exportTreeNode(nodeCtx: NodeContext):
    node = nodeCtx.node

    pluginDesc = PluginDesc(Names.nextVirtualNode(nodeCtx, _PLUGIN_TYPE), _PLUGIN_TYPE)
    pluginDesc.vrayPropGroup = node.TexRamp

    rampAttributes = ("colors", "positions", "interpolation")

    for sock in node.inputs:
        if nodeLink := sock.getFarLink():
            if sock.bl_idname == VRaySocketColorRamp.bl_idname:
                origin_node = sock.links[0].from_node
                colors, positions, interpolation = origin_node.exportGradTreeNode(nodeCtx)
                pluginDesc.setAttribute('colors', colors)
                pluginDesc.setAttribute('positions', positions)
                pluginDesc.setAttribute('interpolation', [interpolation] * len(positions))
                continue

            linkedPlugin = commonNodesExport.exportVRayNode(nodeCtx, nodeLink)

            linkedPlugin = commonNodesExport._exportConverters(
                    nodeCtx, nodeLink.to_socket, linkedPlugin)
            pluginDesc.setAttribute(sock.vray_attr, linkedPlugin)
        elif sock.vray_attr not in ('', *rampAttributes):
            if hasattr(sock, 'exportUnlinked'):
                attrDesc = getPluginAttr(getPluginModule(_PLUGIN_TYPE), sock.vray_attr)
                sock.exportUnlinked(nodeCtx, pluginDesc, attrDesc)

    return commonNodesExport.exportPluginWithStats(nodeCtx, pluginDesc)
