
import bpy

from vray_blender.exporting.tools import getInputSocketByAttr, removeInputSocketLinks
from vray_blender.lib import plugin_utils
from vray_blender.lib.defs import NodeContext, PluginDesc
from vray_blender.lib.names import Names
from vray_blender.nodes.utils import getUpdateCallbackPropertyContext, getNodeOfPropGroup
from vray_blender.exporting import node_export as commonNodesExport

plugin_utils.loadPluginOnModule(globals(), __name__)


def onUpdateInnerLine(updateSource, context: bpy.types.Context, attrName: str ):
    propContext = getUpdateCallbackPropertyContext(updateSource, "VolumeVRayToon")

    drawInnerLine = propContext.get('innerLineControl')
    sockLineColor = getInputSocketByAttr(propContext.node, 'lineColorInner_tex')
    sockLineWidth = getInputSocketByAttr(propContext.node, 'lineWidthInner_tex')

    sockLineColor.enabled = drawInnerLine
    sockLineWidth.enabled = drawInnerLine
    
    if not drawInnerLine:
        removeInputSocketLinks(sockLineColor)
        removeInputSocketLinks(sockLineWidth)


_CURVE_NODES_TREE_NAME = ".volumeVRayToonTree" # name for hidden node tree containing ShaderNodeRGBCurve nodes
_CURVE_ATTRIBUTES = {'depth_curve': 'depth', 'angular_curve': 'angular'}
_CURVE_TYPES = ('depth', 'angular')

def nodeInit(node: bpy.types.Node):
    node.assignStaticId() # The static id is needed for the name of the curves node

    # In order to use Blender's template_curve_mapping template, we need a property of type 'CurveMapping'.
    # Unfortunately, The current "bpy" API does not allow direct creation of a CurveMapping object.
    # To work around this, we create a ShaderNodeRGBCurve in a hidden node tree and use its widget.
    for curveType in _CURVE_TYPES:
        _createCurvesNode(node, curveType)


def nodeFree(node: bpy.types.Node):
    for curveType in _CURVE_TYPES:
        curvesNode = _getCurvesNode(node, curveType)
        bpy.msgbus.clear_by_owner(curvesNode)
        bpy.data.node_groups[_CURVE_NODES_TREE_NAME].nodes.remove(curvesNode)    


def nodeCopy(copyNode: bpy.types.Node, origNode: bpy.types.Node):
    copyNode.assignStaticId() # The static id is needed for the name of the curves node


    for curveType in _CURVE_TYPES:
        _createCurvesNode(copyNode, curveType)
        copyCurvesNode = _getCurvesNode(copyNode, curveType)
        origCurvesNode = _getCurvesNode(origNode, curveType)

        for i, curve in enumerate(origCurvesNode.mapping.curves):
                copyCurve = copyCurvesNode.mapping.curves[i]

                # By default, CurveMapping contains a minimum of 2 points. 
                # This ensures that the curve in origNode cannot have fewer points than the one in copyCurve.         
                while len(copyCurve.points) < len(curve.points):
                    copyCurve.points.new(0, 0)

                for j, point in enumerate(curve.points):
                    copyCurve.points[j].location = point.location
                    copyCurve.points[j].handle_type = point.handle_type


def drawCurveTemplate(context, layout, propGroup, widgetAttr):
    node = getNodeOfPropGroup(propGroup)
    
    curveType = _CURVE_ATTRIBUTES.get(widgetAttr['name'])
    curvesNode = _getCurvesNode(node, curveType)
    layout.template_curve_mapping(curvesNode, "mapping", type='NONE')
    


def exportTreeNode(nodeCtx: NodeContext):
    node = nodeCtx.node

    # We subscribe again on every export because if the msgbus subscription is initialized in nodeInit(),
    # on closing and reloading the scene the subscription is cleared.
    # Another solution would be to search all node trees for VRayNodeVolumeVRayToon,
    # but this would unnecessarily spread the code and add complexity.
     # TODO find more elegant solution
    for curveType in _CURVE_TYPES:
        _addCurvesUpdateCallback(node, curveType)

        propGroup = getattr(node, node.vray_plugin)
        pluginName = Names.treeNode(nodeCtx)
        pluginDesc = PluginDesc(pluginName, node.vray_plugin)
        pluginDesc.vrayPropGroup = propGroup

        for curveType in _CURVE_TYPES:

            curvesNode = _getCurvesNode(node, curveType)
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


def _getCurvesNodeName(node, curveType: str):
    return f"{node.vray_plugin}_{curveType}_{node.static_id}"


def _getCurvesNode(node, curveType: str):
    return bpy.data.node_groups[_CURVE_NODES_TREE_NAME].nodes[_getCurvesNodeName(node, curveType)]


def _addCurvesUpdateCallback(node, curveType: str):
    from vray_blender.nodes.nodes import vrayNodeUpdate
    # To handle updates to the ShaderNodeRGBCurve used for its CurveMapping widget,
    # we subscribe to it with msgbus and attach the vrayNodeUpdate function.
    curvesNode = _getCurvesNode(node, curveType)
    bpy.msgbus.clear_by_owner(curvesNode) # clearing previous subscriptions to be sure that there aren't other update callbacks left
    bpy.msgbus.subscribe_rna(
        key=curvesNode,
        owner=curvesNode,
        args=(node,),
        notify=vrayNodeUpdate,
    )

def _createCurvesNode(node, curveType: str):
    if _CURVE_NODES_TREE_NAME not in bpy.data.node_groups:
        bpy.data.node_groups.new(_CURVE_NODES_TREE_NAME, "ShaderNodeTree")

    toonTree = bpy.data.node_groups[_CURVE_NODES_TREE_NAME]
    toonTree.use_fake_user=True
    curvesNode = toonTree.nodes.new("ShaderNodeRGBCurve")
    curvesNode.name = _getCurvesNodeName(node, curveType)

    curve  = curvesNode.mapping.curves[3]
    curve.points[0].location[1] = 1.0
    curve.points[1].location[1] = 0.0