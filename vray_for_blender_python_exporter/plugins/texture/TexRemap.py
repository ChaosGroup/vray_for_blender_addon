# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later


import bpy
import math

import vray_blender.nodes.curves_node as cn
from vray_blender.exporting import node_export as commonNodesExport
from vray_blender.lib.draw_utils import getAttrLabel
from vray_blender.lib.names import Names
from vray_blender.lib.defs import NodeContext, PluginDesc
from vray_blender.lib import plugin_utils
from vray_blender.nodes.utils import getNodeOfPropGroup
from vray_blender.plugins import getPluginModule

plugin_utils.loadPluginOnModule(globals(), __name__)


# Channel names for "_postion", "_value" and "_type" attributes
_CHANNEL_NAMES = {
    "1": ["red", "green", "blue"], # For "Remap Color"
    "2": ["hue", "saturation", "value"], # For "Remap HSV"
}


def nodeInit(node: bpy.types.Node):
    node.assignStaticId() # The static id is needed for the name of the curves node

    # The current "bpy" API does not allow direct creation of a CurveMapping widget.
    # To work around this, we create a ShaderNodeRGBCurve in a hidden node tree and use its widget.
    cn.createCurvesNode(node)


def nodeFree(node: bpy.types.Node):
    """Executes upon node deletion."""
    cn.removeCurvesNode(node)


def nodeUpdate(node: bpy.types.Node):
    if not cn.hasCurvesNode(node):
        return

    # Encode the curves data as a string so that it could be saved in the TexRemap node, and not
    # just be available from the auxiliary ShaderNodeRGBCurve node. This will allow us to restore
    # the curves state if the node is imported into another scene.
    curvesNode = cn.getCurvesNode(node)
    node.TexRemap['curves_data'] = cn.encodeMapping(curvesNode.mapping)


def nodeCopy(copyNode: bpy.types.Node, origNode: bpy.types.Node):
    cn.curvesCopy(copyNode, origNode)


def drawCurveTemplate(context: bpy.types.Context, layout: bpy.types.UILayout, propGroup, widgetAttr):
    # "custom_draw" function for the TexRemap.type attribute that also draws
    # CurveMapping widget

    if propGroup.type == '1':
        attrName = widgetAttr['name']
        attrLabel = getAttrLabel(getPluginModule('TexRemap'), widgetAttr, propGroup, node=None)
        layout.prop(propGroup, attrName, text=attrLabel)

    layout.separator()

    node = getNodeOfPropGroup(propGroup)
    curvesNode = cn.getCurvesNode(node)
    curveType = { "1":"COLOR", "2":"HUE" }[node.TexRemap.type]
    layout.template_curve_mapping(curvesNode, "mapping", type=curveType)


def exportTreeNode(nodeCtx: NodeContext):
    node = nodeCtx.node

    # The plugin has dedicated inputs for RGB and HSV data. We need to activate
    # the one set and deactivate the other. Set the lists of input names for
    # the active and inactive inputs.
    activeChannels = _CHANNEL_NAMES[node.TexRemap.type]
    inactiveChannels = _CHANNEL_NAMES[{"1":"2", "2":"1"}[node.TexRemap.type]]

    def setChannel(channel: int, pluginDesc: dict, positions: list[float], values: list[float], types: list[int]):
        # Set the active inputs and reset the inactive ones for a single channel
        pluginDesc.setAttribute(f"{activeChannels[channel]}_positions", positions)
        pluginDesc.setAttribute(f"{activeChannels[channel]}_values", values)
        pluginDesc.setAttribute(f"{activeChannels[channel]}_types", types)

        pluginDesc.resetAttribute(f"{inactiveChannels[channel]}_positions")
        pluginDesc.resetAttribute(f"{inactiveChannels[channel]}_values")
        pluginDesc.resetAttribute(f"{inactiveChannels[channel]}_types")


    pluginName = Names.treeNode(nodeCtx)
    pluginDesc = PluginDesc(pluginName, node.vray_plugin)
    pluginDesc.vrayPropGroup = node.TexRemap

    curvesNode = cn.getCurvesNode(node)
    curveMapping = curvesNode.mapping

    isCombined = node.TexRemap.color_spline_type == '0'
    isColor = node.TexRemap.type == '1' # HSV does not support the combined curve

    if isCombined and isColor:
        # When using the "Combined" spline type with "Color Remap", all color channels
        # should be assigned the values of the "C" (combined) curve.
        curve  = curveMapping.curves[3]
        positions, values, types = cn.fillSplineData(curve, curveMapping.extend)

        for i in range(3):
            setChannel(i, pluginDesc, positions, values, types)
    else:
        # Export individual channels using the "R", "G", and "B" curves for "Color Remap",
        # or "H", "S", and "V" curves for "HSV Remap".
        for i in range(3):
            curve  = curveMapping.curves[i]
            positions, values, types = cn.fillSplineData(curve, curveMapping.extend)
            setChannel(i, pluginDesc, positions, values, types)

    commonNodesExport.exportNodeTree(nodeCtx, pluginDesc)
    return commonNodesExport.exportPluginWithStats(nodeCtx, pluginDesc)
