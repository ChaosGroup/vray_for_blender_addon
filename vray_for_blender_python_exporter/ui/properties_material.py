# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later


import bpy

from vray_blender.ui      import classes
from vray_blender.nodes import utils as NodesUtils
from vray_blender.plugins import PLUGINS, getPluginModule
from vray_blender.ui.icons import getUIIcon
from vray_blender.menu import VRAY_OT_convert_materials

def getMaterialPanelNode(tree):
    node = NodesUtils.getActiveTreeNode(tree, "MATERIAL")
    if node:
        return node

    output = NodesUtils.getOutputNode(tree, "MATERIAL")
    if not output:
        return None

    link = NodesUtils.getFarNodeLink(output.inputs["Material"])
    return link.from_node if link else None

def renderMaterialPanel(mtl, context, layout: bpy.types.UILayout):
    assert mtl.vray.is_vray_class, "Can draw property pages for V-Ray materials only"

    if not (activeNode := getMaterialPanelNode(mtl.node_tree)):
        return

    layout.use_property_split = True
    layout.use_property_decorate = True
    box = layout.box()
    box.label(text=f'  {activeNode.bl_label}')
    layout.separator()

    layout.prop(mtl, "diffuse_color", text="Viewport Color")

    layout.separator()

    if activeNode.bl_idname == 'VRayNodeOutputMaterial':
        layout.separator()
        # The material options (MtlMaterialID etc.) will be drawn below
    elif hasattr(activeNode, "draw_buttons_ext"):
        activeNode.draw_buttons_ext(context, layout)
    else:
        classes.drawNodePanel(context, layout, activeNode, PLUGINS)


def renderMaterialOptionsPanel(mtl, context, layout):
    assert  mtl.vray.is_vray_class, "Can draw property pages for V-Ray materials only"

    if not NodesUtils.treeHasNodes(mtl.node_tree):
        return

    container = layout.column(align=True)
    classes.drawPluginUI(context, container, mtl.vray.MtlRenderStats, getPluginModule('MtlRenderStats'))


def renderMaterialSelector(layout: bpy.types.UILayout, obj: bpy.types.Object):
    mtl = obj.active_material

    row = layout.row(align=True)

    if mtl:
        if mtl.vray.is_vray_class:
            if NodesUtils.getOutputNode(mtl.node_tree, 'MATERIAL') is not None:
                row.template_ID(obj, "active_material", new="vray.copy_material")
        else:
            row.template_ID(obj, "active_material", new="material.new")
            layout.operator("vray.replace_nodetree_material", icon="NODETREE", text="Use V-Ray Material Nodes")
            if mtl.use_nodes:
                layout.operator("vray.convert_nodetree_material", icon_value=getUIIcon(VRAY_OT_convert_materials), text="Convert to V-Ray Material")
    else:
        row.template_ID(obj, "active_material", new="vray.add_new_material")
    row.menu("VRAY_MT_material_add_popup", icon='DOWNARROW_HLT', text="")


class VRAY_MT_material_add_popup(bpy.types.Menu):
    bl_label = "Add Material"
    bl_idname = "VRAY_MT_material_add_popup"

    def draw(self, context):
        layout = self.layout

        from vray_blender.ui import icons

        def addOp(nodeType, label, icon=None):
            iconVal = icons.getIcon(icon) if icon else 0
            op = layout.operator("vray.add_new_material", text=label, icon_value=iconVal)
            op.nodeType = nodeType
            op.nodeLabel = label

        addOp('VRayNodeBRDFVRayMtl', 'V-Ray Mtl', icon='MTL_VRAY')
        addOp('VRayNodeBRDFLayered', 'V-Ray Blend Mtl', icon='MTL_BLEND')
        addOp('VRayNodeMtlDisplacement', 'V-Ray Displacement Mtl', icon='MTL_DISPLACEMENT')
        addOp('VRayNodeBRDFLight', 'V-Ray Light Mtl', icon='MTL_LIGHT')
        addOp('VRayNodeBRDFAlSurface', 'V-Ray AL Surface Mtl', icon='MTL_AL_SURFACE')
        addOp('VRayNodeBRDFSSS2Complex', 'V-Ray Fast SSS2', icon='MTL_FAST_SSS2')
        addOp('VRayNodeBRDFBump', 'V-Ray Bump Mtl', icon='MTL_BUMP')
        addOp('VRayNodeBRDFHair4', 'V-Ray Hair Next Mtl', icon='MTL_HAIR_NEXT')
        addOp('VRayNodeBRDFCarPaint2', 'V-Ray Car Paint 2 Mtl', icon='MTL_CAR_PAINT2')
        addOp('VRayNodeBRDFFlakes2', 'V-Ray Flakes 2 Mtl', icon='MTL_FLAKES')
        addOp('VRayNodeBRDFScanned', 'V-Ray Scanned Mtl', icon='MTL_SCANNED')
        addOp('VRayNodeBRDFStochasticFlakes', 'V-Ray Stochastic Flakes Mtl', icon='MTL_STOCHASTIC_FLAKES')
        addOp('VRayNodeBRDFToonMtl', 'V-Ray Toon Mtl', icon='MTL_TOON')
        addOp('VRayNodeMtlMulti', 'V-Ray Switch Mtl', icon='MTL_SWITCH')
        addOp('VRayNodeMtl2Sided', 'V-Ray Mtl 2Sided', icon='MTL_2SIDED')
        addOp('VRayNodeMtlOverride', 'V-Ray Mtl Override', icon='MTL_OVERRIDE')
        addOp('VRayNodeMtlVRmat', 'V-Ray VRmat Mtl', icon='MTL_VRMAT')

class VRAY_PT_context_material(classes.VRayMaterialPanel):
    bl_label = ""
    bl_options = {'HIDE_HEADER'}

    @classmethod
    def poll(cls, context):
        return (context.material or context.object) and classes.pollBase(cls, context)

    def draw(self, context):
        layout = self.layout

        mat = context.material

        ob = context.object
        slot = context.material_slot
        space = context.space_data

        if ob:
            row = layout.row()

            row.template_list("VRAY_UL_MaterialSlots", "", ob, "material_slots", ob, "active_material_index", rows=4)

            col = row.column(align=True)
            col.operator("object.material_slot_add", icon='ADD', text="")
            col.operator("object.material_slot_remove", icon='REMOVE', text="")

            col.menu("MATERIAL_MT_context_menu", icon='DOWNARROW_HLT', text="")

            if ob.mode == 'EDIT':
                row = layout.row(align=True)
                row.operator("object.material_slot_assign", text="Assign")
                row.operator("object.material_slot_select", text="Select")
                row.operator("object.material_slot_deselect", text="Deselect")

        if ob:
            renderMaterialSelector(layout, ob)
        elif mat:
            layout.template_ID(space, "pin_id")


class VRAY_PT_preview(classes.VRayMaterialPanel):
    bl_label = "Preview"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        self.layout.template_preview(context.material, show_buttons=True)


class VRAY_PT_material(classes.VRayMaterialPanel):
    bl_label = "Material"
    bl_idname = "VRAY_PT_material"
    bl_options = set()

    @classmethod
    def poll_custom(cls, context):
        return context.material

    def draw(self, context):
        if (mtl := context.material) and mtl.vray.is_vray_class:
            renderMaterialPanel(mtl, context, self.layout)


class VRAY_PT_mtl_material_render_stats(classes.VRayMaterialPanel):
    bl_label = "Render stats"
    bl_parent_id = "VRAY_PT_material"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll_custom(cls, context):
        if context.material:
            sel = [n for n in context.material.node_tree.nodes if n.select]
            return sel and (sel[0].bl_idname == 'VRayNodeOutputMaterial')
        return False

    def drawPanelCheckBox(self, context):
        mtl = context.material
        self.layout.label(text="")
        self.layout.prop(mtl.vray.MtlRenderStats, 'use', text="")

    def draw(self, context):
        split = self.layout.split(factor=0.05, align=True)

        enabled = context.material.vray.MtlRenderStats.use
        split.enabled = enabled
        split.active = enabled

        split.column()
        col = split.column()
        propGroup = context.material.vray.MtlRenderStats
        classes.drawPluginUI(context, col, propGroup, getPluginModule('MtlRenderStats'))


class VRAY_PT_mtl_material_wrapper(classes.VRayMaterialPanel):
    bl_label = "Wrapper"
    bl_parent_id = "VRAY_PT_material"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll_custom(cls, context):
        if context.material:
            sel = [n for n in context.material.node_tree.nodes if n.select]
            return sel and (sel[0].bl_idname == 'VRayNodeOutputMaterial')
        return False

    def drawPanelCheckBox(self, context):
        mtl = context.material
        self.layout.label(text="")
        self.layout.prop(mtl.vray.MtlWrapper, 'use', text="")

    def draw(self, context):
        split = self.layout.split(factor=0.05, align=True)

        enabled = context.material.vray.MtlWrapper.use
        split.enabled = enabled
        split.active = enabled

        split.column()
        col = split.column()
        propGroup = context.material.vray.MtlWrapper
        classes.drawPluginUI(context, col, propGroup, getPluginModule('MtlWrapper'))


class VRAY_PT_material_id(classes.VRayMaterialPanel):
    bl_label = "Material ID"
    bl_parent_id = "VRAY_PT_material"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll_custom(cls, context):
        if context.material:
            sel = [n for n in context.material.node_tree.nodes if n.select]
            return sel and (sel[0].bl_idname == 'VRayNodeOutputMaterial')
        return False


    def drawPanelCheckBox(self, context):
        mtl = context.material
        self.layout.label(text="")
        self.layout.prop(mtl.vray.MtlMaterialID, 'use', text="")

    def draw(self, context):
        split = self.layout.split(factor=0.05, align=True)

        enabled = context.material.vray.MtlMaterialID.use
        split.enabled = enabled
        split.active = enabled

        split.column()
        col = split.column()
        propGroup = context.material.vray.MtlMaterialID
        classes.drawPluginUI(context, col, propGroup, getPluginModule('MtlMaterialID'))


class VRAY_PT_mtl_material_round_edges(classes.VRayMaterialPanel):
    bl_label = "Round Edges"
    bl_parent_id = "VRAY_PT_material"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll_custom(cls, context):
        if context.material:
            sel = [n for n in context.material.node_tree.nodes if n.select]
            return sel and (sel[0].bl_idname == 'VRayNodeOutputMaterial')
        return False

    def drawPanelCheckBox(self, context):
        mtl = context.material
        self.layout.label(text="")
        self.layout.prop(mtl.vray.MtlRoundEdges, 'use', text="")

    def draw(self, context):
        split = self.layout.split(factor=0.05, align=True)

        enabled = context.material.vray.MtlRoundEdges.use
        split.enabled = enabled
        split.active = enabled

        split.column()
        col = split.column()
        propGroup = context.material.vray.MtlRoundEdges
        classes.drawPluginUI(context, col, propGroup, getPluginModule('MtlRoundEdges'))



def getRegClasses():
    return (
        VRAY_MT_material_add_popup,
        VRAY_PT_preview,
        VRAY_PT_context_material,
        VRAY_PT_material,
        VRAY_PT_material_id,
        VRAY_PT_mtl_material_render_stats,
        VRAY_PT_mtl_material_wrapper,
        VRAY_PT_mtl_material_round_edges
    )


def register():
    from vray_blender.lib.class_utils import registerClass
    for regClass in getRegClasses():
        registerClass(regClass)


def unregister():
    for regClass in reversed(getRegClasses()):
        bpy.utils.unregister_class(regClass)
