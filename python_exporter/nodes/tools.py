# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

from collections import defaultdict, deque

import bpy

from vray_blender.lib.attribute_types import CompatibleNonVrayNodes
from vray_blender.lib.mixin import VRayNodeBase


NODE_LEVEL_WIDTH = VRayNodeBase.bl_width_default + 50

# image_utils parks this bookkeeping node at x = -100000 so the user never sees it. Select All takes it with them, so anything doing
# geometry over nodes must skip it. Defined here because image_utils imports this module.
TEXTURE_PLACEHOLDER_NODE_NAME = "V-Ray Texture Placeholder"


def isTexturePlaceholder(node) -> bool:
    return node.name == TEXTURE_PLACEHOLDER_NODE_NAME

# Approximate UI height (UI units) of one drawn row - a socket or a body widget.
# Calibrated against measured node.dimensions (see _getNodeHeight).
SOCKET_HEIGHT = 20

# Node title bar + top/bottom padding, before any rows are added.
_HEADER_HEIGHT = 50

# Layout tuning, all in UI units (see _uiScale for why the distinction matters).
_COLUMN_GAP = 50    # horizontal gap between columns
_ROW_GAP    = 2    # vertical gap between stacked nodes
_FRAME_PAD  = 30    # padding around a frame's children

# Extra height (UI units) for the few node types whose body is dominated by a large
# NON-socket widget that no socket inspection can predict: an image preview thumbnail
# or a colour ramp. This is stable UI structure (a node either draws a ramp or it does
# not) and is NOT tied to the parameter list, so it does not silently break when a
# plugin's parameters change. Only consulted by the headless estimate below; when
# node.dimensions is available the real drawn size is used and this is irrelevant.
_EXTRA_WIDGET_HEIGHT_BY_PLUGIN = {
    'TexBitmap':   190,   # image preview thumbnail
    'TexGradRamp': 150,   # colour ramp widget
}


def _socketRowCount(sock: bpy.types.NodeSocket) -> int:
    """ Rows an input socket occupies when drawn.

        A linked (or value-hidden) socket collapses to a single label row. An unlinked
        vector socket draws one numeric field per component (a stacked column), so it is
        a few rows tall; colour swatches and (compactly drawn / usually-linked) matrices
        stay one row. Read from the socket's own 'value' property - its array length and
        subtype - so it is correct for any node instead of hard-coding node types.
    """
    if sock.is_linked or getattr(sock, 'hide_value', False):
        return 1

    prop = sock.bl_rna.properties.get('value')
    if prop is not None and getattr(prop, 'is_array', False) and prop.array_length >= 2:
        subtype = getattr(prop, 'subtype', 'NONE')
        # COLOR -> single swatch; MATRIX (e.g. a 4x4 transform) draws compactly and is
        # almost always driven by a link; only XYZ/EULER/plain vectors stack per-axis.
        if subtype in {'COLOR', 'MATRIX'}:
            return 1
        return min(prop.array_length, 3)
    return 1


_bodyWidgetRowsByPlugin = {}   # plugin type -> row count (layout is static per type)


def _countWidgetRows(widgets) -> int:
    rows = 0
    for w in widgets or ():
        if not isinstance(w, dict):
            continue
        attrs = w.get('attrs')
        if attrs:
            # A ROW packs its props onto one line; COLUMN / default stacks them.
            rows += 1 if w.get('layout') == 'ROW' else len(attrs)
        if 'widgets' in w:
            rows += _countWidgetRows(w['widgets'])
    return rows


def _bodyWidgetRows(node: bpy.types.Node) -> int:
    """ Rows a node draws in its body from non-socket property widgets (e.g. TexFalloff's
        'type' / 'direction_type' controls). Read from the plugin's declared node UI
        layout (Node['widgets']) - the same description Blender draws from - so it tracks
        the real UI rather than guessing from the raw parameter list. Cached per plugin.
    """
    plugin = getattr(node, 'vray_plugin', None)
    if not plugin:
        return 0
    cached = _bodyWidgetRowsByPlugin.get(plugin)
    if cached is not None:
        return cached

    from vray_blender.plugins import findPluginModule
    rows = 0
    if (mod := findPluginModule(plugin)) is not None:
        nodeDesc = getattr(mod, 'Node', None)
        if isinstance(nodeDesc, dict):
            rows = _countWidgetRows(nodeDesc.get('widgets', []))
    _bodyWidgetRowsByPlugin[plugin] = rows
    return rows


def _getNodeHeight(node: bpy.types.Node) -> float:
    """ Estimate a node's drawn height (UI units) when node.dimensions is unavailable -
        e.g. a Cosmos/vrscene tree that has not been drawn yet. Calibrated against real
        measured dimensions; the interactive layout uses the exact node.dimensions.
    """
    height = _HEADER_HEIGHT

    # Stock Blender nodes (NodeReroute, NodeFrame, Cycles nodes via the
    # compatibility shim) don't carry `vray_plugin`. Read it defensively.
    plugin = getattr(node, 'vray_plugin', None)
    height += _EXTRA_WIDGET_HEIGHT_BY_PLUGIN.get(plugin, 0)

    # Non-socket property widgets drawn in the node body.
    height += _bodyWidgetRows(node) * SOCKET_HEIGHT

    for inp in node.inputs:
        if inp.enabled and not inp.hide:
            height += _socketRowCount(inp) * SOCKET_HEIGHT
    for out in node.outputs:
        if out.enabled and not out.hide:
            height += SOCKET_HEIGHT

    return height



# -------------------------------------------------------------
#  DPI SCALE  - node.dimensions is in DPI-scaled device pixels, while
#  node.location is in UI units. Dividing dimensions by this factor puts
#  both in the same coordinate space.
#
#  The factor is dpi/72 (the same one the wrangler uses for hit-testing,
#  helpers._dpiFac). It must NOT be pixel_size * ui_scale: on high-DPI
#  Windows the OS/monitor DPI inflates system.dpi (and the rendered node
#  dimensions) but leaves the ui_scale preference at 1.0, so that product
#  under-divides and every node ends up estimated far too tall - which
#  showed up as vertical gaps larger than the nodes themselves.
# -------------------------------------------------------------
def _uiScale() -> float:
    return bpy.context.preferences.system.dpi / 72.0


def _getNodeSize(node: bpy.types.Node, scale: float) -> tuple[float, float]:
    """ Return (width, height) of a node in UI units (i.e. node.location space).

        node.dimensions is only populated after the node editor has drawn the node
        (and is expressed in DPI-scaled pixels). When it is available we convert it to
        UI units; otherwise - e.g. during a headless/batch import with no visible
        editor - we fall back to the V-Ray height estimate (already in UI units).
    """
    # A reroute is a pass-through dot; treat it as a zero-size point so it neither
    # reserves a row in its column nor inflates the column width. It is placed onto
    # its wire separately (_positionReroutes).
    if node.type == 'REROUTE':
        return 0.0, 0.0

    dim = node.dimensions
    if dim.x > 1.0 and scale > 0.0:
        return dim.x / scale, dim.y / scale
    return node.width, _getNodeHeight(node)


def _nodeCenterY(node: bpy.types.Node, scale: float) -> float:
    return node.location.y - _getNodeSize(node, scale)[1] * 0.5


def _centerY(name: str, pos: dict, sizes: dict) -> float:
    """ _nodeCenterY for a node the layout is still working on, i.e. whose position lives in
        'pos' rather than on the node itself. """
    return pos[name][1] - sizes[name][1] * 0.5


def calculateTreeBounds(ntree):
    """ Axis-aligned bounds (minX, minY, maxX, maxY) of every non-frame node, in UI units, excluding the off-canvas placeholder. """
    scale = _uiScale()
    minX = minY = float('inf')
    maxX = maxY = float('-inf')

    for node in ntree.nodes:
        if node.type == 'FRAME' or isTexturePlaceholder(node):
            continue
        x, y = node.location
        w, h = _getNodeSize(node, scale)
        minX = min(minX, x)
        maxX = max(maxX, x + w)
        minY = min(minY, y - h)
        maxY = max(maxY, y)

    if minX == float('inf'):
        return (0, 0, 0, 0)
    return (minX, minY, maxX, maxY)


# -------------------------------------------------------------
#  LAYOUT PASSES
#
#  Topologically sort the graph into columns (sinks on the right,
#  sources on the left), order nodes inside each column to reduce wire
#  crossings, align columns to their wired neighbours, then remove any
#  remaining vertical overlaps. Adapted from the standalone
#  material-node auto-arranger; keyed by node name so the passes never
#  hold on to bpy node references across list mutations.
# --------------------------------------------------------------
def _assignDepths(nodes, links) -> dict:
    """ Distance (in edges) from each node to the nearest sink. Sinks are depth 0. """
    nameSet  = {n.name for n in nodes}
    children = defaultdict(set)   # node -> nodes it feeds
    parents  = defaultdict(set)   # node -> nodes feeding it

    for link in links:
        if link.from_node is None or link.to_node is None:
            continue
        fn, tn = link.from_node.name, link.to_node.name
        if fn in nameSet and tn in nameSet:
            children[fn].add(tn)
            parents[tn].add(fn)

    roots = [n for n in nodes if not children[n.name]] or list(nodes)

    # A cycle has no sink to terminate the relaxation below, so depths would grow without
    # bound. Imported trees can carry one (cyclic plugin references in real .vrscenes), so
    # cap the depth at the longest possible path in an acyclic graph - that breaks the
    # back-edge while leaving every acyclic layout untouched.
    maxDepth = len(nodes)

    depth = {}
    queue = deque()
    for r in roots:
        depth[r.name] = 0
        queue.append(r.name)
    while queue:
        name = queue.popleft()
        for parent in parents[name]:
            nd = depth[name] + 1
            if nd <= maxDepth and depth.get(parent, -1) < nd:
                depth[parent] = nd
                queue.append(parent)

    for n in nodes:
        depth.setdefault(n.name, 0)
    return depth


def _buildColumns(nodes, depth) -> dict:
    """ Group nodes into columns. Column index increases left→right, so the
        sinks (depth 0) end up in the rightmost column - the V-Ray convention
        of the output node on the right.
    """
    maxD = max(depth.values()) if depth else 0
    cols = defaultdict(list)
    for n in nodes:
        cols[maxD - depth[n.name]].append(n)
    return cols


def _columnLayout(cols, pos, sizes):
    xCursor = 0.0
    for ci in sorted(cols):
        colNodes = cols[ci]
        colW = max(sizes[n.name][0] for n in colNodes)
        yCursor = 0.0
        for n in colNodes:
            h = sizes[n.name][1]
            pos[n.name] = [xCursor, yCursor]
            yCursor -= h + _ROW_GAP
        xCursor += colW + _COLUMN_GAP


def _sortByPort(cols, links, pos, sizes):
    """ Order the nodes inside every column by (neighbour rank, socket index) so
        that wires run as straight as possible and cross each other less.
    """
    colRank = {}

    # Process right→left so a column's downstream neighbour ranks are already known.
    for ci in sorted(cols.keys(), reverse=True):
        colNodes = cols[ci]
        keys = {}

        for n in colNodes:
            scores = []

            # Prefer ordering by downstream targets (keeps wires straight into the sink).
            # The target may be OUTSIDE the arranged set (e.g. a shared node the whole
            # selection feeds into). Such nodes have no rank, so colRank.get(..., 0)
            # leaves the socket index as the sole tie-break - which orders this column
            # top-to-bottom by the sink's sockets and stops those wires from crossing.
            for link in links:
                if link.from_node is None or link.from_node.name != n.name:
                    continue
                toNode = link.to_node
                if toNode is None:
                    continue
                sockIdx = next((i for i, s in enumerate(toNode.inputs)
                                if s == link.to_socket), 0)
                scores.append(colRank.get(toNode.name, 0) * 10000 + sockIdx)

            if scores:
                keys[n.name] = sum(scores) / len(scores)
                continue

            # No downstream target: fall back to upstream sources (also possibly external).
            for link in links:
                if link.to_node is None or link.to_node.name != n.name:
                    continue
                fromNode = link.from_node
                if fromNode is None:
                    continue
                sockIdx  = next((i for i, s in enumerate(fromNode.outputs)
                                 if s == link.from_socket), 0)
                scores.append(colRank.get(fromNode.name, 0) * 10000 + sockIdx)

            keys[n.name] = sum(scores) / len(scores) if scores else -_centerY(n.name, pos, sizes)

        colNodes.sort(key=lambda n: keys[n.name])

        topY = max(_centerY(n.name, pos, sizes) + sizes[n.name][1] * 0.5 for n in colNodes)
        yCursor = topY
        for rank, n in enumerate(colNodes):
            h = sizes[n.name][1]
            pos[n.name][1] = yCursor
            yCursor -= h + _ROW_GAP
            colRank[n.name] = rank


def _alignColumn(colNodes, neighbors, pos, sizes):
    """ Shift a single column so its nodes sit at the average height of the nodes they
        are wired to in other columns, then re-stack it (kept non-overlapping) around
        the shifted centre. A lone node in the column ends up exactly on the average
        height of its wired neighbours, which keeps the connecting wires short.
    """
    targets = {
        n.name: sum(_centerY(nb, pos, sizes)
                    for nb in neighbors[n.name]
                    if nb in pos) / len(neighbors[n.name])
        for n in colNodes if neighbors[n.name]
    }
    if not targets:
        return

    deltaY = (
        sum(targets.get(n.name, _centerY(n.name, pos, sizes)) for n in colNodes) -
        sum(_centerY(n.name, pos, sizes) for n in colNodes)
    ) / len(colNodes)

    for n in colNodes:
        pos[n.name][1] += deltaY

    colNodes.sort(key=lambda n: -pos[n.name][1])
    colCy   = sum(_centerY(n.name, pos, sizes) for n in colNodes) / len(colNodes)
    totalH  = (sum(sizes[n.name][1] for n in colNodes)
               + _ROW_GAP * (len(colNodes) - 1))
    yCursor = colCy + totalH * 0.5
    for n in colNodes:
        h = sizes[n.name][1]
        pos[n.name][1] = yCursor
        yCursor -= h + _ROW_GAP


def _alignToNeighbors(cols, links, pos, sizes, passes=6):
    """ Line every column up with its wired neighbours in adjacent columns.

        Runs several alternating left→right / right→left sweeps (a barycenter relaxation)
        instead of a single pass. A single pass aligns the outer columns against inner
        ones that have not settled yet, which left lone nodes stranded far above/below the
        node they connect to; iterating lets those chains converge so connected sockets
        end up close together.
    """
    colOrder   = sorted(cols.keys())
    nodeToCol  = {n.name: ci for ci in cols for n in cols[ci]}

    neighbors = defaultdict(list)
    for link in links:
        if link.from_node is None or link.to_node is None:
            continue
        fn, tn = link.from_node.name, link.to_node.name
        if fn not in nodeToCol or tn not in nodeToCol:
            continue
        if nodeToCol[fn] != nodeToCol[tn]:
            neighbors[fn].append(tn)
            neighbors[tn].append(fn)

    if not neighbors:
        return

    for i in range(passes):
        order = colOrder if (i % 2 == 0) else colOrder[::-1]
        for ci in order:
            _alignColumn(cols[ci], neighbors, pos, sizes)


def _greedyOverlap(cols, pos, sizes, passes=3):
    """ Push nodes down until no two nodes in a column overlap vertically. """
    for colNodes in cols.values():
        if len(colNodes) < 2:
            continue
        for _ in range(passes):
            colNodes.sort(key=lambda n: -pos[n.name][1])
            changed = False
            for i in range(len(colNodes) - 1):
                a = colNodes[i]
                b = colNodes[i + 1]
                ah      = sizes[a.name][1]
                aBottom = pos[a.name][1] - ah
                gap     = pos[b.name][1] - aBottom
                if gap < _ROW_GAP:
                    push = _ROW_GAP - gap
                    for k in range(i + 1, len(colNodes)):
                        pos[colNodes[k].name][1] -= push
                    changed = True
            if not changed:
                break


def _updateFrames(ntree, scale):
    """ Resize every frame node to fit the children that were just laid out. """
    for frame in (n for n in ntree.nodes if n.type == 'FRAME'):
        children = [n for n in ntree.nodes
                    if n.parent == frame and n.type != 'FRAME']
        if not children:
            continue
        xs = [n.location.x for n in children]
        ys = [n.location.y for n in children]
        ws = [_getNodeSize(n, scale)[0] for n in children]
        hs = [_getNodeSize(n, scale)[1] for n in children]
        frame.location.x = min(xs) - _FRAME_PAD
        frame.location.y = max(ys) + _FRAME_PAD
        frame.width      = max(x + w for x, w in zip(xs, ws)) + _FRAME_PAD - frame.location.x
        frame.height     = frame.location.y - (min(y - h for y, h in zip(ys, hs)) - _FRAME_PAD)


def _positionReroutes(nodes, links, pos, sizes, passes=3):
    """ Snap reroute nodes onto the wire they sit on.

        Reroutes are pass-through dots: the column layout gives them a slot but no
        meaningful position, which is why they end up in odd places. Instead, place each
        reroute midway (in x) between the nodes it connects and at the average height of
        those nodes, so the wire stays readable. Iterated so chains of reroutes settle.
    """
    rerouteNames = {n.name for n in nodes if n.type == 'REROUTE'}
    if not rerouteNames:
        return

    nameSet = {n.name for n in nodes}
    inSrc  = defaultdict(list)   # reroute -> upstream nodes
    outTgt = defaultdict(list)   # reroute -> downstream nodes
    for link in links:
        if link.from_node is None or link.to_node is None:
            continue
        fn, tn = link.from_node.name, link.to_node.name
        if tn in rerouteNames and fn in nameSet:
            inSrc[tn].append(link.from_node)
        if fn in rerouteNames and tn in nameSet:
            outTgt[fn].append(link.to_node)

    reroutes = [n for n in nodes if n.type == 'REROUTE']
    for _ in range(passes):
        for r in reroutes:
            xs, ys = [], []
            for s in inSrc.get(r.name, ()):
                xs.append(pos[s.name][0] + sizes[s.name][0])   # source right edge
                ys.append(_centerY(s.name, pos, sizes))
            for t in outTgt.get(r.name, ()):
                xs.append(pos[t.name][0])                       # target left edge
                ys.append(_centerY(t.name, pos, sizes))
            if xs:
                pos[r.name][0] = sum(xs) / len(xs)
            if ys:
                pos[r.name][1] = sum(ys) / len(ys)


def _layout(nodes, links, scale):
    """ Run every layout pass on 'nodes'/'links'. Positions are produced around the
        origin; callers translate the result to wherever they want it anchored.

        Returns ({node name: [x, y]}, {node name: (width, height)}). Nothing is written to
        node.location here: each write dirties the node tree, and the next pass reading a
        location makes Blender flush that - which re-runs the update callback for every node
        in the tree. Across the relaxation passes those flushes cost far more than the layout
        arithmetic (measured on a 230-material .vrscene: ~12 s of a 31 s material import, and
        cutting the passes to one was as fast as skipping the layout altogether). The caller
        writes each final position exactly once. Sizes are computed once for the same reason -
        they do not change while only positions move.
    """
    sizes = {n.name: _getNodeSize(n, scale) for n in nodes}
    pos = {}

    depth = _assignDepths(nodes, links)
    cols  = _buildColumns(nodes, depth)
    _columnLayout(cols, pos, sizes)
    _sortByPort(cols, links, pos, sizes)
    _alignToNeighbors(cols, links, pos, sizes)
    _greedyOverlap(cols, pos, sizes)
    _positionReroutes(nodes, links, pos, sizes)

    return pos, sizes


def _connectedComponent(ntree, rootNode, links):
    """ rootNode plus every non-frame node connected to it (links treated as undirected). """
    byName = {n.name: n for n in ntree.nodes if n.type != 'FRAME'}
    adj = defaultdict(set)
    for link in links:
        if link.from_node and link.to_node:
            adj[link.from_node.name].add(link.to_node.name)
            adj[link.to_node.name].add(link.from_node.name)

    component = []
    seen = set()
    stack = [rootNode.name]
    while stack:
        name = stack.pop()
        if name in seen or name not in byName:
            continue
        seen.add(name)
        component.append(byName[name])
        stack.extend(adj[name] - seen)
    return component, seen


def rearrangeTree(ntree: bpy.types.NodeTree, rootNode: bpy.types.Node, depth=0, bounds=(0, 0, 0, 0), appendLeft=False):
    """ Lay out the connected component of 'rootNode' with a DPI-aware, crossing-reducing
        column layout.

        'rootNode' (normally the tree's output node) is kept anchored at its original
        location so the graph does not jump around in the editor. When 'appendLeft' is set
        and the tree contains other, unrelated nodes, the arranged block is instead placed
        just to the left of those nodes.

        'depth' and 'bounds' are accepted for backward compatibility and are otherwise unused.
    """
    scale    = _uiScale()
    allLinks = list(ntree.links)

    arranged, seen = _connectedComponent(ntree, rootNode, allLinks)
    if not arranged:
        return

    links = [l for l in allLinks
             if l.from_node and l.to_node
             and l.from_node.name in seen and l.to_node.name in seen]

    anchorX, anchorY = rootNode.location.x, rootNode.location.y
    pos, sizes = _layout(arranged, links, scale)

    # Translate the freshly-laid-out block into place.
    if appendLeft:
        # Anchoring to the off-canvas placeholder would put the block out there too.
        externals = [n for n in ntree.nodes if n.type != 'FRAME' and n.name not in seen and not isTexturePlaceholder(n)]
        if externals:
            extMinX   = min(n.location.x for n in externals)
            blockMaxX = max(pos[n.name][0] + sizes[n.name][0] for n in arranged)
            dx = extMinX - NODE_LEVEL_WIDTH - blockMaxX
        else:
            dx = anchorX - pos[rootNode.name][0]
    else:
        dx = anchorX - pos[rootNode.name][0]
    dy = anchorY - pos[rootNode.name][1]

    for n in arranged:
        x, y = pos[n.name]
        n.location = (x + dx, y + dy)

    _updateFrames(ntree, scale)


def arrangeNodes(ntree: bpy.types.NodeTree, nodes):
    """ Arrange an explicit subset of nodes (e.g. the current selection), keeping the
        subset centred on its original position. Frames are ignored.

        Links that cross the selection boundary (one endpoint outside it) are kept:
        _assignDepths ignores them when building columns, but _sortByPort uses them to
        order the boundary nodes by the socket they plug into on the fixed outside node,
        so wires from the selection into a shared unselected node do not cross.
    """
    # The off-canvas placeholder would drag the centroid, and so the whole selection, 100k units out.
    nodes = [n for n in nodes if n.type != 'FRAME' and not isTexturePlaceholder(n)]
    if len(nodes) < 2:
        return

    scale   = _uiScale()
    nameSet = {n.name for n in nodes}
    links = [l for l in ntree.links
             if l.from_node and l.to_node
             and (l.from_node.name in nameSet or l.to_node.name in nameSet)]

    def centerX(ns):
        return sum(n.location.x + _getNodeSize(n, scale)[0] * 0.5 for n in ns) / len(ns)

    def centerY(ns):
        return sum(_nodeCenterY(n, scale) for n in ns) / len(ns)

    oldCx, oldCy = centerX(nodes), centerY(nodes)
    pos, sizes = _layout(nodes, links, scale)

    newCx = sum(pos[n.name][0] + sizes[n.name][0] * 0.5 for n in nodes) / len(nodes)
    newCy = sum(_centerY(n.name, pos, sizes) for n in nodes) / len(nodes)
    dx, dy = oldCx - newCx, oldCy - newCy
    for n in nodes:
        x, y = pos[n.name]
        n.location = (x + dx, y + dy)

    _updateFrames(ntree, scale)


def rearrangeTreeRecursive(ntree: bpy.types.NodeTree, rootNode: bpy.types.Node, bounds=(0, 0, 0, 0), appendLeft=False):
    """ Lay out 'ntree' from 'rootNode', then recurse into the inner tree of every
        V-Ray group node it contains. Each inner group tree is laid out from its
        NodeGroupOutput node (the group's sink).
    """
    from vray_blender.nodes.group.utils import VRAY_GROUP_NODE_TYPE

    rearrangeTree(ntree, rootNode, bounds=bounds, appendLeft=appendLeft)

    for node in ntree.nodes:
        if node.bl_idname == VRAY_GROUP_NODE_TYPE and node.node_tree:
            groupOutput = next((n for n in node.node_tree.nodes if n.bl_idname == 'NodeGroupOutput'), None)
            if groupOutput:
                rearrangeTreeRecursive(node.node_tree, groupOutput,
                                       bounds=calculateTreeBounds(node.node_tree))


# -------------------------------------------------------------
#  IMPORTED-TREE ARRANGE  (estimate now, refine with real sizes once drawn)
# -------------------------------------------------------------
def _anyNodeDrawn(nodes) -> bool:
    """ True once at least one node reports a real drawn size. node.dimensions is only
        filled after the node editor draws the tree, so this is how we tell whether the
        exact sizes are available yet. """
    return any(n.dimensions.y > 1.0 for n in nodes)


def _tagNodeEditorsRedraw():
    wm = bpy.context.window_manager
    if not wm:
        return
    for window in wm.windows:
        for area in window.screen.areas:
            if area.type == 'NODE_EDITOR':
                area.tag_redraw()


# Marker (ID custom properties) left on a freshly imported tree that could not be laid
# out with real sizes yet. The node-editor draw handler consumes it the first time the
# tree is drawn, then clears it - so a material the user has since rearranged is never
# touched again. The marker persists in the .blend, so an import that is only opened in a
# later session is still refined then.
_PENDING_ARRANGE_KEY         = "vray_pending_arrange"             # value: root node name
_PENDING_ARRANGE_APPEND_LEFT = "vray_pending_arrange_appendleft"  # value: 0/1
_PENDING_ARRANGE_RECURSE     = "vray_pending_arrange_recurse"     # value: 0/1

_pendingArrangeQueue = []      # trees the draw handler saw ready; drained by the timer
_pendingArrangePtrs  = set()   # tree pointers already queued (dedup across many redraws)
_arrangeDrawHandle   = None    # SpaceNodeEditor draw handler handle

# Same idea for framing: a tree converted or created while no node editor was open has nowhere to frame, so the marker defers it to
# the first V-Ray editor that shows the tree.
_PENDING_FRAME_KEY  = "vray_pending_frame"   # value: 1

_pendingFrameQueue = []
_pendingFramePtrs  = set()


def markPendingFrame(ntree):
    ntree[_PENDING_FRAME_KEY] = 1


def _processPendingFrame():
    """ Timer callback: frame each queued tree once and drop its marker. """
    from vray_blender.nodes.operators.misc import frameVRayNodes

    trees = _pendingFrameQueue[:]
    _pendingFrameQueue.clear()
    for tree in trees:
        try:
            _pendingFramePtrs.discard(tree.as_pointer())
            del tree[_PENDING_FRAME_KEY]
            frameVRayNodes(tree)
        except (ReferenceError, KeyError, TypeError):
            pass
    return None


def _markPendingArrange(ntree, rootNode, appendLeft, recurse):
    ntree[_PENDING_ARRANGE_KEY]         = rootNode.name
    ntree[_PENDING_ARRANGE_APPEND_LEFT] = 1 if appendLeft else 0
    ntree[_PENDING_ARRANGE_RECURSE]     = 1 if recurse else 0


def _clearPendingArrange(ntree):
    for key in (_PENDING_ARRANGE_KEY, _PENDING_ARRANGE_APPEND_LEFT, _PENDING_ARRANGE_RECURSE):
        try:
            del ntree[key]
        except (KeyError, TypeError):
            pass


def _onNodeEditorDraw():
    """ Runs on every node-editor redraw. When a freshly imported tree that still carries
        the pending-arrange marker is being drawn (so node.dimensions is now populated),
        queue it for a one-time re-arrange with exact sizes. Must stay cheap and never
        raise - a draw handler that errors spams the console and can break drawing.
    """
    try:
        space = bpy.context.space_data
        if space is None or space.type != 'NODE_EDITOR':
            return
        tree = getattr(space, 'edit_tree', None)
        if tree is None:
            return
        if tree.get(_PENDING_ARRANGE_KEY) is None:
            # Framing waits for the arrange to settle: _processPendingArrange tags a redraw, and this runs again with the marker gone
            # and the nodes at their final positions.
            if (tree.get(_PENDING_FRAME_KEY) is not None and space.tree_type == 'VRayNodeTreeEditor'
                    and tree.as_pointer() not in _pendingFramePtrs):
                _pendingFramePtrs.add(tree.as_pointer())
                _pendingFrameQueue.append(tree)
                if not bpy.app.timers.is_registered(_processPendingFrame):
                    bpy.app.timers.register(_processPendingFrame, first_interval=0.0)
            return
        ptr = tree.as_pointer()
        if ptr in _pendingArrangePtrs:
            return
        # POST_PIXEL runs after the nodes are drawn, so a shown tree has real sizes now.
        if not any(n.dimensions.y > 1.0 for n in tree.nodes if n.type != 'FRAME'):
            return
        _pendingArrangePtrs.add(ptr)
        _pendingArrangeQueue.append(tree)
        if not bpy.app.timers.is_registered(_processPendingArrange):
            bpy.app.timers.register(_processPendingArrange, first_interval=0.0)
    except Exception:
        pass


def _processPendingArrange():
    """ Timer callback (runs outside the draw, so it may move nodes). Re-arranges every
        queued tree once with exact node.dimensions and clears its marker. """
    trees = _pendingArrangeQueue[:]
    _pendingArrangeQueue.clear()
    for tree in trees:
        try:
            _pendingArrangePtrs.discard(tree.as_pointer())
            rootName = tree.get(_PENDING_ARRANGE_KEY)
            if rootName is None:
                continue
            rootNode = tree.nodes.get(rootName)
            if rootNode is not None:
                appendLeft = bool(tree.get(_PENDING_ARRANGE_APPEND_LEFT, 0))
                recurse    = bool(tree.get(_PENDING_ARRANGE_RECURSE, 0))
                if recurse:
                    rearrangeTreeRecursive(tree, rootNode, appendLeft=appendLeft)
                else:
                    rearrangeTree(tree, rootNode, appendLeft=appendLeft)
            _clearPendingArrange(tree)
        except ReferenceError:
            # The tree or root node was deleted before it was drawn.
            pass
        except Exception:
            pass
    _tagNodeEditorsRedraw()
    return None


def arrangeImportedTree(ntree: bpy.types.NodeTree, rootNode: bpy.types.Node, appendLeft=False, recurse=False):
    """ Arrange a freshly imported tree (Cosmos / vrscene).

        These trees have not been drawn when the import runs, so node.dimensions is
        unavailable and the layout uses the height estimate. We lay it out immediately
        with the estimate, then leave a marker so the node-editor draw handler re-arranges
        it a single time - with exact node.dimensions - the first time it is opened, so
        the final result is pixel accurate (TexFalloff, TexTriPlanar, etc. stop
        overlapping). The marker is cleared after that one pass, so a material the user
        has already rearranged is never disturbed.
    """
    if recurse:
        rearrangeTreeRecursive(ntree, rootNode, appendLeft=appendLeft)
    else:
        rearrangeTree(ntree, rootNode, appendLeft=appendLeft)

    nodes = [n for n in ntree.nodes if n.type != 'FRAME']
    if nodes and not _anyNodeDrawn(nodes):
        _markPendingArrange(ntree, rootNode, appendLeft, recurse)


def register():
    global _arrangeDrawHandle
    if _arrangeDrawHandle is None:
        _arrangeDrawHandle = bpy.types.SpaceNodeEditor.draw_handler_add(
            _onNodeEditorDraw, (), 'WINDOW', 'POST_PIXEL')


def unregister():
    global _arrangeDrawHandle
    if _arrangeDrawHandle is not None:
        bpy.types.SpaceNodeEditor.draw_handler_remove(_arrangeDrawHandle, 'WINDOW')
        _arrangeDrawHandle = None
    _pendingArrangeQueue.clear()
    _pendingArrangePtrs.clear()
    _pendingFrameQueue.clear()
    _pendingFramePtrs.clear()


def deselectNodes(ntree):
    for node in ntree.nodes:
        node.select = False


def selectOnlyNode(ntree, node):
    """ Deselect every node in the tree, then select and activate only 'node'.

        Keeps the node's animation channels visible in the Graph Editor / Dope Sheet under
        the default "Only Show Selected" filter, which hides the F-curves of unselected nodes
        (Blender anim_filter.cc: skip_fcurve_selected_data).
    """
    deselectNodes(ntree)
    node.select = True
    ntree.nodes.active = node


def addVRayNodeTreeSettings(ntree: bpy.types.ShaderNodeTree, treeType: str):
    """ Add a 'vray' settings attribute to a node tree  """
    ntree.vray.tree_type = treeType


def isVrayNode(node: bpy.types.Node):
    # All nodes have a 'vray' attribute, only V-Ray nodes have 'vray_type'
    return hasattr(node, 'vray_type')


def isVrayNodeTree(ntree: bpy.types.NodeTree, treeType: str):
    return hasattr(ntree, 'vray') and (ntree.vray.tree_type == treeType)


# Tree types stored in bpy.data.node_groups: the object-level trees (displacement, fur,
# decal) and the V-Ray node groups the user creates inside any tree. A group's contents are
# ordinary V-Ray nodes, so a whole-file scan has to reach them too.
_STANDALONE_TREE_TYPES = {'OBJECT', 'FUR', 'DECAL', 'GROUP'}


def iterVRayNodeTreesWithOwners():
    """ Yield (owner, ntree) for every V-Ray node tree in the file: the material, world and light
        trees plus the trees stored as node groups (displacement / fur / decal and user-created
        V-Ray groups). A node group owns itself. Embedded trees are all named 'Shader Nodetree',
        so UI that needs a readable name must use the owner's.
    """
    for collection in (bpy.data.materials, bpy.data.worlds, bpy.data.lights):
        for block in collection:
            if ntree := getattr(block, 'node_tree', None):
                yield block, ntree

    for ng in bpy.data.node_groups:
        if hasattr(ng, 'vray') and ng.vray.tree_type in _STANDALONE_TREE_TYPES:
            yield ng, ng


def iterVRayNodeTrees():
    """ Yield every V-Ray node tree in the file. See iterVRayNodeTreesWithOwners(). """
    for _, ntree in iterVRayNodeTreesWithOwners():
        yield ntree


_isVraySocketByType = {}   # socket class -> bool

def isVraySocket(sock: bpy.types.NodeSocket):
    """ Return True if the socket has an associated V-Ray plugin attribute.

        NOTE: If a plugin description has been changed between V4B versions,
        the newly added sockets might not have a 'vray_attr' field in the data
        loaded from an old scene.
    """
    sockType = type(sock)
    cached = _isVraySocketByType.get(sockType)
    if cached is None:
        cached = hasattr(sock, 'vray_attr')
        _isVraySocketByType[sockType] = cached
    return cached


def isVrayLight(light: bpy.types.Light):
    """ Return True if the light is one of the V-Ray types """
    return hasattr(light, 'vray') and (light.vray.light_type != 'BLENDER')


def isCompatibleNode(node: bpy.types.Node):
    """ Return True if node is of type that can be exported by V-Ray """
    return isVrayNode(node) or (node.bl_idname in CompatibleNonVrayNodes)


def isSameNode(node1: bpy.types.Node, node2: bpy.types.Node):
    nodeTree1 = node1.id_data.original
    nodeTree2 = node2.id_data.original

    if (nodeTree1 is None) or (nodeTree2 is None):
        return False

    return  nodeTree1.session_uid == nodeTree2.session_uid and \
            node1.name == node2.name


def getFilterFunction(pluginType: str, filterFnName: str):
    """ Get a filter function by its name.

    Args:
        pluginType (str): the type of plugin the function is defined for
        filterFnName (str): the function name. If prefixed by 'filters.', the function is
                            looked up in the nodes.filters module, otherwise - in the plugin
                            module

    Returns:
        function: the filter function object or None if not defined
    """
    from vray_blender.nodes import filters
    from vray_blender.plugins import getPluginModule

    COMMON_FILTER_PREFIX = 'filters.'

    if filterFnName.startswith(COMMON_FILTER_PREFIX):
        # The filter function is defined in the 'nodes.filters' package
        filterModule = filters
        filterFnName = filterFnName[len(COMMON_FILTER_PREFIX):]
    else:
        # The filter function is defined in the plugin module
        filterModule = getPluginModule(pluginType)

    return getattr(filterModule, filterFnName, None)


def getLinkInfo(pluginType: str, attrName: str):
    """ Get the information defined in the plugin description for links to the socket.

    Returns:
        lib.defs.LinkInfo: populated structure
    """

    from vray_blender.plugins import findPluginModule, getPluginAttr
    from vray_blender.lib.defs import LinkInfo

    linkInfo = LinkInfo()

    if not (pluginModule := findPluginModule(pluginType)):
        # The linked node does not have a corresponding V-Ray plugin, return empty info.
        return linkInfo

    if (attrDesc := getPluginAttr(pluginModule, attrName)) is None:
        # Attribute may be a structural socket, e.g. rollout
        return linkInfo

    if linkDesc := attrDesc.get('options', {}).get('link_info'):
        linkInfo.linkType = linkDesc.get('link_type')

        if filterFnName := linkDesc.get("filter_function"):
            linkInfo.fnFilter = getFilterFunction(pluginType, filterFnName)

    return linkInfo


def getSocketPanelName(pluginModule: dict, vrayAttrName: str):
    """ Returns the name of the panel that the socket for the vray attribute is placed on, or None """
    return next((name for name in pluginModule.SocketPanels if vrayAttrName in pluginModule.SocketPanels[name]), None)


def getSocketPanel(pluginModule: dict, panelName: str):
    """ Returns the 'panel' socket by its name, or None """
    return next((d for d in pluginModule.Node.get('input_sockets', []) if d['name'] == panelName), None)
