# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Contains logic related to node links (node-to-node connections)"""

import bpy
from dataclasses import dataclass
from typing import Callable

from vray_blender.nodes.tools import isCompatibleNode
from vray_blender.nodes.utils import (
    getPluginTypeOfNode,
    getOutputNode,
    getNodeByType,
    getVrayPropGroup,
    isAutoConnectEnabled,
    getMaterialWrapperSocket,
)
from vray_blender import debug
from vray_blender.nodes.sockets import (
    STRUCTURAL_SOCKET_CLASSES,
    MTL_SOCKET_TYPES,
    OBJECT_SOCKET_TYPES,
    SAME_TYPE_SOCKET_TYPES,
    SPECIAL_CHANNEL_SOCKETS,
    SPECIAL_CHANNEL_SOCKET_TYPES,
    SPECIAL_CHANNEL_INPUT_TO_OUTPUT,
    RENDER_CHANNEL_OUTPUT_TYPES,
    getSpecialChannelSocket,
)
from vray_blender.plugins import getPluginModule, findPluginModule
from vray_blender.exporting.tools import getVRayBaseSockType, getFarNodeLink
from vray_blender.lib.blender_utils import getFullPathToNode, resolveNodeFromPath


def _sockType(sock: bpy.types.NodeSocket) -> str:
    """ Return the base socket type for link-validation purposes.

        Prefers vray_socket_base_type (set by addInput/addOutput) but falls back
        to bl_idname for sockets in older scenes that were created before
        vray_socket_base_type was consistently populated.
    """
    return getVRayBaseSockType(sock) or sock.bl_idname


# Keyed by node path, not pointer - a freed node's pointer can be reused.
_ScheduledLinkFixes = set()
_ScheduledRolloutRedirects = set()


def clearScheduledLinkFixes():
    """ Drop the pending-fix registries (called on file load). """
    _ScheduledLinkFixes.clear()
    _ScheduledRolloutRedirects.clear()


def scheduleFixMisdirectedLink(node: bpy.types.Node, wrongSocketName: str, correctSocketName: str, validSourceTypes: set):
    """ When a node is dropped onto an existing link, Blender may connect it to the wrong socket
        because insert_link doesn't get called in that case. This schedules a deferred check to
        move the link to the correct socket if it ended up in the wrong one.
    """
    if not (nodePath := getFullPathToNode(node)) or (nodePath in _ScheduledLinkFixes):
        return

    def _fix():
        _ScheduledLinkFixes.discard(nodePath)
        # Shadows the outer 'node' - do not reference it below.
        if not (node := resolveNodeFromPath(nodePath)):
            return
        wrongSock = node.inputs.get(wrongSocketName)
        if wrongSock and wrongSock.is_linked:
            correctSock = node.inputs.get(correctSocketName)
            if correctSock and not correctSock.is_linked:
                link = wrongSock.links[0]
                if link.from_socket.bl_idname in validSourceTypes:
                    node.id_data.links.new(link.from_socket, correctSock)
                    node.id_data.links.remove(link)

    _ScheduledLinkFixes.add(nodePath)
    bpy.app.timers.register(_fix)


def _getRolloutRedirectSocket(node: bpy.types.Node, rolloutSock: bpy.types.NodeSocket):
    """ For a rollout header socket that declares a 'link_redirect' target in its plugin
        description, return the input socket that dropped links should be forwarded to.
        Returns None if the socket is not a rollout or the rollout has no redirect target.
    """
    from vray_blender.nodes.tools import getSocketPanel

    if rolloutSock.bl_idname != 'VRaySocketRollout':
        return None
    if (pluginType := getattr(node, 'vray_plugin', 'NONE')) == 'NONE':
        return None

    panelDesc = getSocketPanel(getPluginModule(pluginType), rolloutSock.vray_attr)
    if not panelDesc or not (targetAttr := panelDesc.get('link_redirect')):
        return None

    return next((s for s in node.inputs if s.vray_attr == targetAttr), None)


def scheduleRolloutLinkRedirect(node: bpy.types.Node, link: bpy.types.NodeLink) -> bool:
    """ Forward a link dropped onto a rollout header socket to the rollout's declared
        'link_redirect' target (VBLD-2608). Rollout headers cannot hold a link themselves, so
        the caller removes the header link; this schedules creation of the real link to the
        target socket, matching Blender's single-input semantics (a new drop replaces the old).
        Returns True if a redirect was scheduled.
    """
    rolloutSock = link.to_socket
    targetSock = _getRolloutRedirectSocket(node, rolloutSock)
    if not targetSock or not isConnectionAllowed(link.from_socket, targetSock):
        return False

    if not (nodePath := getFullPathToNode(node)):
        return False

    key = (nodePath, rolloutSock.name)
    if key in _ScheduledRolloutRedirects:
        return True
    _ScheduledRolloutRedirects.add(key)

    fromNodeName = link.from_node.name
    fromSockId   = link.from_socket.identifier
    targetAttr   = targetSock.vray_attr
    rolloutName  = rolloutSock.name

    def _redirect():
        _ScheduledRolloutRedirects.discard(key)
        # Shadows the outer 'node' - do not reference it below.
        if node := resolveNodeFromPath(nodePath):
            applyRolloutLinkRedirect(node, fromNodeName, fromSockId, targetAttr, rolloutName)

    bpy.app.timers.register(_redirect)
    return True


def applyRolloutLinkRedirect(node: bpy.types.Node, fromNodeName: str, fromSockId: str, targetAttr: str, rolloutName: str):
    """ Create the redirected link scheduled by scheduleRolloutLinkRedirect. Kept separate so the
        deferred work can be exercised directly (bpy timers do not fire in background mode).
    """
    ntree = node.id_data
    targetSock = next((s for s in node.inputs if s.vray_attr == targetAttr), None)
    fromNode = ntree.nodes.get(fromNodeName)
    fromSock = next((s for s in fromNode.outputs if s.identifier == fromSockId), None) if fromNode else None
    if not (targetSock and fromSock) or not isConnectionAllowed(fromSock, targetSock):
        return

    # Match Blender's single-input semantics: a new drop replaces an existing link.
    for existing in list(targetSock.links):
        ntree.links.remove(existing)
    ntree.links.new(fromSock, targetSock)

    # Reveal the connected socket by opening the rollout the user dropped onto.
    if rolloutSock := node.inputs.get(rolloutName):
        rolloutSock.is_open = True


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

    if node != link.to_node:
        return

    # Defer processing of the newly created link to VRayNodeBase.update(). At this point the
    # 'link' parameter is not a real link yet and we cannot store it for later use, nor is it
    # safe to touch socket/node state (the tree topology cache is not yet built). We record the
    # link's identity so update() can find the real link once the topology has been committed.
    # A link is recorded when it needs any deferred handling, i.e. when:
    #   - the target socket reacts to being connected (e.g. auto-enables a 'use' toggle), or
    #   - a custom insert-link callback was supplied, or
    #   - the plugin module defines a nodeInsertLink callback.
    # findPluginModule, not getPluginModule: a generic plugin node carries the type of a plugin
    # V-Ray has no description for, and the latter raises for those.
    hasPluginCallback = hasattr(findPluginModule(getattr(node, 'vray_plugin', 'NONE')), "nodeInsertLink")

    if customInsertLinkCallback or hasPluginCallback or hasattr(link.to_socket, "onLinkConnected"):
        global _NewlyCreatedLinks
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
            # A special channel's own output type is not accepted here, so it cannot take a
            # numbered socket
            return fromSocket.bl_idname == 'VRaySocketRenderChannelOutput'
        nodeVrayType = getattr(node, 'vray_type', 'NONE')
        if nodeVrayType == 'BRDF':
            return fromSocket.bl_idname == 'VRaySocketBRDF'
        if nodeVrayType == 'MATERIAL':
            return fromSocket.bl_idname == 'VRaySocketBRDF' or _sockType(fromSocket) == 'VRaySocketMtl'
        if nodeVrayType == 'TEXTURE':
            return fromSocket.bl_idname not in ({'VRaySocketBRDF', 'VRaySocketEffectOutput'} | RENDER_CHANNEL_OUTPUT_TYPES)
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
    # Import exception: a legacy glass BRDF's 'volume' slot takes a VolumeFog (effect output)
    # directly so imported .vrscenes keep their per-material fog. That socket only exists on the
    # hidden import-only BRDFs, so this does not expose effect->object linking in normal use.
    if fromSocket.bl_idname == 'VRaySocketEffectOutput' and getattr(toSocket, 'vray_attr', '') == 'volume':
        return True
    if (_sockType(toSocket) == 'VRaySocketEffect'
            and fromSocket.bl_idname != 'VRaySocketEffectOutput'):
        return False
    if (fromSocket.bl_idname == 'VRaySocketEffectOutput'
            and _sockType(toSocket) != 'VRaySocketEffect'):
        return False
    # Light Mix and the Denoiser each have their own socket on the channels container, fed only
    # by the matching output type. That also keeps them out of the numbered 'Channel N' slots,
    # which take the generic channel output alone.
    if _sockType(toSocket) in SPECIAL_CHANNEL_SOCKET_TYPES:
        if fromSocket.node.bl_idname == 'VRayNodeGroup':
            return True
        return fromSocket.bl_idname == SPECIAL_CHANNEL_INPUT_TO_OUTPUT[_sockType(toSocket)]
    if (_sockType(toSocket) == 'VRaySocketRenderChannel'
            and fromSocket.bl_idname != 'VRaySocketRenderChannelOutput'):
        return False
    if (fromSocket.bl_idname in RENDER_CHANNEL_OUTPUT_TYPES
            and _sockType(toSocket) != 'VRaySocketRenderChannel'):
        return False
    if (_sockType(toSocket) == 'VRaySocketObjectProps'
            and (_sockType(fromSocket) != 'VRaySocketObjectProps'
                 or fromSocket.name != toSocket.name)):
        return False
    if (_sockType(toSocket) in SAME_TYPE_SOCKET_TYPES
            or _sockType(fromSocket) in SAME_TYPE_SOCKET_TYPES):
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
    if (_sockType(toSocket) in OBJECT_SOCKET_TYPES) != (_sockType(fromSocket) in OBJECT_SOCKET_TYPES):
        return False
    if (fromSocket.bl_idname == 'VRaySocketBRDF'
            and _sockType(toSocket) not in MTL_SOCKET_TYPES):
        return False
    return True


def _specialChannelLinkWarning(fromSocket: bpy.types.NodeSocket, toSocket: bpy.types.NodeSocket) -> str:
    """ The message for a rejected link between a render channel and the wrong socket of the
        channels container, or an empty string when the link was rejected for another reason.
        Light Mix and the Denoiser have a socket of their own, and without a warning the
        rejected link would simply disappear with no hint as to why.
    """
    if (sockDesc := SPECIAL_CHANNEL_SOCKETS.get(fromSocket.node.bl_idname)) \
            and _sockType(toSocket) == 'VRaySocketRenderChannel':
        return f"The {sockDesc.name} render element has its own '{sockDesc.name}' socket on the " \
               f"Render Channels Container and cannot use a numbered channel socket"

    if sockDesc := next((d for d in SPECIAL_CHANNEL_SOCKETS.values()
                         if d.inputType == _sockType(toSocket)), None):
        return f"The '{sockDesc.name}' socket only accepts the {sockDesc.name} render element"

    return ''


def isLinkValid(node: bpy.types.Node, link: bpy.types.NodeLink) -> bool:
    """Checks different conditions to determine if a given link is valid."""
    if node == link.to_node and not isCompatibleNode(link.from_node):
        debug.report('WARNING', f"Node '{link.from_node.name}' not compatible with V-Ray node tree")
        return False
    if node == link.from_node and not isCompatibleNode(link.to_node):
        debug.report('WARNING', f"Node '{link.to_node.name}' not compatible with V-Ray node tree")
        return False
    if not isConnectionAllowed(link.from_socket, link.to_socket):
        if warning := _specialChannelLinkWarning(link.from_socket, link.to_socket):
            debug.report('WARNING', warning)
        return False
    return True

# Automatic node connections. When a node is added to a tree (and only on direct user edits,
# i.e. outside a DisableAutoConnect block), it is wired up to the obvious target if there is one.

def autoConnectObjectNode(node: bpy.types.Node, socketName: str):
    """ Automatically connect an object node to a specific socket in the VRayNodeObjectOutput node. """
    if not isAutoConnectEnabled():
        return

    if (ntree := node.id_data) and (ntree.vray.tree_type in {'OBJECT', 'FUR'}):
        if outputNode := getOutputNode(ntree, 'OBJECT'):
            targetSocket = outputNode.inputs.get(socketName)
            if targetSocket and not targetSocket.is_linked:
                ntree.links.new(node.outputs[0], targetSocket)


def autoConnectNode(node: bpy.types.Node):
    """ Automatically connect specific nodes to the output node of the tree. """
    if not isAutoConnectEnabled():
        return

    if not (ntree := node.id_data):
        return

    if ntree.vray.tree_type == 'OBJECT':
        socketName = None
        match node.bl_idname:
            case 'VRayNodeDisplacement':           socketName = 'Displacement'
            case 'VRayNodeGeomStaticSmoothedMesh': socketName = 'Subdivision'
            case 'VRayNodeObjectMatteProps':       socketName = 'Matte'
            case 'VRayNodeObjectSurfaceProps':     socketName = 'Surface'
            case 'VRayNodeObjectVisibilityProps':  socketName = 'Visibility'

        if socketName:
            autoConnectObjectNode(node, socketName)

    elif ntree.vray.tree_type == 'MATERIAL':
        if outputNode := getOutputNode(ntree, 'MATERIAL'):
            # BRDFToonOverride auto-connects to the dedicated Outlines socket
            if getattr(node, 'vray_plugin', '') == 'BRDFToonOverride':
                outlinesSock = outputNode.inputs.get("Outlines")
                if outlinesSock and not outlinesSock.is_linked:
                    if sourceSocket := node.outputs.get("BRDF"):
                        ntree.links.new(sourceSocket, outlinesSock)
            else:
                targetSocket = outputNode.inputs.get("Material")
                autoLinkSocket = None
                if targetSocket:
                    if not targetSocket.is_linked:
                        autoLinkSocket = targetSocket
                    if targetSocket.is_linked and (link := getFarNodeLink(targetSocket)):
                        if (wrapperNode := link.from_node) and (sock := getMaterialWrapperSocket(wrapperNode)):
                            if not sock.is_linked:
                                autoLinkSocket = sock

                if autoLinkSocket:
                    vrayType = getattr(node, 'vray_type', 'NONE')
                    if vrayType in {'BRDF', 'MATERIAL'}:
                        # Find the primary output socket
                        sourceSocket = None
                        if vrayType == 'BRDF':
                            sourceSocket = node.outputs.get("BRDF")
                        elif vrayType == 'MATERIAL':
                            sourceSocket = node.outputs.get("Material") or node.outputs.get("Ci")

                        if sourceSocket:
                            ntree.links.new(sourceSocket, autoLinkSocket)

    elif ntree.vray.tree_type == 'WORLD':
        if vrayType := getattr(node, 'vray_type', 'NONE'):
            containerType = None
            socketType = None
            if vrayType == 'RENDERCHANNEL':
                containerType = 'VRayNodeRenderChannels'
                socketType = 'VRaySocketRenderChannel'
            elif vrayType == 'EFFECT':
                containerType = 'VRayNodeEffectsHolder'
                socketType = 'VRaySocketEffect'

            if containerType:
                containerNode = getNodeByType(ntree, containerType)
                if containerNode:
                    # A channel that owns a socket on the container may only go there. A taken
                    # socket means the scene already has this channel, so leave the new node
                    # unconnected rather than silently replacing the active one.
                    if specialSocket := getSpecialChannelSocket(containerNode, node.bl_idname):
                        targetSocket = None if specialSocket.is_linked else specialSocket
                    else:
                        # Find first unlinked input socket (excluding the extend and special sockets)
                        targetSocket = next((s for s in containerNode.inputs
                                             if not s.is_linked
                                             and s.bl_idname != 'VRaySocketExtend'
                                             and s.bl_idname not in SPECIAL_CHANNEL_SOCKET_TYPES), None)

                        if not targetSocket:
                            # Add a new socket
                            from vray_blender.nodes.sockets import addInput, moveExtendSocketToBottom
                            sockNamePrefix = "Channel" if vrayType == 'RENDERCHANNEL' else "Effect"

                            # Count existing regular sockets to determine next name
                            existingSockets = [s for s in containerNode.inputs if s.bl_idname == socketType]
                            targetSocket = addInput(containerNode, socketType, f"{sockNamePrefix} {len(existingSockets) + 1}")

                            moveExtendSocketToBottom(containerNode)

                    if targetSocket:
                        # Find primary output socket of the node
                        sourceSocket = next((s for s in node.outputs if s.bl_idname in (RENDER_CHANNEL_OUTPUT_TYPES | {'VRaySocketEffectOutput'})), None)
                        if not sourceSocket and node.outputs:
                            sourceSocket = node.outputs[0]

                        if sourceSocket:
                            ntree.links.new(sourceSocket, targetSocket)

    # Helper nodes (uvwgen, object selectors, transform/matrix) connect to a single
    # unconnected compatible socket anywhere in the tree, regardless of tree type.
    autoConnectSingleSocket(node)


# Helper nodes that should connect to a single unconnected compatible input socket
# anywhere in the tree when added. UVWGen nodes are matched by 'vray_type' so that
# both the meta mapping node and the auto-generated UVWGenRandomizer are covered.
_SINGLE_SOCKET_AUTOCONNECT_IDNAMES = {
    'VRayNodeSelectObject',
    'VRayNodeMultiSelect',
    'VRayPluginListHolder',
    'VRayNodeTransform',
    'VRayNodeMatrix',
}


def _isSingleSocketAutoConnectNode(node: bpy.types.Node):
    return (getattr(node, 'vray_type', 'NONE') == 'UVWGEN') \
        or (node.bl_idname in _SINGLE_SOCKET_AUTOCONNECT_IDNAMES)


def _socketsConnectable(srcType: str, dstType: str):
    """ Return True if an output socket of srcType is a natural auto-connect target for an
        input of dstType: the same base type, or any pair within the object-reference family
        (object / object-list / include-exclude list), matching isConnectionAllowed. """
    if not srcType or not dstType:
        return False
    if srcType == dstType:
        return True
    return (srcType in OBJECT_SOCKET_TYPES) and (dstType in OBJECT_SOCKET_TYPES)


def _socketAcceptsAutoConnect(sock: bpy.types.NodeSocket):
    """ Exclude sockets that are always shown but only relevant in a particular mode, so they
        don't pollute the single-candidate detection.

        BRDFVRayMtl (and other BRDFs) expose an always-visible 'anisotropy_uvwgen' Mapping
        socket which only affects the material when the anisotropy axes are derived from a uvw
        generator (anisotropy_derivation == 1). In every other mode it should not be treated as
        an auto-connect target, otherwise a freshly added mapping node would never reach the
        texture's uvwgen socket. """
    if getattr(sock, 'vray_attr', '') == 'anisotropy_uvwgen':
        propGroup = getVrayPropGroup(sock.node)
        return (propGroup is not None) and (getattr(propGroup, 'anisotropy_derivation', '1') == '1')
    return True


def autoConnectSingleSocket(node: bpy.types.Node):
    """ Connect a newly added helper node (uvwgen, object selector, transform/matrix) to
        the single unconnected compatible input socket in the tree, if exactly one exists.
        When the connection is ambiguous (more than one candidate) nothing is connected. """
    if not isAutoConnectEnabled():
        return

    if not _isSingleSocketAutoConnectNode(node):
        return

    if not (ntree := node.id_data) or not node.outputs:
        return

    sourceSocket = node.outputs[0]
    if sourceSocket.is_linked:
        return

    srcType = _sockType(sourceSocket)

    candidate = None
    for treeNode in ntree.nodes:
        if treeNode == node:
            continue
        for sock in treeNode.inputs:
            if sock.is_linked or not sock.enabled:
                continue
            if _socketsConnectable(srcType, _sockType(sock)):
                if not _socketAcceptsAutoConnect(sock):
                    continue
                if candidate is not None:
                    # More than one candidate: ambiguous, do not auto-connect.
                    return
                candidate = sock

    if candidate is not None:
        ntree.links.new(sourceSocket, candidate)
