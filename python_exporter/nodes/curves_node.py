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

# Stable owners for curve node msgbus subscriptions, keyed by curves node name.
_CURVE_NODE_OWNERS: dict[str, object] = {}


def _getCurveNodeOwner(curvesNodeName: str) -> object:
    return _CURVE_NODE_OWNERS.setdefault(curvesNodeName, object())


def copyCurvesData(srcCurvesNode: bpy.types.Node, dstCurvesNode: bpy.types.Node):
    """Copy CurveMapping data from one ShaderNodeRGBCurve to another."""
    for i, curve in enumerate(srcCurvesNode.mapping.curves):
        dstCurve = dstCurvesNode.mapping.curves[i]
        while len(dstCurve.points) < len(curve.points):
            dstCurve.points.new(0, 0)
        for j, point in enumerate(curve.points):
            dstCurve.points[j].location = point.location
            dstCurve.points[j].handle_type = point.handle_type
    dstCurvesNode.mapping.extend = srcCurvesNode.mapping.extend
    dstCurvesNode.mapping.update()


def curvesCopy(copyNode: bpy.types.Node, origNode: bpy.types.Node):
    """Handles the logic for copying the node."""
    copyNode.assignStaticId() # The static id is needed for the name of the curves node

    createCurvesNode(copyNode)
    copyCurvesNode = getCurvesNode(copyNode)
    origCurvesNode = getCurvesNode(origNode)

    copyCurvesData(origCurvesNode, copyCurvesNode)


def hasCurvesNode(node: bpy.types.Node, nameSuffix=''):
    """Return True if a curves node has already been created for this node."""
    if group := bpy.data.node_groups.get(_CURVES_NODE_TREE_NAME, None):
        return getCurvesNodeName(node, nameSuffix) in group.nodes

    return False


def registerCurveNodes(handlers: dict):
    """Register update callbacks for all curve-based nodes in a single pass.

    handlers: {bl_idname: callable(node)} mapping node types to their registration functions.
    """
    from vray_blender.nodes.tools import iterVRayNodeTrees
    for ntree in iterVRayNodeTrees():
        for n in ntree.nodes:
            if handler := handlers.get(n.bl_idname):
                handler(n)


def registerCurveNodeSubscriptions():
    """ (Re)subscribe msgbus updates for all CurvesMap (Remap) widget nodes. """
    from vray_blender.plugins.effects.VolumeVRayToon import registerNodeCurves as registerVolumeVRayToonNodeCurves
    from vray_blender.plugins.BRDF.BRDFToonMtl import registerNodeCurves as registerBRDFToonMtlNodeCurves
    from vray_blender.plugins.BRDF.BRDFToonOverride import registerNodeCurves as registerBRDFToonOverrideNodeCurves

    registerCurveNodes({
        'VRayNodeTexRemap': addCurvesUpdateCallback,
        'VRayNodeBRDFToonMtl': registerBRDFToonMtlNodeCurves,
        'VRayNodeBRDFToonOverride': registerBRDFToonOverrideNodeCurves,
        'VRayNodeVolumeVRayToon': registerVolumeVRayToonNodeCurves,
    })


def loadCurvesData(node: bpy.types.Node):
    """Creates a curves node and decodes its data."""
    curvesNode = createCurvesNode(node)
    decodeMapping(node.BRDFToonMtl.curves_data, curvesNode.mapping)


def initImportedCurveNodes(ntree: bpy.types.NodeTree):
    """Initialize curve nodes for all nodes in an imported node tree.

    Unlike load-time registration (which assumes curves nodes already exist in .texRemapTree),
    this creates curves nodes from stored data or defaults as appropriate for each node type.
    """
    from vray_blender.plugins.effects.VolumeVRayToon import registerNodeCurves as _registerToonCurves
    from vray_blender.plugins.BRDF.BRDFToonMtl import registerNodeCurves as _registerToonMtlCurves
    from vray_blender.plugins.BRDF.BRDFToonOverride import registerNodeCurves as _registerToonOverrideCurves

    for node in ntree.nodes:
        if node.bl_idname == 'VRayNodeBRDFToonMtl':
            loadCurvesData(node)                # highlight-shape curve (from the stored string)
            _registerToonMtlCurves(node)        # depth/angular line-width curves
        elif node.bl_idname == 'VRayNodeBRDFToonOverride':
            _registerToonOverrideCurves(node)
        elif node.bl_idname == 'VRayNodeTexRemap':
            curvesNode = createCurvesNode(node)
            if curvesData := node.TexRemap.get('curves_data'):
                decodeMapping(curvesData, curvesNode.mapping)
        elif node.bl_idname == 'VRayNodeVolumeVRayToon':
            _registerToonCurves(node)


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

    for c in range(len(curvesData)):
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


def removeCurvesNode(node: bpy.types.Node, nameSuffix=''):
    """Removes a CurvesMap node from the CurvesMap node tree."""
    curvesNode = getCurvesNode(node, nameSuffix)
    bpy.msgbus.clear_by_owner(_CURVE_NODE_OWNERS.pop(curvesNode.name))
    bpy.data.node_groups[_CURVES_NODE_TREE_NAME].nodes.remove(curvesNode)


def getCurvesNode(node: bpy.types.Node, nameSuffix=''):
    """Retrieves the curve node from the hidden node tree."""
    return bpy.data.node_groups[_CURVES_NODE_TREE_NAME].nodes[getCurvesNodeName(node, nameSuffix)]


def addCurvesUpdateCallback(node: bpy.types.Node, curvesNodeOverride: bpy.types.Node = None):
    """To handle updates to the ShaderNodeRGBCurve used for its CurveMapping widget,
    it is subscribed with msgbus and the vrayNodeUpdate function is attached."""
    curvesNode = curvesNodeOverride or getCurvesNode(node)

    # The evaluated node may be invalid when accessed by the msgbus,
    # so we use the original node instead.
    nodeTree = node.id_data.original
    originalNode = nodeTree.nodes.get(node.name)

    # Clear previous subscriptions to be sure that there aren't other update callbacks left
    owner = _getCurveNodeOwner(curvesNode.name)
    bpy.msgbus.clear_by_owner(owner)
    bpy.msgbus.subscribe_rna(
        key=curvesNode,
        owner=owner,
        args=(originalNode,),
        notify=vrayNodeUpdate,
    )


def getCurvesNodeName(node: bpy.types.Node, nameSuffix=''):
    """Retrieves the curves node name from the node."""
    return f"{node.vray_plugin}{node.static_id}{nameSuffix}"


def createCurvesNode(node: bpy.types.Node, nameSuffix=''):
    """Creates a new tree and the curves node inside it."""
    if _CURVES_NODE_TREE_NAME not in bpy.data.node_groups:
        bpy.data.node_groups.new(_CURVES_NODE_TREE_NAME, "ShaderNodeTree")

    toonRemapTree = bpy.data.node_groups[_CURVES_NODE_TREE_NAME]
    toonRemapTree.use_fake_user = True
    curvesNode = toonRemapTree.nodes.new("ShaderNodeRGBCurve")
    curvesNode.name = getCurvesNodeName(node, nameSuffix)

    addCurvesUpdateCallback(node, curvesNode)

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


def exportLineWidthCurves(nodeCtx, plDesc, node: bpy.types.Node, curveTypes=('depth', 'angular')):
    """Export a node's per-material depth/angular line-width curves as V-Ray float curves.

    Reads the node's suffixed curve widget nodes ('_depth', '_angular') and sets
    <type>CurvePositions / <type>CurveInterpolations / <type>CurveValues on plDesc.
    Shared by BRDFToonMtl (surface) and BRDFToonOverride (Material Output 'Outlines' socket),
    both of which carry the same curve parameters as the global VolumeVRayToon.
    """
    from vray_blender.lib.names import Names
    from vray_blender.lib.defs import PluginDesc
    from vray_blender.exporting import node_export as commonNodesExport

    for curveType in curveTypes:
        curve = getCurvesNode(node, f'_{curveType}').mapping.curves[3]

        plDesc.setAttribute(f"{curveType}CurvePositions", [pt.location[0] for pt in curve.points])
        plDesc.setAttribute(f"{curveType}CurveInterpolations",
                            [("3" if "AUTO" in pt.handle_type else "1") for pt in curve.points])

        # The Y coordinates are exported as a TEXTURE_FLOAT_LIST, i.e. a list of plugins.
        pointYCoords = []
        for pt in curve.points:
            texPlugin = PluginDesc(Names.nextVirtualNode(nodeCtx, 'FloatToTex'), 'FloatToTex')
            texPlugin.setAttribute('input', pt.location[1])
            pointYCoords.append(commonNodesExport.exportPluginWithStats(nodeCtx, texPlugin))

        plDesc.setAttribute(f"{curveType}CurveValues", pointYCoords)


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
