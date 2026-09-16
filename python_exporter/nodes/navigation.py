# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

""" Resolution of "which node the V-Ray property pages are editing".

    Shared by the Material Properties tab, the World Properties tab and the Scene Lister's detail
    pane. The node tree's own selection is the single source of truth, so the property pages and
    the Shader Editor stay in sync, and the lister - which lives in a Preferences window with no
    node editor - works off exactly the same state.

    Nothing here reads bpy.context except getEditedTree(), which every caller invokes once per
    panel rather than once per row.
"""

from dataclasses import dataclass

import bpy

from vray_blender.exporting.tools import getFarNodeLink
from vray_blender.nodes.group.utils import VRAY_EDITOR_TREE_TYPES
from vray_blender.nodes.tools import selectOnlyNode
from vray_blender.nodes.utils import getLightOutputNode, getOutputNode, treeHasNodes


# The output node socket that carries a tree's "main" branch, i.e. the one whose source node the
# property pages fall back to when nothing is selected.
_ROOT_SOCKET_NAMES = {
    'MATERIAL': "Material",
    'WORLD':    "Environment",
}

# Node vray_types that count as the root of a tree when the output link is missing. Only consulted
# under getTreeRootNode(fallbackScan=True).
_ROOT_FALLBACK_TYPES = {
    'MATERIAL': ('BRDF', 'MATERIAL'),
    'WORLD':    ('WORLD',),
}

# Guards against a pathological or cyclic graph stalling a redraw. A shading tree deeper or wider
# than this is not navigable in a property page anyway.
MAX_TREE_DEPTH = 12
MAX_TREE_ROWS = 200


def getEditedTree(context: bpy.types.Context, dataBlock) -> bpy.types.NodeTree | None:
    """ The tree a V-Ray node editor is currently showing for `dataBlock` - so the property page
        follows the user into a node group - else the datablock's own root tree.

        Hosts with no node editor of their own (the lister) should pass the tree directly to the
        resolvers below instead of calling this: here the screen scan is a guaranteed miss.
    """
    if dataBlock is None:
        return None

    for area in context.screen.areas:
        if area.type != 'NODE_EDITOR':
            continue
        space = area.spaces.active
        if (space.tree_type in VRAY_EDITOR_TREE_TYPES
                and space.id == dataBlock
                and space.edit_tree is not None):
            return space.edit_tree

    return getattr(dataBlock, 'node_tree', None)


def treeTypeOf(ntree: bpy.types.NodeTree) -> str:
    """ The tree's own declared type ('MATERIAL', 'WORLD', 'LIGHT', ...), or '' when it has none.
        Lets a caller that only holds a tree resolve nodes without being told the type. """
    return getattr(getattr(ntree, 'vray', None), 'tree_type', '') if ntree else ''


def getTreeRootNode(ntree: bpy.types.NodeTree, treeType: str, fallbackScan=False) -> bpy.types.Node | None:
    """ The deterministic "top" node of a tree, and the panel target when nothing is selected.

        MATERIAL resolves the Output node's "Material" socket, WORLD its "Environment" socket.

        With fallbackScan, a tree whose output link is missing - imported or half-built materials -
        falls back to the first BRDF/MATERIAL node in the tree. The Materials lister needs that to
        group such materials by their real shader instead of dumping them into "Other Materials".
        The property pages leave it off on purpose: a node that does not reach the output does not
        contribute to the render, so offering it as the thing you are editing would be a lie.
    """
    if not treeHasNodes(ntree):
        return None

    if treeType == 'LIGHT':
        # Lights have no single output node type - each light type is its own node - so the light
        # node IS the root, and there is no socket to follow.
        return getLightOutputNode(ntree)

    if outputNode := getOutputNode(ntree, treeType):
        if sockName := _ROOT_SOCKET_NAMES.get(treeType):
            if (sock := outputNode.inputs.get(sockName)) and (link := getFarNodeLink(sock)):
                return link.from_node

    if fallbackScan and (fallbackTypes := _ROOT_FALLBACK_TYPES.get(treeType)):
        return next((n for n in ntree.nodes if getattr(n, 'vray_type', 'NONE') in fallbackTypes), None)

    return None


def getPanelNode(ntree: bpy.types.NodeTree, treeType: str, skipOutput=True) -> bpy.types.Node | None:
    """ The node the V-Ray property pages edit, resolved purely from tree state:

          1. ntree.nodes.active, when it exists and is also selected
          2. the single selected node, when exactly one is selected
          3. getTreeRootNode()

        Rule 1 checks `select` as well because Blender leaves nodes.active pointing at a node the
        user has since ctrl-clicked away, and box-select does not update nodes.active at all. Rule
        2 requires exactly one selection rather than picking the last of many - "last in
        ntree.nodes" is creation order, i.e. arbitrary.

        With skipOutput, landing on the tree's output node yields the root node instead. The Output
        panel owns that node, so selecting it in the Shader Editor must not blank the Material panel.
    """
    if not treeHasNodes(ntree):
        return None

    node = None
    try:
        if (active := ntree.nodes.active) and active.select:
            node = active
    except ReferenceError:
        # nodes.active can outlive the node it points at within a single redraw
        node = None

    if node is None:
        selected = [n for n in ntree.nodes if n.select]
        if len(selected) == 1:
            node = selected[0]

    if skipOutput and node is not None and node == getOutputNode(ntree, treeType):
        node = None

    return node if node is not None else getTreeRootNode(ntree, treeType)


def setPanelNode(ntree: bpy.types.NodeTree, node: bpy.types.Node):
    """ Make `node` the panel target: deselect the tree, then select and activate it.

        The one navigation primitive - the breadcrumb, the up button, the shading tree rows and the
        button on a linked slot all funnel through here.
    """
    selectOnlyNode(ntree, node)


def getParentNode(ntree: bpy.types.NodeTree, node: bpy.types.Node) -> bpy.types.Node | None:
    """ The node consuming `node`'s output, i.e. one step towards the tree's output node.

        Walks this node's own output links rather than scanning every node's inputs, which is what
        keeps "go up" O(links of this node) instead of O(nodes * inputs * links).
    """
    for sock in node.outputs:
        for link in sock.links:
            if link.to_node is not node:
                return link.to_node
    return None


def getNodePath(ntree: bpy.types.NodeTree, node: bpy.types.Node, treeType: str) -> list[bpy.types.Node]:
    """ root -> ... -> node, for a breadcrumb.

        The tree's output node is excluded: with getPanelNode(skipOutput=True) the property page
        can never show it, so offering it as a breadcrumb segment would be a dead link.
    """
    if node is None:
        return []

    outputNode = getOutputNode(ntree, treeType)
    path = [node]
    seen = {node.name}

    current = node
    for _ in range(MAX_TREE_DEPTH):
        parent = getParentNode(ntree, current)
        if (parent is None) or (parent == outputNode) or (parent.name in seen):
            break
        path.append(parent)
        seen.add(parent.name)
        current = parent

    path.reverse()
    return path


############################################################
# Navigation history (back / forward)
############################################################

# (ownerType, ownerName) -> {'stack': [nodeName, ...], 'index': int}
# Momentary UI state: never saved, never part of undo. Entries are node names, so one that no
# longer resolves is simply skipped rather than dangling.
_navHistory: dict = {}

_HISTORY_LIMIT = 32


def _historyFor(ownerKey):
    return _navHistory.setdefault(ownerKey, {'stack': [], 'index': -1})


def recordNavigation(ownerKey, nodeName: str, fromNodeName: str = ""):
    """ Push a visited node, dropping any forward branch - standard back/forward semantics.

        `fromNodeName` seeds the node being navigated away from when the history is still empty.
        Without it the first jump leaves a one-entry stack, Back is greyed out immediately, and the
        node the user started on is unreachable.
    """
    history = _historyFor(ownerKey)
    stack, index = history['stack'], history['index']

    if not stack and fromNodeName and fromNodeName != nodeName:
        stack.append(fromNodeName)
        index = history['index'] = 0

    if 0 <= index < len(stack) and stack[index] == nodeName:
        return

    del stack[index + 1:]
    stack.append(nodeName)

    if len(stack) > _HISTORY_LIMIT:
        del stack[0]

    history['index'] = len(stack) - 1


def historyTarget(ownerKey, delta: int):
    """ (index, nodeName) `delta` steps from the cursor, or (None, None) at the end of the run.

        Read-only, so asking whether a button is enabled never mutates the history. The entry for
        the owner is created by recordNavigation, which the navigation draw calls with the node
        currently on screen - a displayed owner therefore has a one-entry history, and both arrows
        correctly read as disabled until the user actually goes somewhere.
    """
    history = _navHistory.get(ownerKey)
    if history is None:
        return None, None

    index = history['index'] + delta
    if 0 <= index < len(history['stack']):
        return index, history['stack'][index]
    return None, None


def setHistoryIndex(ownerKey, index: int):
    _historyFor(ownerKey)['index'] = index


def clearNavigationHistory():
    _navHistory.clear()


@dataclass
class TreeRow:
    """ One row of a flattened shading tree. `barMask` and `isLast` carry everything a glyph-style
        renderer needs, computed during the single traversal - the prefix never has to be derived by
        rescanning the row list.
    """
    node: bpy.types.Node
    depth: int
    socketName: str             # the input socket on the parent that this node drives
    isLast: bool                # no further sibling under the same parent
    barMask: tuple              # per ancestor column, whether that column still has siblings below
    isRef: bool                 # already expanded elsewhere in the list; drawn as a leaf


def collectShadingTree(rootNode: bpy.types.Node) -> list[TreeRow]:
    """ Depth-first flattening of everything feeding `rootNode`, in socket order.

        Descends through BRDF and MATERIAL nodes too, so the layers of a Blend material and the
        per-ID materials of a Switch material are reachable - an earlier prototype excluded them and
        made those sub-materials unnavigable.

        A node is expanded once. A second reference to it is emitted as a leaf row with isRef set,
        so a texture wired into two slots is visible under both without duplicating its subtree, and
        a cycle terminates. Single pass, O(links), capped by MAX_TREE_DEPTH / MAX_TREE_ROWS.
    """
    rows: list[TreeRow] = []
    if rootNode is None:
        return rows

    expanded = {rootNode.name}

    def walk(node, depth, barMask):
        if depth >= MAX_TREE_DEPTH or len(rows) >= MAX_TREE_ROWS:
            return

        children = [(sock, link.from_node) for sock in node.inputs if sock.is_linked
                    for link in sock.links if link.from_node is not node]

        for index, (sock, child) in enumerate(children):
            if len(rows) >= MAX_TREE_ROWS:
                return

            isLast = (index == len(children) - 1)
            isRef = child.name in expanded
            rows.append(TreeRow(node=child, depth=depth, socketName=sock.name,
                                isLast=isLast, barMask=barMask, isRef=isRef))
            if not isRef:
                expanded.add(child.name)
                walk(child, depth + 1, barMask + (not isLast,))

    walk(rootNode, 0, ())
    return rows
