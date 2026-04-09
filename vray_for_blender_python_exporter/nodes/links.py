# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Contains logic related to node links (node-to-node connections)"""

import bpy
from dataclasses import dataclass
from typing import Callable

from vray_blender.nodes.tools import isCompatibleNode
from vray_blender import debug
from vray_blender.nodes.sockets import STRUCTURAL_SOCKET_CLASSES
from vray_blender.plugins import getPluginModule


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


def isLinkValid(node: bpy.types.Node, link: bpy.types.NodeLink) -> bool:
    """Checks different conditions to determine if a given link is valid."""
    socketsConnectingToSameType = ["VRaySocketColorRamp"]
    isLinkValid = False

    if node == link.to_node and not isCompatibleNode(link.from_node):
        msg = f"Node '{link.from_node.name}' not compatible with V-Ray node tree"
        debug.report('WARNING', msg)
    elif node == link.from_node and not isCompatibleNode(link.to_node):
        msg = f"Node '{link.to_node.name}' not compatible with V-Ray node tree"
        debug.report('WARNING', msg)
    elif link.to_socket.bl_idname in STRUCTURAL_SOCKET_CLASSES.values():
        pass
    elif (link.to_socket.bl_idname in socketsConnectingToSameType
            or link.from_socket.bl_idname in socketsConnectingToSameType):
        return link.to_socket.bl_idname == link.from_socket.bl_idname
    else:
        isLinkValid = True

    return isLinkValid
