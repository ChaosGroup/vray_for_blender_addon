# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

""" The Shift+W popup menu and the Node Editor right-click context append.
    Gives users a way to reach every operator without memorising shortcuts.

    The menu body is factored into `drawWranglerLayout` so a future sidebar
    panel can reuse the same content.
"""

import bpy

from vray_blender.lib.mixin import VRayOperatorBase
from vray_blender.nodes.utils import getLightOutputNode
from vray_blender.nodes.operators.wrangler.merge import _MODE_ITEMS
from vray_blender.nodes.operators.wrangler.poll import (
    isVrayEditor, hasEditTree,
)


def drawWranglerLayout(layout, context):
    """Shared layout used by the Shift+W popup menu; kept as a standalone
       function so a future sidebar panel can reuse the same content.

       Caller is responsible for setting `layout.operator_context` - the
       popup menu uses `'INVOKE_DEFAULT'` so file-browser ops (PBR import,
       HDRI import, displacement import) open the browser instead of
       executing immediately.
    """
    layout.operator("vray.wr_preview_node",           text="Preview Node",            icon='RESTRICT_RENDER_OFF')
    layout.operator("vray.wr_link_out",               text="Link to Output",          icon='DRIVER')
    layout.operator("vray.wr_link_active_to_selected",text="Link Active to Selected", icon='LINKED')
    layout.operator("vray.wr_swap_links",             text="Swap Links",              icon='UV_SYNC_SELECT')
    layout.operator("vray.wr_detach_outputs",         text="Detach Outputs",          icon='UNLINKED')
    layout.operator("vray.wr_del_unused",             text="Delete Unused",           icon='X')
    layout.separator()

    layout.menu("VRAY_MT_WR_add_reroutes",            text="Add Reroutes",            icon='LAYER_USED')
    layout.operator("vray.wr_align_nodes",            text="Align Selected",          icon='CENTER_ONLY')
    layout.operator("vray.wr_center_nodes",           text="Center Selected",         icon='SNAP_FACE_CENTER')
    layout.operator("vray.wr_arrange_tree",           text="Arrange Tree",            icon='SORTBYEXT')
    layout.separator()

    layout.menu("VRAY_MT_WR_merge",                   text="Merge Selected",          icon='AUTOMERGE_ON')
    layout.operator("vray.wr_copy_settings",          text="Copy Settings",           icon='COPYDOWN')
    layout.operator("vray.wr_reset_nodes",            text="Reset Selected",          icon='LOOP_BACK')
    layout.separator()

    # Tree-specific quick-setup section.
    ntree    = context.space_data.edit_tree if hasEditTree(context) else None
    treeType = getattr(getattr(ntree, 'vray', None), 'tree_type', '') if ntree else ''

    if treeType == 'MATERIAL':
        layout.operator("vray.wr_add_pbr_setup",   text="Add PBR Texture Setup",      icon='NODE_TEXTURE')
        layout.menu("VRAY_MT_WR_uvw_mapping",      text="Add UV Mapping",             icon='UV_DATA')
        layout.menu("VRAY_MT_WR_wrap",             text="Wrap Selected",              icon='NODETREE')
    elif treeType == 'OBJECT':
        layout.operator("vray.wr_add_disp_subdiv",    text="Add Displacement + Subdivision", icon='MOD_DISPLACE')
        layout.operator("vray.wr_add_shadow_catcher", text="Make Shadow Catcher",            icon='HOLDOUT_ON')
        layout.operator("vray.wr_add_disp_texture",   text="Import Displacement Texture",    icon='NODE_TEXTURE')
        layout.menu("VRAY_MT_WR_uvw_mapping",         text="Add UV Mapping",                 icon='UV_DATA')
    elif treeType == 'WORLD':
        layout.operator("vray.wr_add_hdri",            text="Import HDRI",                   icon='WORLD')
        layout.operator("vray.wr_add_beauty_channels", text="Add Beauty Render Elements",    icon='RENDERLAYERS')
    elif treeType == 'LIGHT':
        domeNode = getLightOutputNode(ntree)
        if domeNode is not None and getattr(domeNode, 'vray_plugin', '') == 'LightDome':
            layout.operator("vray.wr_add_dome_hdri",   text="Import HDRI",                   icon='WORLD')

    layout.separator()

    layout.menu("VRAY_MT_WR_labels",                  text="Labels",                  icon='SMALL_CAPS')


class VRAY_MT_WR_wrap(bpy.types.Menu):
    bl_idname = "VRAY_MT_WR_wrap"
    bl_label  = "Wrap Selected"

    def draw(self, context):
        layout = self.layout
        layout.operator("vray.wr_wrap_selected", text="Switch Material (MtlMulti)").wrapper_type = 'MTL_MULTI'
        layout.operator("vray.wr_wrap_selected", text="Blend Material").wrapper_type             = 'BRDF_LAYERED'
        layout.separator()
        layout.operator("vray.wr_wrap_selected", text="Bump Material").wrapper_type              = 'BRDF_BUMP'
        layout.operator("vray.wr_wrap_selected", text="Displacement Material").wrapper_type      = 'MTL_DISPLACEMENT'
        layout.operator("vray.wr_wrap_selected", text="Override Material").wrapper_type          = 'MTL_OVERRIDE'


class VRAY_MT_WR_uvw_mapping(bpy.types.Menu):
    bl_idname = "VRAY_MT_WR_uvw_mapping"
    bl_label  = "Add UV Mapping"

    def draw(self, context):
        layout = self.layout
        layout.operator("vray.wr_add_uvw_mapping", text="UV").mapping_type = 'UV'
        layout.operator("vray.wr_add_uvw_mapping", text="Projection").mapping_type              = 'PROJECTION'
        layout.operator("vray.wr_add_uvw_mapping", text="Object").mapping_type                  = 'OBJECT'
        layout.operator("vray.wr_add_uvw_mapping", text="Environment").mapping_type             = 'ENVIRONMENT'


class VRAY_OT_WR_menu_popup(VRayOperatorBase):
    """Open the V-Ray Wrangler popup menu (Shift+W)"""
    bl_idname = "vray.wr_menu_popup"
    bl_label  = "V-Ray Wrangler Menu"
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        # Real poll so Blender skips this keymap entry in non-V-Ray editors
        # and falls through to Node Wrangler's Shift+W correctly.
        return isVrayEditor(context) and hasEditTree(context)

    def invoke(self, context, event):
        bpy.ops.wm.call_menu('INVOKE_DEFAULT', name=VRAY_MT_WR_menu.bl_idname)
        return {'FINISHED'}


class VRAY_MT_WR_menu(bpy.types.Menu):
    bl_idname = "VRAY_MT_WR_menu"
    bl_label = "V-Ray Wrangler"

    @classmethod
    def poll(cls, context):
        return isVrayEditor(context) and hasEditTree(context)

    def draw(self, context):
        # Popup menus default to EXEC_* dispatch; force INVOKE so file-browser
        # / confirm-dialog / modal ops run their invoke() path.
        self.layout.operator_context = 'INVOKE_DEFAULT'
        drawWranglerLayout(self.layout, context)


class VRAY_MT_WR_add_reroutes(bpy.types.Menu):
    bl_idname = "VRAY_MT_WR_add_reroutes"
    bl_label = "Add Reroutes"

    def draw(self, context):
        layout = self.layout
        layout.operator("vray.wr_add_reroutes", text="To All Outputs").option = 'ALL'
        layout.operator("vray.wr_add_reroutes", text="To Loose Outputs").option = 'LOOSE'
        layout.operator("vray.wr_add_reroutes", text="To Linked Outputs").option = 'LINKED'


class VRAY_MT_WR_labels(bpy.types.Menu):
    bl_idname = "VRAY_MT_WR_labels"
    bl_label = "Labels"

    def draw(self, context):
        layout = self.layout
        layout.operator("vray.wr_copy_label", text="Copy Label")
        layout.operator("vray.wr_clear_label", text="Clear Labels")
        layout.operator("vray.wr_modify_labels", text="Modify Labels")


class VRAY_MT_WR_merge(bpy.types.Menu):
    bl_idname = "VRAY_MT_WR_merge"
    bl_label = "Merge Selected"

    def draw(self, context):
        layout = self.layout
        for mode, label, _desc in _MODE_ITEMS:
            layout.operator("vray.wr_merge_nodes", text=label).mode = mode


# The wrangler submenu is drawn inline by the NODE_MT_context_menu override
# in nodes/operators/misc.py (placed above the Online Manual entry), so no
# global append is needed here.


def getRegClasses():
    return (
        VRAY_MT_WR_add_reroutes,
        VRAY_MT_WR_labels,
        VRAY_MT_WR_merge,
        VRAY_MT_WR_wrap,
        VRAY_MT_WR_uvw_mapping,
        VRAY_MT_WR_menu,
        VRAY_OT_WR_menu_popup,
    )


def register():
    for regClass in getRegClasses():
        bpy.utils.register_class(regClass)


def unregister():
    for regClass in reversed(getRegClasses()):
        bpy.utils.unregister_class(regClass)
