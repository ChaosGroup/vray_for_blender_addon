# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

# The Light Mix and Denoiser render channels now have a socket of their own on both ends: a
# named output socket on the channel node and a permanent input socket on the render channels
# container, instead of the generic 'Channel' output and a numbered 'Channel N' slot. Re-type
# the sockets of already saved scenes and move the links over.

import bpy
from vray_blender.nodes.sockets import (
    SPECIAL_CHANNEL_SOCKETS,
    SPECIAL_CHANNEL_SOCKET_TYPES,
    addChannelOutput,
    addSpecialChannelSockets,
    getChannelOutput,
)
from vray_blender.utils.upgrade_scene import scopedForUpgrade


def _vrayTrees():
    """ The node trees that may hold render channels. They live in world node trees, but may
        also sit inside a V-Ray group.
    """
    trees = [w.node_tree for w in scopedForUpgrade(bpy.data.worlds) if getattr(w, 'node_tree', None)]
    trees.extend(scopedForUpgrade(bpy.data.node_groups))
    return trees


def _misplacedLinks(container: bpy.types.Node):
    """ (numbered socket name, channel node name) for every special channel still sitting on a
        numbered 'Channel N' socket. Plain data, because the socket surgery below invalidates
        the references to the remaining sockets.
    """
    return [(s.name, s.links[0].from_node.name)
            for s in container.inputs
            if s.bl_idname == 'VRaySocketRenderChannel'
            and s.is_linked
            and s.links[0].from_node.bl_idname in SPECIAL_CHANNEL_SOCKETS]


def _hasSpecialOutput(node: bpy.types.Node):
    return any(s.bl_idname == SPECIAL_CHANNEL_SOCKETS[node.bl_idname].outputType for s in node.outputs)


def _upgradeChannelOutput(node: bpy.types.Node):
    """ Give a special channel node its own output socket. A socket's type cannot be changed in
        place, so the generic 'Channel' one is replaced; the caller re-creates the link to the
        channels container, which is the only link these outputs are meant to carry.
    """
    for sock in [s for s in node.outputs if s.bl_idname == 'VRaySocketRenderChannelOutput']:
        node.outputs.remove(sock)

    addChannelOutput(node)


def _renumberChannelSockets(container: bpy.types.Node):
    channelCnt = 1
    for sock in container.inputs:
        if sock.bl_idname == 'VRaySocketRenderChannel':
            sock.name = f"Channel {channelCnt}"
            channelCnt += 1


def _upgradeTree(ntree: bpy.types.NodeTree):
    containers = [n for n in ntree.nodes if n.bl_idname == 'VRayNodeRenderChannels']
    misplaced = [(c.name, *link) for c in containers for link in _misplacedLinks(c)]

    for node in [n for n in ntree.nodes if n.bl_idname in SPECIAL_CHANNEL_SOCKETS]:
        if not _hasSpecialOutput(node):
            _upgradeChannelOutput(node)

    for container in containers:
        addSpecialChannelSockets(container)

    for containerName, oldSockName, channelNodeName in misplaced:
        container = ntree.nodes[containerName]
        channelNode = ntree.nodes[channelNodeName]
        sockType = SPECIAL_CHANNEL_SOCKETS[channelNode.bl_idname].inputType

        if oldSock := container.inputs.get(oldSockName):
            container.inputs.remove(oldSock)

        targetSock = next((s for s in container.inputs if s.bl_idname == sockType), None)
        sourceSock = getChannelOutput(channelNode)
        if targetSock and sourceSock and not targetSock.is_linked:
            ntree.links.new(sourceSock, targetSock)

    for container in containers:
        _renumberChannelSockets(container)


def _needsUpgrade(ntree: bpy.types.NodeTree):
    for node in ntree.nodes:
        if node.bl_idname in SPECIAL_CHANNEL_SOCKETS:
            if not _hasSpecialOutput(node):
                return True
        elif node.bl_idname == 'VRayNodeRenderChannels':
            if not SPECIAL_CHANNEL_SOCKET_TYPES.issubset({s.bl_idname for s in node.inputs}):
                return True
            if _misplacedLinks(node):
                return True

    return False


def run():
    for ntree in _vrayTrees():
        _upgradeTree(ntree)


def check():
    return any(_needsUpgrade(ntree) for ntree in _vrayTrees())
