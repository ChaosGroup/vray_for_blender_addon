# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy

from vray_blender.lib.attribute_types import CompatibleNonVrayNodes
from vray_blender.lib.mixin import VRayNodeBase


NODE_LEVEL_WIDTH = VRayNodeBase.bl_width_default + 50

SOCKET_HEIGHT = 15

def _getNodeHeight(node: bpy.types.Node) -> float:
    height = node.height + 50

    if node.vray_plugin == 'TexBitmap':
        height += 120
    elif node.vray_plugin == 'TexGradRamp':
        height += 80

    for inp in node.inputs:
        if inp.enabled and not inp.hide:
            height += SOCKET_HEIGHT
    for out in node.outputs:
        if out.enabled and not out.hide:
            height += SOCKET_HEIGHT

    return height



def _collectLeafs(tree, ntree, n: bpy.types.Node, depth):
    def getConnectedNode(toSocket):
        for l in toSocket.links:
            if l.from_node:
                return l.from_node
        return None

    for inSock in n.inputs:
        if inSock.is_linked:
            inNode = getConnectedNode(inSock)
            if not depth in tree:
                tree[depth] = set()
            # Remove the node if it was in any of the previous levels since it can cause some weird
            # arrangements if a node is used in multiple levels.
            for layer in range(depth-1):
                tree[layer].discard(inNode)
            tree[depth].add(inNode)
            tree = _collectLeafs(tree, ntree, inNode, depth+1)
    return tree

def calculateTreeBounds(ntree):
    minX = float('inf')
    maxX = float('-inf')
    minY = float('inf')
    maxY = float('-inf')

    for node in ntree.nodes:
        x, y = node.location
        w, h = node.dimensions

        minX = min(minX, x)
        maxX = max(maxX, x + w)
        minY = min(minY, y - h)
        maxY = max(maxY, y)

    return (minX, minY, maxX, maxY)


def rearrangeTree(ntree, n, depth=0, bounds=(0, 0, 0, 0)):
    tree = {
        depth : { n },
    }

    tree = _collectLeafs(tree, ntree, n, depth+1)
    minX, minY, maxX, maxY = bounds
    for level in sorted(tree):
        if level == 0:
            node = next(iter(tree[0]))
            node.location.x = n.location.x + minX - NODE_LEVEL_WIDTH
            node.location.y = n.location.y + (maxY - minY) / 2.0
            continue

        levelNodes = tree[level]
        levelHeigth = 0

        # Calculate full level height
        for node in levelNodes:
            levelHeigth += _getNodeHeight(node)

        levelTop        = levelHeigth + (maxY - minY) / 2.0
        levelHeightHalf = levelHeigth / 2.0

        for node in levelNodes:
            node.location.x = n.location.x - (level * NODE_LEVEL_WIDTH)
            node.location.y = levelTop - levelHeightHalf

            levelTop -= _getNodeHeight(node)


def deselectNodes(ntree):
    for node in ntree.nodes:
        node.select = False


def addVRayNodeTreeSettings(ntree: bpy.types.ShaderNodeTree, treeType: str):
    """ Add a 'vray' settings attribute to a node tree  """
    ntree.vray.tree_type = treeType


def isVrayNode(node: bpy.types.Node):
    return hasattr(node, 'vray_type')


def isVrayNodeTree(ntree: bpy.types.NodeTree, treeType: str):
    return hasattr(ntree, 'vray') and (ntree.vray.tree_type == treeType)


def isVraySocket(sock: bpy.types.NodeSocket):
    """ Return True if the socket has an associated V-Ray plugin attribute.

        NOTE: If a plugin description has been changed between V4B versions, 
        the newly added sockets might not have a 'vray_attr' field in the data
        loaded from an old scene.
    """
    return hasattr(sock, 'vray_attr')


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
