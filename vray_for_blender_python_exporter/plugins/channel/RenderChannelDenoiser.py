# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

from vray_blender.engine import resetActiveIprRendering, resetViewportIprRendering
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


def onUpdateViewportEngine(src, context, attrName):
    resetViewportIprRendering()


def exportTreeNode(nodeCtx: NodeContext):
    pluginType = nodeCtx.node.vray_plugin
    pluginDesc = PluginDesc(Names.singletonPlugin(pluginType), pluginType)
    pluginDesc.vrayPropGroup = nodeCtx.scene.world.vray.RenderChannelDenoiser

    commonNodesExport.exportNodeTree(nodeCtx, pluginDesc, skippedSockets=[])
    return commonNodesExport.exportPluginWithStats(nodeCtx, pluginDesc)