import bpy

from vray_blender.lib import plugin_utils
from vray_blender.lib.names import Names
from vray_blender.lib.defs import NodeContext, PluginDesc
from vray_blender.lib.export_utils import wrapAsTexture
from vray_blender.nodes.sockets import getHiddenInput
from vray_blender.exporting import node_export as commonNodesExport
from vray_blender.exporting.tools import getFarNodeLink

plugin_utils.loadPluginOnModule(globals(), __name__)


def exportTreeNode(nodeCtx: NodeContext):
    node = nodeCtx.node

    textures    = []
    masks       = []
    blendModes  = []
    opacities   = []

    for l in range(node.layers):
        humanIndex = l + 1

        sockTexture = node.inputs[f'Texture {humanIndex}']
        if texLink := getFarNodeLink(sockTexture):
            linkedPlugin = commonNodesExport.exportSocketLink(nodeCtx, texLink)
            textures.append(wrapAsTexture(nodeCtx, linkedPlugin))
        else:
            textures.append(wrapAsTexture(nodeCtx, sockTexture.value))

        sockMask = node.inputs[f'Mask {humanIndex}']
        if maskLink := getFarNodeLink(sockMask):
            linkedPlugin = commonNodesExport.exportSocketLink(nodeCtx, maskLink)
            masks.append(linkedPlugin)
        else:
            masks.append(wrapAsTexture(nodeCtx, sockMask.value))

        blendModeSocket = getHiddenInput(node, f'Blend Mode {humanIndex}')
        opacitySocket = getHiddenInput(node, f'Opacity {humanIndex}')
        blendModes.append(int(blendModeSocket.value))
        opacities.append(opacitySocket.value)

    pluginName = Names.treeNode(nodeCtx)
    pluginDesc = PluginDesc(pluginName, "TexLayeredMax")
    pluginDesc.vrayPropGroup = node.TexLayeredMax

    pluginDesc.setAttributes({
        "textures": textures,
        "masks":  masks,
        "blend_modes": blendModes,
        "opacities": opacities
    })
    skippedSockets = [ "textures", "masks", "opacities", "blend_modes" ]
    commonNodesExport.exportNodeTree(nodeCtx, pluginDesc, skippedSockets)
    return commonNodesExport.exportPluginWithStats(nodeCtx, pluginDesc)


