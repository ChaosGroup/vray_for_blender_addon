# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
from vray_blender.engine import resetActiveIprRendering
from vray_blender.exporting import node_export as commonNodesExport
from vray_blender.lib import plugin_utils
from vray_blender.lib.defs import NodeContext, PluginDesc
from vray_blender.lib.names import Names
from vray_blender.plugins.channel.render_channel_common import drawChannelType


plugin_utils.loadPluginOnModule(globals(), __name__)


def nodeInit(node: bpy.types.Node):
    vrayExporter = bpy.context.scene.vray.Exporter
    world = bpy.context.world

    if vrayExporter.viewport_denoiser_enabled and vrayExporter.linked_denoiser:
        world.vray.RenderChannelDenoiser.engine = vrayExporter.viewport_denoiser_engine


def widgetDrawChannelType(context, layout, propGroup, widgetAttr):
    assert widgetAttr['name'] == 'channelType'
    drawChannelType(layout, "Denoiser")


def onUpdateEngine(src, context, attrName):
    # Unlink the denoisers if the user selects a denoiser from the Denoiser node
    # while viewport denoising is on. If it is off, turning it on will perform
    # the sync so no need to unlink.
    vrayExporter = context.scene.vray.Exporter
    if vrayExporter.viewport_denoiser_enabled and src.engine != vrayExporter.viewport_denoiser_engine:
        vrayExporter.linked_denoiser = False
    
    # The change of the engine type requires a restart in order to take effect
    resetActiveIprRendering()


def exportTreeNode(nodeCtx: NodeContext):
    pluginType = nodeCtx.node.vray_plugin
    pluginDesc = PluginDesc(Names.singletonPlugin(pluginType), pluginType)
    pluginDesc.vrayPropGroup = nodeCtx.scene.world.vray.RenderChannelDenoiser

    commonNodesExport.exportNodeTree(nodeCtx, pluginDesc, skippedSockets=[])
    return commonNodesExport.exportPluginWithStats(nodeCtx, pluginDesc)