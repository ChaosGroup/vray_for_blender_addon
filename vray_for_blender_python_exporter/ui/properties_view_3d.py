import bpy
from vray_blender.ui import classes
from vray_blender.ui.icons import getIcon
from vray_blender import operators as ops
from vray_blender.nodes import utils as NodesUtils
from vray_blender.engine.render_engine import VRayRenderEngine
from vray_blender.bin import VRayBlenderLib as vray


def drawVRayInteractiveRenderMenu(self, context):
    """ Draws buttons for V-Ray settings in the 3d viewport bar """
    if classes.pollEngine(context):

        iprRow = self.layout.row(align=True)
        iprRow.enabled = vray.isInitialized()
        if not VRayRenderEngine.iprRenderer:
            op = ops.VRAY_OT_render_interactive
            iprRow.operator(op.bl_idname, text='', icon_value=getIcon("RENDER_IPR_START"))
        else:
            op = ops.VRAY_OT_render_interactive_stop
            iprRow.operator(op.bl_idname, text='', icon_value=getIcon("RENDER_IPR_STOP"))

        iprRow.popover(panel="VRAY_PT_View_3D_Options", text="")

        if not vray.isInitialized():
            message = "V-Ray initializing..." if vray.hasLicense() else "Obtaining V-Ray license..."
            self.layout.label(text=message, icon="INFO")


class VRAY_PT_View_3D_Options(classes.VRayPanel):
    """ Defines the V-Ray Options panel located in the headers of the 3D Viewport regions """
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'HEADER'
    bl_label = "V-Ray Options"

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True 
        
        layout.label(text="V-Ray Options")
        layout.separator(factor = 1)
        layout.scale_x = 0.1 # This scale that makes all the labels visible

        world = context.scene.world
        # Indication that there isn't a node tree created
        if (world is None) or (world.node_tree is None):
            layout.column().label(icon="ERROR", text="Denoiser requires World Tree.")
            self.layout.operator('vray.add_nodetree_world', text="Create a V-Ray World Node Tree")
            return
        
        # Drawing the property for node creation
        channelsDenoiserPropGroup = world.vray.VRayRenderChannels.VRayNodeRenderChannelDenoiser
        layout.prop(channelsDenoiserPropGroup, "enabled", text="Enable Denoiser")

        # Drawing RenderChannelDenoiser property
        denoiserColumn = layout.column()
        denoiserColumn.active = channelsDenoiserPropGroup.enabled
        denoiserColumn.prop(world.vray.RenderChannelDenoiser, "engine", text="Denoiser Type")



def getRegClasses():
    return (
        VRAY_PT_View_3D_Options,
    )


def register():
    from vray_blender.lib.class_utils import registerClass
    for regClass in getRegClasses():
        registerClass(regClass)

    bpy.types.VIEW3D_HT_header.append(drawVRayInteractiveRenderMenu)


def unregister():
    bpy.types.VIEW3D_HT_header.remove(drawVRayInteractiveRenderMenu)

    for regClass in reversed(getRegClasses()):
        bpy.utils.unregister_class(regClass)