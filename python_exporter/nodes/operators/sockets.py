# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later


from vray_blender.exporting.tools import getInputSocketByName
from vray_blender.nodes.sockets import addInput


class VRayNodeAddCustomSocket:
    vray_socket_type    = None
    vray_socket_name    = None

    def execute(self, context):
        node = context.node
        sockNamePrefix = f"{self.vray_socket_name} "

        nSockets = 0
        for sock in node.inputs:
            if sock.name.startswith(sockNamePrefix):
                nSockets += 1

        newIndex = nSockets + 1
        sockName = f"{sockNamePrefix}{newIndex}"

        addInput(node, self.vray_socket_type, sockName)

        if hasattr(self, 'set_value'):
            self.set_value(getInputSocketByName(node, sockName), newIndex)

        return {'FINISHED'}


class VRayNodeDelCustomSocket:
    vray_socket_type    = None
    vray_socket_name    = None

    def execute(self, context):
        node = context.node
        sockNamePrefix = f"{self.vray_socket_name} "

        # Locate the sockets by position rather than by a name rebuilt from their count, as the
        # index embedded in the name may be non-consecutive (e.g. for an imported scene).
        sockets = [s for s in node.inputs if s.name.startswith(sockNamePrefix)]

        if not sockets:
            return {'CANCELLED'}

        # Remove the last socket if it is not linked. Report CANCELLED when nothing is
        # removed, otherwise Blender would push an empty undo step (VBLD-2686).
        sock = sockets[-1]

        if sock.is_linked:
            self.report({'WARNING'}, "Cannot remove socket while it is linked, disconnect and try again")
            return {'CANCELLED'}

        node.inputs.remove(sock)
        return {'FINISHED'}

