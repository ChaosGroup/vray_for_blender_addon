# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later


import bpy

from vray_blender.exporting.tools import getInputSocketByAttr, removeSocketLinks
from vray_blender.lib import plugin_utils
from vray_blender.lib.defs import NodeContext, PluginDesc
from vray_blender.lib.names import Names
from vray_blender.nodes.utils import getUpdateCallbackPropertyContext, getNodeOfPropGroup, getVrayPropGroup
from vray_blender.exporting import node_export as commonNodesExport
from vray_blender.nodes.curves_node import addCurvesUpdateCallback, copyCurvesData, createCurvesNode, getCurvesNode, hasCurvesNode, removeCurvesNode

plugin_utils.loadPluginOnModule(globals(), __name__)


def onUpdateInnerLine(updateSource, context: bpy.types.Context, attrName: str ):
    propContext = getUpdateCallbackPropertyContext(updateSource, "VolumeVRayToon")

    drawInnerLine = propContext.get('innerLineControl')
    sockLineColor = getInputSocketByAttr(propContext.node, 'lineColorInner_tex')
    sockLineWidth = getInputSocketByAttr(propContext.node, 'lineWidthInner_tex')

    sockLineColor.enabled = drawInnerLine
    sockLineWidth.enabled = drawInnerLine

    if not drawInnerLine:
        removeSocketLinks(sockLineColor)
        removeSocketLinks(sockLineWidth)


_CURVE_ATTRIBUTES = {'depth_curve': 'depth', 'angular_curve': 'angular'}
_CURVE_TYPES = ('depth', 'angular')
_OLD_CURVE_NODES_TREE_NAME = ".volumeVRayToonTree"


def nodeInit(node: bpy.types.Node):
    node.assignStaticId() # The static id is needed for the name of the curves node

    # In order to use Blender's template_curve_mapping template, we need a property of type 'CurveMapping'.
    # Unfortunately, The current "bpy" API does not allow direct creation of a CurveMapping object.
    # To work around this, we create a ShaderNodeRGBCurve in a hidden node tree and use its widget.
    for curveType in _CURVE_TYPES:
        curvesNode = createCurvesNode(node, f'_{curveType}')
        curve = curvesNode.mapping.curves[3]
        curve.points[0].location[1] = 1.0
        curve.points[1].location[1] = 0.0


def nodeFree(node: bpy.types.Node):
    for curveType in _CURVE_TYPES:
        removeCurvesNode(node, f'_{curveType}')


def nodeCopy(copyNode: bpy.types.Node, origNode: bpy.types.Node):
    copyNode.assignStaticId() # The static id is needed for the name of the curves node

    for curveType in _CURVE_TYPES:
        copyCurvesData(getCurvesNode(origNode, f'_{curveType}'), createCurvesNode(copyNode, f'_{curveType}'))


def drawCurveTemplate(context, layout, propGroup, widgetAttr):
    node = getNodeOfPropGroup(propGroup)

    curveType = _CURVE_ATTRIBUTES.get(widgetAttr['name'])
    curvesNode = getCurvesNode(node, f'_{curveType}')
    layout.template_curve_mapping(curvesNode, "mapping", type='NONE')


def _ensureCurvesNodes(node: bpy.types.Node):
    """Ensure curve nodes exist. For old scenes that used a separate curves tree, migrate the data."""
    for curveType in _CURVE_TYPES:
        if hasCurvesNode(node, f'_{curveType}'):
            continue
        curvesNode = createCurvesNode(node, f'_{curveType}')
        # Try to restore data from the old per-plugin tree used before curves were moved to curves_node.py.
        oldTree = bpy.data.node_groups.get(_OLD_CURVE_NODES_TREE_NAME)
        if oldTree and (oldNode := oldTree.nodes.get(f"{node.vray_plugin}_{curveType}_{node.static_id}")):
            copyCurvesData(oldNode, curvesNode)
        else:
            curve = curvesNode.mapping.curves[3]
            curve.points[0].location[1] = 1.0
            curve.points[1].location[1] = 0.0


def registerNodeCurves(node: bpy.types.Node):
    """Register update callbacks for a single VolumeVRayToon node."""
    _ensureCurvesNodes(node)
    for curveType in _CURVE_TYPES:
        addCurvesUpdateCallback(node, getCurvesNode(node, f'_{curveType}'))


def exportTreeNode(nodeCtx: NodeContext):
    node = nodeCtx.node
    propGroup = getVrayPropGroup(node)
    pluginName = Names.treeNode(nodeCtx)
    pluginDesc = PluginDesc(pluginName, node.vray_plugin)
    pluginDesc.vrayPropGroup = propGroup

    for curveType in _CURVE_TYPES:
        curvesNode = getCurvesNode(node, f'_{curveType}')
        curve  = curvesNode.mapping.curves[3]

        pluginDesc.setAttribute(f"{curveType}CurvePositions", [point.location[0] for point in curve.points])
        pluginDesc.setAttribute(f"{curveType}CurveInterpolations", [("3" if "AUTO" in point.handle_type else "1") for point in curve.points])

        # The Y coordinate of the points should be exported as TEXTURE_FLOAT_LIST, i.e. a list of plugins
        pointYCoords = []
        for point in curve.points:
            texPlugin = PluginDesc(Names.nextVirtualNode(nodeCtx, 'FloatToTex'), 'FloatToTex')
            texPlugin.setAttribute('input', point.location[1])
            pointYCoords.append(commonNodesExport.exportPluginWithStats(nodeCtx, texPlugin))

        pluginDesc.setAttribute(f"{curveType}CurveValues", pointYCoords )

    commonNodesExport.exportNodeTree(nodeCtx, pluginDesc)
    return commonNodesExport.exportPluginWithStats(nodeCtx, pluginDesc)
