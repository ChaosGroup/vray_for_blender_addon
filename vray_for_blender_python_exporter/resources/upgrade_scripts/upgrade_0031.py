# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy

from vray_blender.nodes.specials.gradient_ramp import VRayNodeColorRamp, VRaySocketColorRamp
from vray_blender.utils.upgrade_scene import UpgradeContext, upgradeNode, sceneNeedsUpgrade
from vray_blender.lib.mixin import VRayNodeBase
from vray_blender.nodes import color_ramp

def _getLink(node: bpy.types.Node):
    """Returns a link to the parent node."""
    for out in node.outputs:
        if out.bl_idname == VRaySocketColorRamp.bl_idname and out.links:
            return out.links[0] # At this point there shouldn't be more than 1 link.
    return None


def _getGradRampNode(nodeTree: bpy.types.NodeTree, texNode: bpy.types.Node):
    """Finds and returns the ColorRamp node that is
    connected to the newly created TexGradRamp node."""
    for node in nodeTree.nodes:
        if node.bl_idname == VRayNodeColorRamp.bl_idname:
            link = _getLink(node)
            if link.to_node == texNode:
                return node
    return None


def _transferTexGradRampInputs(ctx: UpgradeContext, oldNode: VRayNodeBase, newNode: VRayNodeBase):
    # Link any nodes plugged into "Texture {i}" of 'oldNode' to rampNode sockets "Point {i}"

    rampNode = _getGradRampNode(ctx.nodeTree, newNode)
    assert rampNode is not None, f"Failed to find ColorRamp node connected to {newNode.name}"

    socketIndex = 1
    while True:
        texSockName = f"Texture {socketIndex}"
        rampSockName = f"Point {socketIndex}"
        textureSocket = oldNode.inputs.get(texSockName)
        rampSocket = rampNode.inputs.get(rampSockName)
        if textureSocket is None or rampSocket is None:
            break
        if textureSocket.links:
            srcLink = textureSocket.links[0]
            ctx.nodeTree.links.new(srcLink.from_socket, rampSocket)
        socketIndex += 1


def _upgradeNodeTree(nodeTree, objName):
    # Make a copy of the collection because the original
    # one will be modified while processing the list.
    for node in list(nodeTree.nodes):
        if node.bl_idname == "VRayNodeTexGradRamp":
            # Save the gradient ramp texture to transfer to the GradRamp node.
            nodeTexture = node.texture
            # Save the name to access the newly created node afterwards.
            nodeName = node.name

            upgradeNode(
                UpgradeContext(
                    nodeTree = nodeTree,
                    nodeTreeName = objName,
                    nodeTreeType = 'Material',
                    nodesUpgradeInfo = { 'VRayNodeTexGradRamp': { "node_pre_copy": _transferTexGradRampInputs } }),
                node)

            newNode = nodeTree.nodes[nodeName]
            rampNode = _getGradRampNode(nodeTree, newNode)
            assert(rampNode is not None)

            oldTexture = rampNode.texture
            color_ramp.unregisterColorRamp(rampNode, 'texture', oldTexture)

            rampNode.texture = nodeTexture
            color_ramp.registerColorRamp(rampNode, 'texture', nodeTexture)

            oldTexture.use_fake_user = False
            if oldTexture.users == 0:
                bpy.data.textures.remove(oldTexture)


def _hasUpgradeableNodes(obj) -> bool:
    if isinstance(obj, bpy.types.NodeTree):
        ntree = obj
    else:
        ntree = getattr(obj, 'node_tree', None)
    if not ntree or not hasattr(ntree, 'vray'):
        return False
    return any([n for n in ntree.nodes if n.bl_idname == "VRayNodeTexGradRamp"])


def run():
    """Transfers the TexGradRamp properties from the old version of the node to
    the new one, and the "texture" property from the old version to the new
    ColorRamp node created with the new TexGradRamp node."""
    for material in bpy.data.materials:
        if material.use_nodes and _hasUpgradeableNodes(material):
            _upgradeNodeTree(material.node_tree, material.name)

    for light in bpy.data.lights:
        if _hasUpgradeableNodes(light):
            _upgradeNodeTree(light.node_tree, light.name)

    for world in bpy.data.worlds:
        if world.use_nodes and _hasUpgradeableNodes(world):
            _upgradeNodeTree(world.node_tree, world.name)

    for group in bpy.data.node_groups:
        if hasattr(group, 'vray') and _hasUpgradeableNodes(group):
            _upgradeNodeTree(group, group.name)


def check():
    return sceneNeedsUpgrade(UPGRADE_INFO)


UPGRADE_INFO = {
    'nodes': {
        'VRayNodeTexGradRamp': None
    }
}
