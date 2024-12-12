
import bpy



from vray_blender.nodes.mixin import VRayNodeBase
from vray_blender.nodes.sockets import addInput, addOutput
from vray_blender.nodes.utils import selectedObjectTagUpdate
from vray_blender.lib import class_utils
from vray_blender.plugins import PLUGINS


 #######  ########  ######## ########     ###    ########  #######  ########   ######
##     ## ##     ## ##       ##     ##   ## ##      ##    ##     ## ##     ## ##    ##
##     ## ##     ## ##       ##     ##  ##   ##     ##    ##     ## ##     ## ##
##     ## ########  ######   ########  ##     ##    ##    ##     ## ########   ######
##     ## ##        ##       ##   ##   #########    ##    ##     ## ##   ##         ##
##     ## ##        ##       ##    ##  ##     ##    ##    ##     ## ##    ##  ##    ##
 #######  ##        ######## ##     ## ##     ##    ##     #######  ##     ##  ######

class VRAY_OT_node_add_brdf_layered_sockets(bpy.types.Operator):
    bl_idname      = 'vray.node_add_brdf_layered_sockets'
    bl_label       = "Add BRDFLayered Socket"
    bl_description = "Adds BRDFLayered sockets"
    bl_options     = {'INTERNAL'}

    def execute(self, context):
        node = context.node

        newIndex = int(len(node.inputs) / 2) + 1

        # BRDFLayered sockets are always in pairs
        #
        brdfSockName   = "BRDF %i" % newIndex
        weightSockName = "Weight %i" % newIndex

        node.inputs.new('VRaySocketBRDF',  brdfSockName)
        node.inputs.new('VRaySocketFloatColor', weightSockName)

        node.inputs[weightSockName].value = 1.0

        return {'FINISHED'}


class VRAY_OT_node_del_brdf_layered_sockets(bpy.types.Operator):
    bl_idname      = 'vray.node_del_brdf_layered_sockets'
    bl_label       = "Remove BRDFLayered Socket"
    bl_description = "Removes BRDFLayered socket (only not linked sockets will be removed)"
    bl_options     = {'INTERNAL'}
    
    def execute(self, context):
        node = context.node

        nSockets = len(node.inputs)

        if not nSockets:
            return {'FINISHED'}

        for i in range(int(nSockets/2), -1, -1):
            brdfSockName   = "BRDF %s" % i
            weightSockName = "Weight %s" % i

            if not (brdfSockName in node.inputs and weightSockName in node.inputs):
                continue

            brdfSock   = node.inputs[brdfSockName]
            weightSock = node.inputs[weightSockName]

            if not brdfSock.is_linked and not weightSock.is_linked:
                node.inputs.remove(node.inputs[brdfSockName])
                node.inputs.remove(node.inputs[weightSockName])
                break

        return {'FINISHED'}


##    ##  #######  ########  ########  ######
###   ## ##     ## ##     ## ##       ##    ##
####  ## ##     ## ##     ## ##       ##
## ## ## ##     ## ##     ## ######    ######
##  #### ##     ## ##     ## ##             ##
##   ### ##     ## ##     ## ##       ##    ##
##    ##  #######  ########  ########  ######

class VRayNodeBRDFLayered(VRayNodeBase):
    bl_idname = 'VRayNodeBRDFLayered'
    bl_label  = 'V-Ray Blend Mtl'
    bl_icon   = 'SHADING_TEXTURE'

    vray_type  : bpy.props.StringProperty(default='BRDF')
    vray_plugin: bpy.props.StringProperty(default='BRDFLayered')

    additive_mode: bpy.props.BoolProperty(
        name        = "Additive Mode",
        description = "Additive mode",
        default     = False,
        update = selectedObjectTagUpdate
    )

    def init(self, context):
        addInput(self, 'VRaySocketColor', "Transparency").setValue((0.0,0.0,0.0))

        for i in range(2):
            humanIndex = i + 1

            brdfSockName   = f"BRDF {humanIndex}"
            weightSockName = f"Weight {humanIndex}"

            addInput(self,'VRaySocketBRDF',  brdfSockName)
            addInput(self, 'VRaySocketWeight', weightSockName).setValue(1.0)
            
        addOutput(self, 'VRaySocketBRDF', "BRDF")

    def draw_buttons(self, context, layout):
        split = layout.split()
        row = split.row(align=True)
        row.operator('vray.node_add_brdf_layered_sockets', icon="ADD", text="Add")
        row.operator('vray.node_del_brdf_layered_sockets', icon="REMOVE", text="")

        layout.prop(self, 'additive_mode')


########  ########  ######   ####  ######  ######## ########     ###    ######## ####  #######  ##    ##
##     ## ##       ##    ##   ##  ##    ##    ##    ##     ##   ## ##      ##     ##  ##     ## ###   ##
##     ## ##       ##         ##  ##          ##    ##     ##  ##   ##     ##     ##  ##     ## ####  ##
########  ######   ##   ####  ##   ######     ##    ########  ##     ##    ##     ##  ##     ## ## ## ##
##   ##   ##       ##    ##   ##        ##    ##    ##   ##   #########    ##     ##  ##     ## ##  ####
##    ##  ##       ##    ##   ##  ##    ##    ##    ##    ##  ##     ##    ##     ##  ##     ## ##   ###
##     ## ########  ######   ####  ######     ##    ##     ## ##     ##    ##    ####  #######  ##    ##

def getRegClasses():
    return (
        VRAY_OT_node_add_brdf_layered_sockets,
        VRAY_OT_node_del_brdf_layered_sockets,

        VRayNodeBRDFLayered,
    )


def register():
    class_utils.registerPluginPropertyGroup(VRayNodeBRDFLayered, PLUGINS['BRDF']['BRDFLayered'])
    
    for regClass in getRegClasses():
        bpy.utils.register_class(regClass)


def unregister():
    for regClass in getRegClasses():
        bpy.utils.unregister_class(regClass)
