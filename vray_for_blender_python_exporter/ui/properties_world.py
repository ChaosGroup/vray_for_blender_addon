
import bpy

from vray_blender.ui import classes
from vray_blender.plugins import PLUGINS
from vray_blender.nodes import utils as NodesUtils



class VRAY_PT_WorldPreview(classes.VRayWorldPanel):
    bl_label = "Preview"
    bl_options = {'HIDE_HEADER'}

    COMPAT_ENGINES = {'VRAY_RENDER_PREVIEW', 'VRAY_RENDER_RT'}

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
        space = context.space_data

        if scene:
            layout.template_ID(scene, "world", new="vray.add_nodetree_world")
        elif world:
            layout.template_ID(space, "pin_id")

        if context.world:
            VRayWorld = context.world.vray

            layout.separator()
            layout.prop(VRayWorld, 'global_light_level', slider=True)

            if not NodesUtils.treeHasNodes(world.node_tree):
                return

            if not (activeNode := NodesUtils.getActiveTreeNode(context.world.node_tree, 'WORLD')):
                return

            layout.separator()
            classes.drawNodePanel(context, self.layout, activeNode, PLUGINS)


def getRegClasses():
    return (
        # VRAY_PT_WorldPreview,
        VRAY_PT_ContextWorld,
    )


def register():
    from vray_blender.lib.class_utils import registerClass
    for regClass in getRegClasses():
        registerClass(regClass)


def unregister():
    for regClass in reversed(getRegClasses()):
        bpy.utils.unregister_class(regClass)
