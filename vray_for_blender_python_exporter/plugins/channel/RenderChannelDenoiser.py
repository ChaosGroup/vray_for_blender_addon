from vray_blender.engine import resetActiveIprRendering
from vray_blender.exporting import node_export as commonNodesExport
from vray_blender.lib import plugin_utils
from vray_blender.lib.defs import NodeContext, PluginDesc
from vray_blender.lib.names import Names
from vray_blender.nodes.nodes import setUniqueRenderChannelName
from vray_blender.plugins.channel.render_channel_common import drawChannelType


plugin_utils.loadPluginOnModule(globals(), __name__)

def widgetDrawChannelType(context, layout, propGroup, widgetAttr):
    assert widgetAttr['name'] == 'channelType'
    drawChannelType(layout, "Denoiser")

def onUpdateEngine(src, context, attrName):
    # The change of the engine type requires a restart in order to take effect
    resetActiveIprRendering()

def onUpdateChannelName(propGroup, context, attrName):
    # The denoiser node is 'fake' and its parameters are stored in the world itself instead of the node tree.
    ntree = propGroup.id_data.node_tree
    node = next((n for n in ntree.nodes if hasattr(n, "RenderChannelDenoiser")), None)
    setUniqueRenderChannelName(node, isNewNode = False)

def exportTreeNode(nodeCtx: NodeContext):
    pluginType = nodeCtx.node.vray_plugin
    pluginDesc = PluginDesc(Names.singletonPlugin(pluginType), pluginType)
    pluginDesc.vrayPropGroup = nodeCtx.scene.world.vray.RenderChannelDenoiser

    commonNodesExport.exportNodeTree(nodeCtx, pluginDesc, skippedSockets=[])
    return commonNodesExport.exportPluginWithStats(nodeCtx, pluginDesc) 