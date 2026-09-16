# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later


import bpy

from vray_blender import features
from vray_blender.features import Feature
from vray_blender.ui import classes
from vray_blender.lib.draw_utils import UIPainter
from vray_blender.plugins import getPluginModule


def drawEmptyGeometryMaterialSelector(layout: bpy.types.UILayout, obj: bpy.types.Object):
    """ The material selector of an Empty-backed geometry object. """
    row = layout.row(align=True)
    newOp = "vray.copy_material" if obj.vray.material else "vray.add_new_material"
    row.template_ID(obj.vray, 'material', new=newOp)
    row.menu("VRAY_MT_material_add_popup", icon='DOWNARROW_HLT', text="")


def drawEmptyGeometryMaterial(layout: bpy.types.UILayout, context):
    """ The material of an Empty-backed geometry object.

        Blender allows an Empty no material slots at all, so the material is held in a pointer on
        the object and its node tree is drawn here rather than in the Material tab. V-Ray for
        RenderMan solves the same problem the same way for its Empty-backed primitives.
    """
    obj = context.object

    drawEmptyGeometryMaterialSelector(layout, obj)
    if obj.vray.material is None:
        return

    if obj.vray.material.vray.is_vray_class:
        from vray_blender.ui.properties_material import renderMaterialPanel
        # The generic material UI reads context.material.
        layout.context_pointer_set('material', obj.vray.material)
        renderMaterialPanel(obj.vray.material, context, layout)


class VRAY_PT_VRayGaussians(classes.VRayDataPanel):
    """ Object-data panel for V-Ray Gaussian splat objects (Empties).

        The whole UI (General / Lighting / Animation / Clipping / Viewport Preview) is described
        by the GeomGaussians Widget in GeomGaussians.custom.json and rendered by the UIPainter.
    """
    bl_label  = "V-Ray Gaussians"
    bl_idname = "VRAY_PT_VRayGaussians"
    vray_icon = "VRAY_PLACEHOLDER"

    @classmethod
    def poll(cls, context):
        obj = context.object
        return (obj is not None
                and obj.type == 'EMPTY'
                and obj.vray.isVRayGaussian
                and classes.VRayDataPanel.poll(context))

    def draw(self, context):
        propGroup = context.object.vray.GeomGaussians
        pluginModule = getPluginModule('GeomGaussians')
        UIPainter(context, pluginModule, propGroup).renderPluginUI(self.layout)


class VRAY_PT_VRayInfinitePlane(classes.VRayDataPanel):
    """ Object-data panel for V-Ray infinite plane objects (Empties). """
    bl_label  = "V-Ray Infinite Plane"
    bl_idname = "VRAY_PT_VRayInfinitePlane"
    vray_icon = "VRAY_PLACEHOLDER"

    @classmethod
    def poll(cls, context):
        obj = context.object
        return (obj is not None
                and obj.type == 'EMPTY'
                and obj.vray.isVRayInfinitePlane
                and classes.VRayDataPanel.poll(context))

    def draw(self, context):
        propGroup = context.object.vray.GeomPlane
        UIPainter(context, getPluginModule('GeomPlane'), propGroup).renderPluginUI(self.layout)


class VRAY_PT_VRayInfinitePlaneMaterial(classes.VRayDataPanel):
    """ Material of an infinite plane, which as an Empty has no material slot. """
    bl_label  = "Material"
    bl_idname = "VRAY_PT_VRayInfinitePlaneMaterial"
    bl_parent_id = "VRAY_PT_VRayInfinitePlane"
    vray_icon = "VRAY_PLACEHOLDER"

    @classmethod
    def poll(cls, context):
        return VRAY_PT_VRayInfinitePlane.poll(context)

    def draw(self, context):
        drawEmptyGeometryMaterial(self.layout, context)


class VRAY_PT_VRayPerfectSphere(classes.VRayDataPanel):
    """ Object-data panel for V-Ray perfect sphere objects (Empties). """
    bl_label  = "V-Ray Perfect Sphere"
    bl_idname = "VRAY_PT_VRayPerfectSphere"
    vray_icon = "VRAY_PLACEHOLDER"

    @classmethod
    def poll(cls, context):
        obj = context.object
        return (obj is not None
                and obj.type == 'EMPTY'
                and obj.vray.isVRayPerfectSphere
                and classes.VRayDataPanel.poll(context))

    def draw(self, context):
        propGroup = context.object.vray.GeomPerfectSphere
        UIPainter(context, getPluginModule('GeomPerfectSphere'), propGroup).renderPluginUI(self.layout)


class VRAY_PT_VRayPerfectSphereMaterial(classes.VRayDataPanel):
    """ Material of a perfect sphere, which as an Empty has no material slot. """
    bl_label  = "Material"
    bl_idname = "VRAY_PT_VRayPerfectSphereMaterial"
    bl_parent_id = "VRAY_PT_VRayPerfectSphere"
    vray_icon = "VRAY_PLACEHOLDER"

    @classmethod
    def poll(cls, context):
        return VRAY_PT_VRayPerfectSphere.poll(context)

    def draw(self, context):
        drawEmptyGeometryMaterial(self.layout, context)


def register():
    # V-Ray Gaussians is an in-development feature gated behind a flag.
    if features.isEnabled(Feature.GAUSSIAN_SPLATS):
        bpy.utils.register_class(VRAY_PT_VRayGaussians)

    bpy.utils.register_class(VRAY_PT_VRayInfinitePlane)
    bpy.utils.register_class(VRAY_PT_VRayInfinitePlaneMaterial)
    bpy.utils.register_class(VRAY_PT_VRayPerfectSphere)
    bpy.utils.register_class(VRAY_PT_VRayPerfectSphereMaterial)


def unregister():
    if features.isEnabled(Feature.GAUSSIAN_SPLATS):
        bpy.utils.unregister_class(VRAY_PT_VRayGaussians)

    bpy.utils.unregister_class(VRAY_PT_VRayPerfectSphereMaterial)
    bpy.utils.unregister_class(VRAY_PT_VRayPerfectSphere)
    bpy.utils.unregister_class(VRAY_PT_VRayInfinitePlaneMaterial)
    bpy.utils.unregister_class(VRAY_PT_VRayInfinitePlane)
