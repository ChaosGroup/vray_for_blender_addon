# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Common logic related to nodes using the Blender's Curves Node (Remap) widget."""
import bpy
import json
import math

from vray_blender.nodes.nodes import vrayNodeUpdate


_POINT_TYPES_ENCODE = { 'AUTO': 1, 'AUTO_CLAMPED': 2, 'VECTOR': 3 }
_POINT_TYPES_DECODE = { v: k for k, v in _POINT_TYPES_ENCODE.items() }
# Name for hidden node tree containing ShaderNodeRGBCurve nodes.
# Keeping old name for compatibility, even though it's not just for TexRemap anymore.
_CURVES_NODE_TREE_NAME = ".texRemapTree"


def curvesCopy(copyNode: bpy.types.Node, origNode: bpy.types.Node):
    """Handles the logic for copying the node."""
    copyNode.assignStaticId() # The static id is needed for the name of the curves node

    createCurvesNode(copyNode)
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


def hasCurvesNode(node: bpy.types.Node):
    """Return True if a curves node has already been created for this node."""
    if group := bpy.data.node_groups.get(_CURVES_NODE_TREE_NAME, None):
        return getCurvesNodeName(node) in group.nodes

    return False


def registerColorMapCurves(nodeName: str):
    """Register update callbacks for all BRDFToonMtl nodes in the scene."""
    for mtl in bpy.data.materials:
        if ntree := getattr(mtl, 'node_tree', None):
            for n in [n for n in ntree.nodes if n.bl_idname == nodeName]:
                addCurvesUpdateCallback(n)


def loadCurvesData(node: bpy.types.Node):
    """Creates a curves node and decodes its data."""
    curvesNode = createCurvesNode(node)
    decodeMapping(node.BRDFToonMtl.curves_data, curvesNode.mapping)


def createMtlCurvesNodes(mtl: bpy.types.Material):
    """Create curves nodes for every Toon Material node in the tree."""
    if ntree := getattr(mtl, 'node_tree', None):
        for n in [n for n in ntree.nodes if n.bl_idname == 'VRayNodeBRDFToonMtl']:
            loadCurvesData(n)


def encodeMapping(mapping: bpy.types.CurveMapping):
    """Encodes the Remap widget data."""
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


def decodeMapping(jsonData: str, mapping: bpy.types.CurveMapping):
    """Decodes the Remap widget data."""
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


def removeCurvesNode(node: bpy.types.Node):
    """Removes a CurvesMap node from the CurvesMap node tree."""
    curvesNode = getCurvesNode(node)
    bpy.msgbus.clear_by_owner(curvesNode)
    bpy.data.node_groups[_CURVES_NODE_TREE_NAME].nodes.remove(curvesNode)


def getCurvesNode(node: bpy.types.Node):
    """Retrieves the curve node from the hidden node tree."""
    return bpy.data.node_groups[_CURVES_NODE_TREE_NAME].nodes[getCurvesNodeName(node)]


def addCurvesUpdateCallback(node: bpy.types.Node):
    """To handle updates to the ShaderNodeRGBCurve used for its CurveMapping widget,
    it is subscribed with msgbus and the vrayNodeUpdate function is attached."""
    curvesNode = getCurvesNode(node)
    # Clear previous subscriptions to be sure that there aren't other update callbacks left
    bpy.msgbus.clear_by_owner(curvesNode)
    bpy.msgbus.subscribe_rna(
        key=curvesNode,
        owner=curvesNode,
        args=(node,),
        notify=vrayNodeUpdate,
    )


def getCurvesNodeName(node: bpy.types.Node):
    """Retrieves the curves node name from the node."""
    return f"{node.vray_plugin}{node.static_id}"


def createCurvesNode(node: bpy.types.Node):
    """Creates a new tree and the curves node inside it."""
    if _CURVES_NODE_TREE_NAME not in bpy.data.node_groups:
        bpy.data.node_groups.new(_CURVES_NODE_TREE_NAME, "ShaderNodeTree")

    toonRemapTree = bpy.data.node_groups[_CURVES_NODE_TREE_NAME]
    toonRemapTree.use_fake_user=True
    curvesNode = toonRemapTree.nodes.new("ShaderNodeRGBCurve")
    curvesNode.name=getCurvesNodeName(node)

    addCurvesUpdateCallback(node)

    return curvesNode


def _extrapolateToEdge(p1, p2, extend, left=True):
    """Linearly extrapolates a point along the line defined
    by p1 -> p2 in the bounds of the remap widget."""
    # This is not quite correct for bezier curves, but is close enough for now as
    # it simply calculates a linearly extrapolated point from the two next to it.
    # For this to be correct we would need some more complex math which would be
    # easier done in the plugin itself.
    x1, y1 = p1
    x2, y2 = p2

    # Nothing to extrapolate if point lies along the ends of the boundaries.
    if left and math.isclose(x1, 0.0):
        return None
    if not left and math.isclose(x2, 1.0):
        return None

    # Clamp X values. Don't extrapolate along the line. Projects horizontally.
    if extend=='HORIZONTAL':
        return (0.0, y1) if left else (1.0, y2)

    # Compute direction vector.
    dx = x2 - x1
    dy = y2 - y1

    # No extrapolation exists if points are overlapped (len(directionVector) = 0).
    if dx == 0 and dy == 0:
        return None

    # Compute candidate intersection parameters.
    # Compute parametric "t" values where the line would intersect the boundaries.
    if left:
        tx = float('-inf') if dx == 0 else (0 - x1) / dx
        ty = float('-inf') if dy == 0 else (0 - y1) / dy
    else:
        tx = float('inf') if dx == 0 else (1 - x1) / dx
        ty = float('inf') if dy == 0 else (1 - y1) / dy

    t = max(tx, ty) if left else min(tx, ty)

    # Define the parametric line.
    x = x1 + t * dx
    y = y1 + t * dy

    return (x, y)


def _getInterpolation(point: bpy.types.CurveMapPoint):
    """Maps Blender's "VECTOR" interpolation to "linear". Everything else - "bezier"."""
    return 1 if point.handle_type == 'VECTOR' else 4


def fillSplineData(curve: bpy.types.CurveMap, extend: str):
    """Returns all the x, y values of the points of a
    Remap (CurveMap) widget and their handle types."""
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
