
import bpy

from ..importing import getSocketName
from ..mixin    import VRayNodeBase
from ..sockets import addInput, addOutput
from ..operators import sockets as SocketOperators


 #######  ########  ######## ########     ###    ########  #######  ########   ######
##     ## ##     ## ##       ##     ##   ## ##      ##    ##     ## ##     ## ##    ##
##     ## ##     ## ##       ##     ##  ##   ##     ##    ##     ## ##     ## ##
##     ## ########  ######   ########  ##     ##    ##    ##     ## ########   ######
##     ## ##        ##       ##   ##   #########    ##    ##     ## ##   ##         ##
##     ## ##        ##       ##    ##  ##     ##    ##    ##     ## ##    ##  ##    ##
 #######  ##        ######## ##     ## ##     ##    ##     #######  ##     ##  ######

# This opeator will create a socket with the vray_attrNumber
# This will allow as to collect attrubutes after matching the name
#
class VRAY_OT_node_list_socket_add(bpy.types.Operator):
    bl_idname      = 'vray.node_list_socket_add'
    bl_label       = "Add Socket"
    bl_description = "Adds socket"
    bl_options     = {'INTERNAL'}

    socketType: bpy.props.StringProperty()
    socketName: bpy.props.StringProperty()
    vray_attr : bpy.props.StringProperty()

    def execute(self, context):
        if not self.socketType or not self.socketName:
            return {'CANCELLED'}

        node     = context.node
        newIndex = len(node.inputs) + 1

        socketName = "%s %i" % (self.socketName, newIndex)
        attrName   = "%s%i" % (self.vray_attr, newIndex)

        addInput(node, self.socketType, socketName, attrName=attrName)

        return {'FINISHED'}


class VRAY_OT_node_list_socket_del(bpy.types.Operator):
    bl_idname      = 'vray.node_list_socket_del'
    bl_label       = "Remove Socket"
    bl_description = "Removes empty socket"
    bl_options     = {'INTERNAL'}

    def __init__(self):
        self.socketName = ""

    def _firstName(self, sock):
        if self.socketName:
            if sockName := sock.identifier:
                firstName = sockName.split(" ")[0]
                return self.socketName  == firstName
            return False
        return True
    
    def execute(self, context):
        node = context.node

        nSockets = len(node.inputs)

        if not nSockets:
            return {'FINISHED'}

        for i in range(nSockets-1, -1, -1):
            s = node.inputs[i]
            if not s.is_linked and self._firstName(s):
                node.inputs.remove(s)
                break

        return {'FINISHED'}


class VRAY_OT_node_list_plugin_add(SocketOperators.VRayNodeAddCustomSocket, bpy.types.Operator):
    bl_idname      = 'vray.node_list_plugin_add'
    bl_label       = "Add Plugin Socket"
    bl_description = "Adds Plugin sockets"
    bl_options     = {'INTERNAL'}

    def __init__(self):
        self.vray_socket_type = 'VRaySocketObject'
        self.vray_socket_name = "Plugin"


class VRAY_OT_node_list_plugin_del(SocketOperators.VRayNodeDelCustomSocket, bpy.types.Operator):
    bl_idname      = 'vray.node_list_plugin_del'
    bl_label       = "Remove Plugin Socket"
    bl_description = "Removes Plugin socket (only not linked sockets will be removed)"
    bl_options     = {'INTERNAL'}
    
    def __init__(self):
        self.vray_socket_type = 'VRaySocketObject'
        self.vray_socket_name = "Plugin"


##    ##  #######  ########  ########
###   ## ##     ## ##     ## ##
####  ## ##     ## ##     ## ##
## ## ## ##     ## ##     ## ######
##  #### ##     ## ##     ## ##
##   ### ##     ## ##     ## ##
##    ##  #######  ########  ########

class VRayListHolder(VRayNodeBase):
    bl_idname = 'VRayListHolder'
    bl_label  = 'V-Ray Effects Container'
    bl_icon   = 'GHOST_ENABLED'

    vray_type   = 'NONE'
    vray_plugin = 'NONE'

    socketType = None
    socketName = None

    def init(self, context):
        addInput(self, 'VRaySocketEffect', getSocketName(1))
        addOutput(self, 'VRaySocketObject', "Sphere Fade")

    def draw_buttons(self, context, layout):
        split = layout.split()
        row = split.row(align=True)
        row.operator('vray.node_list_socket_add', icon="ADD", text="Add")
        row.operator('vray.node_list_socket_del', icon="REMOVE", text="")


class VRayPluginListHolder(VRayNodeBase):
    """ Representation of V-Ray Plugin List """
    bl_idname = 'VRayPluginListHolder'
    bl_label  = 'V-Ray Plugin List'
    bl_icon   = 'PRESET'

    vray_type   = 'NONE'
    vray_plugin = 'NONE'

    def init(self, context):
        addInput(self, 'VRaySocketObject', "Plugin")
        addOutput(self, 'VRaySocketObjectList', "List")

    def draw_buttons(self, context, layout):
        split = layout.split()
        row = split.row(align=True)
        row.operator('vray.node_list_plugin_add', icon="ADD", text="Add")
        row.operator('vray.node_list_plugin_del', icon="REMOVE", text="")


########  ########  ######   ####  ######  ######## ########     ###    ######## ####  #######  ##    ##
##     ## ##       ##    ##   ##  ##    ##    ##    ##     ##   ## ##      ##     ##  ##     ## ###   ##
##     ## ##       ##         ##  ##          ##    ##     ##  ##   ##     ##     ##  ##     ## ####  ##
########  ######   ##   ####  ##   ######     ##    ########  ##     ##    ##     ##  ##     ## ## ## ##
##   ##   ##       ##    ##   ##        ##    ##    ##   ##   #########    ##     ##  ##     ## ##  ####
##    ##  ##       ##    ##   ##  ##    ##    ##    ##    ##  ##     ##    ##     ##  ##     ## ##   ###
##     ## ########  ######   ####  ######     ##    ##     ## ##     ##    ##    ####  #######  ##    ##

def getRegClasses():
    return (
        VRAY_OT_node_list_plugin_del,
        VRAY_OT_node_list_plugin_add,
        VRAY_OT_node_list_socket_add,
        VRAY_OT_node_list_socket_del,
        VRayListHolder,
        VRayPluginListHolder
    )


def register():
    for regClass in getRegClasses():
        bpy.utils.register_class(regClass)


def unregister():
    for regClass in getRegClasses():
        bpy.utils.unregister_class(regClass)
