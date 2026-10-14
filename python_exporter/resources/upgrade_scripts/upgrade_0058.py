# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

# The Texture/Mask inputs of the V-Ray Layered texture now accept colors above 1. A socket's type
# cannot be changed in place, so the old clamped sockets are replaced, keeping value, links and
# position (the position keeps the socket's animation data paths valid).

import bpy
from vray_blender.utils.upgrade_scene import scopedForUpgrade


def _vrayTrees():
    for collection in (bpy.data.materials, bpy.data.worlds, bpy.data.lights):
        for block in scopedForUpgrade(collection):
            if ntree := getattr(block, 'node_tree', None):
                yield ntree
    yield from scopedForUpgrade(bpy.data.node_groups)


def _clampedLayerSocketNames(node: bpy.types.Node):
    return [s.name for s in node.inputs
            if s.bl_idname == 'VRaySocketColor' and s.name.startswith(("Texture ", "Mask "))]


def _layeredNodes(ntree: bpy.types.NodeTree):
    return [n for n in ntree.nodes if n.bl_idname == 'VRayNodeTexLayeredMax']


def _replaceSocket(ntree: bpy.types.NodeTree, node: bpy.types.Node, sockName: str):
    from vray_blender.nodes.specials.texture import VRayNodeTexLayeredMax

    oldSock = node.inputs[sockName]
    index = node.inputs.find(sockName)
    value = oldSock.value[:]
    multiplier = oldSock.multiplier
    hide = oldSock.hide
    # Plain data, the socket surgery below invalidates the socket references of this node
    fromSockets = [(l.from_node.name, l.from_socket.identifier) for l in oldSock.links]

    node.inputs.remove(oldSock)

    newSock = VRayNodeTexLayeredMax.addLayerColorInput(node, sockName)
    newSock.value = value
    newSock.multiplier = multiplier
    newSock.hide = hide
    node.inputs.move(node.inputs.find(sockName), index)

    for fromNodeName, fromSockId in fromSockets:
        fromNode = ntree.nodes[fromNodeName]
        if fromSock := next((s for s in fromNode.outputs if s.identifier == fromSockId), None):
            ntree.links.new(fromSock, node.inputs[sockName])


def run():
    for ntree in _vrayTrees():
        for node in _layeredNodes(ntree):
            for sockName in _clampedLayerSocketNames(node):
                _replaceSocket(ntree, node, sockName)


def check():
    return any(_clampedLayerSocketNames(node)
               for ntree in _vrayTrees()
               for node in _layeredNodes(ntree))
