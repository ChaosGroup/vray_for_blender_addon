# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

# Per-file UI state for the V-Ray Scene Lister, stored on the Scene so it is saved
# in the .blend (active section, search, sort, group collapse, open state). Global
# layout settings live on the addon preferences instead - see ui/preferences.py.

import bpy

from vray_blender.ui.lister.core import categoryEnumItems, tagListerRedraw


def _redraw(self, context):
    tagListerRedraw(context)


class VRayListerState(bpy.types.PropertyGroup):
    # Whether the lister window was open; used to reopen it on file load
    lister_open: bpy.props.BoolProperty(
        options = {'HIDDEN'},
        default = False,
    )

    # Whether the Material Lister window was open, for the reopen on file load.
    material_lister_open: bpy.props.BoolProperty(
        options = {'HIDDEN'},
        default = False,
    )

    active_category: bpy.props.EnumProperty(
        name = "Category",
        description = "Object Lister category to show",
        items = categoryEnumItems,
        update = _redraw,
    )

    search: bpy.props.StringProperty(
        name = "Search",
        description = "Filter rows by object name",
        options = {'TEXTEDIT_UPDATE'},
        update = _redraw,
    )

    # The Material Lister window's own search, separate from the Scene Lister's.
    material_search: bpy.props.StringProperty(
        name = "Search",
        description = "Filter materials by name",
        options = {'TEXTEDIT_UPDATE'},
        update = _redraw,
    )

    show_selected_only: bpy.props.BoolProperty(
        name = "Selected Only",
        description = "Show only the objects selected in the scene",
        default = False,
        update = _redraw,
    )

    show_viewport_visible_only: bpy.props.BoolProperty(
        name = "Viewport Visible Only",
        description = "Show only the objects that are visible in the viewport",
        default = False,
        update = _redraw,
    )

    show_render_visible_only: bpy.props.BoolProperty(
        name = "Render Visible Only",
        description = "Show only the objects that are enabled for rendering (not disabled in render)",
        default = False,
        update = _redraw,
    )

    show_vray_materials_only: bpy.props.BoolProperty(
        name = "V-Ray Materials Only",
        description = "In the Materials section, show only V-Ray materials (hide standard Blender materials)",
        default = False,
        update = _redraw,
    )

    show_missing_assets_only: bpy.props.BoolProperty(
        name = "Missing Only",
        description = "In the Assets section, show only assets whose file is missing on disk",
        default = False,
        update = _redraw,
    )

    # Key of the group (light type, camera kind, ...) shown in Tabbed mode;
    # falls back to the first group when it doesn't match the active category.
    active_group: bpy.props.StringProperty(
        options = {'HIDDEN'},
        default = "",
    )

    # Space-separated "<category>::<group>" tokens of groups collapsed in Stacked mode
    collapsed_groups: bpy.props.StringProperty(
        options = {'HIDDEN'},
        default = "",
    )

    # Column id the rows are sorted by (empty = by name)
    sort_column: bpy.props.StringProperty(
        options = {'HIDDEN'},
        default = "",
    )

    sort_reverse: bpy.props.BoolProperty(
        options = {'HIDDEN'},
        default = False,
    )

    # Entity ids (object/material names, asset locators) pinned to the top of their
    # group. Newline-separated, not space, because names may contain spaces.
    lister_pinned: bpy.props.StringProperty(
        options = {'HIDDEN'},
        default = "",
    )
