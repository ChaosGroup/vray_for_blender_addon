# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later


import bpy

from vray_blender import plugins
from vray_blender.engine import resetActiveIprRendering
from vray_blender.exporting.world_export import sockConnectedToDenoiser
from vray_blender.lib import class_utils, draw_utils
from vray_blender.lib.mixin import VRayNodeBase, VRayOperatorBase
from vray_blender.nodes.operators import sockets as SocketOperators
from vray_blender.nodes.sockets import addInput, addOutput, moveExtendSocketToBottom
from vray_blender.nodes.links import vrayNodeInsertLink, autoConnectNode
from vray_blender.nodes.utils import getNodeByType
from vray_blender.ui import classes


 #######  ########  ######## ########     ###    ########  #######  ########   ######
##     ## ##     ## ##       ##     ##   ## ##      ##    ##     ## ##     ## ##    ##
##     ## ##     ## ##       ##     ##  ##   ##     ##    ##     ## ##     ## ##
##     ## ########  ######   ########  ##     ##    ##    ##     ## ########   ######
##     ## ##        ##       ##   ##   #########    ##    ##     ## ##   ##         ##
##     ## ##        ##       ##    ##  ##     ##    ##    ##     ## ##    ##  ##    ##
 #######  ##        ######## ##     ## ##     ##    ##     #######  ##     ##  ######

class VRAY_OT_node_renderchannels_socket_add(SocketOperators.VRayNodeAddCustomSocket, VRayOperatorBase):
    bl_idname      = 'vray.node_renderchannels_socket_add'
    bl_label       = "Add Render Channel Socket"
    bl_description = "Adds Render Channel sockets"
    bl_options     = {'INTERNAL', 'UNDO'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.vray_socket_type = 'VRaySocketRenderChannel'
        self.vray_socket_name = "Channel"

    def execute(self, context):
        res = super().execute(context)
        if res == {'FINISHED'}:
            moveExtendSocketToBottom(context.node)
        return res


class VRAY_OT_node_renderchannels_socket_del(SocketOperators.VRayNodeDelCustomSocket, VRayOperatorBase):
    bl_idname      = 'vray.node_renderchannels_socket_del'
    bl_label       = "Remove Render Channel Socket"
    bl_description = "Removes Render Channel socket (only not linked sockets will be removed)"
    bl_options     = {'INTERNAL', 'UNDO'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.vray_socket_type = 'VRaySocketRenderChannel'
        self.vray_socket_name = "Channel"


##    ##  #######  ########  ########  ######
###   ## ##     ## ##     ## ##       ##    ##
####  ## ##     ## ##     ## ##       ##
## ## ## ##     ## ##     ## ######    ######
##  #### ##     ## ##     ## ##             ##
##   ### ##     ## ##     ## ##       ##    ##
##    ##  #######  ########  ########  ######

def addRenderChannelsExtendSocket(node):
    sockExtend = addInput(node, 'VRaySocketExtend', "")
    sockExtend.add_operator = 'vray.node_renderchannels_socket_add'
    sockExtend.del_operator = 'vray.node_renderchannels_socket_del'

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

        addRenderChannelsExtendSocket(self)

        addOutput(self, 'VRaySocketObjectList', "Channels")

    def draw_buttons(self, context, layout):
        classes.drawPluginUI(context, layout, context.scene.vray.SettingsRenderChannels, plugins.getPluginModule('SettingsRenderChannels'), self)
        layout.separator()

        split = layout.split()
        row = split.row(align=True)
        row.operator('vray.node_renderchannels_socket_add', icon="ADD", text="Add")
        row.operator('vray.node_renderchannels_socket_del', icon="REMOVE", text="")

    def update(self: bpy.types.Node):
        super().update()
        # If there is a new denoiser channel or an existing one is disconnected,
        # the viewport renderer (if there is such running) should be reset
        hasDenoiser = any(sock for sock in self.inputs if sockConnectedToDenoiser(sock))

        if hasDenoiser != self.hasDenoiser:
            resetActiveIprRendering()
            self.hasDenoiser = hasDenoiser


    def draw_buttons_ext(self, context, layout):
        classes.drawPluginUI(context, layout, context.scene.vray.SettingsRenderChannels, plugins.getPluginModule('SettingsRenderChannels'), self)

    def insert_link(self: bpy.types.Node, link: bpy.types.NodeLink):
        vrayNodeInsertLink(self, link, customInsertLinkCallback=renderChannelsInsertLink)

def renderChannelsInsertLink(link: bpy.types.NodeLink):
    node = link.to_node
    if link.to_socket.bl_idname == 'VRaySocketExtend':
        from_socket = link.from_socket
        ntree = node.id_data

        humanIndex = len([s for s in node.inputs if s.bl_idname == 'VRaySocketRenderChannel']) + 1
        newSock = addInput(node, 'VRaySocketRenderChannel', f"Channel {humanIndex}")

        # Move the extend socket to the end
        moveExtendSocketToBottom(node)

        # Update the link to use the new socket by creating a new link and removing the old one
        ntree.links.new(from_socket, newSock)
        ntree.links.remove(link)


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
        addOutput(self, 'VRaySocketRenderChannelOutput', "Channel")
        autoConnectNode(self)

    def draw_buttons(self, context, layout):
        pluginModule = plugins.PLUGINS['RENDERCHANNEL']['RenderChannelDenoiser']
        painter = draw_utils.UIPainter(context, pluginModule, context.scene.world.vray.RenderChannelDenoiser, self)
        painter.renderWidgets(layout, pluginModule.Node['widgets'], True)

    def draw_buttons_ext(self, context, layout):
        pluginModule = plugins.PLUGINS['RENDERCHANNEL']['RenderChannelDenoiser']

        uiPainter = draw_utils.UIPainter(context, pluginModule, context.scene.world.vray.RenderChannelDenoiser, self)
        layout.use_property_split = True
        uiPainter.renderPluginUI(layout)


class VRAY_OT_show_denoiser_advanced_settings(VRayOperatorBase):
    """ Open a popup with the full property page of the V-Ray Denoiser
        render channel node from the active world's node tree.
    """
    bl_idname      = 'vray.show_denoiser_advanced_settings'
    bl_label       = "Denoiser Advanced Settings"
    bl_description = "Show advanced settings of the V-Ray Denoiser render channel"
    bl_options     = {'INTERNAL'}

    _POPUP_WIDTH = 450

    def invoke(self, context, event):
        return context.window_manager.invoke_popup(self, width=self._POPUP_WIDTH)

    def execute(self, context):
        return {'FINISHED'}

    def draw(self, context):
        layout = self.layout
        world = context.scene.world

        if not (world and world.node_tree):
            layout.label(text="Requires a V-Ray World node tree.", icon='ERROR')
            return

        denoiserNode = getNodeByType(world.node_tree, 'VRayNodeRenderChannelDenoiser')
        propGroup = world.vray.RenderChannelDenoiser
        pluginModule = plugins.PLUGINS['RENDERCHANNEL']['RenderChannelDenoiser']

        layout.use_property_split = True
        layout.use_property_decorate = False

        painter = draw_utils.UIPainter(context, pluginModule, propGroup, denoiserNode)
        painter.renderPluginUI(layout)


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
        VRAY_OT_show_denoiser_advanced_settings,
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
