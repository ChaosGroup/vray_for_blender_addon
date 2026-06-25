# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Contains logic related to node links (node-to-node connections)"""

import bpy
from dataclasses import dataclass
from typing import Callable

from vray_blender.nodes.tools import isCompatibleNode
from vray_blender.nodes.utils import getPluginTypeOfNode
from vray_blender import debug
from vray_blender.nodes.sockets import STRUCTURAL_SOCKET_CLASSES
from vray_blender.plugins import getPluginModule
from vray_blender.exporting.tools import getVRayBaseSockType


_OBJECT_SOCKETS = frozenset({'VRaySocketObject', 'VRaySocketObjectList', 'VRaySocketIncludeExcludeList'})

_SAME_TYPE_SOCKETS = frozenset({'VRaySocketColorRamp', 'VRaySocketTransform', 'VRaySocketGeom', 'VRaySocketObjectProps'})


def _sockType(sock: bpy.types.NodeSocket) -> str:
    """ Return the base socket type for link-validation purposes.

        Prefers vray_socket_base_type (set by addInput/addOutput) but falls back
        to bl_idname for sockets in older scenes that were created before
        vray_socket_base_type was consistently populated.
    """
    return getVRayBaseSockType(sock) or sock.bl_idname

_ScheduledLinkFixes = set()

def scheduleFixMisdirectedLink(node: bpy.types.Node, wrongSocketName: str, correctSocketName: str, validSourceTypes: set):
    """ When a node is dropped onto an existing link, Blender may connect it to the wrong socket
        because insert_link doesn't get called in that case. This schedules a deferred check to
        move the link to the correct socket if it ended up in the wrong one.
    """
    def _fix():
        _ScheduledLinkFixes.discard(node.as_pointer())
        if not node or not node.id_data:
            return
        wrongSock = node.inputs.get(wrongSocketName)
        if wrongSock and wrongSock.is_linked:
            correctSock = node.inputs.get(correctSocketName)
            if correctSock and not correctSock.is_linked:
                link = wrongSock.links[0]
                if link.from_socket.bl_idname in validSourceTypes:
                    node.id_data.links.new(link.from_socket, correctSock)
                    node.id_data.links.remove(link)

    nodePtr = node.as_pointer()
    if nodePtr not in _ScheduledLinkFixes:
        _ScheduledLinkFixes.add(nodePtr)
        bpy.app.timers.register(_fix)


@dataclass
class _NewLinkInfo:
    nodeTreePtr: int
    fromNodeName: str
    fromSocketName: str
    toNodeName: str
    toSocketName: str
    customInsertLinkCallback: Callable[[bpy.types.NodeLink], None]

    def isSameAs(self, link: bpy.types.NodeLink):
        return (id(link.id_data)       == self.nodeTreePtr and
                link.from_node.name    == self.fromNodeName and
                link.to_node.name      == self.toNodeName and
                link.from_socket.name  == self.fromSocketName and
                link.to_socket.name    == self.toSocketName)


_NewlyCreatedLinks: list[_NewLinkInfo] = []


def checkAndRemoveNewlyCreatedLink(link: bpy.types.NodeLink):
    newLinkInfo = next((l for l in _NewlyCreatedLinks if l.isSameAs(link)), None)
    if newLinkInfo:
        _NewlyCreatedLinks.remove(newLinkInfo)
        return newLinkInfo
    return None


def vrayNodeInsertLink(node: bpy.types.Node, link: bpy.types.NodeLink, customInsertLinkCallback: Callable[[bpy.types.NodeLink], None] = None):
    """ Handler for node link creation callback.

        This insert_link callback is registered in VRayNodeBase and calls this
        function to process the event.
    """
    assert type(link) is bpy.types.NodeLink

    # Store newly created links for nodes that have registered a custom nodeInsertLink callback
    if getattr(node, 'vray_plugin', 'NONE') == 'NONE' and not customInsertLinkCallback:
        return

    if (node == link.to_node) and (customInsertLinkCallback or hasattr(getPluginModule(node.vray_plugin), "nodeInsertLink")):
        global _NewlyCreatedLinks
        # Give the node a chance to initialize plugin-specific state. At this point,
        # the 'link' parameter is not a real link yet and we cannot store it for later use.
        # Store information about the link which will be used later to identify it
        _NewlyCreatedLinks.append(_NewLinkInfo(id(node.id_data),
                                               link.from_node.name,
                                               link.from_socket.name,
                                               link.to_node.name,
                                               link.to_socket.name,
                                               customInsertLinkCallback))


def isConnectionAllowed(fromSocket: bpy.types.NodeSocket, toSocket: bpy.types.NodeSocket) -> bool:
    """ Check if a direct socket-to-socket connection is valid under V-Ray's rules.

        V-Ray permits cross-type connections except for strict same-type sockets
        (ColorRamp, Transform, Geom, ObjectProps), BRDFToonOverride which may only
        feed the Outlines socket, and BRDF outputs which may only feed BRDF or Material inputs.
    """
    if toSocket.bl_idname in STRUCTURAL_SOCKET_CLASSES.values():
        return False
    if toSocket.bl_idname == 'VRaySocketExtend':
        node = toSocket.node
        if node.bl_idname == 'VRayNodeEffectsHolder':
            return fromSocket.bl_idname == 'VRaySocketEffectOutput'
        if node.bl_idname == 'VRayNodeRenderChannels':
            return fromSocket.bl_idname == 'VRaySocketRenderChannelOutput'
        nodeVrayType = getattr(node, 'vray_type', 'NONE')
        if nodeVrayType == 'BRDF':
            return fromSocket.bl_idname == 'VRaySocketBRDF'
        if nodeVrayType == 'MATERIAL':
            return fromSocket.bl_idname == 'VRaySocketBRDF' or _sockType(fromSocket) == 'VRaySocketMtl'
        if nodeVrayType == 'TEXTURE':
            return fromSocket.bl_idname not in {'VRaySocketBRDF', 'VRaySocketEffectOutput', 'VRaySocketRenderChannelOutput'}
        return True
    if toSocket.node.bl_idname == 'VRayNodeObjectOutput':
        isGroup = fromSocket.node.bl_idname == 'VRayNodeGroup'
        if toSocket.name == 'Displacement' \
                and not isGroup \
                and getPluginTypeOfNode(fromSocket.node) != 'GeomDisplacedMesh':
            return False
        if toSocket.name == 'Subdivision' \
                and not isGroup \
                and getPluginTypeOfNode(fromSocket.node) != 'GeomStaticSmoothedMesh':
            return False
    if toSocket.node.bl_idname == 'VRayNodeWorldOutput':
        fromNodeId = fromSocket.node.bl_idname
        if toSocket.name == 'Environment' and fromNodeId not in {'VRayNodeEnvironment', 'VRayNodeGroup'}:
            return False
        if toSocket.name == 'Effects' and fromNodeId not in {'VRayNodeEffectsHolder', 'VRayNodeGroup'}:
            return False
        if toSocket.name == 'Channels' and fromNodeId not in {'VRayNodeRenderChannels', 'VRayNodeGroup'}:
            return False
    if (_sockType(toSocket) == 'VRaySocketEffect'
            and fromSocket.bl_idname != 'VRaySocketEffectOutput'):
        return False
    if (fromSocket.bl_idname == 'VRaySocketEffectOutput'
            and _sockType(toSocket) != 'VRaySocketEffect'):
        return False
    if (_sockType(toSocket) == 'VRaySocketRenderChannel'
            and fromSocket.bl_idname != 'VRaySocketRenderChannelOutput'):
        return False
    if (fromSocket.bl_idname == 'VRaySocketRenderChannelOutput'
            and _sockType(toSocket) != 'VRaySocketRenderChannel'):
        return False
    if (_sockType(toSocket) == 'VRaySocketObjectProps'
            and (_sockType(fromSocket) != 'VRaySocketObjectProps'
                 or fromSocket.name != toSocket.name)):
        return False
    if (_sockType(toSocket) in _SAME_TYPE_SOCKETS
            or _sockType(fromSocket) in _SAME_TYPE_SOCKETS):
        # Group interface sockets (NodeGroupInput outputs / NodeGroupOutput
        # inputs) are created by Blender with generic NodeSocket* types and
        # can't carry the exact V-Ray subtype. Allow them through so users
        # can expose Transform/Geom/etc. sockets across group boundaries;
        # syncGroupNodeSockets sets the exterior socket type correctly.
        if (fromSocket.node.bl_idname == 'NodeGroupInput'
                or toSocket.node.bl_idname == 'NodeGroupOutput'):
            return True
        return _sockType(toSocket) == _sockType(fromSocket)
    if toSocket.node.bl_idname == 'VRayNodeOutputMaterial':
        if (toSocket.name == 'Outlines'
                and getPluginTypeOfNode(fromSocket.node) != 'BRDFToonOverride'):
            return False
    if (getPluginTypeOfNode(fromSocket.node) == 'BRDFToonOverride'
            and not (toSocket.node.bl_idname == 'VRayNodeOutputMaterial'
                     and toSocket.name == 'Outlines')):
        return False
    if (_sockType(toSocket) in _OBJECT_SOCKETS) != (_sockType(fromSocket) in _OBJECT_SOCKETS):
        return False
    if (fromSocket.bl_idname == 'VRaySocketBRDF'
            and _sockType(toSocket) not in {'VRaySocketBRDF', 'VRaySocketMtl', 'VRaySocketMtlMulti'}):
        return False
    return True


def isLinkValid(node: bpy.types.Node, link: bpy.types.NodeLink) -> bool:
    """Checks different conditions to determine if a given link is valid."""
    if node == link.to_node and not isCompatibleNode(link.from_node):
        debug.report('WARNING', f"Node '{link.from_node.name}' not compatible with V-Ray node tree")
        return False
    if node == link.from_node and not isCompatibleNode(link.to_node):
        debug.report('WARNING', f"Node '{link.to_node.name}' not compatible with V-Ray node tree")
        return False
    return isConnectionAllowed(link.from_socket, link.to_socket)
