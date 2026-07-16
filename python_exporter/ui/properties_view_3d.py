# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
from vray_blender.ui import classes
from vray_blender.ui.icons import getIcon
from vray_blender import operators as ops
from vray_blender.engine.render_engine import VRayRenderEngine
from vray_blender.engine.renderer_vantage import VRayRendererVantageLiveLink
from vray_blender.bin import VRayBlenderLib as vray
from vray_blender.nodes.utils import getChannelsOutputNode
from vray_blender.exporting.world_export import sockConnectedToDenoiser


def drawVRayInteractiveRenderMenu(self, context):
    """ Draws buttons for V-Ray settings in the 3d viewport bar """
    if classes.pollEngine(context):

        iprRow = self.layout.row(align=True)
        iprRow.enabled = vray.isInitialized()
        isVFBIpr = VRayRenderEngine.iprRenderer and not isinstance(VRayRenderEngine.iprRenderer, VRayRendererVantageLiveLink)
        if not isVFBIpr:
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
    bl_ui_units_x = 16

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        layout.label(text="V-Ray Options")
        layout.separator(factor = 1)
        layout.scale_x = 0.05 # This scale makes all the labels visible

        layout.prop(context.scene.vray, "viewport_resolution_override", expand=True)

        world = context.scene.world

        vrayExporter = context.scene.vray.Exporter

        denoiserColumn = layout.column()
        denoiserColumn.prop(vrayExporter, "viewport_denoiser_enabled", text="Viewport Denoising")

        viewportDenoiserControls = denoiserColumn.column()
        viewportDenoiserControls.enabled = vrayExporter.viewport_denoiser_enabled
        viewportDenoiserControls.prop(vrayExporter, "viewport_denoiser_engine", text="Denoiser Engine")

        channelsNode = getChannelsOutputNode(world.node_tree) if (world and world.node_tree) else None
        hasDenoiserNode = channelsNode is not None and \
                            any(sock for sock in channelsNode.inputs if sockConnectedToDenoiser(sock))

        linkedDenoiserActive = hasDenoiserNode

        linkedRow = viewportDenoiserControls.row()
        linkedRow.enabled = linkedDenoiserActive
        linkedRow.prop(vrayExporter, "linked_denoiser", text="Linked Denoiser")


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