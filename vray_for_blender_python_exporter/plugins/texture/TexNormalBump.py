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
    plugin = commonNodesExport.exportPluginWithStats(nodeCtx, pluginDesc)
    if propGroup := pluginDesc.vrayPropGroup:
        if propGroup.flip_red or propGroup.flip_green or propGroup.swap_red_green:
            flipName = pluginDesc.name+"|flip"
            plDesc = PluginDesc(flipName, "TexNormalMapFlip")
            plDesc.setAttribute('flip_red', propGroup.flip_red)
            plDesc.setAttribute('flip_green', propGroup.flip_green)
            plDesc.setAttribute('swap_redgreen', propGroup.swap_red_green)
            plDesc.setAttribute("texmap", plugin)

            return commonNodesExport.exportPluginWithStats(nodeCtx, plDesc)
    return plugin