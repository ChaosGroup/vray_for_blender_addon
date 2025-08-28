
import bpy

from vray_blender.ui      import classes
from vray_blender.nodes import utils as NodesUtils
from vray_blender.plugins import PLUGINS, getPluginModule
from vray_blender.ui.icons import getUIIcon
from vray_blender.menu import VRAY_OT_convert_materials

def renderMaterialPanel(mtl, context, layout):
    assert  mtl.vray.is_vray_class, "Can draw property pages for V-Ray materials only"

    if not (activeNode := NodesUtils.getActiveTreeNode(mtl.node_tree, 'MATERIAL')):
        return

    layout.label(text=f"Node:  {activeNode.name}")
    layout.label(text=f"Node type:  {activeNode.bl_label}")
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
    
    if mtl:
        if mtl.vray.is_vray_class:
            if NodesUtils.getOutputNode(mtl.node_tree, 'MATERIAL') is not None:
                layout.template_ID(obj, "active_material", new="vray.copy_material")
        else:
            layout.operator("vray.replace_nodetree_material", icon="NODETREE", text="Use V-Ray Material Nodes")
            if mtl.use_nodes:
                layout.operator("vray.convert_nodetree_material", icon_value=getUIIcon(VRAY_OT_convert_materials), text="Convert to V-Ray Material")
    else:
            layout.template_ID(obj, "active_material", new="vray.add_new_material")

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
        sel = [n for n in context.material.node_tree.nodes if n.select]
        return context.material and sel and (sel[0].bl_idname == 'VRayNodeOutputMaterial')

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
        sel = [n for n in context.material.node_tree.nodes if n.select]
        return context.material and sel and (sel[0].bl_idname == 'VRayNodeOutputMaterial')


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
        sel = [n for n in context.material.node_tree.nodes if n.select]
        return context.material and sel and (sel[0].bl_idname == 'VRayNodeOutputMaterial')

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
