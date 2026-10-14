# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy

from vray_blender.exporting.tools import getFirstSocketLink


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
        return hasattr(ntree, 'vray') and (ntree.vray.tree_type != '' or (ntree.is_runtime_data and not ntree.is_embedded_data))

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
    # Each entry: (vray_type, interface_id, name, src_sock_or_None)
    desiredInputs = []
    if inputNode:
        for outSock in list(inputNode.outputs)[:-1]:  # skip __extend__
            vrayType, srcSock = _determineVRaySocketInfo(outSock, isOutput=True)
            desiredInputs.append((vrayType, outSock.identifier, outSock.name, srcSock))

    # Build desired output sockets from GroupOutput's incoming connections
    desiredOutputs = []
    if outputNode:
        for inSock in list(outputNode.inputs)[:-1]:  # skip __extend__
            vrayType, srcSock = _determineVRaySocketInfo(inSock, isOutput=False)
            desiredOutputs.append((vrayType, inSock.identifier, inSock.name, srcSock))

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
        if sock.node.bl_idname == 'NodeUndefined':
            # Registering or unregistering any node class triggers a tree update, so update()
            # can run before the inner nodes' classes exist. A value getter on such a node
            # reads a V-Ray property group that is not there yet and crashes Blender. Socket
            # classes register earlier, so the type is still good.
            return baseType, None
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
                if not (link := getFirstSocketLink(node.outputs[0])):
                    break
                target = link.to_socket
                node = link.to_node
            if target.bl_idname.startswith('VRay'):
                return target
    else:
        for link in interfaceSock.links:
            target = link.from_socket
            node = link.from_node
            while node.bl_idname == 'NodeReroute':
                if not (link := getFirstSocketLink(node.inputs[0])):
                    break
                target = link.from_socket
                node = link.from_node
            if target.bl_idname.startswith('VRay'):
                return target
    return None


def _isSameSocket(sock, interfaceId, name):
    """ Sockets made before group_interface_id existed match by name. """
    return sock.group_interface_id == interfaceId if sock.group_interface_id else sock.name == name


def _applySockets(sockCollection, desired, parentTree, isInput):
    """ Make sockCollection match the desired list of (type, interface_id, name, src_sock)
        tuples. Rebuilds if there's any mismatch, preserving external links
        by matching the interface socket after the rebuild. Values are transferred
        via each socket's own copy() method. Existing sockets keep their
        value; only new ones take the inner node's.
    """
    if len(sockCollection) == len(desired):
        if all(s.bl_idname == t and s.group_interface_id == i and s.name == n
               for s, (t, i, n, _) in zip(sockCollection, desired)):
            # Fill the sockets created before their internal link existed.
            for sock, (_, _, _, srcSock) in zip(sockCollection, desired):
                if srcSock is not None and sock.needs_inner_value:
                    if fnCopy := getattr(srcSock, 'copy', None):
                        fnCopy(sock)
                    sock.needs_inner_value = False
            return

    # Snapshot external links before removing the old sockets.
    # Each entry: (interface_id, other_node_name, other_socket_name)
    externalLinks = []
    for sock in sockCollection:
        interfaceId = next((i for _, i, n, _ in desired if _isSameSocket(sock, i, n)), None)
        if interfaceId is None:
            continue
        for link in sock.links:
            if isInput:
                externalLinks.append((interfaceId, link.from_node.name, link.from_socket.name))
            else:
                externalLinks.append((interfaceId, link.to_node.name, link.to_socket.name))

    # Remove the old sockets only after copying their values.
    oldSockets = list(sockCollection)
    for sockType, interfaceId, sockName, srcSock in desired:
        newSock = sockCollection.new(sockType, sockName)
        newSock.group_interface_id = interfaceId
        oldSock = next((s for s in oldSockets if s.bl_idname == sockType
                        and _isSameSocket(s, interfaceId, sockName) and not s.needs_inner_value), None)
        # srcSock lives in the group tree, so it is still valid here. newSock has
        # srcSock's base type, so their copy() methods are compatible.
        if valueSock := oldSock or srcSock:
            if fnCopy := getattr(valueSock, 'copy', None):
                fnCopy(newSock)
        else:
            newSock.needs_inner_value = True
    for sock in oldSockets:
        sockCollection.remove(sock)

    # Restore external links by interface socket. The links of removed sockets are dropped.
    if parentTree is None:
        return
    for interfaceId, otherNodeName, otherSockName in externalLinks:
        mySock = next((s for s in sockCollection if s.group_interface_id == interfaceId), None)
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
