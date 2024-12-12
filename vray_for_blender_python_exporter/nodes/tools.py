import bpy

from vray_blender.nodes.mixin import VRayNodeBase


NODE_LEVEL_WIDTH = VRayNodeBase.bl_width_default + 50


def _getNodeHeight(n):
    socketHeigth = 15
    return n.height + socketHeigth * (len(n.inputs) + len(n.outputs))


def _getConnectedNode(toSocket):
    for l in toSocket.links:
        if l.from_node:
            return l.from_node
    return None

def collectLeafs(tree, ntree, n, depth):
    for inSock in n.inputs:
        if inSock.is_linked:
            inNode = _getConnectedNode(inSock)
            if not depth in tree:
                tree[depth] = []
            tree[depth].append(inNode)
            tree = collectLeafs(tree, ntree, inNode, depth+1)
    return tree


def rearrangeTree(ntree, n, depth=0):
    tree = {
        depth : [n],
    }

    tree = collectLeafs(tree, ntree, n, depth+1)

    # pprint(tree)

    for level in sorted(tree):
        if level == 0:
            continue

        levelNodes = tree[level]
        levelHeigth = 0

        # Calculate full level height
        for node in levelNodes:
            levelHeigth += _getNodeHeight(node)

        levelTop        = levelHeigth
        levelHeightHalf = levelHeigth / 2.0

        for node in levelNodes:
            node.location.x = n.location.x - (level * NODE_LEVEL_WIDTH)
            node.location.y = levelTop - levelHeightHalf

            levelTop -= _getNodeHeight(node)


def deselectNodes(ntree):
    for node in ntree.nodes:
        node.select = False


def addVRayNodeTreeSettings(ntree: bpy.types.NodeTree, treeType: str):
    """ Add a 'vray' settings attribute to a node tree  """
    ntree.vray.tree_type = treeType


def isVrayNodeTree(ntree: bpy.types.NodeTree, treeType: str):
    return hasattr(ntree, 'vray') and (ntree.vray.tree_type == treeType)


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
    
    attrDesc = getPluginAttr(pluginModule, attrName)
    
    if linkDesc := attrDesc.get('options', {}).get('link_info'):
        linkInfo.linkType = linkDesc.get('link_type')

        if filterFnName := linkDesc.get("filter_function"):
            linkInfo.fnFilter = getFilterFunction(pluginType, filterFnName)

    return linkInfo