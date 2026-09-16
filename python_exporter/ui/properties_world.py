# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later


import bpy

from vray_blender.exporting.tools import getInputSocketByAttr
from vray_blender.lib import draw_utils
from vray_blender.ui import classes
from vray_blender.ui import node_nav
from vray_blender.ui import node_slots
from vray_blender.plugins import PLUGINS, getPluginModule
from vray_blender.nodes import navigation as NodesNav
from vray_blender.nodes import utils as NodesUtils



class VRAY_PT_WorldPreview(classes.VRayWorldPanel):
    bl_label = "Preview"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        self.layout.template_preview(context.world)


class VRAY_PT_ContextWorld(classes.VRayPanel):
    bl_space_type  = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context     = 'world'
    bl_label = ""
    bl_options = {'HIDE_HEADER'}

    def draw(self, context):
        layout = self.layout

        scene = context.scene
        world = context.world

        if scene:
            if world and world.vray.is_vray_class:
                layout.template_ID(scene, "world", new="vray.copy_world")
            else:
                layout.template_ID(scene, "world", new="vray.add_new_world")

        if world:
            layout.separator()
            layout.prop(world.vray, 'global_light_level', slider=True)


# The SettingsEnvironment parameters the Environment node exposes as sockets, in display order.
_ENV_ATTRS = ('bg_tex', 'gi_tex', 'reflect_tex', 'refract_tex', 'secondary_matte_tex')


def drawEnvironmentNode(layout, context, envNode):
    """ Draw the Environment node's parameters.

        VRayNodeEnvironment is hand-written: vray_plugin is 'NONE' and it implements neither
        draw_buttons nor draw_buttons_ext, so classes.drawActiveNodePanel() cannot draw it at all
        and falls through to "Selected node has no properties to show". Its parameters really
        belong to SettingsEnvironment and live on its input sockets, so paint them with a UIPainter
        bound to that plugin. Going through the painter is also what earns these sockets a texture
        picker, rather than hand-drawing them a second time.
    """
    painter = draw_utils.UIPainter(context, getPluginModule('SettingsEnvironment'), None, envNode)

    for attrName in _ENV_ATTRS:
        if socket := getInputSocketByAttr(envNode, attrName):
            painter.drawAttr(layout, attrName, socket.name)


class VRAY_PT_world_environment(classes.VRayPanel):
    """ The world's environment, mirroring the Material tab: the node currently being edited, with
        a breadcrumb back to the Environment node. """
    bl_space_type  = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context     = 'world'
    bl_label       = "Environment"

    @classmethod
    def poll_custom(cls, context):
        world = context.world
        return world and world.vray.is_vray_class and NodesUtils.treeHasNodes(world.node_tree)

    def draw(self, context):
        layout = self.layout
        world = context.world

        if not (activeNode := NodesNav.getPanelNode(world.node_tree, 'WORLD')):
            return

        layout.use_property_split = True
        layout.use_property_decorate = True

        slotContext = node_slots.makeSlotContext(context, world, world.node_tree)

        if slotContext is not None:
            navCol = layout.column(align=True)
            navCol.use_property_split = False
            node_nav.drawNavigation(navCol, context, slotContext.ownerType, slotContext.ownerName,
                                    world.node_tree, 'WORLD', activeNode)
            layout.separator()

        with draw_utils.slotEditing(slotContext):
            if activeNode.bl_idname == 'VRayNodeEnvironment':
                drawEnvironmentNode(layout, context, activeNode)
            else:
                classes.drawActiveNodePanel(context, layout, activeNode, PLUGINS)


def getRegClasses():
    return (
        VRAY_PT_ContextWorld,
        VRAY_PT_WorldPreview,
        VRAY_PT_world_environment,
    )


def register():
    from vray_blender.lib.class_utils import registerClass
    for regClass in getRegClasses():
        registerClass(regClass)


def unregister():
    for regClass in reversed(getRegClasses()):
        bpy.utils.unregister_class(regClass)
