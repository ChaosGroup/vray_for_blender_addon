
from vray_blender import debug
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

        nSockets = len([s for s in node.inputs if s.name.startswith(sockNamePrefix)])
        
        if not nSockets:
            return {'FINISHED'}

        # Remove the last socket if it is not linked.
        sockID = nSockets
        sockName = f"{sockNamePrefix}{sockID}"
        sock = getInputSocketByName(node, sockName)
        
        if not sock.is_linked:
            node.inputs.remove(sock)
        else:
            debug.report('INFO', 'Cannot remove socket while it is linked, disconnect and try again')
        
        return {'FINISHED'}

