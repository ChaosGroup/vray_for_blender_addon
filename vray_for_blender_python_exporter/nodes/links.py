# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Contains logic related to node links (node-to-node connections)"""

import bpy
from vray_blender.nodes.tools import isCompatibleNode
from vray_blender import debug
from vray_blender.nodes.sockets import STRUCTURAL_SOCKET_CLASSES


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
