# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later


import bpy

from vray_blender.exporting import node_export as commonNodesExport
from vray_blender.lib import plugin_utils
from vray_blender.lib.defs import  PluginDesc, NodeContext
from vray_blender.lib.names import Names
from vray_blender.plugins import getPluginAttr, getPluginModule
from vray_blender.nodes.specials.gradient_ramp import VRaySocketColorRamp, createColorRampNode

plugin_utils.loadPluginOnModule(globals(), __name__)


# TexBerconGrad is an import-only plugin (hidden from the node-add menu); it mirrors TexRamp's
# color-ramp handling. See plugins/skipped_plugins.py (HIDDEN_PLUGINS).
_PLUGIN_TYPE = 'TexBerconGrad'

# Blender ramp interpolation <-> TexBerconGrad.interpolation
# (0 linear, 1 smooth, 2 solid nearest, 3 solid left, 4 solid right). The enum differs from the
# one TexGradRamp/TexRamp use, which is what VRayNodeColorRamp.exportGradTreeNode returns.
_GRAD_RAMP_TO_BERCON = {0: 2, 1: 0, 2: 1}
_BERCON_TO_GRAD_RAMP = {0: 1, 1: 2, 2: 0, 3: 0, 4: 0}


def toGradRampInterpolation(berconInterpolation) -> int:
    """ Translate TexBerconGrad.interpolation to the enum _fillRamp() expects on import. """
    return _BERCON_TO_GRAD_RAMP.get(int(float(berconInterpolation)), 1)


def nodeInit(node: bpy.types.Node):
    """ Create the gradient ramp node and attach it to the TexBerconGrad node. """
    rampSocket = next(sock for sock in node.inputs if sock.bl_idname == VRaySocketColorRamp.bl_idname)
    createColorRampNode(node, rampSocket)

    node.id_data.nodes.active = node


def exportTreeNode(nodeCtx: NodeContext):
    node = nodeCtx.node

    pluginDesc = PluginDesc(Names.nextVirtualNode(nodeCtx, _PLUGIN_TYPE), _PLUGIN_TYPE)
    pluginDesc.vrayPropGroup = node.TexBerconGrad

    rampAttributes = ("colors", "positions", "interpolation")

    for sock in node.inputs:
        if nodeLink := sock.getFarLink():
            if sock.bl_idname == VRaySocketColorRamp.bl_idname:
                origin_node = sock.links[0].from_node
                colors, positions, interpolation = origin_node.exportGradTreeNode(nodeCtx)
                pluginDesc.setAttribute('colors', colors)
                pluginDesc.setAttribute('positions', positions)
                # A single enum here, unlike TexGradRamp's per-point list.
                pluginDesc.setAttribute('interpolation', _GRAD_RAMP_TO_BERCON.get(interpolation, 0))
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
