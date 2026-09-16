# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later


import bpy

from vray_blender.lib    import draw_utils
from vray_blender.ui      import classes
from vray_blender.ui      import node_nav
from vray_blender.ui      import node_slots
from vray_blender.ui      import ui_operators
from vray_blender.nodes import navigation as NodesNav
from vray_blender.nodes import utils as NodesUtils
from vray_blender.plugins import PLUGINS, getPluginModule
from vray_blender.ui.icons import getUIIcon
from vray_blender.menu import VRAY_OT_convert_materials


def getMaterialPanelTarget(context, mtl):
    """ The (tree, node) the Material tab is editing.

        The tree a node editor is showing wins, so the panel follows the user into a node group.
        But a group tree has no VRayNodeOutputMaterial, so with nothing selected there is no root to
        fall back to and the panel would draw nothing at all - fall back to the material's own tree
        instead. Tree and node are returned together because the slot operators address the tree the
        node actually lives in; resolving them separately is how you edit the wrong graph.
    """
    ntree = NodesNav.getEditedTree(context, mtl)

    if (node := NodesNav.getPanelNode(ntree, 'MATERIAL')) is None:
        ntree = mtl.node_tree
        node = NodesNav.getPanelNode(ntree, 'MATERIAL')

    return ntree, node


def getMaterialPanelNode(context, mtl):
    """ The node the Material tab is editing. The panel and the property-page operators must
        resolve the same node. """
    return getMaterialPanelTarget(context, mtl)[1]


def renderMaterialPanel(mtl, context, layout: bpy.types.UILayout):
    assert mtl.vray.is_vray_class, "Can draw property pages for V-Ray materials only"

    # Resolved together, in one screen scan: the slot operators must address the tree the node
    # actually lives in.
    ntree, activeNode = getMaterialPanelTarget(context, mtl)
    if activeNode is None:
        return

    layout.use_property_split = True
    layout.use_property_decorate = True

    slotContext = node_slots.makeSlotContext(context, mtl, ntree)

    headerRow = layout.row(align=True)
    headerRow.label(text=f'  {activeNode.bl_label}')
    ui_operators.drawPropertyPageButtons(headerRow, context, 'MATERIAL')

    if slotContext is not None:
        navCol = layout.column(align=True)
        navCol.use_property_split = False
        node_nav.drawNavigation(navCol, context, slotContext.ownerType, slotContext.ownerName,
                                ntree, 'MATERIAL', activeNode)

    layout.separator()

    with draw_utils.slotEditing(slotContext):
        classes.drawActiveNodePanel(context, layout, activeNode, PLUGINS)


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
    bl_order = 0

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
    bl_order = 1

    def draw(self, context):
        self.layout.template_preview(context.material, show_buttons=True)


class VRAY_PT_material(classes.VRayMaterialPanel):
    bl_label = "Material"
    bl_idname = "VRAY_PT_material"
    bl_options = set()
    bl_order = 2

    @classmethod
    def poll_custom(cls, context):
        return context.material

    def draw(self, context):
        if (mtl := context.material) and mtl.vray.is_vray_class:
            renderMaterialPanel(mtl, context, self.layout)


class VRAY_PT_material_output(classes.VRayMaterialPanel):
    """ The material's Output node: its own settings, plus the per-material output plugins as
        sub-panels. Previously those were children of 'Material' and polled on the Output node
        being selected in a node editor, so they were unreachable unless the user went and found
        it. """
    bl_label = "Output"
    bl_idname = "VRAY_PT_material_output"
    bl_options = {'DEFAULT_CLOSED'}
    bl_order = 3

    @classmethod
    def poll_custom(cls, context):
        mtl = context.material
        return mtl and mtl.vray.is_vray_class and NodesUtils.getOutputNode(mtl.node_tree, 'MATERIAL')

    def draw(self, context):
        outputNode = NodesUtils.getOutputNode(context.material.node_tree, 'MATERIAL')
        self.layout.use_property_split = True
        self.layout.prop(outputNode, 'dontOverride')


class _VRayMaterialOutputOption(classes.VRayMaterialPanel):
    """ Shared base for the per-material output option rollouts. Each is a child of 'Output' with
        an enable checkbox in its header and a body greyed out while 'use' is off; they differ only
        by the propgroup attribute, the plugin type and the label. """
    bl_parent_id = "VRAY_PT_material_output"
    bl_options = {'DEFAULT_CLOSED'}

    # Attribute on mtl.vray holding the propgroup; also the plugin type.
    optionAttr = ''

    @classmethod
    def poll_custom(cls, context):
        return context.material and context.material.vray.is_vray_class

    def _propGroup(self, context):
        return getattr(context.material.vray, self.optionAttr)

    def drawPanelCheckBox(self, context):
        self.layout.label(text="")
        self.layout.prop(self._propGroup(context), 'use', text="")

    def draw(self, context):
        propGroup = self._propGroup(context)

        split = self.layout.split(factor=0.05, align=True)
        split.enabled = propGroup.use
        split.active = propGroup.use

        split.column()
        col = split.column()
        classes.drawPluginUI(context, col, propGroup, getPluginModule(self.optionAttr))


class VRAY_PT_mtl_material_wrapper(_VRayMaterialOutputOption):
    bl_label = "Wrapper"
    optionAttr = 'MtlWrapper'


class VRAY_PT_material_id(_VRayMaterialOutputOption):
    bl_label = "Material ID"
    optionAttr = 'MtlMaterialID'


class VRAY_PT_mtl_material_round_edges(_VRayMaterialOutputOption):
    bl_label = "Round Edges"
    optionAttr = 'MtlRoundEdges'


class VRAY_PT_mtl_material_render_stats(_VRayMaterialOutputOption):
    bl_label = "Render stats"
    optionAttr = 'MtlRenderStats'


def getRegClasses():
    # Sub-panel order under 'Output' follows registration order; keep it the same as
    # ui/lister/core._MATERIAL_OPTIONS so the lister and the Properties tab agree.
    return (
        VRAY_MT_material_add_popup,
        VRAY_PT_context_material,
        VRAY_PT_preview,
        VRAY_PT_material,
        VRAY_PT_material_output,
        VRAY_PT_mtl_material_wrapper,
        VRAY_PT_material_id,
        VRAY_PT_mtl_material_round_edges,
        VRAY_PT_mtl_material_render_stats,
    )


def register():
    from vray_blender.lib.class_utils import registerClass
    from bl_ui.properties_material import MATERIAL_PT_lineart

    for regClass in getRegClasses():
        registerClass(regClass)

    # The stock 'Line Art' panel ignores COMPAT_ENGINES in its poll, so hide it explicitly.
    classes.hideStockPanels([MATERIAL_PT_lineart])


def unregister():
    from bl_ui.properties_material import MATERIAL_PT_lineart
    classes.restoreStockPanels([MATERIAL_PT_lineart])

    for regClass in reversed(getRegClasses()):
        bpy.utils.unregister_class(regClass)
