# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy

from vray_blender.ui import classes
from vray_blender.lib import lib_utils
from vray_blender.lib.mixin import VRayOperatorBase
from vray_blender.lib.path_utils import PATH_PLACEHOLDERS, PATH_DIR_PLACEHOLDERS
from vray_blender.plugins import getPluginModule
from vray_blender.utils.utils_bake import VRAY_PT_Bake
from vray_blender.bin import VRayBlenderLib as vray

# Import stock Blender panels
from bl_ui import properties_output as BlenderOutput


class VRAY_OT_insert_path_placeholder(VRayOperatorBase):
    """ Append a path placeholder (e.g. $frame) to one of the output path string
        properties. Driven by the per-input dropdown next to path/filename fields.
    """
    bl_idname      = "vray.insert_path_placeholder"
    bl_label       = "Insert Path Placeholder"
    bl_description = "Insert this placeholder into the path"
    bl_options     = {'INTERNAL', 'UNDO'}

    placeholder:       bpy.props.StringProperty()
    target_attr:       bpy.props.StringProperty()
    target_prop_group: bpy.props.StringProperty(default="SETTINGS_OUTPUT")
    tooltip:           bpy.props.StringProperty()

    @classmethod
    def description(cls, context, properties):
        return properties.tooltip or cls.bl_description

    def execute(self, context):
        if self.target_prop_group == "BAKE":
            propGroup = context.scene.vray.BatchBake.getSelectedItem()
        elif self.target_prop_group == "EXPORTER":
            propGroup = context.scene.vray.Exporter
        else:
            propGroup = context.scene.vray.SettingsOutput
        currVal = getattr(propGroup, self.target_attr, "")
        separator = "_" if currVal and currVal[-1].isalnum() else ""
        setattr(propGroup, self.target_attr, currVal + separator + self.placeholder)
        return {'FINISHED'}


# Bake-specific placeholder (object name) shown in addition to the common list.
_BAKE_EXTRA_PLACEHOLDER = ('$object', "Object Name", "Name of the object being baked")


class VRAY_MT_path_placeholders(bpy.types.Menu):
    """ Menu listing all path placeholders supported in the Output Path and Filename
        inputs. The target attribute and prop group are set on the class before
        wm.call_menu is invoked.
    """
    bl_idname = "VRAY_MT_path_placeholders"
    bl_label  = "Path Placeholders"

    target_attr       = ""
    target_prop_group = "SETTINGS_OUTPUT"

    def draw(self, context):
        layout = self.layout
        is_dir = VRAY_MT_path_placeholders.target_attr.endswith('_dir')
        base = PATH_DIR_PLACEHOLDERS if is_dir else PATH_PLACEHOLDERS
        if VRAY_MT_path_placeholders.target_prop_group == "BAKE":
            base = (*base, _BAKE_EXTRA_PLACEHOLDER)
        placeholders = base
        for placeholder, name, desc in sorted(placeholders, key=lambda p: p[1]):
            op = layout.operator(VRAY_OT_insert_path_placeholder.bl_idname,
                                 text=f"{name} - {placeholder}")
            op.placeholder       = placeholder
            op.target_attr       = VRAY_MT_path_placeholders.target_attr
            op.target_prop_group = VRAY_MT_path_placeholders.target_prop_group
            op.tooltip           = desc


class VRAY_OT_show_path_placeholders(VRayOperatorBase):
    """ Open the placeholders menu for a specific string path attribute. """
    bl_idname      = "vray.show_path_placeholders"
    bl_label       = "Path Placeholders"
    bl_description = "Show available placeholders that can be inserted into the path"
    bl_options     = {'INTERNAL'}

    target_attr:       bpy.props.StringProperty()
    target_prop_group: bpy.props.StringProperty(default="SETTINGS_OUTPUT")

    def execute(self, context):
        VRAY_MT_path_placeholders.target_attr       = self.target_attr
        VRAY_MT_path_placeholders.target_prop_group = self.target_prop_group
        bpy.ops.wm.call_menu(name=VRAY_MT_path_placeholders.bl_idname)
        return {'FINISHED'}


def _drawPathPropWithPlaceholders(layout, propGroup, attr, text, target_prop_group="SETTINGS_OUTPUT"):
    row = layout.row(align=True)
    row.prop(propGroup, attr, text=text)
    op = row.operator(VRAY_OT_show_path_placeholders.bl_idname, text="", icon='DOWNARROW_HLT')
    op.target_attr       = attr
    op.target_prop_group = target_prop_group


class VRAY_PT_Output(classes.VRayOutputPanel):
    """ PROPERTIES->Output->Dimensions panel """
    bl_label = "Image Output"
    bl_icon = "NONE"
    vray_icon = "VRAY_PLACEHOLDER"

    def drawPanelCheckBox(self, context):
        VRayExporter = context.scene.vray.Exporter
        self.layout.prop(VRayExporter, 'auto_save_render', text="")

    def draw(self, context):
        layout = self.layout

        scene = context.scene

        VRayScene      = scene.vray
        VRayExporter   = VRayScene.Exporter
        SettingsOutput = VRayScene.SettingsOutput

        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.enabled = VRayExporter.auto_save_render

        _drawPathPropWithPlaceholders(layout, SettingsOutput, 'img_dir',  "Output Path")
        _drawPathPropWithPlaceholders(layout, SettingsOutput, 'img_file', "Filename")


class VRAY_PT_OutputFormat(classes.VRayOutputPanel):
    bl_label     = "File Format"
    bl_parent_id = "VRAY_PT_Output"

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        VRayScene      = context.scene.vray
        VRayExporter   = VRayScene.Exporter
        SettingsOutput = VRayScene.SettingsOutput

        layout.enabled = VRayExporter.auto_save_render

        layout.prop(SettingsOutput, 'img_format', text="Format")

        formatPropGroupName = lib_utils.FormatToSettings[SettingsOutput.img_format]
        imgFormatPropGroup  = getattr(VRayScene, formatPropGroupName)
        classes.drawPluginUI(context, layout, imgFormatPropGroup, getPluginModule(formatPropGroupName))

        if SettingsOutput.img_format in {'5', '6'}:
            layout.prop(SettingsOutput, 'img_deepFile')

        layout.separator()

        layout.prop(SettingsOutput, 'img_noAlpha',               text="Don't Save Alpha Channel")
        layout.prop(SettingsOutput, 'img_separateAlpha',         text="Output Alpha to Separate File")
        layout.prop(SettingsOutput, 'relements_separateFolders', text="Relements to Separate Folders")
        if SettingsOutput.img_format in {'5', '6'}:
            layout.prop(SettingsOutput, 'relements_separateFiles', text="Relements to Separate Files")
        layout.prop(SettingsOutput, 'img_file_needFrameNumber',  text="Always Write Frame Number")


class VRAY_PT_ResumableRendering(classes.VRayOutputPanel):
    bl_label   = "Resumable Rendering"
    bl_parent_id = "VRAY_PT_Output"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll_custom(cls, context):
        # Resumable Rendering is not supported in the Community Edition.
        return not vray.isCommunityEdition()

    def draw_header(self, context):
        SettingsOutput = context.scene.vray.SettingsOutput
        self.layout.prop(SettingsOutput, 'resumable_rendering', text="")

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False

        VRayScene      = context.scene.vray
        SettingsOutput = VRayScene.SettingsOutput
        isProgressive  = VRayScene.SettingsImageSampler.type == '3'

        layout.enabled = SettingsOutput.resumable_rendering
        layout.prop(SettingsOutput, 'resumable_delete_on_success')
        row = layout.row()
        row.enabled = SettingsOutput.resumable_rendering and isProgressive
        row.prop(SettingsOutput, 'resumable_autosave_interval')


class VRAY_PT_VRayStereoscopicSettings(classes.VRayOutputPanel):
    bl_label = "Stereoscopy"
    bl_icon = "NONE"
    vray_icon = "VRAY_PLACEHOLDER"
    bl_options = {'DEFAULT_CLOSED'}

    def drawPanelCheckBox(self, context):
        vrayExporter= context.scene.vray.Exporter
        self.layout.prop(vrayExporter, 'use_stereo', text="")

    def draw(self, context):
        layout = self.layout

        vrayScene = context.scene.vray
        vrayStereoscopicSettings = vrayScene.VRayStereoscopicSettings

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
    vray_icon = "VRAY_PLACEHOLDER"

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False  # No animation.

        scene = context.scene

        vrayExporter = scene.vray.Exporter
        layout.prop(vrayExporter, 'animation_mode', text="Output")
        layout.separator()

        col = layout.column(align=True)
        col.enabled = vrayExporter.animation_mode == 'ANIMATION'
        col.prop(vrayExporter, "use_frame_range")

        if vrayExporter.use_frame_range:
            col.prop(scene, "frame_start", text="Frame Start")
            col.prop(scene, "frame_end", text="End")
            col.prop(scene, "frame_step", text="Step")
        else:
            col.prop(vrayExporter, "frames_list")

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
        VRAY_OT_insert_path_placeholder,
        VRAY_OT_show_path_placeholders,
        VRAY_MT_path_placeholders,
        VRAY_PT_FrameRange,
        VRAY_PT_TimeStretching,
        VRAY_PT_Output,
        VRAY_PT_OutputFormat,
        VRAY_PT_ResumableRendering,
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
