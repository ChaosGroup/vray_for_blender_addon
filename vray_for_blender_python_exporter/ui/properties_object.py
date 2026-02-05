
import bpy

from vray_blender.ui import classes
from vray_blender.plugins import getPluginModule
from vray_blender.lib import blender_utils
from vray_blender.lib import draw_utils


class VRAY_PT_context_node(classes.VRayObjectPanel):
    bl_label = ""
    bl_options = {'HIDE_HEADER'}

    @classmethod
    def poll_custom(cls, context):
        return context.object.type not in blender_utils.NonGeometryTypes

    def draw(self, context):
        VRayObject = context.object.vray

        classes.ntreeWidget(self.layout, VRayObject, "Object Tree", "vray.add_nodetree_object", 'OBJECT')


class VRAY_PT_VRayPattern(classes.VRayObjectPanel):
    bl_label   = "VRayPattern"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll_custom(cls, context):
        return context.scene.vray.Exporter.experimental

    def drawPanelCheckBox(self, context):
        ob = context.object
        VRayObject = ob.vray
        GeomVRayPattern = VRayObject.GeomVRayPattern
        self.layout.prop(GeomVRayPattern, 'use', text="")

    def draw(self, context):
        wide_ui = context.region.width > classes.narrowui

        ob = context.object
        VRayObject = ob.vray
        GeomVRayPattern = VRayObject.GeomVRayPattern

        layout = self.layout
        layout.active = GeomVRayPattern.use

        split = layout.split()
        split.label(text="Pattern Object:")
        split.prop_search(GeomVRayPattern, 'pattern_object',
                          context.scene,   'objects',
                          text="")

        layout.separator()

        layout.prop(GeomVRayPattern, 'crop_size')
        layout.operator('vray.pattern_fit', icon='MOD_MESHDEFORM')
        layout.separator()

        split = layout.split()
        col = split.column()
        col.prop(GeomVRayPattern, 'height')
        if wide_ui:
            col = split.column()
        col.prop(GeomVRayPattern, 'shift')

        layout.separator()

        layout.prop(GeomVRayPattern, 'use_real_world')
        split = layout.split()
        col = split.column()
        col.prop(GeomVRayPattern, 'tiling_u', text="U")
        if wide_ui:
            col = split.column()
        col.prop(GeomVRayPattern, 'tiling_v', text="V")

        layout.label(text="Polygon ID:")
        split = layout.split()
        col = split.column()
        col.prop(GeomVRayPattern, 'polygon_id_from', text="From")
        if wide_ui:
            col = split.column()
        col.prop(GeomVRayPattern, 'polygon_id_to', text="To")

        layout.label(text="Random Segment Count:")
        split = layout.split()
        col = split.column()
        col.prop(GeomVRayPattern, 'random_segment_u', text="U")
        if wide_ui:
            col = split.column()
        col.prop(GeomVRayPattern, 'random_segment_v', text="V")

        layout.prop(GeomVRayPattern, 'random_segment_seed')

        layout.separator()

        split = layout.split()
        col = split.column()
        col.prop(GeomVRayPattern, 'render_base_object')
        if wide_ui:
            col = split.column()
        col.prop(GeomVRayPattern, 'render_pattern_object')


class VRAY_PT_object_properties(classes.VRayObjectPanel):
    bl_label = "Object Properties"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll_custom(cls, context):
        return context.material or (context.object and not context.object.vray.isVRayDecal)

    def draw(self, context):
        propGroup = context.object.vray.VRayObjectProperties

        col = self.layout.column()
        col.use_property_split = True
        col.prop(propGroup, 'objectID')

        split = self.layout.split(factor=0.1, align=True)

        split.column()
        col = split.column()

        classes.drawPluginUI(context, col, propGroup, getPluginModule('VRayObjectProperties'))

        col = col.column()
        col.enabled = any(s for s in context.object.material_slots if s.material and s.material.node_tree and s.material.vray.is_vray_class)
        propGroup = context.object.vray.MtlRoundEdges
        roundEdgesModule = getPluginModule('MtlRoundEdges')
        uiPainter = draw_utils.UIPainter(context, roundEdgesModule, propGroup)
        uiPainter.renderWidgetsSection(col, 'sidebar')

class VRAY_PT_object_motion_blur(classes.VRayObjectPanel):
    bl_label = "Motion Blur"
    bl_options = {'DEFAULT_CLOSED'}
    incompatTypes  = {'CAMERA', 'SPEAKER', 'ARMATURE'}

    def draw(self, context):
        col = self.layout.column()
        col.use_property_split = True
        propGroup = context.object.vray.VRayObjectProperties
        col.prop(propGroup, "override_motion_blur_samples", text="Override samples")

        samplesRow = col.row()
        samplesRow.active = propGroup.override_motion_blur_samples
        samplesRow.prop(propGroup, "motion_blur_samples", text="Samples")


class VRAY_PT_VRayClipper(classes.VRayObjectPanel):
    bl_label = "Clipper"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        obj = context.object
        nonClipperObject = obj.vray.isVRayDecal or obj.vray.isVRayFur
        return obj and (obj.type not in cls.incompatTypes) and classes.VRayObjectPanel.poll(context) and not nonClipperObject

    def drawPanelCheckBox(self, context):
        vrayClipper = context.object.vray.VRayClipper
        self.layout.prop(vrayClipper, 'clipper_enabled', text="")

    def draw(self, context):
        layout  = self.layout
        vrayClipper = context.object.vray.VRayClipper

        layout.active = vrayClipper.enabled
        layout.alignment = 'CENTER'

        split = layout.split(align = True)
        col = split.column()
        col.prop(vrayClipper, 'affect_light')
        col.prop(vrayClipper, 'clip_lights')

        col.prop(vrayClipper, 'only_camera_rays')
        col.prop(vrayClipper, 'use_obj_mtl')
        col = layout.column()
        col.active = not vrayClipper.set_material_id and not vrayClipper.use_obj_mtl
        col.prop(vrayClipper, 'selectedMaterial')

        col.prop(vrayClipper, 'use_obj_mesh')
        col = layout.column()
        col.active = vrayClipper.use_obj_mesh
        col.prop(vrayClipper, 'invert_inside')

        col = layout.column(align=True)
        col.active = True
        col.prop(vrayClipper, 'exclusion_mode', text="As exclusive set")
        searchBoxLabel = "Exclude" if vrayClipper.exclusion_mode else "Include"
        col.prop_search(vrayClipper, 'exclusion_nodes_ptr', bpy.data, 'collections', text=searchBoxLabel)

        # TODO: Don't knoe how to export this parameter, is it even used by VRay
        # when setting clipper materials?

        # layout.prop(vrayClipper, 'set_material_id')
        # col = layout.column()
        # col.active = vrayClipper.set_material_id and not vrayClipper.use_obj_mtl
        # col.prop(vrayClipper, 'material_id')


class VRAY_PT_UserAttributes(classes.VRayObjectPanel, bpy.types.Panel):
    bl_label = "User Attributes"
    bl_options = {'DEFAULT_CLOSED'}

    incompatTypes  = {'CAMERA', 'SPEAKER', 'ARMATURE'}

    @classmethod
    def poll(cls, context):
        obj = context.object
        return obj and (obj.type not in cls.incompatTypes) and \
            (obj.type != 'LIGHT' or obj.data.vray.light_type == 'MESH') and \
            classes.pollBase(cls, context)


    def draw(self, context):
        layout = self.layout
        ob = context.object

        ua = ob.vray.UserAttributes

        row = layout.row()
        row.template_list('VRAY_UL_UserAttributes',
            "",
            ua, 'user_attributes',
            ua, 'user_attributes_selected',
            rows = 4)

        col = row.column()
        sub = col.row()
        subsub = sub.column(align=True)
        subsub.operator('vray.user_attribute_add', text="", icon='ADD')
        subsub.operator('vray.user_attribute_del', text="", icon='REMOVE')

        selectedItem     = ua.user_attributes_selected
        haveSelectedItem = selectedItem >= 0 and len(ua.user_attributes)
        if haveSelectedItem:
            user_attribute = ua.user_attributes[selectedItem]

            box = layout.box()
            box.label(text=f"Assign '{user_attribute.name}' To Selection")
            box.prop(ua, 'user_attributes_rnd_use')
            box_split = box.split()
            box_split.active = ua.user_attributes_rnd_use
            if user_attribute.value_type == '0':
                sub = box_split.row(align=True)
                sub.prop(ua, 'user_attributes_int_rnd_min')
                sub.prop(ua, 'user_attributes_int_rnd_max')
            elif user_attribute.value_type == '1':
                sub = box_split.row(align=True)
                sub.prop(ua, 'user_attributes_float_rnd_min')
                sub.prop(ua, 'user_attributes_float_rnd_max')
            box.operator('vray.user_attribute_assign_to_selected')


class VRAY_PT_Advanced(classes.VRayObjectPanel):
    """ !!! UNUSED """

    bl_label   = "Advanced"
    bl_options = {'DEFAULT_CLOSED'}

    def drawPanelCheckBox(self, context):
        self.layout.label(text="")

    def draw(self, context):
        VRayObject = context.object.vray

        box = self.layout.box()
        box.label(text="Duplication / Particles:")
        box.prop(VRayObject, 'dupliShowEmitter', text="Force Show Emitter")
        box.prop(VRayObject, 'dupliGroupIDOverride', text="Object ID Override")

        box = self.layout.box()
        box.label(text="Animation:")
        box.prop(VRayObject, 'subframes')


def getRegClasses():
    return (
        VRAY_PT_context_node,
        VRAY_PT_VRayPattern,

        VRAY_PT_object_properties,
        VRAY_PT_object_motion_blur,
        VRAY_PT_VRayClipper,

        VRAY_PT_UserAttributes,

        # TODO: fix functionality of the following panels
        # VRAY_PT_Advanced,
    )


def register():
    from vray_blender.lib.class_utils import registerClass
    for regClass in getRegClasses():
        registerClass(regClass)


def unregister():
    for regClass in reversed(getRegClasses()):
        bpy.utils.unregister_class(regClass)
