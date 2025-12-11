from vray_blender.lib import plugin_utils
from vray_blender.lib.names import Names
from vray_blender.lib.defs import NodeContext, PluginDesc
from vray_blender.exporting import node_export as commonNodesExport
from vray_blender.exporting.tools import getInputSocketByName
from vray_blender.nodes.tools import isInputSocketLinked

plugin_utils.loadPluginOnModule(globals(), __name__)

def exportTreeNode(nodeCtx: NodeContext):
    pluginName = Names.treeNode(nodeCtx)
    pluginDesc = PluginDesc(pluginName, "TexNormalBump")
    pluginDesc.vrayPropGroup = nodeCtx.node.TexNormalBump

    mappingSock = getInputSocketByName(nodeCtx.node, "Mapping")
    if mappingSock and isInputSocketLinked(mappingSock):
        uvwPlugin = commonNodesExport.exportLinkedSocket(nodeCtx, mappingSock)
        pluginDesc.setAttribute("normal_uvwgen", uvwPlugin)
        pluginDesc.setAttribute("normal_uvwgen_auto", False)
    else:
        pluginDesc.setAttribute("normal_uvwgen_auto", True)

    commonNodesExport.exportNodeTree(nodeCtx, pluginDesc)
    if propGroup := pluginDesc.vrayPropGroup:
        if propGroup.flip_red or propGroup.flip_green or propGroup.swap_red_green:
            flipName = pluginDesc.name+"|flip"
            flipPluginDesc = PluginDesc(flipName, "TexNormalMapFlip")
            flipPluginDesc.setAttribute('flip_red', propGroup.flip_red)
            flipPluginDesc.setAttribute('flip_green', propGroup.flip_green)
            flipPluginDesc.setAttribute('swap_redgreen', propGroup.swap_red_green)
            flipPluginDesc.setAttribute("texmap", pluginDesc.getAttribute("bump_tex_color"))

            # We want to end up with the following: TexBitmap->TexNormalMapFlip->TexNormalBump->BRDFVRayMtl
            # so swap the flip's texmap with the bump input and the bump_tex_color with the flip plugin.
            flipPlugin = commonNodesExport.exportPluginWithStats(nodeCtx, flipPluginDesc)
            pluginDesc.setAttribute("bump_tex_color", flipPlugin)
    return commonNodesExport.exportPluginWithStats(nodeCtx, pluginDesc)