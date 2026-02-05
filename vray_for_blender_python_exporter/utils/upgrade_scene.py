import bpy
from dataclasses import dataclass
from vray_blender.lib import blender_utils
from vray_blender.nodes.sockets import VRaySocketUse, VRaySocketMult

from vray_blender import debug

_DEBUG_OUTPUT = True         # Turn debug logs on and enable lowest verbosity level
_DEBUG_LEVEL_VERBOSE = False # Enable verbose log level


@dataclass
class UpgradeContext:
    """ Context data about the current upgrade. """
    nodeTree: bpy.types.NodeTree
    nodeTreeName: str
    nodeTreeType: str

    # The whole "nodes" section of the upgrade info dict
    nodesUpgradeInfo: dict

    # The node type being currently upgraded
    currentNodeType: str = ''

    # The name of the property being currently upgraded
    currentPropName: str = ''

    @property
    def nodeUpgradeInfo(self):
        assert self.currentNodeType != ''
        return self.nodesUpgradeInfo[self.currentNodeType]


@dataclass
class NodeLink:
    fromNodeName: str
    fromSockName: str
    fromSockAttr: str
    toNodeName: str
    toSockName: str
    toSockAttr: str

# Logging functions are intentionally left public so that they can be used by 
# custom upgrade code.
def logMsg(msg: str):
    """ Log message at low verbosity """
    if _DEBUG_OUTPUT:
        debug.printDebug(f"UPDATE: {msg}")


def logVerboseMsg(msg: str):
    """ Log message at full verbosity """
    if _DEBUG_OUTPUT and _DEBUG_LEVEL_VERBOSE:
        debug.printDebug(f"UPDATE:   {msg}")


def _getNodeLinksInfo(node: bpy.types.Node):
    """ Returns a list of all links of a node. """

    nodeLinks: list[NodeLink] = []

    for socketGroup in (node.inputs, node.outputs):
        for sock in [s for s in socketGroup if s.is_linked]:
            nodeLinks.extend([NodeLink( link.from_node.name, link.from_socket.name, link.from_socket.vray_attr,
                                        link.to_node.name, link.to_socket.name, link.to_socket.vray_attr )
                                for link in sock.links])
    return nodeLinks


def _upgradeNodeTree(ctx: UpgradeContext):
    """ Upgrade a single node tree """

    assert ctx is not None
    assert ctx.nodesUpgradeInfo is not None

    # Make a copy of the collection because the original one will be modified while
    # we are processing the list.
    nodesList = list(ctx.nodeTree.nodes)

    for node in nodesList:
        if node.bl_idname in ctx.nodesUpgradeInfo.keys():
            upgradeNode(ctx, node)


def upgradeNode(ctx: UpgradeContext, node: bpy.types.Node):
    """ Upgrade a single node by creating a new node of the same type, copying
        properties and links from the old node and then deleting the old node.
        Custom upgrade functions are invoked at specific points of the upgrade.
    """

    from vray_blender.nodes.nodes import vrayNodeCopy

    assert ctx is not None
    assert node is not None

    logVerboseMsg(f"Updating node '{node.name}' [{node.bl_idname}]")

    ctx.currentNodeType = node.bl_idname

    # Create a new node of the same type
    nodeTree = ctx.nodeTree
    newNode = nodeTree.nodes.new(node.bl_idname)
    newNode.location = node.location
    newNode.location.x = node.location.x - (newNode.width - node.width) # right-align node

    # Run custom script to process any data that is not part of the plugin properties
    if ctx.nodeUpgradeInfo and (fnPreCopyNode := ctx.nodeUpgradeInfo.get('node_pre_copy', None)):
        fnPreCopyNode(ctx, node, newNode)

    # Copy node properties and set up/tear down any changed links.
    _copyNode(ctx, node, newNode)

    # Store existing node data that will need to be restored on the new node
    # once the old node is deleted.
    nodeLinks = _getNodeLinksInfo(node)
    nodeName     = node.name
    nodeLabel    = node.label

    # Call the custom copy procedure AFTER the properties have been copied so that it could
    # override any values if necessary.
    vrayNodeCopy(newNode, node)

    # Transfer all animation f-curves from the old to the new node. We need to map old sockets
    # to new ones and replace the socket index in the f-curve data path.
    _transferAnimations(ctx.nodeTree, node, newNode)

    # F-curves data paths directly use the name of the node to store animations so they are first
    # change to point to the new node's name and then brough back to point back to the original node.
    # Without doing so they will just be deleted when we remove the old node.
    newTmpNodeName = newNode.name

    nodeTree.nodes.remove(node)
    newNode.name     = nodeName
    newNode.label    = nodeLabel

    # Recreate node links on the new node
    failedLinks = _createNodeLinks(ctx, nodeLinks)

    # Change back the f-curve data paths to use the original node's name.
    for fcurve in blender_utils.getFCurves(ctx.nodeTree):
        fcurve.data_path = fcurve.data_path.replace(newTmpNodeName, nodeName)

    # Run custom script to process any data that is not part of the plugin properties
    if ctx.nodeUpgradeInfo and (fnPostCopyNode := ctx.nodeUpgradeInfo.get('node_post_copy', None)):
        fnPostCopyNode(ctx, nodeLinks, newNode)


    if failedLinks:
        logMsg("Failed to create some links:")
        for l in failedLinks:
            logMsg(f"  {l}")


def _copyNode(ctx: UpgradeContext, srcNode: bpy.types.Node, targetNode: bpy.types.Node):
    """ Copy node properties (both property group and sockets) between nodes.
        Socket links are NOT copied by this function but new links may be added
        or old one removed by the custom processing functions if necessary.
    Args:
        srcNode (bpy.types.Node): source for the copy operation
        targetNode (bpy.typesNode): destination for the copy operation
        nodeType (str): the type name of the node (bl_idname)
    """
    from vray_blender.plugins import getPluginModule, getPluginAttr
    from vray_blender.lib.attribute_utils import isOutputAttribute

    assert ctx is not None
    assert srcNode is not None
    assert targetNode is not None

    # Skip custom V-Ray nodes that don’t correspond to actual V-Ray plugins
    if srcNode.vray_plugin == 'NONE':
        return

    sourceProps = getattr(srcNode, srcNode.vray_plugin)
    targetProps = getattr(targetNode, targetNode.vray_plugin)

    if not hasattr(targetProps, "__annotations__"):
        debug.printDebug(f"\tNo anotations for Node {ctx.nodeTreeName::}{srcNode}")
        return

    sourcePropertyNames = list(sourceProps.__annotations__.keys())
    pluginModule = getPluginModule(srcNode.vray_plugin)

    # Copy meta sockets (the ones not directly backed by vray properties)
    for srcSocket in [s for s in srcNode.inputs if hasattr(s, 'vray_attr')]:
        if srcSocket.vray_attr not in sourcePropertyNames or isinstance(srcSocket, (VRaySocketUse, VRaySocketMult)):
            if fnCopy := getattr(srcSocket, 'copy', None):
                if targetSocket := next((s for s in targetNode.inputs if (not s.is_linked) and (s.name.lower() == srcSocket.name.lower())), None):
                    logVerboseMsg(f"Copy socket: {srcNode.name} => {targetNode.name} {srcSocket.vray_attr}")
                    fnCopy(targetSocket)

    # Iterate over old node's properties. Due to backward compatibility requirements
    # for V-Ray plugins, the old node's properties must be a strict subset of
    # the old node's properties. Any newly added properties can be handled in the
    # node's post-process callback.
    for propName in sourcePropertyNames:

        try:
            if ctx.nodeUpgradeInfo and (fnUpgrade := ctx.nodeUpgradeInfo.get('attributes', {}).get(propName, None)):
                # A custom upgrade function is defined for this attribute, call it instead of the generic upgrade.
                ctx.currentPropName = propName
                fnUpgrade(ctx, srcNode, targetNode)
            elif not(isOutputAttribute(pluginModule, propName)): # Outputs don't have a value that can be copied
                if (attrDesc := getPluginAttr(pluginModule, propName)) and (attrDesc['type'] == 'TEMPLATE'):
                    logVerboseMsg(f"Copy template: {srcNode.name} => {targetNode.name} {propName}")

                    srcProp    = getattr(sourceProps, propName)
                    targetProp = getattr(targetProps, propName, None)

                    assert targetProp is not None

                    # Call the optional 'copy' method of the template
                    if hasattr(srcProp, 'copy'):
                        srcProp.copy(targetProp)
                else:
                    logVerboseMsg(f"Copy attribute: {srcNode.name} => {targetNode.name} {propName}")
                    setattr(targetProps, propName, getattr(sourceProps, propName))
        except Exception as ex:
            debug.printError(f"Failed to copy property {ctx.nodeTreeType} :: {ctx.nodeTreeName} :: {srcNode.name} :: {propName}: {ex}")
            pass


def _transferAnimations(obj, oldNode, newNode):
    fcurves = blender_utils.getFCurves(obj)
    if not fcurves:
        return
    for i, oldSock in enumerate(oldNode.inputs):
        for j, newSock in enumerate(newNode.inputs):
            if oldSock.vray_attr and oldSock.vray_attr == newSock.vray_attr:
                for fcurve in fcurves:
                    if oldNode.name in fcurve.data_path and f'inputs[{i}]' in fcurve.data_path:
                        fcurve.data_path = fcurve.data_path.replace(
                            f'inputs[{i}]',
                            f'inputs[{j}]'
                        ).replace(oldNode.name, newNode.name)
                break


def _createNodeLinks(ctx: UpgradeContext, nodeLinks: list[bpy.types.NodeLink]):
    assert ctx.nodeTree is not None
    assert nodeLinks is not None

    failedLinks = []

    for link in nodeLinks:
        try:
            fromNode = ctx.nodeTree.nodes[link.fromNodeName]
            toNode = ctx.nodeTree.nodes[link.toNodeName]
            if toNode and fromNode:

                # Search first by vray attribute name and then by lowercase socket name (meta sockets don't have an underlying  vray attribute)
                toSocket = next((s for s in toNode.inputs if s.vray_attr and (s.vray_attr == link.toSockAttr) or (s.name.lower() == link.toSockName.lower())), None)

                if len(fromNode.outputs) > 1:
                    fromSocket = next((s for s in fromNode.outputs if s.vray_attr and (s.vray_attr == link.toSockAttr) or (s.name.lower() == link.fromSockName.lower())), None)
                else:
                    # For nodes with just a default output, do not try to match the socket by name
                    fromSocket = fromNode.outputs[0]

                if toSocket is None:
                    failedLinks.append(f"'{toNode.name}' doesn't have input socket '{link.toSockName}' connected to '{fromNode.name}'")
                    continue
                if fromSocket is None:
                    failedLinks.append(f"'{fromNode.name}' doesn't have output socket '{link.fromSockName}' connected to '{toNode.name}'")
                    continue
                ctx.nodeTree.links.new(fromSocket, toSocket)
        except Exception as e:
            failedLinks.append(f"'{fromNode.name}' couldn't be connected to '{toNode.name}'\nException: {e}")

    return failedLinks


def _hasUpgradeableNodes(obj, upgradeInfo: dict) -> bool:
    if isinstance(obj, bpy.types.NodeTree):
        ntree = obj
    else:
        ntree = getattr(obj, 'node_tree', None)
    if not ntree or not hasattr(ntree, 'vray'):
        return False
    return any([n for n in ntree.nodes if n.bl_idname in upgradeInfo['nodes'].keys()])


def upgradeScene(upgradeInfo: dict):
    """ Upgrade the scene according to the upgrade configuration data provided.

        The upgrade consists of walking all node trees in the scene, locating the nodes
        of types included in the upgradeInfo list, recreating them while optionally
        running custom upgrade actions for each node/property, and finally recreating
        all links to other nodes in the tree.

        NOTE: There may be upgrade scenarios currently not covered by this algorithm.
        We will evolve the procedure as the need arises.

        Structure of upgradeInfo (pseudocode):

        {
            "nodes": {
                "Node1.bl_idname": {
                    "attributes": {
                        "attr1_name": attr1UpgradeFunction(upgradeContext, oldNode, newNode),
                        "attr2_name": attr2UpgradeFunction(upgradeContext, oldNode, newNode)
                    },
                    "node_pre_copy": nodePreCopyFunction(upgradeContext, oldNode, newNode)
                },
                "Node2.bl_idname": {
                    ...
                }
            }
        }
    """


    logVerboseMsg("Updating scene materials ...")
    for material in bpy.data.materials:
        if material.use_nodes and _hasUpgradeableNodes(material, upgradeInfo):
            logVerboseMsg(f"Updating material '{material.name}'")
            _upgradeNodeTree(UpgradeContext(nodeTree     = material.node_tree,
                                            nodeTreeName = material.name,
                                            nodeTreeType = 'Material',
                                            nodesUpgradeInfo  = upgradeInfo['nodes']))


    logVerboseMsg("Updating scene lights ...")
    for light in bpy.data.lights:
        if _hasUpgradeableNodes(light, upgradeInfo):
            logVerboseMsg(f"Updating light '{light.name}'")
            _upgradeNodeTree(UpgradeContext(nodeTree     = light.node_tree,
                                            nodeTreeName = light.name,
                                            nodeTreeType = 'Light',
                                            nodesUpgradeInfo  = upgradeInfo['nodes']))


    logVerboseMsg("Updating scene worlds ...")
    for world in bpy.data.worlds:
        if world.use_nodes and _hasUpgradeableNodes(world, upgradeInfo):
            logVerboseMsg(f"Updating world '{world.name}'")
            _upgradeNodeTree(UpgradeContext(nodeTree     = world.node_tree,
                                            nodeTreeName = world.name,
                                            nodeTreeType = 'World',
                                            nodesUpgradeInfo  = upgradeInfo['nodes']))

    logVerboseMsg("Updating scene object node trees ...")
    for group in bpy.data.node_groups:
        if hasattr(group, 'vray') and (group.vray.tree_type == 'OBJECT') and _hasUpgradeableNodes(group, upgradeInfo):
            logVerboseMsg(f"Updating object node tree '{group.name}'")
            _upgradeNodeTree(UpgradeContext(nodeTree     = group,
                                            nodeTreeName = group.name,
                                            nodeTreeType = 'Node tree',
                                            nodesUpgradeInfo  = upgradeInfo['nodes']))

    logMsg("Update complete")


def sceneNeedsUpgrade(upgradeInfo: dict):
    """ Return True if the scene contains data that need to be upgraded. """
    for material in bpy.data.materials:
        if material.use_nodes and _hasUpgradeableNodes(material, upgradeInfo):
            return True

    for light in bpy.data.lights:
        if _hasUpgradeableNodes(light, upgradeInfo):
            return True

    for world in bpy.data.worlds:
        if world.use_nodes and _hasUpgradeableNodes(world, upgradeInfo):
           return True

    for group in bpy.data.node_groups:
        if hasattr(group, 'vray') and _hasUpgradeableNodes(group, upgradeInfo):
            return True
    return False