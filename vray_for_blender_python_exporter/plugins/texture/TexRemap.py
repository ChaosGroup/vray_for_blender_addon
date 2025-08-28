
import bpy
import math

from vray_blender.exporting import node_export as commonNodesExport
from vray_blender.lib import plugin_utils
from vray_blender.lib.defs import PluginDesc
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

_CURVE_NODES_TREE_NAME = ".texRemapTree" # name for hidden node tree containing ShaderNodeRGBCurve nodes

_POINT_TYPES_ENCODE = { 'AUTO' : 1, 'AUTO_CLAMPED': 2, 'VECTOR' : 3 }
_POINT_TYPES_DECODE = {v: k for k,v in _POINT_TYPES_ENCODE.items()}

    
def _encodeMapping(mapping: bpy.types.CurveMapping):
    import json
    
    mappingData = {'curves': []}
    
    for curve in mapping.curves:
        curveData = {'points': []}
    
        for point in curve.points:
            pointData = {}
            pointData['location'] = [point.location.x, point.location.y]
            pointData['type'] = _POINT_TYPES_ENCODE[point.handle_type]
            
            curveData['points'].append(pointData)
    
        mappingData['curves'].append(curveData)

    mappingData['extend'] = mapping.extend

    return json.dumps(mappingData)


def _decodeMapping(jsonData: str, mapping: bpy.types.CurveMapping):
    import json

    mappingData = json.JSONDecoder().decode(jsonData)
    curvesData = mappingData['curves']
    mapping.extend = mappingData['extend']

    for c in range (len(curvesData)):
        curve = mapping.curves[c]
        curveData = curvesData[c]
        pointsData = curveData['points']
        numPoints = len(pointsData)
        
        while len(curve.points) < numPoints:
            curve.points.new(0, 0)

        for p in range(numPoints):
            pointData = pointsData[p]

            point = curve.points[p]
            point.location = pointData['location']
            point.handle_type = _POINT_TYPES_DECODE[pointData['type']]


def nodeFree(node: bpy.types.Node):
    curvesNode = getCurvesNode(node)
    bpy.msgbus.clear_by_owner(curvesNode)
    bpy.data.node_groups[_CURVE_NODES_TREE_NAME].nodes.remove(curvesNode)    


def nodeInit(node: bpy.types.Node):
    node.assignStaticId() # The static id is needed for the name of the curves node

    # The current "bpy" API does not allow direct creation of a CurveMapping widget.
    # To work around this, we create a ShaderNodeRGBCurve in a hidden node tree and use its widget.
    _createCurvesNode(node)


def nodeUpdate(node: bpy.types.Node):
    if not _hasCurvesNode(node):
        return
    
    # Encode the curves data as a string so that it could be saved in the TexRemap node, and not 
    # just be available from the auxiliary ShaderNodeRGBCurve node. This will allow us to restore
    # the curves state if the node is imported into another scene.
    curvesNode = getCurvesNode(node)
    node.TexRemap['curves_data'] = _encodeMapping(curvesNode.mapping)
   

def loadCurvesData(node):
    curvesNode = _createCurvesNode(node)
    _decodeMapping(node.TexRemap.curves_data, curvesNode.mapping)
    

def nodeCopy(copyNode: bpy.types.Node, origNode: bpy.types.Node):
    copyNode.assignStaticId() # The static id is needed for the name of the curves node

    _createCurvesNode(copyNode)
    copyCurvesNode = getCurvesNode(copyNode)
    origCurvesNode = getCurvesNode(origNode)

    for i, curve in enumerate(origCurvesNode.mapping.curves):
            copyCurve = copyCurvesNode.mapping.curves[i]

            # By default, CurveMapping contains a minimum of 2 points. 
            # This ensures that the curve in origNode cannot have fewer points than the one in copyCurve.         
            while len(copyCurve.points) < len(curve.points):
                copyCurve.points.new(0, 0)

            for j, point in enumerate(curve.points):
                copyCurve.points[j].location = point.location
                copyCurve.points[j].handle_type = point.handle_type


def drawCurveTemplate(context: bpy.types.Context, layout: bpy.types.UILayout, propGroup, widgetAttr):
    # "custom_draw" function for the TexRemap.type attribute that also draws
    # CurveMapping widget
    
    if propGroup.type == '1':
        attrName = widgetAttr['name']
        attrLabel = getAttrLabel(getPluginModule('TexRemap'), widgetAttr)
        layout.prop(propGroup, attrName, text=attrLabel)
    
    layout.separator()

    node = getNodeOfPropGroup(propGroup)
    curvesNode = getCurvesNode(node)
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

    curvesNode = getCurvesNode(node)
    curveMapping = curvesNode.mapping

    isCombined = node.TexRemap.color_spline_type == '0'
    isColor = node.TexRemap.type == '1' # HSV does not support the combined curve

    if isCombined and isColor:
        # When using the "Combined" spline type with "Color Remap", all color channels
        # should be assigned the values of the "C" (combined) curve.
        curve  = curveMapping.curves[3]
        positions, values, types = fillSplineData(curve, curveMapping.extend)

        for i in range(0, 3):
            setChannel(i, pluginDesc, positions, values, types)
    else:
        # Export individual channels using the "R", "G", and "B" curves for "Color Remap",
        # or "H", "S", and "V" curves for "HSV Remap".
        for i in range(0, 3):
            curve  = curveMapping.curves[i]
            positions, values, types = fillSplineData(curve, curveMapping.extend)
            setChannel(i, pluginDesc, positions, values, types)
            
    commonNodesExport.exportNodeTree(nodeCtx, pluginDesc)
    return commonNodesExport.exportPluginWithStats(nodeCtx, pluginDesc)



def registerCurvesNodes():
    """ Register update callbacks for all TexRemap nodes in the scene """
    for mtl in bpy.data.materials:
        if ntree := getattr(mtl, 'node_tree', None):
            for n in [n for n in ntree.nodes if n.bl_idname == 'VRayNodeTexRemap']:
                _addCurvesUpdateCallback(n)


def createMtlCurvesNodes(mtl: bpy.types.Material):
    if ntree := getattr(mtl, 'node_tree', None):
        for n in [n for n in ntree.nodes if n.bl_idname == 'VRayNodeTexRemap']:
            loadCurvesData(n)

def _getCurvesNodeName(node: bpy.types.Node):
    return f"{node.vray_plugin}{node.static_id}"


def getCurvesNode(node):
    return bpy.data.node_groups[_CURVE_NODES_TREE_NAME].nodes[_getCurvesNodeName(node)]


def _hasCurvesNode(node: bpy.types.Node):
    """ Return True if a curves node has already been created for the input node """
    if group := bpy.data.node_groups.get(_CURVE_NODES_TREE_NAME, None):
        return _getCurvesNodeName(node) in group.nodes

    return False


def _addCurvesUpdateCallback(node: bpy.types.Node):
    from vray_blender.nodes.nodes import vrayNodeUpdate
    # To handle updates to the ShaderNodeRGBCurve used for its CurveMapping widget,
    # we subscribe to it with msgbus and attach the vrayNodeUpdate function.
    curvesNode = getCurvesNode(node)
    bpy.msgbus.clear_by_owner(curvesNode) # clearing previous subscriptions to be sure that there aren't other update callbacks left
    bpy.msgbus.subscribe_rna(
        key=curvesNode,
        owner=curvesNode,
        args=(node,),
        notify=vrayNodeUpdate,
    )


def _createCurvesNode(node: bpy.types.Node):
    if _CURVE_NODES_TREE_NAME not in bpy.data.node_groups:
        bpy.data.node_groups.new(_CURVE_NODES_TREE_NAME, "ShaderNodeTree")

    texRemapTree = bpy.data.node_groups[_CURVE_NODES_TREE_NAME]
    texRemapTree.use_fake_user=True
    curvesNode = texRemapTree.nodes.new("ShaderNodeRGBCurve")
    curvesNode.name=_getCurvesNodeName(node)
    
    _addCurvesUpdateCallback(node)

    return curvesNode


# This is not quite correct for bezier curves, but is close enough for now as it simply calculates a linearly
# extrapolated point from the two next to it. For this to be correct we would need some more complex math which
# would be easier done in the plugin itself.
def _extrapolateToEdge(p1, p2, extend, left=True):
    x1, y1 = p1
    x2, y2 = p2

    if left and math.isclose(x1, 0.0):
        return None
    if not left and math.isclose(x2, 1.0):
        return None

    if extend=='HORIZONTAL':
        if left:
            return (0.0, y1)
        else:
            return (1.0, y2)

    dx = x2 - x1
    dy = y2 - y1

    if dx == 0 and dy == 0:
        return None

    if left:
        tx = float('-inf') if dx == 0 else (0 - x1) / dx
        ty = float('-inf') if dy == 0 else (0 - y1) / dy
    else:
        tx = float('inf') if dx == 0 else (1 - x1) / dx
        ty = float('inf') if dy == 0 else (1 - y1) / dy

    if left:
        t = max(tx, ty)
    else:
        t = min(tx, ty)

    x = x1 + t * dx
    y = y1 + t * dy

    return (x, y)


def _getInterpolation(point: bpy.types.CurveMapPoint):
    if point.handle_type == 'VECTOR':
        return 0 # linear
    else:
        return 4 # bezier
    
    
def fillSplineData(curve: bpy.types.CurveMap, extend: str):
    positions, values, types = [], [], []
    left, right = None, None
    if len(curve.points) >= 2:
        left = _extrapolateToEdge(curve.points[0].location, curve.points[1].location, extend, True)
        right = _extrapolateToEdge(curve.points[-2].location, curve.points[-1].location, extend, False)

    if left:
        positions.append(left[0])
        values.append(left[1])
        types.append(_getInterpolation(curve.points[0]))
    for point in curve.points:
        positions.append(point.location.x)
        values.append(point.location.y)
        types.append(_getInterpolation(point))
    if right:
        positions.append(right[0])
        values.append(right[1])
        types.append(_getInterpolation(curve.points[-1]))
    return positions, values, types

