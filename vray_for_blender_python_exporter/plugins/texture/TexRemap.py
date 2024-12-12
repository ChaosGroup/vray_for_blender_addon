
import bpy

from vray_blender.lib import plugin_utils
from vray_blender.lib.defs import PluginDesc

from vray_blender.lib.names import Names
from vray_blender.lib.defs import NodeContext, PluginDesc
from vray_blender.lib import plugin_utils
from vray_blender.exporting import node_export as commonNodesExport
from vray_blender.nodes.utils import getNodeOfPropGroup

plugin_utils.loadPluginOnModule(globals(), __name__)

# Channel names for "_postion", "_value" and "_type" attributes
CHANNEL_NAMES = {
    "1": ["red", "green", "blue"], # For "Remap Color"
    "2": ["hue", "saturation", "value"], # For "Remap HSV"
}

CURVE_NODES_TREE_NAME = ".texRemapTree" # name for hidden node tree containing ShaderNodeRGBCurve nodes

def nodeFree(node: bpy.types.Node):
    curvesNode = _getCurvesNode(node)
    bpy.msgbus.clear_by_owner(curvesNode)
    bpy.data.node_groups[CURVE_NODES_TREE_NAME].nodes.remove(curvesNode)    

def nodeInit(node: bpy.types.Node):
    node.assignStaticId() # The static id is needed for the name of the curves node

    # The current "bpy" API does not allow direct creation of a CurveMapping widget.
    # To work around this, we create a ShaderNodeRGBCurve in a hidden node tree and use its widget.
    _createCurvesNode(node)

def nodeCopy(copyNode: bpy.types.Node, origNode: bpy.types.Node):
    copyNode.assignStaticId() # The static id is needed for the name of the curves node

    _createCurvesNode(copyNode)
    copyCurvesNode = _getCurvesNode(copyNode)
    origCurvesNode = _getCurvesNode(origNode)

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
    # "custom_draw" function for the TexRemap.type attribute that also draws
    # CurveMapping widget
    node = getNodeOfPropGroup(propGroup)
    curvesNode = _getCurvesNode(node)
    curveType = { "1":"COLOR", "2":"HUE" }[node.TexRemap.type]
    layout.template_curve_mapping(curvesNode, "mapping", type=curveType)
    
    attrName = widgetAttr['name']
    layout.prop(propGroup, attrName, text=widgetAttr.get('label', attrName))


def exportTreeNode(nodeCtx: NodeContext):
    node = nodeCtx.node

    # We subscribe again on every export because if the msgbus subscription is initialized in nodeInit(),
    # no closing and loading again of the scene, the subscription is cleared.
    # Another solution would be to search all node trees for VRayNodeTexRemap,
    # but this would unnecessarily spread the code and add complexity.
     # TODO find more elegant solution
    _addCurvesUpdateCallback(node)

    propGroup = getattr(node, node.vray_plugin)
    pluginName = Names.treeNode(nodeCtx)
    pluginDesc = PluginDesc(pluginName, node.vray_plugin)
    pluginDesc.vrayPropGroup = propGroup

    curvesNode = _getCurvesNode(node)
    
    activeChannels = CHANNEL_NAMES[node.TexRemap.type]
    inactiveChannels = CHANNEL_NAMES[{"1":"2", "2":"1"}[node.TexRemap.type]]

    for i in range(0, 3):
        # When using the "Combined" spline type with "Color Remap", all color channels
        # should be assigned the values of the "C" (combined) curve.
        # Otherwise, set the color channels individually using the "R", "G", and "B" curves,
        # or "H", "S", and "V" curves if the "type" attribute is "HSV Remap".
        curveIdx = 3 if node.TexRemap.type == "1" and node.TexRemap.color_spline_type == "0" else i
        curve  = curvesNode.mapping.curves[curveIdx]

        pluginDesc.setAttribute(f"{activeChannels[i]}_positions", [point.location[0] for point in curve.points])
        pluginDesc.setAttribute(f"{activeChannels[i]}_values", [point.location[1] for point in curve.points])
        pluginDesc.setAttribute(f"{activeChannels[i]}_types", [("3" if "AUTO" in point.handle_type else "1") for point in curve.points])

        pluginDesc.resetAttribute(f"{inactiveChannels[i]}_positions")
        pluginDesc.resetAttribute(f"{inactiveChannels[i]}_values")
        pluginDesc.resetAttribute(f"{inactiveChannels[i]}_types")

    commonNodesExport.exportNodeTree(nodeCtx, pluginDesc)
    return commonNodesExport.exportPluginWithStats(nodeCtx, pluginDesc)


def _getCurvesNodeName(node):
    return f"{node.vray_plugin}{node.static_id}"

def _getCurvesNode(node):
    return bpy.data.node_groups[CURVE_NODES_TREE_NAME].nodes[_getCurvesNodeName(node)]

def _addCurvesUpdateCallback(node):
    from vray_blender.nodes.nodes import vrayNodeUpdate
    # To handle updates to the ShaderNodeRGBCurve used for its CurveMapping widget,
    # we subscribe to it with msgbus and attach the vrayNodeUpdate function.
    curvesNode = _getCurvesNode(node)
    bpy.msgbus.clear_by_owner(curvesNode) # clearing previous subscriptions to be sure that there aren't other update callbacks left
    bpy.msgbus.subscribe_rna(
        key=curvesNode,
        owner=curvesNode,
        args=(node,),
        notify=vrayNodeUpdate,
    )

def _createCurvesNode(node):
    if CURVE_NODES_TREE_NAME not in bpy.data.node_groups:
        bpy.data.node_groups.new(CURVE_NODES_TREE_NAME, "ShaderNodeTree")

    texRemapTree = bpy.data.node_groups[CURVE_NODES_TREE_NAME]
    texRemapTree.use_fake_user=True
    curvesNode = texRemapTree.nodes.new("ShaderNodeRGBCurve")
    curvesNode.name=_getCurvesNodeName(node)