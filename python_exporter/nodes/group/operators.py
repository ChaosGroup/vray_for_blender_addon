# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy

from vray_blender.nodes.group.utils import (
    isGroupNodesEnabled,
    VRAY_GROUP_TREE_TYPE, VRAY_GROUP_NODE_TYPE, NON_GROUPABLE_NODE_TYPES,
    VRAY_EDITOR_TREE_TYPES, VRAY_OBJECT_TREE_TYPES,
)
from vray_blender.nodes.group.node_copy import copyNodesBetweenTrees
from vray_blender.nodes.group.node import syncGroupNodeSockets


def _isVRayNodeEditor(context):
    """ Return True if the current space is any V-Ray node editor. """
    space = context.space_data
    return (space and space.type == 'NODE_EDITOR'
            and space.tree_type in VRAY_EDITOR_TREE_TYPES
            and space.edit_tree is not None)


def _getGroupTreeType(context):
    """ Return the node tree bl_idname to use for a new group tree.
        For object-level trees the group tree must match the parent tree type
        (Blender's C-level constraint). Material/world contexts use ShaderNodeTree.
        NOTE: space.tree_type is always 'VRayNodeTreeEditor' regardless of whether
        an object tree is shown, so we must check edit_tree.bl_idname instead.
    """
    space = context.space_data
    editTree = space.edit_tree if (space and hasattr(space, 'edit_tree')) else None
    parentType = editTree.bl_idname if editTree else ''
    return parentType if parentType in VRAY_OBJECT_TREE_TYPES else VRAY_GROUP_TREE_TYPE


def _getEditTree(context):
    """ Return the currently edited node tree. """
    return context.space_data.edit_tree


def _isInsideGroup(context):
    """ Return True if the editor is currently inside a group tree. """
    return len(context.space_data.path) > 1



def _removeNodes(tree, nodeNames):
    """ Remove nodes from a tree by name. """
    for name in nodeNames:
        if node := tree.nodes.get(name):
            tree.nodes.remove(node)


# ---------------------------------------------------------------------------
# Socket/link helpers
# ---------------------------------------------------------------------------

_BLENDER_SOCKET_TYPE_MAP = {
    'VRaySocketFloat':          'NodeSocketFloat',
    'VRaySocketFloatColor':     'NodeSocketFloat',
    'VRaySocketInt':            'NodeSocketInt',
    'VRaySocketBool':           'NodeSocketBool',
    'VRaySocketColor':          'NodeSocketColor',
    'VRaySocketAColor':         'NodeSocketColor',
    'VRaySocketColorTexture':   'NodeSocketColor',
    'VRaySocketColorMult':      'NodeSocketColor',
    'VRaySocketColorUse':       'NodeSocketColor',
    'VRaySocketVector':         'NodeSocketVector',
    'VRaySocketVectorInt':      'NodeSocketVector',
    'VRaySocketVectorRotation': 'NodeSocketVector',
    'VRaySocketVectorScale':    'NodeSocketVector',
    'VRaySocketVectorOffset':   'NodeSocketVector',
    'VRaySocketString':         'NodeSocketString',
    # V-Ray socket types without a native Blender equivalent.
    # NodeSocketShader would be the closest match visually for many of these,
    # but the shader tree's validate_link rejects SHADER -> SOCK_CUSTOM links,
    # which makes the interior link inside a group flag as invalid (red error).
    # NodeSocketColor passes validation (any non-SHADER type does), so the
    # interior link is recognized as valid by Blender; the exterior socket on
    # the group node is still the correct V-Ray type via _syncGroupNodeSockets.
    'VRaySocketBRDF':                  'NodeSocketColor',
    'VRaySocketMtl':                   'NodeSocketColor',
    'VRaySocketPlugin':                'NodeSocketColor',
    'VRaySocketGeom':                  'NodeSocketColor',
    'VRaySocketObject':                'NodeSocketColor',
    'VRaySocketCoords':                'NodeSocketColor',
    'VRaySocketTransform':             'NodeSocketColor',
    'VRaySocketRenderChannelOutput':   'NodeSocketColor',
    'VRaySocketEffectOutput':          'NodeSocketColor',
    'VRaySocketObjectList':            'NodeSocketColor',
    'VRaySocketWeight':                'NodeSocketFloat',
    'VRaySocketMult':                  'NodeSocketFloat',
    'VRaySocketEnum':                  'NodeSocketInt',
    'VRaySocketUse':                   'NodeSocketBool',
}


def _getSocketByName(sockets, name):
    """ Find a socket by name in a socket collection. """
    for sock in sockets:
        if sock.name == name:
            return sock
    return None


def _getSocketIndex(sockets, socket):
    """ Get the index of a socket in a socket collection. """
    for idx, sock in enumerate(sockets):
        if sock == socket:
            return idx
    return None


def _snapshotLinks(tree, selectedNames):
    """ Snapshot all links in a tree relative to a selection boundary.
        Returns (incoming, outgoing) as lists of plain-data tuples.

        Each tuple: (from_node_name, from_socket_name, from_socket_bl_idname,
                     to_node_name, to_socket_name, to_socket_bl_idname)

        This captures everything as strings so that Blender link/socket
        references are never held past tree modifications.
    """
    incoming = []   # outside -> inside
    outgoing = []   # inside -> outside

    for link in tree.links:
        # Use base socket type for V-Ray sockets so _BLENDER_SOCKET_TYPE_MAP
        # lookups work (dynamic subclass names like VRaySocketColor_BRDFVRM_diffuse
        # wouldn't match).
        fromType = getattr(link.from_socket, 'vray_socket_base_type', '') or link.from_socket.bl_idname
        toType   = getattr(link.to_socket, 'vray_socket_base_type', '') or link.to_socket.bl_idname
        data = (link.from_node.name, link.from_socket.name, fromType,
                link.to_node.name,   link.to_socket.name,   toType)
        fromInside = data[0] in selectedNames
        toInside   = data[3] in selectedNames

        if not fromInside and toInside:
            incoming.append(data)
        elif fromInside and not toInside:
            outgoing.append(data)

    return incoming, outgoing


# ---------------------------------------------------------------------------
# Core group construction (space-independent)
# ---------------------------------------------------------------------------

def makeGroupFromNodes(parentTree, nodes, groupTreeType, groupName="VRayGroup", center=None):
    """ Move 'nodes' from 'parentTree' into a new V-Ray group and return the
        created VRayNodeGroup node (left in parentTree).

        Performs the same steps as VRAY_OT_node_group_make.execute but does not
        touch the node editor (space.path) or selection state, so it can be used
        headlessly (e.g. from the Cycles->V-Ray conversion). The caller is
        responsible for any editor/selection bookkeeping and for laying out the
        result. 'nodes' is used verbatim - the caller decides what is groupable.
    """
    if not nodes:
        return None

    nonFrameNodes = [n for n in nodes if n.bl_idname != 'NodeFrame'] or nodes
    if center is None:
        centerX = sum(n.location.x for n in nonFrameNodes) / len(nonFrameNodes)
        centerY = sum(n.location.y for n in nonFrameNodes) / len(nonFrameNodes)
    else:
        centerX, centerY = center

    selectedNames = {n.name for n in nodes}

    # Snapshot link data as plain strings BEFORE any tree modifications.
    incoming, outgoing = _snapshotLinks(parentTree, selectedNames)

    # Create the group tree and group node
    groupTree = bpy.data.node_groups.new(groupName, groupTreeType)
    groupTree.vray.tree_type = 'GROUP'

    groupNode = parentTree.nodes.new(VRAY_GROUP_NODE_TYPE)
    groupNode.node_tree = groupTree

    # Copy the selected nodes into the group tree. Direct copy preserves
    # absolute positions, so we subtract the original center to land them
    # around origin in the group tree.
    copyNodesBetweenTrees(parentTree, nodes, groupTree)
    for n in groupTree.nodes:
        n.location.x -= centerX
        n.location.y -= centerY

    # Add GroupInput and GroupOutput nodes
    inputNode = groupTree.nodes.new('NodeGroupInput')
    outputNode = groupTree.nodes.new('NodeGroupOutput')

    contentNodes = [n for n in groupTree.nodes
                    if n.bl_idname not in ('NodeGroupInput', 'NodeGroupOutput')]
    if contentNodes:
        inputX  = min(n.location.x for n in contentNodes) - 300
        outputX = max(n.location.x for n in contentNodes) + 300
    else:
        inputX, outputX = -250, 250
    inputNode.location  = (inputX, 0)
    outputNode.location = (outputX, 0)

    # Create group interface sockets for incoming links and wire them up
    inputSocketMap = {}  # (from_node, from_socket) -> index
    for fromNodeName, fromSockName, _, toNodeName, toSockName, toSockType in incoming:
        key = (fromNodeName, fromSockName)
        if key not in inputSocketMap:
            blenderType = _BLENDER_SOCKET_TYPE_MAP.get(toSockType, 'NodeSocketFloat')
            groupTree.interface.new_socket(
                name=toSockName, in_out='INPUT', socket_type=blenderType
            )
            idx = len(inputSocketMap)
            inputSocketMap[key] = idx

        idx = inputSocketMap[key]
        internalNode = groupTree.nodes.get(toNodeName)
        internalSocket = _getSocketByName(internalNode.inputs, toSockName) if internalNode else None
        if internalSocket and idx < len(inputNode.outputs) - 1:
            groupTree.links.new(inputNode.outputs[idx], internalSocket)

    # Create group interface sockets for outgoing links and wire them up
    outputSocketMap = {}  # (from_node, from_socket) -> index
    for fromNodeName, fromSockName, fromSockType, toNodeName, toSockName, _ in outgoing:
        key = (fromNodeName, fromSockName)
        if key not in outputSocketMap:
            blenderType = _BLENDER_SOCKET_TYPE_MAP.get(fromSockType, 'NodeSocketFloat')
            groupTree.interface.new_socket(
                name=fromSockName, in_out='OUTPUT', socket_type=blenderType
            )
            idx = len(outputSocketMap)
            outputSocketMap[key] = idx

        idx = outputSocketMap[key]
        internalNode = groupTree.nodes.get(fromNodeName)
        internalSocket = _getSocketByName(internalNode.outputs, fromSockName) if internalNode else None
        if internalSocket and idx < len(outputNode.inputs) - 1:
            groupTree.links.new(internalSocket, outputNode.inputs[idx])

    # Remove the original selected nodes from the parent tree.
    _removeNodes(parentTree, selectedNames)

    # Set up the group node
    groupNode.location = (centerX, centerY)
    groupNode.width = 230

    # Sync V-Ray sockets from GroupInput/GroupOutput connections
    syncGroupNodeSockets(groupNode)

    # Reconnect incoming links: external upstream node -> group node input
    for fromNodeName, fromSockName, _, _, _, _ in incoming:
        key = (fromNodeName, fromSockName)
        idx = inputSocketMap[key]
        fromNode = parentTree.nodes.get(fromNodeName)
        if fromNode and idx < len(groupNode.inputs):
            fromSock = _getSocketByName(fromNode.outputs, fromSockName)
            if fromSock:
                parentTree.links.new(fromSock, groupNode.inputs[idx])

    # Reconnect outgoing links: group node output -> external downstream node
    for fromNodeName, fromSockName, _, toNodeName, toSockName, _ in outgoing:
        key = (fromNodeName, fromSockName)
        idx = outputSocketMap[key]
        toNode = parentTree.nodes.get(toNodeName)
        if toNode and idx < len(groupNode.outputs):
            toSock = _getSocketByName(toNode.inputs, toSockName)
            if toSock:
                parentTree.links.new(groupNode.outputs[idx], toSock)

    return groupNode


# ---------------------------------------------------------------------------
# Create new empty group tree
# ---------------------------------------------------------------------------

class VRAY_OT_node_group_new(bpy.types.Operator):
    bl_idname  = 'vray.node_group_new'
    bl_label   = "New V-Ray Group"
    bl_description = "Create a new V-Ray group node tree"

    def execute(self, context):
        groupTree = bpy.data.node_groups.new("VRayGroup", _getGroupTreeType(context))
        groupTree.vray.tree_type = 'GROUP'

        inputNode  = groupTree.nodes.new('NodeGroupInput')
        outputNode = groupTree.nodes.new('NodeGroupOutput')
        inputNode.location  = (-250, 0)
        outputNode.location = (250, 0)

        # template_ID's "new" button expects the operator to assign the new
        # datablock to the bound property itself — it does not auto-wire.
        # Assign to the active group node if we can find one.
        node = context.active_node
        if node and node.bl_idname == VRAY_GROUP_NODE_TYPE:
            node.node_tree = groupTree

        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Edit group (enter/exit via Tab)
# ---------------------------------------------------------------------------

class VRAY_OT_node_group_edit(bpy.types.Operator):
    bl_idname  = 'vray.node_group_edit'
    bl_label   = "Edit V-Ray Group"
    bl_description = "Enter or exit V-Ray group node"

    @classmethod
    def poll(cls, context):
        if not isGroupNodesEnabled():
            return False
        return _isVRayNodeEditor(context)

    def execute(self, context):
        space = context.space_data
        activeNode = context.active_node

        # Enter group if active node is a group node (works at any depth)
        if activeNode and activeNode.bl_idname == VRAY_GROUP_NODE_TYPE and activeNode.node_tree:
            space.path.append(activeNode.node_tree, node=activeNode)
            return {'FINISHED'}

        # Exit group if inside one and active node is not a group
        if _isInsideGroup(context):
            # Sync group node sockets before exiting so any changes
            # made inside (e.g. dragging to __extend__) are reflected.
            groupTree = _getEditTree(context)
            parentTree = space.path[-2].node_tree if len(space.path) >= 2 else None
            if parentTree:
                for n in parentTree.nodes:
                    if n.bl_idname == VRAY_GROUP_NODE_TYPE and n.node_tree == groupTree:
                        syncGroupNodeSockets(n)
                        break

            space.path.pop()
            return {'FINISHED'}

        return {'PASS_THROUGH'}


# ---------------------------------------------------------------------------
# Make group from selected nodes (Ctrl+G)
# ---------------------------------------------------------------------------

class VRAY_OT_node_group_make(bpy.types.Operator):
    bl_idname  = 'vray.node_group_make'
    bl_label   = "Make V-Ray Group"
    bl_description = "Group selected nodes into a V-Ray group"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        if not isGroupNodesEnabled():
            return False
        if not _isVRayNodeEditor(context):
            return False
        editTree = _getEditTree(context)
        return any(n.select for n in editTree.nodes)

    def execute(self, context):
        space = context.space_data
        editTree = _getEditTree(context)

        selectedNodes = [n for n in editTree.nodes
                         if n.select and n.bl_idname not in NON_GROUPABLE_NODE_TYPES]

        if not selectedNodes:
            self.report({'WARNING'}, "No groupable nodes selected")
            return {'CANCELLED'}

        if not (hasattr(editTree, 'vray') and editTree.vray.tree_type):
            self.report({'WARNING'}, "V-Ray grouping is not supported in this node tree")
            return {'CANCELLED'}

        groupNode = makeGroupFromNodes(editTree, selectedNodes, _getGroupTreeType(context))
        if groupNode is None:
            return {'CANCELLED'}

        # Editor/selection bookkeeping (handled here, not in the headless helper).
        for n in editTree.nodes:
            n.select = False
        groupNode.select = True
        editTree.nodes.active = groupNode

        # Enter the newly created group for editing
        space.path.append(groupNode.node_tree, node=groupNode)

        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Ungroup (Ctrl+Alt+G)
# ---------------------------------------------------------------------------

class VRAY_OT_node_group_ungroup(bpy.types.Operator):
    bl_idname  = 'vray.node_group_ungroup'
    bl_label   = "Ungroup V-Ray Group"
    bl_description = "Dissolve selected V-Ray group nodes"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        if not isGroupNodesEnabled():
            return False
        if not _isVRayNodeEditor(context):
            return False
        editTree = _getEditTree(context)
        return any(n.select and n.bl_idname == VRAY_GROUP_NODE_TYPE and n.node_tree for n in editTree.nodes)

    def execute(self, context):
        space = context.space_data
        editTree = _getEditTree(context)

        groupNodes = [n for n in editTree.nodes
                      if n.select and n.bl_idname == VRAY_GROUP_NODE_TYPE and n.node_tree]

        for groupNode in groupNodes:
            _ungroupNode(space, editTree, groupNode)

        return {'FINISHED'}


def _ungroupNode(space, parentTree, groupNode):
    """ Dissolve a group node, moving its contents back into the parent tree. """
    groupTree = groupNode.node_tree
    offsetX = groupNode.location.x
    offsetY = groupNode.location.y

    # Select content nodes to be moved (everything except GroupInput/Output).
    contentNodes = [n for n in groupTree.nodes
                    if n.bl_idname not in ('NodeGroupInput', 'NodeGroupOutput')]

    # Snapshot GroupInput -> internal connections as (outIdx, to_node_name, to_socket_name)
    inputConnections = []
    for link in groupTree.links:
        if link.from_node.bl_idname == 'NodeGroupInput':
            outIdx = _getSocketIndex(link.from_node.outputs, link.from_socket)
            if outIdx is not None:
                inputConnections.append((outIdx, link.to_node.name, link.to_socket.name))

    # Snapshot internal -> GroupOutput connections as (from_node_name, from_socket_name, inIdx)
    outputConnections = []
    for link in groupTree.links:
        if link.to_node.bl_idname == 'NodeGroupOutput':
            inIdx = _getSocketIndex(link.to_node.inputs, link.to_socket)
            if inIdx is not None:
                outputConnections.append((link.from_node.name, link.from_socket.name, inIdx))

    # Snapshot parent-side links to/from the group node
    parentIncoming = []   # (from_node_name, from_socket_name, group_input_idx)
    for idx, sock in enumerate(groupNode.inputs):
        for link in sock.links:
            parentIncoming.append((link.from_node.name, link.from_socket.name, idx))

    parentOutgoing = []   # (group_output_idx, to_node_name, to_socket_name)
    for idx, sock in enumerate(groupNode.outputs):
        for link in sock.links:
            parentOutgoing.append((idx, link.to_node.name, link.to_socket.name))

    # Direct copy the content nodes into the parent tree. Returns a
    # src_name -> dst_node map so we can rewire without relying on name
    # lookups (which would be fragile if Blender suffixes on collision).
    nodeMap = copyNodesBetweenTrees(groupTree, contentNodes, parentTree)

    # Shift the copied nodes so their center sits at the group node's position.
    pastedNodes = list(nodeMap.values())
    pastedNonFrame = [n for n in pastedNodes if n.bl_idname != 'NodeFrame']
    anchor = pastedNonFrame or pastedNodes
    if anchor:
        cX = sum(n.location.x for n in anchor) / len(anchor)
        cY = sum(n.location.y for n in anchor) / len(anchor)
        for n in pastedNodes:
            n.location.x += offsetX - cX
            n.location.y += offsetY - cY

    # Deselect everything in parent, then select the pasted nodes.
    for n in parentTree.nodes:
        n.select = False
    for n in pastedNodes:
        n.select = True

    # Reconnect incoming: parent upstream -> copied internal node
    for fromNodeName, fromSockName, groupInputIdx in parentIncoming:
        for outIdx, toNodeName, toSockName in inputConnections:
            if outIdx != groupInputIdx:
                continue
            fromNode = parentTree.nodes.get(fromNodeName)
            toNode   = nodeMap.get(toNodeName)
            if fromNode and toNode:
                fromSock = _getSocketByName(fromNode.outputs, fromSockName)
                toSock   = _getSocketByName(toNode.inputs, toSockName)
                if fromSock and toSock:
                    parentTree.links.new(fromSock, toSock)

    # Reconnect outgoing: copied internal node -> parent downstream
    for internalFromName, internalFromSock, groupOutputIdx in outputConnections:
        for outIdx, toNodeName, toSockName in parentOutgoing:
            if outIdx != groupOutputIdx:
                continue
            fromNode = nodeMap.get(internalFromName)
            toNode   = parentTree.nodes.get(toNodeName)
            if fromNode and toNode:
                fromSock = _getSocketByName(fromNode.outputs, internalFromSock)
                toSock   = _getSocketByName(toNode.inputs, toSockName)
                if fromSock and toSock:
                    parentTree.links.new(fromSock, toSock)

    # Remove the group node. Original content nodes remain inside groupTree
    # (which persists in bpy.data.node_groups) so any .copy() timers still
    # have valid src references when they fire.
    parentTree.nodes.remove(groupNode)


# ---------------------------------------------------------------------------
# Insert selected nodes into group
# ---------------------------------------------------------------------------

class VRAY_OT_node_group_insert(bpy.types.Operator):
    bl_idname  = 'vray.node_group_insert'
    bl_label   = "Insert Into V-Ray Group"
    bl_description = "Move selected nodes into the active V-Ray group node"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        if not isGroupNodesEnabled():
            return False
        if not _isVRayNodeEditor(context):
            return False
        activeNode = context.active_node
        if not activeNode or not (activeNode.bl_idname == VRAY_GROUP_NODE_TYPE and activeNode.node_tree):
            return False
        editTree = _getEditTree(context)
        return any(n.select and n != activeNode for n in editTree.nodes)

    def execute(self, context):
        editTree = _getEditTree(context)
        groupNode = context.active_node
        groupTree = groupNode.node_tree

        selectedNodes = [n for n in editTree.nodes
                         if n.select and n != groupNode
                         and n.bl_idname not in NON_GROUPABLE_NODE_TYPES]

        if not selectedNodes:
            self.report({'WARNING'}, "No nodes to insert")
            return {'CANCELLED'}

        selectedNames = {n.name for n in selectedNodes}
        incoming, outgoing = _snapshotLinks(editTree, selectedNames)

        # Copy selected nodes directly into the group tree.
        nodeMap = copyNodesBetweenTrees(editTree, selectedNodes, groupTree)

        # Find GroupInput/GroupOutput nodes
        inputNode  = next((n for n in groupTree.nodes if n.bl_idname == 'NodeGroupInput'), None)
        outputNode = next((n for n in groupTree.nodes if n.bl_idname == 'NodeGroupOutput'), None)

        # Wire incoming links through new group inputs.
        # New sockets are appended after any pre-existing ones.
        inputSocketMap = {}
        if inputNode:
            existingInputs = len(inputNode.outputs) - 1  # -1 for __extend__
            for fromNodeName, fromSockName, _, toNodeName, toSockName, toSockType in incoming:
                if fromNodeName == groupNode.name:
                    continue

                key = (fromNodeName, fromSockName)
                if key not in inputSocketMap:
                    blenderType = _BLENDER_SOCKET_TYPE_MAP.get(toSockType, 'NodeSocketFloat')
                    groupTree.interface.new_socket(
                        name=toSockName, in_out='INPUT', socket_type=blenderType
                    )
                    inputSocketMap[key] = existingInputs + len(inputSocketMap)

                idx = inputSocketMap[key]
                internalNode = nodeMap.get(toNodeName)
                internalSocket = _getSocketByName(internalNode.inputs, toSockName) if internalNode else None
                if internalSocket and idx < len(inputNode.outputs) - 1:
                    groupTree.links.new(inputNode.outputs[idx], internalSocket)

        # Wire outgoing links through new group outputs
        outputSocketMap = {}
        if outputNode:
            existingOutputs = len(outputNode.inputs) - 1  # -1 for __extend__
            for fromNodeName, fromSockName, fromSockType, toNodeName, toSockName, _ in outgoing:
                if toNodeName == groupNode.name:
                    continue

                key = (fromNodeName, fromSockName)
                if key not in outputSocketMap:
                    blenderType = _BLENDER_SOCKET_TYPE_MAP.get(fromSockType, 'NodeSocketFloat')
                    groupTree.interface.new_socket(
                        name=fromSockName, in_out='OUTPUT', socket_type=blenderType
                    )
                    outputSocketMap[key] = existingOutputs + len(outputSocketMap)

                idx = outputSocketMap[key]
                internalNode = nodeMap.get(fromNodeName)
                internalSocket = _getSocketByName(internalNode.outputs, fromSockName) if internalNode else None
                if internalSocket and idx < len(outputNode.inputs) - 1:
                    groupTree.links.new(internalSocket, outputNode.inputs[idx])

        # Sync V-Ray sockets on the group node
        syncGroupNodeSockets(groupNode)

        # Reconnect external links to the group node
        for fromNodeName, fromSockName, _, _, _, _ in incoming:
            if fromNodeName == groupNode.name:
                continue
            key = (fromNodeName, fromSockName)
            if key in inputSocketMap:
                idx = inputSocketMap[key]
                fromNode = editTree.nodes.get(fromNodeName)
                if fromNode and idx < len(groupNode.inputs):
                    fromSock = _getSocketByName(fromNode.outputs, fromSockName)
                    if fromSock:
                        editTree.links.new(fromSock, groupNode.inputs[idx])

        for fromNodeName, fromSockName, _, toNodeName, toSockName, _ in outgoing:
            if toNodeName == groupNode.name:
                continue
            key = (fromNodeName, fromSockName)
            if key in outputSocketMap:
                idx = outputSocketMap[key]
                toNode = editTree.nodes.get(toNodeName)
                if toNode and idx < len(groupNode.outputs):
                    toSock = _getSocketByName(toNode.inputs, toSockName)
                    if toSock:
                        editTree.links.new(groupNode.outputs[idx], toSock)

        # Deferred removal so that per-node copy() timers can complete
        # before the source nodes are freed.
        _removeNodes(editTree, selectedNames)

        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Separate nodes from group
# ---------------------------------------------------------------------------

class VRAY_OT_node_group_separate(bpy.types.Operator):
    bl_idname  = 'vray.node_group_separate'
    bl_label   = "Separate From V-Ray Group"
    bl_description = "Move or copy selected nodes out of the V-Ray group"
    bl_options = {'REGISTER', 'UNDO'}

    # Descriptions mirror Blender's own NODE_OT_group_separate for consistency.
    mode: bpy.props.EnumProperty(
        name = "Mode",
        items = (
            ('MOVE', "Move", "Move to parent node tree, remove from group"),
            ('COPY', "Copy", "Copy to parent node tree, keep group intact"),
        ),
        default = 'MOVE'
    )

    @classmethod
    def poll(cls, context):
        if not isGroupNodesEnabled():
            return False
        if not _isVRayNodeEditor(context):
            return False
        if not _isInsideGroup(context):
            return False
        editTree = _getEditTree(context)
        return any(n.select and n.bl_idname not in ('NodeGroupInput', 'NodeGroupOutput')
                   for n in editTree.nodes)

    def execute(self, context):
        space = context.space_data
        groupTree = _getEditTree(context)

        if len(space.path) < 2:
            return {'CANCELLED'}

        parentPath = space.path[-2]
        parentTree = parentPath.node_tree

        # Find the group node in the parent tree
        groupNode = None
        for node in parentTree.nodes:
            if node.bl_idname == VRAY_GROUP_NODE_TYPE and node.node_tree == groupTree:
                groupNode = node
                break

        if not groupNode:
            self.report({'WARNING'}, "Could not find parent group node")
            return {'CANCELLED'}

        selectedNodes = [n for n in groupTree.nodes
                         if n.select
                         and n.bl_idname not in ('NodeGroupInput', 'NodeGroupOutput')]

        if not selectedNodes:
            return {'CANCELLED'}

        selectedNames = {n.name for n in selectedNodes}

        # Snapshot GroupInput/GroupOutput connections before any modifications
        groupInputConns = []   # (outIdx, to_node_name, to_socket_name)
        groupOutputConns = []  # (from_node_name, from_socket_name, inIdx)
        for link in groupTree.links:
            if link.from_node.bl_idname == 'NodeGroupInput' and link.to_node.name in selectedNames:
                outIdx = _getSocketIndex(link.from_node.outputs, link.from_socket)
                if outIdx is not None:
                    groupInputConns.append((outIdx, link.to_node.name, link.to_socket.name))
            if link.to_node.bl_idname == 'NodeGroupOutput' and link.from_node.name in selectedNames:
                inIdx = _getSocketIndex(link.to_node.inputs, link.to_socket)
                if inIdx is not None:
                    groupOutputConns.append((link.from_node.name, link.from_socket.name, inIdx))

        # Copy selected nodes directly from the group tree into the parent tree.
        nodeMap = copyNodesBetweenTrees(groupTree, selectedNodes, parentTree)

        # Offset copied nodes to land near the group node's position in parent.
        offsetX = groupNode.location.x
        offsetY = groupNode.location.y
        pastedNodes = list(nodeMap.values())
        pastedNonFrame = [n for n in pastedNodes if n.bl_idname != 'NodeFrame']
        anchor = pastedNonFrame or pastedNodes
        if anchor:
            cX = sum(n.location.x for n in anchor) / len(anchor)
            cY = sum(n.location.y for n in anchor) / len(anchor)
            for n in pastedNodes:
                n.location.x += offsetX - cX
                n.location.y += offsetY - cY

        # Exit to parent tree now that pasted nodes are in place.
        space.path.pop()

        # Select the newly pasted nodes.
        for n in parentTree.nodes:
            n.select = False
        for n in pastedNodes:
            n.select = True

        # Rewire external connections only in MOVE mode. In COPY mode we keep
        # the group intact (matching Blender's own NODE_OT_group_separate
        # semantics: "Copy to parent node tree, keep group intact").
        if self.mode == 'MOVE':
            # Incoming: external source still feeds the group node as before;
            # also create a duplicate link to the moved copy so it works.
            for outIdx, toNodeName, toSockName in groupInputConns:
                if outIdx >= len(groupNode.inputs):
                    continue
                toNode = nodeMap.get(toNodeName)
                if not toNode:
                    continue
                toSock = _getSocketByName(toNode.inputs, toSockName)
                if not toSock:
                    continue
                for parentLink in list(parentTree.links):
                    if parentLink.to_socket == groupNode.inputs[outIdx]:
                        parentTree.links.new(parentLink.from_socket, toSock)
                        break

            # Outgoing: the external downstream input can only have one link,
            # so this transfers the connection from the group node to the
            # moved copy.
            for fromNodeName, fromSockName, inIdx in groupOutputConns:
                if inIdx >= len(groupNode.outputs):
                    continue
                fromNode = nodeMap.get(fromNodeName)
                if not fromNode:
                    continue
                fromSock = _getSocketByName(fromNode.outputs, fromSockName)
                if not fromSock:
                    continue
                for parentLink in list(parentTree.links):
                    if parentLink.from_socket == groupNode.outputs[inIdx]:
                        parentTree.links.new(fromSock, parentLink.to_socket)

            # Deferred removal so the copy() timers on pasted nodes can
            # finish reading from the originals before they're freed.
            _removeNodes(groupTree, selectedNames)

        return {'FINISHED'}


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------
# The right-click context menu and top-bar Node menu entries for V-Ray groups
# are drawn by the NODE_MT_context_menu / NODE_MT_node draw overrides in
# nodes/operators/misc.py, which replace Blender's default group operators
# (node.group_*) with vray.node_group_* equivalents.

def getRegClasses():
    return (
        VRAY_OT_node_group_new,
        VRAY_OT_node_group_edit,
        VRAY_OT_node_group_make,
        VRAY_OT_node_group_ungroup,
        VRAY_OT_node_group_insert,
        VRAY_OT_node_group_separate,
    )


def register():
    for regClass in getRegClasses():
        bpy.utils.register_class(regClass)


def unregister():
    for regClass in getRegClasses():
        bpy.utils.unregister_class(regClass)
