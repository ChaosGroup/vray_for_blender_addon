# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Common logic related to nodes using the Blender's Curves Node (Remap) widget."""
import bpy
import json
import math

from vray_blender import debug
from vray_blender.nodes.nodes import vrayNodeUpdate


_POINT_TYPES_ENCODE = { 'AUTO': 1, 'AUTO_CLAMPED': 2, 'VECTOR': 3 }
_POINT_TYPES_DECODE = { v: k for k, v in _POINT_TYPES_ENCODE.items() }
# Name for hidden node tree containing ShaderNodeRGBCurve nodes.
# Keeping old name for compatibility, even though it's not just for TexRemap anymore.
_CURVES_NODE_TREE_NAME = ".texRemapTree"

# Stable owners for curve node msgbus subscriptions, keyed by curves node name.
_CURVE_NODE_OWNERS: dict[str, object] = {}

# Curves node names that existed before the current append, link or paste started.
_curvesNodesBeforeImport: set[str] = set()


def _getCurveNodeOwner(curvesNodeName: str) -> object:
    return _CURVE_NODE_OWNERS.setdefault(curvesNodeName, object())


def assignCurvesId(node: bpy.types.Node, nameSuffixes=('',)):
    """Assign the node a static id for which all its curves node names are still free.

    The id comes from a per-scene counter while the curves tree is global to the .blend, so an id from
    another file (append, link, paste) may be taken. A node can own several curves nodes sharing one
    id, hence the suffixes.
    """
    node.assignStaticId()

    if tree := bpy.data.node_groups.get(_CURVES_NODE_TREE_NAME, None):
        while any(getCurvesNodeName(node, s) in tree.nodes for s in nameSuffixes):
            node.assignStaticId()


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


def curvesCopy(copyNode: bpy.types.Node, origNode: bpy.types.Node, nameSuffixes=('',)):
    """Give copyNode curves nodes of its own with origNode's curves."""
    hasOwnCurves = copyNode.static_id != origNode.static_id
    if not hasOwnCurves:
        assignCurvesId(copyNode, nameSuffixes)

    for suffix in nameSuffixes:
        copyCurvesNode = ensureCurvesNode(copyNode, suffix) if hasOwnCurves else createCurvesNode(copyNode, suffix)
        copyCurvesData(ensureCurvesNode(origNode, suffix), copyCurvesNode)


def hasCurvesNode(node: bpy.types.Node, nameSuffix=''):
    """Return True if a curves node has already been created for this node."""
    if group := bpy.data.node_groups.get(_CURVES_NODE_TREE_NAME, None):
        return getCurvesNodeName(node, nameSuffix) in group.nodes

    return False


def registerCurveNodes(handlers: dict):
    """Register update callbacks for all curve-based nodes in a single pass.

    handlers: {bl_idname: callable(node)} mapping node types to their registration functions.
    Also stores each node's curves, for files saved before they were stored on the node.
    """
    from vray_blender.nodes.tools import iterVRayNodeTreesWithOwners
    for owner, ntree in iterVRayNodeTreesWithOwners():
        for n in ntree.nodes:
            if handler := handlers.get(n.bl_idname):
                handler(n)
                if owner.library is None:
                    storeCurvesData(n)


def ensureCurvesNode(node: bpy.types.Node, nameSuffix=''):
    """Return the node's curves node, recreating it from the stored data if it is missing.

    Scenes saved before name collisions were prevented can point at a curves node that is gone: a
    second node took the '.001' name, then deleting the first took the name the survivor still uses.
    """
    if hasCurvesNode(node, nameSuffix):
        return getCurvesNode(node, nameSuffix)

    debug.printInfo(f"Curves node '{getCurvesNodeName(node, nameSuffix)}' is missing, recreating it")
    curvesNode = createCurvesNode(node, nameSuffix)

    propGroup = getattr(node, node.vray_plugin, None)
    if propGroup is not None and (curvesData := propGroup.get('curves_data')):
        decodeMapping(curvesData, curvesNode.mapping)

    return curvesNode


def getCurvesNodeForDraw(node: bpy.types.Node, nameSuffix=''):
    """Return the node's curves node for a draw callback, or None if it is missing.

    Draw code cannot create datablocks, so it cannot use ensureCurvesNode. Callers report the gap in
    the UI; the node itself is rebuilt by ensureCurvesNode the next time the file loads.
    """
    tree = bpy.data.node_groups.get(_CURVES_NODE_TREE_NAME, None)
    return tree.nodes.get(getCurvesNodeName(node, nameSuffix)) if tree else None


def drawMissingCurvesNode(layout: bpy.types.UILayout):
    """Placeholder for a curve widget whose curves node is missing."""
    layout.label(text="Curve unavailable. Reopen the file to restore it.", icon='ERROR')


def registerTexRemapCurves(node: bpy.types.Node):
    """Register the update callback for a single TexRemap node."""
    addCurvesUpdateCallback(node, ensureCurvesNode(node))


def _curveNodeRegisterHandlers() -> dict:
    """Return {bl_idname: callable(node)} that creates a node's missing curves nodes and subscribes them."""
    from vray_blender.plugins.effects.VolumeVRayToon import registerNodeCurves as registerVolumeVRayToonNodeCurves
    from vray_blender.plugins.BRDF.BRDFToonMtl import registerNodeCurves as registerBRDFToonMtlNodeCurves
    from vray_blender.plugins.BRDF.BRDFToonOverride import registerNodeCurves as registerBRDFToonOverrideNodeCurves

    return {
        'VRayNodeTexRemap': registerTexRemapCurves,
        'VRayNodeBRDFToonMtl': registerBRDFToonMtlNodeCurves,
        'VRayNodeBRDFToonOverride': registerBRDFToonOverrideNodeCurves,
        'VRayNodeVolumeVRayToon': registerVolumeVRayToonNodeCurves,
    }


def registerCurveNodeSubscriptions():
    """ (Re)subscribe msgbus updates for all CurvesMap (Remap) widget nodes. """
    registerCurveNodes(_curveNodeRegisterHandlers())

    pruneOrphanCurveNodes()


# Node types that own curves nodes, and the name suffixes each one uses.
_CURVE_NODE_SUFFIXES = {
    'VRayNodeTexRemap':         ('',),
    'VRayNodeBRDFToonMtl':      ('', '_depth', '_angular'),
    'VRayNodeBRDFToonOverride': ('_depth', '_angular'),
    'VRayNodeVolumeVRayToon':   ('_depth', '_angular'),
}


def pruneOrphanCurveNodes():
    """Remove curves nodes that no node uses any more.

    Copying a node tree creates temporary nodes whose curves nodes stay behind, so the tree grows on
    every copy, paste and append. Run after the curve nodes have been registered, so anything
    ensureCurvesNode had to recreate is counted as still in use.
    """
    from vray_blender.nodes.tools import iterVRayNodeTrees

    if (tree := bpy.data.node_groups.get(_CURVES_NODE_TREE_NAME, None)) is None:
        return

    inUse = set()
    for ntree in iterVRayNodeTrees():
        for n in ntree.nodes:
            for suffix in _CURVE_NODE_SUFFIXES.get(n.bl_idname, ()):
                inUse.add(getCurvesNodeName(n, suffix))

    for curvesNode in [n for n in tree.nodes if n.name not in inUse]:
        bpy.msgbus.clear_by_owner(_CURVE_NODE_OWNERS.pop(curvesNode.name, object()))
        tree.nodes.remove(curvesNode)


def storeCurvesData(node: bpy.types.Node):
    """Save the node's curves on its property group as 'curves_data<suffix>'.

    The curves nodes live in a hidden tree that append, link and paste do not bring along, so the
    imported node is rebuilt from this copy.
    """
    propGroup = getattr(node, node.vray_plugin)
    for suffix in _CURVE_NODE_SUFFIXES[node.bl_idname]:
        if hasCurvesNode(node, suffix):
            # Dictionary access, so saving does not tag the node as updated.
            propGroup[f'curves_data{suffix}'] = encodeMapping(getCurvesNode(node, suffix).mapping)


def storeAllCurvesData():
    """storeCurvesData() for every local node that owns curves nodes. Linked data is read-only."""
    from vray_blender.nodes.tools import iterVRayNodeTreesWithOwners

    for owner, ntree in iterVRayNodeTreesWithOwners():
        if owner.library is None:
            for n in ntree.nodes:
                if n.bl_idname in _CURVE_NODE_SUFFIXES:
                    storeCurvesData(n)


def recordCurvesNodesBeforeImport():
    """Remember which curves nodes exist before an import. See _reassignImportedCurvesId."""
    tree = bpy.data.node_groups.get(_CURVES_NODE_TREE_NAME, None)
    _curvesNodesBeforeImport.clear()
    if tree:
        _curvesNodesBeforeImport.update(n.name for n in tree.nodes)


def _reassignImportedCurvesId(node: bpy.types.Node, nameSuffixes) -> bool:
    """Give an imported node a free static id, unless it already owns all its curves nodes.

    Imported nodes carry the static ids of the file they came from, which may be taken here. A pasted
    node is the exception: its nodeCopy already ran and gave it curves nodes of its own, and a new id
    would leave those behind unused. Only curves nodes created during this import can be such. One
    that was already there belongs to a local node, or is an orphan a copy left behind.

    Returns True if a new id was assigned, i.e. the caller still has to create the nodes.
    """
    if all(hasCurvesNode(node, s)
           and getCurvesNodeName(node, s) not in _curvesNodesBeforeImport
           and not _isCurvesNodeUsedByOtherNode(node, s) for s in nameSuffixes):
        return False

    assignCurvesId(node, nameSuffixes)
    return True


def initImportedCurveNodes(ntree: bpy.types.NodeTree, getSourceCurvesData):
    """Initialize curve nodes for all nodes in an imported node tree.

    A node that got a new id has no curves nodes yet, so they are rebuilt from the data stored by
    storeCurvesData. Files saved before that existed only have the curves in their own curves tree,
    which getSourceCurvesData() returns as {curves node name: encoded curve}. Whatever is still
    missing is created with its defaults by the node's handler.
    """
    handlers = _curveNodeRegisterHandlers()

    for node in ntree.nodes:
        if (suffixes := _CURVE_NODE_SUFFIXES.get(node.bl_idname)) is None:
            continue

        # The names in the source file, before the node gets a new id.
        sourceNames = {s: getCurvesNodeName(node, s) for s in suffixes}

        if _reassignImportedCurvesId(node, suffixes):
            propGroup = getattr(node, node.vray_plugin)
            for suffix in suffixes:
                if curvesData := (propGroup.get(f'curves_data{suffix}')
                                  or getSourceCurvesData().get(sourceNames[suffix])):
                    decodeMapping(curvesData, createCurvesNode(node, suffix).mapping)

        handlers[node.bl_idname](node)


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


def _isCurvesNodeUsedByOtherNode(node: bpy.types.Node, nameSuffix: str) -> bool:
    """Return True if a node other than 'node' resolves to the same curves node.

    Copying an object creates temporary nodes that still carry the static id of the node they were
    copied from. Freeing one of those would take the curves node the original is still using.
    """
    from vray_blender.nodes.tools import iterVRayNodeTrees

    curvesNodeName = getCurvesNodeName(node, nameSuffix)

    for ntree in iterVRayNodeTrees():
        for n in ntree.nodes:
            if (n != node) and hasattr(n, 'static_id') and getCurvesNodeName(n, nameSuffix) == curvesNodeName:
                return True
    return False


def removeCurvesNode(node: bpy.types.Node, nameSuffix=''):
    """Removes a CurvesMap node from the CurvesMap node tree."""
    if _isCurvesNodeUsedByOtherNode(node, nameSuffix):
        # An unused curves node left behind does no harm. Removing one that is still in use leaves its
        # node pointing at a name that no longer exists, which breaks its widget and its export.
        return

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

    # Clear previous subscriptions to be sure that there aren't other update callbacks left
    owner = _getCurveNodeOwner(curvesNode.name)
    bpy.msgbus.clear_by_owner(owner)
    bpy.msgbus.subscribe_rna(
        key=curvesNode,
        owner=owner,
        args=(curvesNode.name,),
        notify=_onCurvesNodeUpdate,
    )


def _onCurvesNodeUpdate(curvesNodeName: str):
    """ msgbus notify for a curves node. Finds the nodes that use it by name because a node kept
        in the subscription args goes stale after undo, node removal or addon re-enable.
    """
    from vray_blender.nodes.tools import iterVRayNodeTrees
    for ntree in iterVRayNodeTrees():
        for n in ntree.nodes:
            if any(getCurvesNodeName(n, s) == curvesNodeName for s in _CURVE_NODE_SUFFIXES.get(n.bl_idname, ())):
                # Also on edit, not only on save: Ctrl+C writes the copy buffer without a save.
                storeCurvesData(n)
                vrayNodeUpdate(n)


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

    nodeName = getCurvesNodeName(node, nameSuffix)
    curvesNode.name = nodeName

    if curvesNode.name != nodeName:
        # Another node owns the name, so both now resolve to the same curves node.
        debug.printError(f"Curves node name '{nodeName}' is already taken, got '{curvesNode.name}'")

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
