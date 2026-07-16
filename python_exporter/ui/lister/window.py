# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

# Floating-window host for the V-Ray Scene Lister.
#
# Add-ons can't register a custom editor type, so we open a Preferences window and
# override the draw() methods of the USERPREF_* classes. Windows carrying our marker
# draw the lister; others fall through to the original draw. Everything is restored
# on close and on unregister.
#
# Controls (search, options, close) live in the header, category tabs in the navbar.

import bpy

from vray_blender import debug
from vray_blender.lib.mixin import VRayOperatorBase
from vray_blender.ui.lister import core, relink


# Screen property marking a window as a lister window
VRAY_LISTER_FLAG = "vray_is_lister_window"

# Original draw() methods we replaced, keyed by USERPREF_* type name
_prevDraw: dict[str, object] = {}
# Original ui_type of areas we converted to PREFERENCES, for restore
_prevAreaUiTypes: dict[bpy.types.Area, str] = {}


def _isListerWindow(window: bpy.types.Window) -> bool:
    return bool(window) and bool(window.screen.get(VRAY_LISTER_FLAG, False))


############################################################
# Draw overrides
############################################################

def _overrideProxy(self, context, override):
    """ Route to our draw only for lister windows; otherwise call the original. """
    typeName = type(self).__name__
    if not _isListerWindow(context.window):
        original = _prevDraw.get(typeName)
        if original is not None:
            return original(self, context)
        return None
    return override(self, context)


def _drawHeader(self, context):
    layout = self.layout
    view = core.getListerView(context)  # per-file state

    row = layout.row(align=True)
    row.label(text="V-Ray Scene Lister", icon='OUTLINER')
    # Navbar gives its space to the material list, hiding the category tabs;
    # surface a compact category switcher here instead.
    if view is not None and core.editorUsesNavbarList(context):
        catSel = row.row()
        catSel.ui_units_x = 9
        catSel.prop(view, 'active_category', text="")
    row.separator_spacer()
    if view is not None:
        # Fixed search width; otherwise separator_spacer shrinks it to its minimum
        searchRow = row.row(align=True)
        searchRow.ui_units_x = 12
        searchRow.prop(view, 'search', text="", icon='VIEWZOOM')
        filters = row.row(align=True)
        filters.prop(view, 'show_selected_only', text="", icon='RESTRICT_SELECT_OFF')
        filters.prop(view, 'show_viewport_visible_only', text="", icon='RESTRICT_VIEW_OFF')
        filters.prop(view, 'show_render_visible_only', text="", icon='RESTRICT_RENDER_OFF')
        # Show just V-Ray materials; only relevant in the Materials section
        if view.active_category == 'MATERIALS':
            from vray_blender.ui import icons
            filters.prop(view, 'show_vray_materials_only', text="",
                         icon_value=icons.getIcon('VRAY_LOGO'))
    row.popover(panel="VRAY_PT_lister_options", text="", icon='PREFERENCES')
    row.separator()
    row.operator("vray.lister_close", text="", icon='PANEL_CLOSE')


def _drawNavbar(self, context):
    layout = self.layout
    view = core.getListerView(context)
    if view is None:
        return

    # Material Editor: navbar hosts only the material list (category switcher moves to
    # the header dropdown), so the divider to the content-region detail panel is a real,
    # mouse-resizable region divider.
    if core.editorUsesNavbarList(context):
        core.drawMaterialList(layout, context)
        return

    col = layout.column(align=True)
    col.scale_y = 1.3
    col.prop(view, 'active_category', expand=True)


def _drawContent(self, context):
    layout = self.layout
    view = core.getListerView(context)
    if view is None:
        layout.label(text="Open a .blend / scene to use the lister.", icon='INFO')
        return

    category = core.getCategory(view.active_category)
    if category is None:
        layout.label(text="No lister categories registered.", icon='ERROR')
        return

    core.drawLister(context, layout, category, core.makeListerState(context))


def _drawBlank(self, context):
    pass


def _makeDrawOverride(drawImpl):
    """ Build a UNIQUE override function object for one hijacked USERPREF type. A distinct
        function per type is required because Blender stores a panel's appended draw callbacks
        on the draw function itself (draw._draw_funcs); sharing one function across all the
        content panels would let each type overwrite that same _draw_funcs, so only the last
        type's appended draws would survive while the hijack is active.
    """
    def override(self, context):
        return _overrideProxy(self, context, drawImpl)
    return override


############################################################
# Hijack / restore
############################################################

def _hijackPreferences(context: bpy.types.Context):
    context.preferences.active_section = 'ADDONS'

    for typeName in filter(lambda t: t.startswith("USERPREF_"), dir(bpy.types)):
        if typeName in _prevDraw:
            continue  # already overridden
        userType = getattr(bpy.types, typeName)
        if not hasattr(userType, "draw"):
            continue

        _prevDraw[typeName] = userType.draw
        # Blender 4.2+ may attach multiple draw functions to a panel; preserve them
        drawFuncs = getattr(userType.draw, "_draw_funcs", None)

        if userType == bpy.types.USERPREF_PT_navigation_bar:
            drawImpl = _drawNavbar
        elif userType == bpy.types.USERPREF_HT_header:
            drawImpl = _drawHeader
        elif typeName == "USERPREF_PT_save_preferences":
            # Drawn in the footer region; blank it so it doesn't duplicate the table
            drawImpl = _drawBlank
        else:
            drawImpl = _drawContent

        userType.draw = _makeDrawOverride(drawImpl)
        if drawFuncs is not None:
            userType.draw._draw_funcs = drawFuncs

    core.tagListerRedraw(context)


def restorePreferences(context: bpy.types.Context):
    """ Restore every hijacked draw() and converted area. Safe to call when no
        hijack is active.
    """
    for typeName, prevDraw in _prevDraw.items():
        if not hasattr(bpy.types, typeName):
            continue
        getattr(bpy.types, typeName).draw = prevDraw
    _prevDraw.clear()

    aliveAreas = {a for w in context.window_manager.windows for a in w.screen.areas}
    for area, prevUiType in _prevAreaUiTypes.items():
        if area in aliveAreas:
            area.ui_type = prevUiType
    _prevAreaUiTypes.clear()

    core.tagListerRedraw(context)


@bpy.app.handlers.persistent
def _onLoadClearListerFlags(_e):
    """ The per-window lister marker lives on window.screen, which is saved into the .blend,
        but the in-memory draw hijack is not carried across a file load. Clear stale markers on
        load so a reloaded Preferences window is treated as an ordinary one - otherwise the
        depsgraph redraw handler would keep poking it and 'open lister' would latch onto it
        instead of opening a fresh window.
    """
    for screen in bpy.data.screens:
        if VRAY_LISTER_FLAG in screen:
            del screen[VRAY_LISTER_FLAG]

    # New file references different files; drop cached file-existence results
    relink.refreshAssetCache()

    # Drop runtime-only view state (solo target, open material) from the old file
    core.resetRuntimeState()

    # Reopen the lister if the loaded file had it open. Deferred via a timer so the
    # open operator runs with a settled window context.
    bpy.app.timers.register(_reopenListerIfFlagged, first_interval=0.1)


def _findPreferencesWindow(context: bpy.types.Context):
    """ A window that is showing the Preferences editor, or None. """
    for window in context.window_manager.windows:
        for area in window.screen.areas:
            if area.ui_type == 'PREFERENCES' or area.type == 'PREFERENCES':
                return window
    return None


def _reopenListerIfFlagged():
    """ One-shot timer: restore the lister if the loaded scene had it open. Blender
        saves the Preferences window into the .blend, so on load it comes back as a
        plain Preferences window; reuse that one (re-mark + re-hijack) instead of
        opening a second window. Only open a fresh one if none was restored. """
    try:
        context = bpy.context
        view = core.getListerView(context)
        if not (view and view.lister_open):
            return None
        if any(_isListerWindow(w) for w in context.window_manager.windows):
            return None  # a lister window already exists

        prefsWindow = _findPreferencesWindow(context)
        if prefsWindow is not None:
            prefsWindow.screen[VRAY_LISTER_FLAG] = True
            _hijackPreferences(context)
        else:
            bpy.ops.vray.lister_open()
        # Header may be hidden/at-bottom right after load; flip it to the top
        bpy.app.timers.register(_flipListerHeaderTop, first_interval=0.3)
    except Exception as ex:
        debug.printError(f"V-Ray Scene Lister: reopen-on-load failed: {ex}")
    return None  # one-shot


def _flipListerHeaderTop():
    """ One-shot timer: move the lister window's header region to the top if it is at
        the bottom (the header is already un-hidden by the open operator). """
    try:
        window = next((w for w in bpy.context.window_manager.windows if _isListerWindow(w)), None)
        if window is None:
            return None
        area = next((a for a in window.screen.areas if a.ui_type == 'PREFERENCES'), None)
        if area is None and window.screen.areas:
            area = window.screen.areas[0]
        header = next((r for r in area.regions if r.type == 'HEADER'), None) if area else None
        if header is not None and getattr(header, 'alignment', '') == 'BOTTOM':
            with bpy.context.temp_override(window=window, area=area, region=header):
                bpy.ops.screen.region_flip()
    except Exception as ex:
        debug.printError(f"V-Ray Scene Lister: could not move reopened header to top: {ex}")
    return None  # one-shot


############################################################
# Options popover (header "wrench" menu)
############################################################

class VRAY_PT_lister_options(bpy.types.Panel):
    bl_idname = "VRAY_PT_lister_options"
    bl_label = "Lister Options"
    bl_space_type = 'PREFERENCES'
    bl_region_type = 'HEADER'

    def draw(self, context):
        layout = self.layout
        # Layout settings are global (prefs); active section is per-file (scene)
        prefs = core.getListerPrefs(context)
        view = core.getListerView(context)

        layout.label(text="Layout")
        layout.prop(prefs, 'layout_mode', expand=True)
        layout.prop(prefs, 'geometry_combined')
        layout.prop(prefs, 'max_visible_rows')

        # Column alignment only applies in Stacked mode. Disabled while a
        # force-Independent section (Materials) is active.
        activeCategory = core.getCategory(view.active_category) if view is not None else None
        forcedIndependent = bool(getattr(activeCategory, 'forceIndependentAlignment', False))
        layout.label(text="Column Alignment")
        alignRow = layout.row(align=True)
        alignRow.enabled = prefs.layout_mode == 'STACKED' and not forcedIndependent
        alignRow.prop(prefs, 'alignment_mode', expand=True)

        # 'Hide Single-Type Columns' only affects the Unified Grid; disable it elsewhere
        compactRow = layout.row()
        compactRow.enabled = (prefs.layout_mode == 'STACKED' and not forcedIndependent
                              and prefs.alignment_mode == 'UNIFIED')
        compactRow.prop(prefs, 'unified_hide_exclusive')

        # Material Editor list-placement option
        core.drawOptions(layout, prefs)

        _drawColumnPicker(layout, context, prefs, view)


def _drawColumnPicker(layout, context, prefs, view):
    layout.separator()
    layout.label(text="Columns")
    # Presets that bulk-set the checkboxes below: Default = basic set, Show All = every column
    presetRow = layout.row(align=True)
    op = presetRow.operator("vray.lister_reset_columns", text="Default")
    op.mode = 'DEFAULT'
    op = presetRow.operator("vray.lister_reset_columns", text="Show All")
    op.mode = 'ALL'

    # Section tabs: pick which section's columns to edit; selecting one switches the lister to it
    if view is None:
        return
    combined = bool(getattr(prefs, 'geometry_combined', False))
    row = layout.row(align=True)
    for cat in core.getCategories():
        if core.isCategoryVisible(cat, combined):
            row.prop_enum(view, 'active_category', cat.id, text="", icon=cat.icon)

    category = core.getCategory(view.active_category)
    if category is None:
        return

    # Categories may split the picker into labelled sub-sections (Materials: one per
    # shader type); otherwise show the flat, deduplicated column list.
    sections = category.pickerSections(context, prefs)
    if sections is not None:
        shown = {'select', 'name'}
        for label, cols in sections:
            if not cols:
                continue
            shown.update(c.id for c in cols)
            layout.label(text=label)
            _drawPickerColumns(layout.box().column(align=True), prefs, category, cols)
        # Surface injected feature columns (Pinned, Solo, Fake User, ...) the custom
        # sections didn't list, so they can still be toggled off.
        extra = [c for c in core.collectCategoryColumns(category, context, prefs) if c.id not in shown]
        if extra:
            layout.label(text="Lister")
            _drawPickerColumns(layout.box().column(align=True), prefs, category, extra)
        return

    cols = core.collectCategoryColumns(category, context, prefs)
    if not cols:
        return
    _drawPickerColumns(layout.box().column(align=True), prefs, category, cols)


def _drawPickerColumns(colBox, prefs, category, cols):
    for col in cols:
        if col.id in ('select', 'name'):
            continue
        # Checkbox = default visibility flipped by the user's per-column toggle
        visible = (not col.defaultHidden) != core.isColumnToggled(prefs, category.id, col.id)
        op = colBox.operator(
            "vray.lister_toggle_column",
            text=col.pickerLabel or col.label or col.id,
            icon='CHECKBOX_HLT' if visible else 'CHECKBOX_DEHLT',
            emboss=False,
        )
        op.category = category.id
        op.column = col.id


############################################################
# Window creation
############################################################

def _openListerWindow(context: bpy.types.Context):
    """ Open the window that will host the lister. Returns the new window.

        We open a plain new window and switch its area to the Preferences editor -
        deliberately NOT screen.userpref_show. screen.userpref_show opens/focuses
        Blender's single 'temp' Preferences window; if the lister used that, then
        Edit > Preferences would just focus the lister and the real Preferences would
        be unreachable. A regular window stays distinct from the temp Preferences
        window, so the real Preferences still open normally alongside the lister.
        (Downside: Blender gives add-ons no window-size control, so it opens at the
        default new-window size; the user can resize it.)
    """
    bpy.ops.wm.window_new()
    window = context.window_manager.windows[-1]
    area = window.screen.areas[0]
    _prevAreaUiTypes[area] = area.ui_type
    area.ui_type = 'PREFERENCES'
    # Regular window's header is already at the top, so no un-hide/flip
    return window


############################################################
# Operators
############################################################

class VRAY_OT_lister_open(VRayOperatorBase):
    bl_idname = "vray.lister_open"
    bl_label = "V-Ray Scene Lister"
    bl_description = "Open the V-Ray Scene Lister in a new window"

    def execute(self, context):
        _setListerOpen(context, True)

        # Focus an already-open lister window instead of opening a second one
        existing = [w for w in context.window_manager.windows if _isListerWindow(w)]
        if existing:
            _hijackPreferences(context)
            return {'FINISHED'}

        window = _openListerWindow(context)
        if window is None:
            self.report({'ERROR'}, "Could not open the V-Ray Scene Lister window")
            return {'CANCELLED'}

        window.screen[VRAY_LISTER_FLAG] = True
        _hijackPreferences(context)
        return {'FINISHED'}


class VRAY_OT_lister_close(VRayOperatorBase):
    bl_idname = "vray.lister_close"
    bl_label = "Close V-Ray Scene Lister"
    bl_description = "Close the V-Ray Scene Lister and restore the Preferences"

    def execute(self, context):
        _setListerOpen(context, False)
        restorePreferences(context)

        for window in list(context.window_manager.windows):
            if _isListerWindow(window):
                try:
                    del window.screen[VRAY_LISTER_FLAG]
                except KeyError:
                    pass
                with context.temp_override(window=window):
                    bpy.ops.wm.window_close()

        return {'FINISHED'}


def _setListerOpen(context: bpy.types.Context, isOpen: bool):
    """ Record (per-file, on the scene) whether the lister is open, so it can be
        reopened when the .blend is loaded again. """
    view = core.getListerView(context)
    if view is not None:
        view.lister_open = isOpen


def getRegClasses():
    return (
        # This feature is still under development, don't show it to the users
        # VRAY_PT_lister_options,
        # VRAY_OT_lister_open,
        # VRAY_OT_lister_close,
    )


def onUnregister():
    """ Make sure no Preferences window is left with hijacked draw methods. """
    try:
        restorePreferences(bpy.context)
    except Exception as ex:
        debug.printError(f"V-Ray Scene Lister: failed to restore Preferences on unregister: {ex}")
