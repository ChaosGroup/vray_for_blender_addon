
import bpy

from vray_blender.nodes.mixin import VRayNodeBase
from vray_blender.nodes.sockets import addInput, addOutput
from vray_blender.nodes.nodes import updateRenderChannelsState
from vray_blender.nodes.operators import sockets as SocketOperators
from vray_blender import plugins
from vray_blender.engine import resetActiveIprRendering
from vray_blender.exporting.tools import getNodeLink
from vray_blender.lib import class_utils
from vray_blender.ui import classes
from vray_blender.lib import draw_utils

 #######  ########  ######## ########     ###    ########  #######  ########   ######
##     ## ##     ## ##       ##     ##   ## ##      ##    ##     ## ##     ## ##    ##
##     ## ##     ## ##       ##     ##  ##   ##     ##    ##     ## ##     ## ##
##     ## ########  ######   ########  ##     ##    ##    ##     ## ########   ######
##     ## ##        ##       ##   ##   #########    ##    ##     ## ##   ##         ##
##     ## ##        ##       ##    ##  ##     ##    ##    ##     ## ##    ##  ##    ##
 #######  ##        ######## ##     ## ##     ##    ##     #######  ##     ##  ######

class VRAY_OT_node_renderchannels_socket_add(SocketOperators.VRayNodeAddCustomSocket, bpy.types.Operator):
    bl_idname      = 'vray.node_renderchannels_socket_add'
    bl_label       = "Add Render Channel Socket"
    bl_description = "Adds Render Channel sockets"
    bl_options     = {'INTERNAL'}

    def __init__(self):
        self.vray_socket_type = 'VRaySocketRenderChannel'
        self.vray_socket_name = "Channel"


class VRAY_OT_node_renderchannels_socket_del(SocketOperators.VRayNodeDelCustomSocket, bpy.types.Operator):
    bl_idname      = 'vray.node_renderchannels_socket_del'
    bl_label       = "Remove Render Channel Socket"
    bl_description = "Removes Render Channel socket (only not linked sockets will be removed)"
    bl_options     = {'INTERNAL'}
    
    def __init__(self):
        self.vray_socket_type = 'VRaySocketRenderChannel'
        self.vray_socket_name = "Channel"


##    ##  #######  ########  ########  ######
###   ## ##     ## ##     ## ##       ##    ##
####  ## ##     ## ##     ## ##       ##
## ## ## ##     ## ##     ## ######    ######
##  #### ##     ## ##     ## ##             ##
##   ### ##     ## ##     ## ##       ##    ##
##    ##  #######  ########  ########  ######

class VRayNodeRenderChannels(VRayNodeBase):
    bl_idname = 'VRayNodeRenderChannels'
    bl_label  = 'Render Channels Container'
    bl_icon   = 'SCENE_DATA'

    vray_type   = 'NONE'
    vray_plugin = 'NONE'

    hasDenoiser: bpy.props.BoolProperty(
        name        = "Has Denoiser Channel",
        description = "There is denoiser channel connected",
        default     = False
    )

    def init(self, context):
        addInput(self, 'VRaySocketRenderChannel', "Channel 1")
        addOutput(self, 'VRaySocketObjectList', "Channels")

    def draw_buttons(self, context, layout):
        classes.drawPluginUI(context, layout, context.scene.vray.SettingsRenderChannels, plugins.getPluginModule('SettingsRenderChannels'), self)
        layout.separator()

        split = layout.split()
        row = split.row(align=True)
        row.operator('vray.node_renderchannels_socket_add', icon="ADD", text="Add")
        row.operator('vray.node_renderchannels_socket_del', icon="REMOVE", text="")

    def update(self: bpy.types.Node):
        # If there is a new denoiser channel or an existing one is disconnected,
        # the viewport renderer (if there is such running) should be reset
        hasDenoiser = any([sock for sock in self.inputs if sock.is_linked and sock.use and \
                              (link := getNodeLink(sock)) and (link.from_node.bl_idname == "VRayNodeRenderChannelDenoiser")])
        
        if hasDenoiser != self.hasDenoiser:
            resetActiveIprRendering()
            self.hasDenoiser = hasDenoiser

        
    def draw_buttons_ext(self, context, layout):
        classes.drawPluginUI(context, layout, context.scene.vray.SettingsRenderChannels, plugins.getPluginModule('SettingsRenderChannels'), self)




class VRayNodeRenderChannelDenoiser(VRayNodeBase):
    """ Custom Denoiser Render Channel class.  
        This is required because the denoiser properties reside in context.scene.world.vray,  
        rather than in the node itself.  
    """
    bl_idname = 'VRayNodeRenderChannelDenoiser'
    bl_label  = 'VRay Denoiser'
    bl_icon   = 'SCENE_DATA'

    vray_type   = 'RENDERCHANNEL'
    vray_plugin = 'RenderChannelDenoiser'

    @property
    def RenderChannelDenoiser(self):
        """ Fake 'RenderChannelDenoiser' property  
            to allow the node to be used like the other nodes that represent V-Ray plugins.  
        """
        if (world := getattr(bpy.context.scene, "world", None)) and hasattr(world, "vray"):
            return world.vray.RenderChannelDenoiser
        return None

    def init(self, context):
        updateRenderChannelsState(self, True)
        addOutput(self, 'VRaySocketRenderChannelOutput', "Channel")
    
    def free(self):
        updateRenderChannelsState(self, False)

    def draw_buttons_ext(self, context, layout):
        pluginModule = plugins.PLUGINS['RENDERCHANNEL']['RenderChannelDenoiser']
        
        uiPainter = draw_utils.UIPainter(context, pluginModule, context.scene.world.vray.RenderChannelDenoiser, self)
        layout.use_property_split = True
        uiPainter.renderPluginUI(layout)

########  ########  ######   ####  ######  ######## ########     ###    ######## ####  #######  ##    ##
##     ## ##       ##    ##   ##  ##    ##    ##    ##     ##   ## ##      ##     ##  ##     ## ###   ##
##     ## ##       ##         ##  ##          ##    ##     ##  ##   ##     ##     ##  ##     ## ####  ##
########  ######   ##   ####  ##   ######     ##    ########  ##     ##    ##     ##  ##     ## ## ## ##
##   ##   ##       ##    ##   ##        ##    ##    ##   ##   #########    ##     ##  ##     ## ##  ####
##    ##  ##       ##    ##   ##  ##    ##    ##    ##    ##  ##     ##    ##     ##  ##     ## ##   ###
##     ## ########  ######   ####  ######     ##    ##     ## ##     ##    ##    ####  #######  ##    ##

def getRegClasses():
    return (
        VRAY_OT_node_renderchannels_socket_add,
        VRAY_OT_node_renderchannels_socket_del,
        VRayNodeRenderChannels,
        VRayNodeRenderChannelDenoiser,
    )


def register():
    class_utils.registerPluginPropertyGroup(VRayNodeRenderChannels, plugins.PLUGINS['SETTINGS']['SettingsRenderChannels'])

    for regClass in getRegClasses():
        bpy.utils.register_class(regClass)


def unregister():
    for regClass in getRegClasses():
        bpy.utils.unregister_class(regClass)
