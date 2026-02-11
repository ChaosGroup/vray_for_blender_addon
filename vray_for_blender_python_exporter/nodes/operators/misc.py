# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later


import bpy
from bl_ui.space_node import NODE_HT_header

from vray_blender import debug
from vray_blender.lib import blender_utils
from vray_blender.nodes.utils import getLightOutputNode
from vray_blender.ui.properties_material import renderMaterialSelector
from vray_blender.lib.mixin import VRayOperatorBase

originalNodeEditorDraw = None

def _redrawNodeEditor():
    if area := next((a for a in bpy.context.screen.areas if a.type == 'NODE_EDITOR'), None):
        area.tag_redraw()


class VRAY_OT_show_ntree(VRayOperatorBase):
    bl_idname   = "vray.show_ntree"
    bl_label    = "Show Node Tree"
    bl_options  = {'INTERNAL'}

    data: bpy.props.EnumProperty(
        items = (
            ('MATERIAL', "Material", ""),
            ('OBJECT',   "Object",   ""),
            ('LIGHT',     "Lamp",     ""),
            ('WORLD',    "World",    ""),
            ('SCENE',    "Scene",    ""),
        ),
        default = 'MATERIAL'
    )

    ntree_name: bpy.props.StringProperty()

    def execute(self, context):
        ntree = None

        ob = None
        if hasattr(context, 'active_object'):
            ob = context.active_object
        elif hasattr(context, 'object'):
            ob = context.object

        if not ob:
            return {'CANCELLED'}

        if self.data == 'MATERIAL':
            if not ob:
                self.report({'ERROR_INVALID_CONTEXT'}, "No active object!")
                return {'CANCELLED'}
            if ob.type in blender_utils.NonGeometryTypes:
                self.report({'ERROR_INVALID_CONTEXT'}, "Selected object type doesn't support materials!")
                return {'CANCELLED'}
            if not len(ob.material_slots):
                self.report({'ERROR_INVALID_CONTEXT'}, "Object doesn't have any material slots!")
                return {'CANCELLED'}
            ma = ob.material_slots[ob.active_material_index].material
            if ma:
                ntree = ma.node_tree

            ob.material_slots[0].material = bpy.data.materials[-1]

        elif self.data == 'OBJECT':
            if ob.type in blender_utils.NonGeometryTypes:
                if ob.type == 'LIGHT':
                    ntree = ob.data.node_tree
            else:
                ntree = ob.vray.ntree

        elif self.data == 'WORLD':
            ntree = context.scene.world.node_tree

        elif self.data == 'SCENE':
            ntree = context.scene.vray.ntree

        if not ntree:
            if self.ntree_name and self.ntree_name in bpy.data.node_groups:
                ntree = bpy.data.node_groups[self.ntree_name]

        if not ntree:
            self.report({'ERROR'}, "Node tree not found!")
            return {'CANCELLED'}

        _redrawNodeEditor()

        return {'FINISHED'}


class VRAY_OT_ntree_sync_name(VRayOperatorBase):
    bl_label    = "Sync Node Tree Name"
    bl_idname   = "vray.sync_ntree_name"
    bl_options  = {'INTERNAL'}

    materialName: bpy.props.StringProperty()

    def execute(self, context):
        if self.materialName:
            material = bpy.data.materials[self.materialName]
            if material.node_tree:
                material.node_tree.name = self.materialName
        return {'FINISHED'}


def _drawVrayNodeSelector(layout, data, property, new, icon, text):
    row = layout.row()

    if data and data.vray and data.vray.ntree:
        row.template_ID(data.vray, property, new="", unlink="", filter='AVAILABLE')
    else:
        layout.operator(new, icon=icon, text=text)

    row = layout.row()


# Draws Tree select, View, Select, Add and Node menus
def _drawVRayNodeEditorMenus(layout, context: bpy.types.Context):
    layout.template_header()

    layout.prop(context.scene.vray, "ActiveNodeEditorType", text="")
    layout.menu("NODE_MT_view")
    layout.menu("NODE_MT_select")
    layout.menu("NODE_MT_add")
    layout.menu("NODE_MT_node")
    layout.separator_spacer()

# Draws the menus for assigning and creating node trees
def _drawVRayNodeSelection(layout, context, snode):
    vrayTreeType = context.scene.vray.ActiveNodeEditorType
    if vrayTreeType == "WORLD" and hasattr(context.scene, 'world'):
        # Worlds list
        if context.scene.world:
            # Show the list of worlds
            layout.template_ID(context.scene, "world", new="vray.copy_world")
        else:
            # Show only the 'New' button
            layout.template_ID(context.scene, "world", new="vray.add_nodetree_world")
        
        # If this is not a V-Ray world, show a to-vray conversion button
        if context.scene.world and not context.scene.world.vray.is_vray_class:
            layout.operator("vray.add_nodetree_world", icon="NODETREE", text="Use V-Ray World Nodes")


    elif context.object:
        ob = context.object
        has_material_slots = not snode.pin and ob.type in blender_utils.TypesThatSupportMaterial
        if vrayTreeType == "SHADER" and has_material_slots:
            row = layout.row()
            row.enabled = True
            row.ui_units_x = 4
            row.popover(panel="NODE_PT_material_slots")

            row = layout.row()
            renderMaterialSelector(row, ob)


        elif vrayTreeType == "SHADER" and ob.type == "LIGHT":
            ntree = ob.data.node_tree
            if (not ntree) or (not getLightOutputNode(ntree)):
                # This is a light without a node tree. Show the to-nodetree conversion button.
                text = "Use V-Ray Light Nodes" if not ntree else "Add output V-Ray light node"
                layout.operator("vray.add_nodetree_light", icon="NODETREE", text=text)
            else:
                # Just show a label with the light name for now. Light's node trees are not interchangeable.
                layout.label(text=ob.data.name, icon='LIGHT_DATA')
        
        elif vrayTreeType == "OBJECT" and has_material_slots:
            if ob.vray.isVRayFur:
                _drawVrayNodeSelector(layout, ob, "ntree", "vray.add_nodetree_fur", "OBJECT_DATAMODE", "Use V-Ray Fur Nodes")
            elif ob.vray.isVRayDecal:
                _drawVrayNodeSelector(layout, ob, "ntree", "vray.add_nodetree_decal", "OBJECT_DATAMODE", "Use V-Ray Decal Nodes")
            else:
                _drawVrayNodeSelector(layout, ob, "ntree", "vray.add_nodetree_object", "OBJECT_DATAMODE", "Use V-Ray Object Nodes")
        
        else:
            row = layout.row()
            row.label(text="Selected object type not supported this Node editor")

# Draws Compositor and Tool Settings menus
def _drawVRayNodeCompositorAndToolSettings(layout, context, snode):
    tool_settings = context.tool_settings
    is_compositor = snode.tree_type == 'CompositorNodeTree'
    overlay = snode.overlay

    if not is_compositor:
        layout.prop(snode, "pin", text="", emboss=False)

    layout.separator_spacer()

    # Put pin on the right for Compositing
    if is_compositor:
        layout.prop(snode, "pin", text="", emboss=False)

    layout.operator("node.tree_path_parent", text="", icon='FILE_PARENT')

    # Backdrop
    if is_compositor:
        row = layout.row(align=True)
        row.prop(snode, "show_backdrop", toggle=True)
        sub = row.row(align=True)
        sub.active = snode.show_backdrop
        sub.prop(snode, "backdrop_channels", icon_only=True, text="")


    # Snap
        row = layout.row(align=True)
        row.prop(tool_settings, "use_snap_node", text="")

        # Overlay toggle & popover
        row = layout.row(align=True)
        row.prop(overlay, "show_overlays", icon='OVERLAY', text="")
        sub = row.row(align=True)
        sub.active = overlay.show_overlays
        sub.popover(panel="NODE_PT_overlay", text="")


# Function that draws custom header only for Vray Node Editors
def vrayHeaderDrawSwitch(panel, context):
    if context.space_data.tree_type == "VRayNodeTreeEditor":
        layout = panel.layout
        snode = context.space_data

        _drawVRayNodeEditorMenus(layout, context)
        _drawVRayNodeSelection(layout, context, snode)
        _drawVRayNodeCompositorAndToolSettings(layout, context, snode)

    else:
        originalNodeEditorDraw(panel, context)


def registerVrayHeaderDrawSwitch():
    global originalNodeEditorDraw
    originalNodeEditorDraw = NODE_HT_header.draw
    NODE_HT_header.draw = vrayHeaderDrawSwitch


def unregisterVrayHeaderDrawSwitch():
    NODE_HT_header.draw = originalNodeEditorDraw




def getRegClasses():
    return (
        VRAY_OT_show_ntree,
        VRAY_OT_ntree_sync_name
    )


def register():
    registerVrayHeaderDrawSwitch()
    for regClass in getRegClasses():
        bpy.utils.register_class(regClass)


def unregister():
    unregisterVrayHeaderDrawSwitch()
    for regClass in getRegClasses():
        bpy.utils.unregister_class(regClass)
