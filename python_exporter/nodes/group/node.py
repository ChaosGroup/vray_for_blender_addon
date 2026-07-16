# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy


def wouldCreateCycle(parentTree, candidateTree):
    """ Check if assigning candidateTree to a group node in parentTree would create a cycle. """
    visited = set()

    def _visit(tree):
        treeId = id(tree)
        if treeId in visited:
            return False
        visited.add(treeId)
        if tree == parentTree:
            return True
        for node in tree.nodes:
            if node.bl_idname == 'VRayNodeGroup' and node.node_tree:
                if _visit(node.node_tree):
                    return True
        return False

    return _visit(candidateTree)


class VRayNodeGroup(bpy.types.NodeCustomGroup):
    """ V-Ray Group Node. Wraps a VRayGroupTree to allow grouping V-Ray nodes
        into reusable sub-networks.
    """
    bl_idname = 'VRayNodeGroup'
    bl_label  = "V-Ray Group"
    bl_icon   = 'NODETREE'

    @classmethod
    def poll(cls, ntree):
        # Allows the node to be added to any real VRay tree (MATERIAL, WORLD, GROUP, etc.)
        # and filters the node_tree picker to those same trees.
        # Fake internal trees (.texRemapTree, gradient ramp) have tree_type == '' and are excluded.
        return hasattr(ntree, 'vray') and ntree.vray.tree_type != ''

    def update(self):
        # Validate that the assigned tree doesn't create a cycle.
        if self.node_tree and wouldCreateCycle(self.id_data, self.node_tree):
            self.node_tree = None
            return

        # NodeCustomGroup does not auto-sync sockets from the tree interface.
        # Manually sync V-Ray sockets based on what's connected to
        # GroupInput/GroupOutput inside the group tree.
        if self.node_tree:
            syncGroupNodeSockets(self)

    def draw_label(self):
        return self.node_tree.name if self.node_tree else self.bl_label

    def draw_buttons(self, context, layout):
        layout.template_ID(self, "node_tree", new="vray.node_group_new")


# Note: VRayNodeGroup relies on NodeCustomGroup's default copy() behaviour,
# which keeps the node_tree reference shared across duplicated instances.
# Users can make a single-user copy through the template_ID widget.


# Standard Blender socket type to V-Ray socket type fallback mapping.
# Used when we can't determine the V-Ray type from internal connections.
_BLENDER_TO_VRAY_SOCKET = {
    'NodeSocketFloat':   'VRaySocketFloat',
    'NodeSocketColor':   'VRaySocketColor',
    'NodeSocketVector':  'VRaySocketVector',
    'NodeSocketInt':     'VRaySocketInt',
    'NodeSocketBool':    'VRaySocketBool',
    'NodeSocketString':  'VRaySocketString',
    'NodeSocketShader':  'VRaySocketMtl',
}


def syncGroupNodeSockets(groupNode):
    """ Sync the group node's external sockets with the internal
        GroupInput/GroupOutput connections, using V-Ray socket types.
    """
    groupTree = groupNode.node_tree
    if not groupTree:
        return

    inputNode  = next((n for n in groupTree.nodes if n.bl_idname == 'NodeGroupInput'), None)
    outputNode = next((n for n in groupTree.nodes if n.bl_idname == 'NodeGroupOutput'), None)

    # Build desired input sockets from GroupInput's outgoing connections.
    # Each entry: (vray_type, name, src_sock_or_None)
    desiredInputs = []
    if inputNode:
        for outSock in list(inputNode.outputs)[:-1]:  # skip __extend__
            vrayType, srcSock = _determineVRaySocketInfo(outSock, isOutput=True)
            desiredInputs.append((vrayType, outSock.name, srcSock))

    # Build desired output sockets from GroupOutput's incoming connections
    desiredOutputs = []
    if outputNode:
        for inSock in list(outputNode.inputs)[:-1]:  # skip __extend__
            vrayType, srcSock = _determineVRaySocketInfo(inSock, isOutput=False)
            desiredOutputs.append((vrayType, inSock.name, srcSock))

    parentTree = groupNode.id_data
    _applySockets(groupNode.inputs, desiredInputs, parentTree, isInput=True)
    _applySockets(groupNode.outputs, desiredOutputs, parentTree, isInput=False)


def _determineVRaySocketInfo(interfaceSock, isOutput):
    """ Determine the V-Ray base socket type and a reference to the V-Ray
        socket we can read current values from. Traverses reroute nodes.
        Returns (base_type_str, src_sock_or_None). The returned socket
        (if any) lives inside the group tree and is stable across rebuilds
        of the group node's exterior sockets — so we can later call
        src_sock.copy(dst) to transfer values.
    """
    sock = _resolveToVRaySocket(interfaceSock, isOutput)
    if sock:
        baseType = sock.vray_socket_base_type or sock.bl_idname
        return baseType, sock

    blType = interfaceSock.bl_idname
    return _BLENDER_TO_VRAY_SOCKET.get(blType, 'VRaySocketFloat'), None


def _resolveToVRaySocket(interfaceSock, isOutput):
    """ Follow links (traversing reroutes) from a GroupInput output or
        GroupOutput input to find the connected V-Ray socket.
    """
    if isOutput:
        for link in interfaceSock.links:
            target = link.to_socket
            node = link.to_node
            while node.bl_idname == 'NodeReroute':
                if not node.outputs[0].is_linked:
                    break
                link = node.outputs[0].links[0]
                target = link.to_socket
                node = link.to_node
            if target.bl_idname.startswith('VRay'):
                return target
    else:
        for link in interfaceSock.links:
            target = link.from_socket
            node = link.from_node
            while node.bl_idname == 'NodeReroute':
                if not node.inputs[0].is_linked:
                    break
                link = node.inputs[0].links[0]
                target = link.from_socket
                node = link.from_node
            if target.bl_idname.startswith('VRay'):
                return target
    return None


def _applySockets(sockCollection, desired, parentTree, isInput):
    """ Make sockCollection match the desired list of (type, name, src_sock)
        tuples. Clears and rebuilds if there's any mismatch, preserving
        external links by matching socket names after the rebuild. Values
        are transferred via each socket's own copy() method.
    """
    if len(sockCollection) == len(desired):
        if all(s.bl_idname == t and s.name == n
               for s, (t, n, _) in zip(sockCollection, desired)):
            # Still transfer values: an earlier sync may have created the
            # socket before the internal link existed (srcSock was None).
            for sock, (_, _, srcSock) in zip(sockCollection, desired):
                if srcSock is not None:
                    if fnCopy := getattr(srcSock, 'copy', None):
                        fnCopy(sock)
            return

    # Snapshot external links before clear() so references don't go stale.
    # Each entry: (my_socket_name, other_node_name, other_socket_name)
    externalLinks = []
    for sock in sockCollection:
        for link in sock.links:
            if isInput:
                externalLinks.append((sock.name, link.from_node.name, link.from_socket.name))
            else:
                externalLinks.append((sock.name, link.to_node.name, link.to_socket.name))

    sockCollection.clear()
    for sockType, sockName, srcSock in desired:
        newSock = sockCollection.new(sockType, sockName)
        # srcSock lives in the group tree (on an internal node), unaffected
        # by clearing this collection, so its reference is still valid here.
        # newSock is created with srcSock's base type, so their `.copy()`
        # methods are compatible — no defensive catch needed.
        if srcSock is not None:
            if fnCopy := getattr(srcSock, 'copy', None):
                fnCopy(newSock)

    # Restore external links by name match. If a socket was renamed or removed,
    # its link is dropped — which is the best we can do without identity tracking.
    if parentTree is None:
        return
    for sockName, otherNodeName, otherSockName in externalLinks:
        mySock = next((s for s in sockCollection if s.name == sockName), None)
        otherNode = parentTree.nodes.get(otherNodeName)
        if not mySock or not otherNode:
            continue
        if isInput:
            otherSock = next((s for s in otherNode.outputs if s.name == otherSockName), None)
            if otherSock:
                parentTree.links.new(otherSock, mySock)
        else:
            otherSock = next((s for s in otherNode.inputs if s.name == otherSockName), None)
            if otherSock:
                parentTree.links.new(mySock, otherSock)


def getRegClasses():
    return (
        VRayNodeGroup,
    )


def register():
    for regClass in getRegClasses():
        bpy.utils.register_class(regClass)


def unregister():
    for regClass in getRegClasses():
        bpy.utils.unregister_class(regClass)
