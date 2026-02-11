# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy

from vray_blender.ui import classes
from vray_blender.lib import lib_utils
from vray_blender.plugins import getPluginModule
from vray_blender.utils.utils_bake import VRAY_PT_Bake

# Import stock Blender panels
from bl_ui import properties_output as BlenderOutput


class VRAY_PT_Output(classes.VRayOutputPanel):
    """ PROPERTIES->Output->Dimensions panel """
    bl_label = "Output"

    def drawPanelCheckBox(self, context):
        VRayExporter = context.scene.vray.Exporter
        self.layout.prop(VRayExporter, 'auto_save_render', text="")

    def draw(self, context):
        layout = self.layout

        scene = context.scene

        VRayScene      = scene.vray
        VRayExporter   = VRayScene.Exporter
        SettingsOutput = VRayScene.SettingsOutput

        layout.active = VRayExporter.auto_save_render

        split = layout.split(factor=0.2)
        col = split.column()
        col.label(text="Output Path:")
        col.label(text="Filename:")
        col = split.column()
        col.prop(SettingsOutput, 'img_dir',  text="")
        col.prop(SettingsOutput, 'img_file', text="")

        layout.separator()

        split = layout.split()
        col = split.column()
        col.prop(SettingsOutput, 'img_format', text="Format")

        formatPropGroupName = lib_utils.FormatToSettings[SettingsOutput.img_format]

        imgFormatPropGroup = getattr(VRayScene, formatPropGroupName)

        classes.drawPluginUI(context, layout, imgFormatPropGroup, getPluginModule(formatPropGroupName))

        if SettingsOutput.img_format in {'5', '6'}:
            layout.prop(SettingsOutput, 'img_deepFile')

        layout.separator()

        layout.prop(SettingsOutput, 'img_noAlpha', text="Don't Save Alpha Channel")
        layout.prop(SettingsOutput, 'img_separateAlpha', text="Output Alpha to Separate File")
        layout.prop(SettingsOutput, 'relements_separateFolders', text="Relements to Separate Folders")
        if SettingsOutput.img_format in {'5', '6'}:
            layout.prop(SettingsOutput, 'relements_separateFiles', text="Relements to Separate Files")
        layout.prop(SettingsOutput, 'img_file_needFrameNumber', text="Always Write Frame Number")


class VRAY_PT_VRayStereoscopicSettings(classes.VRayOutputPanel):
    bl_label = "Stereoscopy"
    bl_options = {'DEFAULT_CLOSED'}

    def drawPanelCheckBox(self, context):
        vrayExporter= context.scene.vray.Exporter
        self.layout.prop(vrayExporter, 'use_stereo', text="")

    def draw(self, context):
        layout= self.layout

        vrayScene= context.scene.vray
        vrayStereoscopicSettings= vrayScene.VRayStereoscopicSettings

        self.layout.active = vrayScene.Exporter.use_stereo

        split = layout.split()
        col   = split.column()
        col.prop(vrayStereoscopicSettings, 'eye_distance')

        sub = col.row(align=True)
        sub_f = sub.column()
        sub_f.active = vrayStereoscopicSettings.specify_focus
        sub_f.prop(vrayStereoscopicSettings, 'focus_distance')
        sub.prop(vrayStereoscopicSettings, 'specify_focus', text="")

        split = layout.split()
        col   = split.column()
        col.prop(vrayStereoscopicSettings, 'focus_method', text="Focus")
        col.prop(vrayStereoscopicSettings, 'interocular_method', text="Interocular")
        col.prop(vrayStereoscopicSettings, 'view')

        # NOTE: Shademap is currently broken
        # layout.separator()
        # layout.prop(VRayStereoscopicSettings, 'sm_mode', text="Mode")
        # sub = layout.row()
        # sub.active = VRayStereoscopicSettings.sm_mode != '0'
        # sub.prop(VRayStereoscopicSettings, 'shademap_file', text="Shademap")
        # layout.prop(VRayStereoscopicSettings, 'reuse_threshold')

        #layout.separator()
        #layout.prop(VRayStereoscopicSettings, 'exclude_list')


class VRAY_PT_FrameRange(classes.VRayOutputPanel):
    bl_label = "Frame Range"

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False  # No animation.

        scene = context.scene

        vrayExporter = scene.vray.Exporter
        layout.prop(vrayExporter, 'animation_mode', text="Animation")
        layout.separator()

        col = layout.column(align=True)
        col.enabled = vrayExporter.animation_mode == 'ANIMATION'
        col.prop(scene, "frame_start", text="Frame Start")
        col.prop(scene, "frame_end", text="End")
        col.prop(scene, "frame_step", text="Step")


class VRAY_PT_TimeStretching(classes.VRayOutputPanel):
    bl_label = "Time Stretching"
    bl_parent_id = "VRAY_PT_FrameRange"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        from bpy.app.translations import contexts as i18n_contexts

        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False  # No animation.

        rd = context.scene.render

        col = layout.column(align=True)
        col.enabled = context.scene.vray.Exporter.animation_mode == 'ANIMATION'
        col.prop(rd, "frame_map_old", text="Old", text_ctxt=i18n_contexts.time)
        col.prop(rd, "frame_map_new", text="New", text_ctxt=i18n_contexts.time)


# A list of stock Blender panels to show
_REGISTERED_BLENDER_CLASSES = (
        BlenderOutput.RENDER_PT_format,
    )

def getRegClasses():
    return (
        VRAY_PT_FrameRange,
        VRAY_PT_TimeStretching,
        VRAY_PT_Output,
        VRAY_PT_VRayStereoscopicSettings,
        VRAY_PT_Bake
    )


def register():
    from vray_blender.lib.class_utils import registerClass, setVRayCompatibility

    for uiClass in _REGISTERED_BLENDER_CLASSES:
        setVRayCompatibility(uiClass, makeVRayCompatible=True)

    for regClass in getRegClasses():
        registerClass(regClass)


def unregister():
    from vray_blender.lib.class_utils import setVRayCompatibility

    for uiClass in _REGISTERED_BLENDER_CLASSES:
        setVRayCompatibility(uiClass, makeVRayCompatible=False)

    for regClass in reversed(getRegClasses()):
        bpy.utils.unregister_class(regClass)
