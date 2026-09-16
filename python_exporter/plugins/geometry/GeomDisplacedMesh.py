# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

from vray_blender.lib import plugin_utils
from vray_blender.nodes.utils import getNodeOfPropGroup
from vray_blender.nodes.sockets import addInput
from vray_blender.exporting.tools import getInputSocketByName, getInputSocketByAttr, getVRayBaseSockType
from vray_blender.nodes.utils import selectedObjectTagUpdate

plugin_utils.loadPluginOnModule(globals(), __name__)

def _addNewTexSocket(node, texSock, newSockType):
    if getVRayBaseSockType(texSock) != newSockType:

        # Rebuilding the socket would drop its link, so carry it over to the new socket.
        fromSocket = texSock.links[0].from_socket if (texSock is not None and texSock.is_linked) else None

        if texSock is not None:
            node.inputs.remove(texSock)

        newSock = addInput(node, newSockType, "Displacement Texture")

        if fromSocket is not None:
            node.id_data.links.new(fromSocket, newSock)


def onUpdateDisplacementType(src, context, attrName):
    node = getNodeOfPropGroup(src)
    texSock = getInputSocketByName(node, "Displacement Texture")
    selectedObjectTagUpdate(node, context)

    match src.type:
        case '0' | '1': # Normal | 2D
            _addNewTexSocket(node, texSock, 'VRaySocketFloatNoValue')

        case '2' | '3' | '4': # Vector | Vector (Absolute) | Vector (Object)
            _addNewTexSocket(node, texSock, 'VRaySocketColorNoValue')

    transformSocket = getInputSocketByAttr(node, "displace_2d_transform")
    transformSocket.enabled = src.type == '1'
    transformSocket.hide = src.type != '1'
