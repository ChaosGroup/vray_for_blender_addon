
import bpy

from vray_blender.lib.mixin import VRayOperatorBase, VRayNodeBase
from vray_blender.nodes.sockets import addInput, addOutput
from vray_blender.nodes.operators import sockets as SocketOperators

 #######  ########  ######## ########     ###    ########  #######  ########   ######
##     ## ##     ## ##       ##     ##   ## ##      ##    ##     ## ##     ## ##    ##
##     ## ##     ## ##       ##     ##  ##   ##     ##    ##     ## ##     ## ##
##     ## ########  ######   ########  ##     ##    ##    ##     ## ########   ######
##     ## ##        ##       ##   ##   #########    ##    ##     ## ##   ##         ##
##     ## ##        ##       ##    ##  ##     ##    ##    ##     ## ##    ##  ##    ##
 #######  ##        ######## ##     ## ##     ##    ##     #######  ##     ##  ######

class VRAY_OT_node_effects_add(SocketOperators.VRayNodeAddCustomSocket, VRayOperatorBase):
    bl_idname      = 'vray.node_effects_add'
    bl_label       = "Add Effect Socket"
    bl_description = "Adds Effect sockets"
    bl_options     = {'INTERNAL'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.vray_socket_type = 'VRaySocketEffect'
        self.vray_socket_name = "Effect"


class VRAY_OT_node_effects_del(SocketOperators.VRayNodeDelCustomSocket, VRayOperatorBase):
    bl_idname      = 'vray.node_effects_del'
    bl_label       = "Remove Effect Socket"
    bl_description = "Removes Effect socket (only not linked sockets will be removed)"
    bl_options     = {'INTERNAL'}
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.vray_socket_type = 'VRaySocketEffect'
        self.vray_socket_name = "Effect"


##    ##  #######  ########  ########  ######
###   ## ##     ## ##     ## ##       ##    ##
####  ## ##     ## ##     ## ##       ##
## ## ## ##     ## ##     ## ######    ######
##  #### ##     ## ##     ## ##             ##
##   ### ##     ## ##     ## ##       ##    ##
##    ##  #######  ########  ########  ######

class VRayNodeEffectsHolder(VRayNodeBase):
    bl_idname = 'VRayNodeEffectsHolder'
    bl_label  = 'V-Ray Effects Container'
    bl_icon   = 'GHOST_ENABLED'

    vray_type   = 'NONE'
    vray_plugin = 'NONE'

    def init(self, context):
        addInput(self, 'VRaySocketEffect', "Effect 1")
        addOutput(self, 'VRaySocketObjectList', "Effects")

    def draw_buttons(self, context, layout):
        split = layout.split()
        row = split.row(align=True)
        row.operator('vray.node_effects_add', icon="ADD", text="Add")
        row.operator('vray.node_effects_del', icon="REMOVE", text="")

    def draw_buttons_ext(self, context, layout):
        # Nothing to show here, but keep the method because draw_buttons
        # will be called instead to draw the side bar
        pass
        
########  ########  ######   ####  ######  ######## ########     ###    ######## ####  #######  ##    ##
##     ## ##       ##    ##   ##  ##    ##    ##    ##     ##   ## ##      ##     ##  ##     ## ###   ##
##     ## ##       ##         ##  ##          ##    ##     ##  ##   ##     ##     ##  ##     ## ####  ##
########  ######   ##   ####  ##   ######     ##    ########  ##     ##    ##     ##  ##     ## ## ## ##
##   ##   ##       ##    ##   ##        ##    ##    ##   ##   #########    ##     ##  ##     ## ##  ####
##    ##  ##       ##    ##   ##  ##    ##    ##    ##    ##  ##     ##    ##     ##  ##     ## ##   ###
##     ## ########  ######   ####  ######     ##    ##     ## ##     ##    ##    ####  #######  ##    ##

def getRegClasses():
    return (
        VRAY_OT_node_effects_add,
        VRAY_OT_node_effects_del,
        VRayNodeEffectsHolder,
    )


def register():
    for regClass in getRegClasses():
        bpy.utils.register_class(regClass)


def unregister():
    for regClass in getRegClasses():
        bpy.utils.unregister_class(regClass)
