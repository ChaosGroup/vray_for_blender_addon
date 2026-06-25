# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy

from vray_blender.exporting import node_export as commonNodesExport
from vray_blender.exporting.tools import getFarNodeLink
from vray_blender.lib import plugin_utils
from vray_blender.lib.defs import AttrPlugin, NodeContext, PluginDesc
from vray_blender.lib.names import Names

plugin_utils.loadPluginOnModule(globals(), __name__)

def switchIdActive(propGroup, node: bpy.types.Node) -> bool:
    if node is None:
        return True
    texSock = node.inputs.get('Switch Texture')
    return (texSock is None) or not texSock.hasActiveFarLink()


def exportTreeNode(nodeCtx: NodeContext):
    pluginName = Names.treeNode(nodeCtx)
    pluginDesc = PluginDesc(pluginName, "MtlMulti")

    node = nodeCtx.node
    texSock = node.inputs['Switch Texture']
    if link := getFarNodeLink(texSock):
        switchID = commonNodesExport.exportLinkedSocket(nodeCtx, link.to_socket)
    else:
        switchID = node.MtlMulti.switch_id

    mtlSockets = [s for s in node.inputs if s.bl_idname == 'VRaySocketMtlMulti' and s.enabled and s.hasActiveFarLink()]
    mtlIDs = []
    linkedMtls = []

    for sock in mtlSockets:
        brdfPlugin = commonNodesExport.exportLinkedSocket(nodeCtx, sock)
        mtlPlugin = _exportMtlSingleBrdf(nodeCtx, brdfPlugin)
        linkedMtls.append(mtlPlugin)
        mtlIDs.append(sock.value)

    pluginDesc.setAttributes({
        "ids_list":        mtlIDs,
        "mtls_list":       linkedMtls,
        "mtlid_gen_float": switchID
    })

    pluginDesc.vrayPropGroup = getattr(nodeCtx.node, node.vray_plugin)
    return commonNodesExport.exportPluginWithStats(nodeCtx, pluginDesc)


def _exportMtlSingleBrdf(nodeCtx: NodeContext, brdfPlugin: AttrPlugin):
    pluginType = 'MtlSingleBRDF'
    pluginName = Names.nextVirtualNode(nodeCtx, pluginType)
    plDesc = PluginDesc(pluginName, pluginType)

    plDesc.setAttributes({
        'brdf':       brdfPlugin,
        'scene_name': [pluginName]
    })

    return commonNodesExport.exportPluginWithStats(nodeCtx, plDesc)
