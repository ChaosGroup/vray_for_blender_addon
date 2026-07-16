# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later


import bpy
from bl_ui.space_node import NODE_HT_header

from vray_blender import debug
from vray_blender.lib import blender_utils
from vray_blender.nodes.utils import getLightOutputNode
from vray_blender.nodes.group.utils import isGroupNodesEnabled, VRAY_GROUP_NODE_TYPE
from vray_blender.nodes.operators.wrangler.poll import hasEditTree
from vray_blender.ui.properties_material import renderMaterialSelector
from vray_blender.lib.mixin import VRayOperatorBase

originalNodeEditorDraw = None
originalContextMenuDraw = None
originalNodeMenuDraw = None

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

    row = layout.row(align=True)
    row.prop(context.scene.vray, "ActiveNodeEditorType", expand=True)
    layout.menu("NODE_MT_view")
    layout.menu("NODE_MT_select")
    layout.menu("NODE_MT_vray_add")
    layout.menu("NODE_MT_node")
    layout.separator_spacer()

# Draws the menus for assigning and creating node trees
def _drawVRayNodeSelection(layout, context, snode):
    scene = context.scene
    vrayTreeType = scene.vray.ActiveNodeEditorType
    ob = snode.id_from if snode.pin and snode.id_from else context.object
    layout = layout.row()
    layout.enabled = not snode.pin
    if vrayTreeType == "WORLD" and hasattr(scene, 'world'):
        # Worlds list
        if scene.world and scene.world.vray.is_vray_class:
            # Show the list of worlds
            layout.template_ID(scene, "world", new="vray.copy_world")
        else:
            # Show only the 'New' button
            layout.template_ID(scene, "world", new="vray.add_nodetree_world")

        # If this is not a V-Ray world, show a to-vray conversion button
        if scene.world and not scene.world.vray.is_vray_class:
            layout.operator("vray.add_nodetree_world", icon="NODETREE", text="New V-Ray World Nodes")

    elif ob and (objType := getattr(ob, 'type', '')):
        hasMaterialSlots = objType in blender_utils.TypesThatSupportMaterial
        if vrayTreeType == "SHADER" and hasMaterialSlots:
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

        elif vrayTreeType == "OBJECT" and hasMaterialSlots:
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

    # Use our own path-pop operator instead of Blender's tree_path_parent
    # which jumps to root for custom tree types.
    if len(snode.path) > 1:
        layout.operator("vray.node_group_path_jump", text="", icon='FILE_PARENT').depth = len(snode.path) - 2
    else:
        layout.label(text="", icon='FILE_PARENT')

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
def _drawVRayGroupBreadcrumbs(layout, snode):
    """ Draw breadcrumb path when inside a V-Ray group node. """
    if len(snode.path) <= 1:
        return

    row = layout.row(align=True)
    for i, pathItem in enumerate(snode.path):
        if i > 0:
            row.label(text="", icon='RIGHTARROW_THIN')

        # Use the material/world/light name for the root entry instead of "Shader Nodetree"
        if i == 0 and snode.id:
            name = snode.id.name
        else:
            name = pathItem.node_tree.name

        if i < len(snode.path) - 1:
            props = row.operator("vray.node_group_path_jump", text=name)
            props.depth = i
        else:
            row.label(text=name)


def vrayHeaderDrawSwitch(panel, context):
    if context.space_data.tree_type == "VRayNodeTreeEditor":
        layout = panel.layout
        snode = context.space_data

        _drawVRayNodeEditorMenus(layout, context)
        _drawVRayNodeSelection(layout, context, snode)
        _drawVRayNodeCompositorAndToolSettings(layout, context, snode)
        _drawVRayGroupBreadcrumbs(layout, snode)

    else:
        originalNodeEditorDraw(panel, context)


def registerVrayHeaderDrawSwitch():
    global originalNodeEditorDraw
    originalNodeEditorDraw = NODE_HT_header.draw
    NODE_HT_header.draw = vrayHeaderDrawSwitch


def unregisterVrayHeaderDrawSwitch():
    NODE_HT_header.draw = originalNodeEditorDraw


# ---------------------------------------------------------------------------
# Right-click context menu & top-bar Node menu overrides
#
# Blender's NODE_MT_context_menu.draw and NODE_MT_node.draw are hardcoded to
# call node.group_make / node.group_insert / node.group_edit / node.group_ungroup
# / node.tree_path_parent. In V-Ray editors we must use V-Ray's parallel
# operators (vray.node_group_*) so group bookkeeping stays consistent.
# We can't remove individual layout items from a built-in draw(), so we
# mirror the menus and swap the group entries.
# ---------------------------------------------------------------------------

def _isVRayEditor(context):
    space = context.space_data
    return bool(space and space.type == 'NODE_EDITOR'
                and space.tree_type == 'VRayNodeTreeEditor')


def _drawVRayGroupOps(layout, context, *, isContextMenu):
    """ Draw V-Ray's group operators. Returns True if any entry was drawn.
        - Top-bar Node menu (isContextMenu=False): all four ops shown
          unconditionally, mirroring Blender's NODE_MT_node behaviour.
        - Right-click context menu (isContextMenu=True): Edit/Ungroup gated on
          the active node being a V-Ray group, plus Exit Group and Separate
          (Move/Copy) when inside a group.
    """
    if not isGroupNodesEnabled():
        return False

    snode = context.space_data
    isNested = len(snode.path) > 1
    activeNode = context.active_node

    layout.operator('vray.node_group_make', text="Make Group", icon='NODETREE')
    layout.operator('vray.node_group_insert', text="Insert Into Group")

    showEditUngroup = (not isContextMenu) or (
        activeNode and activeNode.bl_idname == VRAY_GROUP_NODE_TYPE)
    if showEditUngroup:
        layout.operator('vray.node_group_edit', text="Edit Group")
        layout.operator('vray.node_group_ungroup', text="Ungroup")

    if isContextMenu and isNested:
        # Replaces node.tree_path_parent which jumps to root for custom trees.
        layout.operator('vray.node_group_path_jump', text="Exit Group",
                        icon='FILE_PARENT').depth = len(snode.path) - 2
        layout.operator('vray.node_group_separate', text="Separate (Move)").mode = 'MOVE'
        layout.operator('vray.node_group_separate', text="Separate (Copy)").mode = 'COPY'

    return True


def _drawVRayContextMenu(menu, context):
    """ V-Ray replacement for NODE_MT_context_menu.draw.
        Mirrors Blender's menu but swaps node.group_* / node.tree_path_parent
        with the vray.node_group_* equivalents.
    """
    snode = context.space_data
    isNested = len(snode.path) > 1
    selectedCount = len(context.selected_nodes)

    layout = menu.layout

    if selectedCount == 0:
        layout.operator_context = 'INVOKE_DEFAULT'
        layout.menu("NODE_MT_add", icon='ADD')
        layout.operator("node.clipboard_paste", text="Paste", icon='PASTEDOWN')

        layout.separator()
        layout.operator("node.find_node", text="Find...", icon='VIEWZOOM')

        layout.separator()
        layout.operator("node.links_cut")
        layout.operator("node.links_mute")

        if isNested:
            layout.separator()
            layout.operator('vray.node_group_path_jump', text="Exit Group",
                            icon='FILE_PARENT').depth = len(snode.path) - 2

        if hasEditTree(context):
            layout.separator()
            layout.menu("VRAY_MT_WR_menu", icon='NODETREE')
        return

    layout.operator("node.clipboard_copy", text="Copy", icon='COPYDOWN')
    layout.operator("node.clipboard_paste", text="Paste", icon='PASTEDOWN')
    layout.operator_context = 'INVOKE_DEFAULT'
    layout.operator("node.duplicate_move", icon='DUPLICATE')

    layout.separator()
    layout.operator("node.delete", icon='X')
    layout.operator_context = 'EXEC_REGION_WIN'
    layout.operator("node.delete_reconnect", text="Dissolve")

    if selectedCount > 1:
        layout.separator()
        layout.operator("node.link_make").replace = False
        layout.operator("node.link_make", text="Make and Replace Links").replace = True
        layout.operator("node.links_detach")

    layout.separator()
    if _drawVRayGroupOps(layout, context, isContextMenu=True):
        layout.separator()

    layout.operator("node.join", text="Join in New Frame")
    layout.operator("node.detach", text="Remove from Frame")

    layout.separator()
    props = layout.operator("wm.call_panel", text="Rename...")
    props.name = "TOPBAR_PT_name"
    props.keep_open = False

    layout.separator()
    layout.menu("NODE_MT_context_menu_select_menu")
    layout.menu("NODE_MT_context_menu_show_hide_menu")

    if hasEditTree(context):
        layout.separator()
        layout.menu("VRAY_MT_WR_menu", icon='NODETREE')

    activeNode = context.active_node
    if activeNode:
        layout.separator()
        props = layout.operator("wm.doc_view_manual", text="Online Manual", icon='URL')
        props.doc_id = activeNode.bl_idname


def _drawVRayNodeMenu(menu, context):
    """ V-Ray replacement for NODE_MT_node.draw (top-bar Node menu).
        Mirrors Blender's menu but swaps node.group_* with vray.node_group_*.
    """
    layout = menu.layout

    layout.operator("transform.translate").view2d_edge_pan = True
    layout.operator("transform.rotate")
    layout.operator("transform.resize")

    layout.separator()
    layout.operator("node.clipboard_copy", text="Copy", icon='COPYDOWN')
    layout.operator_context = 'EXEC_DEFAULT'
    layout.operator("node.clipboard_paste", text="Paste", icon='PASTEDOWN')
    layout.operator_context = 'INVOKE_REGION_WIN'
    props = layout.operator("node.duplicate_move", icon='DUPLICATE')
    props.NODE_OT_translate_attach.TRANSFORM_OT_translate.view2d_edge_pan = True
    props = layout.operator("node.duplicate_move_linked")
    props.NODE_OT_translate_attach.TRANSFORM_OT_translate.view2d_edge_pan = True

    layout.separator()
    layout.operator("node.delete", icon='X')
    layout.operator("node.delete_reconnect")

    layout.separator()
    layout.operator("node.join", text="Join in New Frame")
    layout.operator("node.detach", text="Remove from Frame")
    # Added in Blender 5.0
    if hasattr(bpy.types, 'NODE_OT_join_nodes'):
        layout.operator("node.join_nodes", text="Join Group Inputs")
    if hasattr(bpy.types, 'NODE_OT_join_named'):
        layout.operator("node.join_named")

    layout.separator()
    props = layout.operator("wm.call_panel", text="Rename...")
    props.name = "TOPBAR_PT_name"
    props.keep_open = False

    layout.separator()
    layout.operator("node.link_make").replace = False
    layout.operator("node.link_make", text="Make and Replace Links").replace = True
    layout.operator("node.links_cut")
    layout.operator("node.links_detach")
    layout.operator("node.links_mute")

    layout.separator()
    if _drawVRayGroupOps(layout, context, isContextMenu=False):
        layout.separator()

    # NODE_MT_swap was added in Blender 5.0; V-Ray populates it via the hook
    # in nodes.py.
    if hasattr(bpy.types, 'NODE_MT_swap'):
        layout.menu("NODE_MT_swap")
    layout.menu("NODE_MT_context_menu_show_hide_menu")


def vrayContextMenuDrawSwitch(menu, context):
    if _isVRayEditor(context):
        _drawVRayContextMenu(menu, context)
    else:
        originalContextMenuDraw(menu, context)


def vrayNodeMenuDrawSwitch(menu, context):
    if _isVRayEditor(context):
        _drawVRayNodeMenu(menu, context)
    else:
        originalNodeMenuDraw(menu, context)


def registerVrayMenuSwitches():
    from bl_ui.space_node import NODE_MT_context_menu, NODE_MT_node
    global originalContextMenuDraw, originalNodeMenuDraw
    originalContextMenuDraw = NODE_MT_context_menu.draw
    originalNodeMenuDraw = NODE_MT_node.draw
    NODE_MT_context_menu.draw = vrayContextMenuDrawSwitch
    NODE_MT_node.draw = vrayNodeMenuDrawSwitch


def unregisterVrayMenuSwitches():
    from bl_ui.space_node import NODE_MT_context_menu, NODE_MT_node
    if originalContextMenuDraw is not None:
        NODE_MT_context_menu.draw = originalContextMenuDraw
    if originalNodeMenuDraw is not None:
        NODE_MT_node.draw = originalNodeMenuDraw


class VRAY_OT_node_group_path_jump(bpy.types.Operator):
    bl_idname  = 'vray.node_group_path_jump'
    bl_label   = "Jump to Group Path"
    bl_description = "Navigate to this level in the group hierarchy"

    depth: bpy.props.IntProperty()

    def execute(self, context):
        space = context.space_data
        # Pop path entries until we reach the target depth
        while len(space.path) > self.depth + 1:
            space.path.pop()
        return {'FINISHED'}


def getRegClasses():
    return (
        VRAY_OT_show_ntree,
        VRAY_OT_ntree_sync_name,
        VRAY_OT_node_group_path_jump,
    )


def register():
    registerVrayHeaderDrawSwitch()
    registerVrayMenuSwitches()
    for regClass in getRegClasses():
        bpy.utils.register_class(regClass)


def unregister():
    unregisterVrayMenuSwitches()
    unregisterVrayHeaderDrawSwitch()
    for regClass in getRegClasses():
        bpy.utils.unregister_class(regClass)
