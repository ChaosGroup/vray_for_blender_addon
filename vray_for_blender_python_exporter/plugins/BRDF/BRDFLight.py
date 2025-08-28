
from vray_blender.lib.names import Names
from vray_blender.lib.color_utils import opacityToTransparency
from vray_blender.lib.defs import NodeContext, PluginDesc
from vray_blender.lib import plugin_utils
from vray_blender.exporting import node_export as commonNodesExport
from vray_blender.exporting.tools import getInputSocketByAttr
from vray_blender.nodes.utils import getVrayPropGroup

plugin_utils.loadPluginOnModule(globals(), __name__)


def exportTreeNode(nodeCtx: NodeContext):
    node = nodeCtx.node
    propGroup = getVrayPropGroup(node)

    pluginName = Names.treeNode(nodeCtx)
    pluginDesc = PluginDesc(pluginName, 'BRDFLight')
    pluginDesc.vrayPropGroup = propGroup

    # The plugin exposes a 'transparency' property, but, for convenience, we show it as 'Opacity'
    # to the user. This is why a conversion is needed.
    sockOpacity = getInputSocketByAttr(node, "transparency")

    if sockOpacity.shouldExportLink():
        plTexOpacity = commonNodesExport.exportLinkedSocket(nodeCtx, sockOpacity)
        
        plTexInvert = PluginDesc(Names.nextVirtualNode(nodeCtx, 'TexInvert'), 'TexInvert')
        plTexInvert.setAttribute("texture", plTexOpacity)
        exportedTexInvert = commonNodesExport.exportPluginWithStats(nodeCtx, plTexInvert)
        
        pluginDesc.setAttribute("transparency", exportedTexInvert)
    else:
        opacityRGBA = propGroup.transparency[:] + (1.0,)
        pluginDesc.setAttribute("transparency", opacityToTransparency(opacityRGBA))

    # Register the material as emissive. This information will be used by the LightMix
    nodeCtx.exporterCtx.emissiveMaterials.append((pluginName, 'channels', node.name))

    commonNodesExport.exportNodeTree(nodeCtx, pluginDesc, skippedSockets=(sockOpacity.vray_attr,))
    return commonNodesExport.exportPluginWithStats(nodeCtx, pluginDesc)

