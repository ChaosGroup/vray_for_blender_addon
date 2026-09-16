# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

# Floating-window hosts for the V-Ray Scene Lister and the V-Ray Material Lister.
#
# Add-ons can't register a custom editor type, so we open a Preferences window and
# override the draw() methods of the USERPREF_* classes. Windows carrying one of our
# markers draw the matching lister; others fall through to the original draw.
# Everything is restored on close and on unregister.
#
# Both are singular. The Material Lister uses two Preferences areas: the material list on the
# left, the selected material's parameters on the right, navbars hidden in both.

import bpy
import gpu
from gpu_extras.batch import batch_for_shader

from vray_blender import debug
from vray_blender.lib.mixin import VRayOperatorBase
from vray_blender.ui import gpu_overlay
from vray_blender.ui.lister import core, relink


# Screen properties marking a window as one of our lister windows
VRAY_LISTER_FLAG = "vray_is_lister_window"
VRAY_MATERIAL_LISTER_FLAG = "vray_is_material_lister_window"

_WINDOW_FLAGS = {'LISTER': VRAY_LISTER_FLAG, 'MATERIALS': VRAY_MATERIAL_LISTER_FLAG}

# Per-file "this lister kind was open" flag on the scene state, for the reopen on file load
_OPEN_PROPS = {'LISTER': 'lister_open', 'MATERIALS': 'material_lister_open'}

# Marks a lister window adopted from the user. Closing it restores the window, never closes it.
VRAY_DOCKED_FLAG = "vray_lister_docked"

# ui_type of the area a dock converted. Kept on the screen so undocking still works after
# the file has been saved and loaded again.
VRAY_PREV_UI_TYPE = "vray_lister_prev_ui_type"

# Fraction of the Material Lister window taken by the list pane; the rest is the parameters pane
_MATERIAL_LISTER_SPLIT = 0.62

# Original draw() methods we replaced, keyed by USERPREF_* type name
_prevDraw: dict[str, object] = {}
# Original ui_type of areas we converted to PREFERENCES, for restore
_prevAreaUiTypes: dict[bpy.types.Area, str] = {}
# Areas we created by splitting. Undocking closes them again.
_createdAreas: set = set()
# Screen name -> the Preferences areas the lister draws in. A Preferences editor the user
# opens in the same screen is not one of them, and keeps drawing the real preferences.
_listerAreas: dict[str, set] = {}
# The user's Preferences section, saved on the first hijack. The hijack forces 'ADDONS' so that
# exactly one content panel polls true (else every polled panel would draw the lister again),
# which also moves the user's real Preferences window - so put it back on restore.
_prevActiveSection: list = []


def _windowKind(window: bpy.types.Window):
    """ 'LISTER' / 'MATERIALS' for our hijacked windows, None for ordinary ones. """
    if not window:
        return None
    for kind, flag in _WINDOW_FLAGS.items():
        if window.screen.get(flag, False):
            return kind
    return None


def isListerOpen(context: bpy.types.Context, *kinds: str) -> bool:
    """ Whether a lister window of one of these kinds ('LISTER' / 'MATERIALS') is open.
        Used to poll() operators that only make sense with their lister window open -
        without it they would otherwise still show (and do nothing) in the F3 search. """
    openKinds = {_windowKind(w) for w in context.window_manager.windows}
    return bool(openKinds.intersection(kinds))


class ListerOperatorBase(VRayOperatorBase):
    """ Base for lister operators that only make sense with their lister window open -
        without this poll() they would otherwise still show (and do nothing) in F3 search.
        Subclasses set _listerKinds to the kind(s) ('LISTER' / 'MATERIALS') they require. """
    _listerKinds: tuple[str, ...] = ('LISTER',)

    @classmethod
    def poll(cls, context):
        return VRayOperatorBase.poll(context) and isListerOpen(context, *cls._listerKinds)


############################################################
# Draw overrides
############################################################

def _registerListerAreas(screen: bpy.types.Screen):
    """ Take the screen's Preferences areas as the lister's own. Call it once the layout is
        final, so the Material Lister's second pane is in there too. """
    # Entries for screens that are gone hold dead Area references, and a recycled address
    # would later pass as one of ours.
    for screenName in [name for name in _listerAreas if name not in bpy.data.screens]:
        del _listerAreas[screenName]

    _listerAreas[screen.name] = {a for a in screen.areas if a.type == 'PREFERENCES'}


def _listerPaneAreas(screen: bpy.types.Screen) -> list:
    """ The screen's Preferences areas that belong to the lister. With nothing registered
        for the screen, every one of them counts, which is what the lister did before. """
    ours = _listerAreas.get(screen.name)
    return [a for a in screen.areas if a.type == 'PREFERENCES' and (not ours or a in ours)]


def _isListerArea(context) -> bool:
    """ Whether this draw is in one of the lister's own areas. """
    ours = _listerAreas.get(context.window.screen.name)
    return not ours or context.area is None or context.area in ours


def _overrideProxy(self, context, drawByKind):
    """ Route to the draw of this window's lister kind; otherwise call the original. """
    typeName = type(self).__name__
    kind = _windowKind(context.window)
    if kind is not None and not _isListerArea(context):
        kind = None  # a Preferences editor the user opened in the lister's screen
    drawImpl = drawByKind.get(kind) if kind is not None else None
    if drawImpl is None:
        original = _prevDraw.get(typeName)
        if original is not None:
            return original(self, context)
        return None
    return drawImpl(self, context)


# Gap between the header title and the actions after it, in UI units. Tuned by eye.
_HEADER_TITLE_GAP = 2.0


def _drawMaterialActions(row, context):
    """ The material actions and view controls in the Material Lister's list-pane header. """
    from vray_blender.ui import icons
    view = core.getListerView(context)
    # Keep this row unaligned, and do not use separator() between the groups below.
    group = row.row()

    buttons = group.row(align=True)
    buttons.operator("vray.lister_new_material", text="", icon='ADD')
    # Both act on the material the parameters pane is showing.
    selectedName = core.editorMaterialName()
    onSelected = buttons.row(align=True)
    onSelected.enabled = bool(selectedName)
    onSelected.operator("vray.lister_duplicate_material", text="", icon='DUPLICATE')
    onSelected.operator("vray.lister_assign_material", text="",
                        icon='BRUSH_DATA').material = selectedName

    # Copy to Selected / Rename / Cleanup.
    category = core.getCategory('MATERIALS')
    if category is not None:
        core.drawHeaderExtras(group, context, category, iconOnly=True)

    # The node editor's convert operator, told which material to take. Shown only for a Cycles
    # material with a node tree - that tree is what the conversion reads - as the Material tab
    # and the node editor do. Disabling instead would leave a near-invisible icon: a custom
    # preview icon drawn at a disabled widget's alpha all but vanishes on the header.
    vrayGroup = group.row(align=True)
    mtlName = core.editorMaterialName()
    mtl = bpy.data.materials.get(mtlName)
    if mtl is not None and mtl.use_nodes and not mtl.vray.is_vray_class:
        vrayGroup.operator("vray.convert_nodetree_material", text="",
                           icon_value=icons.getIcon('CONVERT_MATERIALS')).material = mtlName
    if view is not None:
        vrayGroup.prop(view, 'show_vray_materials_only', text="",
                       icon_value=icons.getIcon('VRAY_LOGO'))

    # How the list pane shows its materials.
    tabs = group.row(align=True)
    prefs = core.getListerPrefs(context)
    tabs.prop_enum(prefs, 'material_editor_list_display', 'LIST', text="", icon='LONGDISPLAY')
    tabs.prop_enum(prefs, 'material_editor_list_display', 'THUMBNAILS', text="", icon='IMGDISPLAY')


def _drawHeader(self, context):
    layout = self.layout
    view = core.getListerView(context)  # per-file state

    row = layout.row(align=True)
    row.label(text="V-Ray Object Lister", icon='OUTLINER')
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
        # Show just missing files; only relevant in the Assets section
        if view.active_category == 'ASSETS':
            filters.prop(view, 'show_missing_assets_only', text="", icon='ERROR')
    row.popover(panel="VRAY_PT_lister_options", text="", icon='PREFERENCES')
    row.separator()
    row.operator("vray.lister_dock_pick", text="", icon='MENU_PANEL')
    row.separator()
    row.operator("vray.lister_close", text="", icon='PANEL_CLOSE').kind = 'LISTER'


def _materialListerPaneAreas(window):
    """ The Material Lister's Preferences areas, left pane first. """
    return sorted(_listerPaneAreas(window.screen), key=lambda a: a.x)


# The panes keep each other alive while a preview renders. A region's own tag_redraw() during
# its draw is dropped (the draw clears do_draw when it finishes), but a tag on the OTHER area
# sticks - so each pane redraws its neighbour instead of itself.
#
# The preview render finishes with an NC_MATERIAL notifier that a Preferences window discards
# (every SPACE_USERPREF listener is a stub), so without this the finished image is never
# painted. While the lister was one area the mouse moving over the list repainted the preview
# for free; the two-pane split took that away and left only a hover over the preview itself.

# Draws to keep the pair alive after the last sign of a render, for a material no object uses.
# is_job_running() reads False for ~0.2s after the draw that asks for the render, and stopping on
# that first False strands the finished image unpainted - the "only updates once I hover" symptom.
#
# Only loose materials need the coast. An assigned one also gets its icon dirtied by update_tag(),
# and that job is delayed 2s and serialized behind the shader render, so it ends with an NC_WINDOW
# - which this window does process - after the image is ready. preview.reload(), which is all a
# loose material has, skips that delay, so its icon job finishes first and repaints too early.
_PREVIEW_IDLE_DRAWS = 180

# What the parameters pane drew last, to spot the changes that need a kick. See below.
_lastPreviewed = {'name': None, 'token': None, 'idle': 0}


def _repaintParametersPane(context):
    """ From the list pane: redraw the parameters pane. """
    areas = _materialListerPaneAreas(context.window)
    if len(areas) > 1:
        areas[-1].tag_redraw()


def _repaintWhileRendering(context):
    """ From the parameters pane: redraw the list pane, which redraws us back on its next draw.

        The list pane is drawn first, so its tag on us is consumed in the same frame - only a
        tag going the other way survives into the next one, and a region's own tag_redraw()
        during its draw is dropped entirely. So this pane drives the pair.

        Two things restart the countdown: a switched or force-refreshed material (the token, see
        nodes.utils.tagMaterialPreview), and a running preview job. A count of 1 is the plain
        "repaint while the render runs" behaviour; only a material no object uses coasts past
        that, and those tail draws are what paint its finished image. It then settles, and an
        idle lister costs nothing.
    """
    from vray_blender.nodes import utils as NodesUtils

    seen = (core.editorMaterialName(), NodesUtils.loosePreviewToken['n'])
    mat = bpy.data.materials.get(seen[0])
    coast = _PREVIEW_IDLE_DRAWS if (mat is not None and core.isUnassigned(mat)) else 1

    if (_lastPreviewed['name'], _lastPreviewed['token']) != seen \
            or bpy.app.is_job_running('RENDER_PREVIEW'):
        _lastPreviewed['idle'] = coast
    else:
        _lastPreviewed['idle'] = max(0, _lastPreviewed['idle'] - 1)
    _lastPreviewed['name'], _lastPreviewed['token'] = seen

    areas = _materialListerPaneAreas(context.window)
    if len(areas) > 1 and _lastPreviewed['idle'] > 0:
        areas[0].tag_redraw()


def _isMaterialParametersPane(context):
    """ Whether the draw targets the Material Lister's right (parameters) pane. With a single
        area (pane setup failed) everything draws as the list pane. """
    areas = _materialListerPaneAreas(context.window)
    return len(areas) > 1 and context.area == areas[-1]


def _drawMaterialListerHeader(self, context):
    layout = self.layout
    view = core.getListerView(context)

    if _isMaterialParametersPane(context):
        # Nothing in the parameters pane's header: its actions live in the list pane's, and the
        # window is closed from its own titlebar.
        return

    # Left (list) pane header: title, the material actions, then search and the filters
    row = layout.row(align=True)
    left = row.row()
    left.label(text="V-Ray Material Manager", icon='MATERIAL')
    left.separator(factor=_HEADER_TITLE_GAP)
    _drawMaterialActions(left, context)
    row.separator_spacer()
    if view is not None:
        searchRow = row.row(align=True)
        searchRow.ui_units_x = 12
        searchRow.prop(view, 'material_search', text="", icon='VIEWZOOM')
        row.row(align=True).prop(view, 'show_selected_only', text="", icon='RESTRICT_SELECT_OFF')
    row.operator("vray.lister_toggle_params", text="", icon='MATERIAL_DATA')
    row.operator("vray.lister_dock_pick", text="", icon='MENU_PANEL')
    row.popover(panel="VRAY_PT_lister_options", text="", icon='PREFERENCES')
    row.separator()
    row.operator("vray.lister_close", text="", icon='PANEL_CLOSE').kind = 'MATERIALS'


def _drawNavbar(self, context):
    layout = self.layout
    view = core.getListerView(context)
    if view is None:
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

    combined = bool(core.getListerPrefs(context).geometry_combined)
    category = core.getCategory(view.active_category)
    if category is not None and not core.isCategoryVisible(category, combined):
        category = None
    if category is None:
        # The saved section may be gone (MATERIALS has its own window). Scene props are read-only in draw.
        category = next((c for c in core.getCategories() if core.isCategoryVisible(c, combined)), None)
    if category is None:
        layout.label(text="No lister categories registered.", icon='ERROR')
        return

    core.drawLister(context, layout, category, core.makeListerState(context))


def _drawMaterialListerContent(self, context):
    layout = self.layout
    if core.getListerView(context) is None:
        layout.label(text="Open a .blend / scene to use the lister.", icon='INFO')
        return
    if _isMaterialParametersPane(context):
        core.drawMaterialListerParams(context, layout)
        _repaintWhileRendering(context)
    else:
        core.drawMaterialListerMain(context, layout)
        _repaintParametersPane(context)


def _drawBlank(self, context):
    pass


def _makeDrawOverride(drawByKind):
    """ Build a UNIQUE override function object for one hijacked USERPREF type. A distinct
        function per type is required because Blender stores a panel's appended draw callbacks
        on the draw function itself (draw._draw_funcs); sharing one function across all the
        content panels would let each type overwrite that same _draw_funcs, so only the last
        type's appended draws would survive while the hijack is active.
    """
    def override(self, context):
        return _overrideProxy(self, context, drawByKind)
    return override


############################################################
# Hijack / restore
############################################################

def _hijackPreferences(context: bpy.types.Context):
    if not _prevDraw:
        _prevActiveSection.append(context.preferences.active_section)
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
            # The Material Lister's panes hide their navbars; blank in case one is un-hidden
            drawByKind = {'LISTER': _drawNavbar, 'MATERIALS': _drawBlank}
        elif userType == bpy.types.USERPREF_HT_header:
            drawByKind = {'LISTER': _drawHeader, 'MATERIALS': _drawMaterialListerHeader}
        elif typeName == "USERPREF_PT_save_preferences":
            # Drawn in the footer region; blank it so it doesn't duplicate the table
            drawByKind = {'LISTER': _drawBlank, 'MATERIALS': _drawBlank}
        else:
            drawByKind = {'LISTER': _drawContent, 'MATERIALS': _drawMaterialListerContent}

        userType.draw = _makeDrawOverride(drawByKind)
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
    # Leave split panes alone on unregister, but drop the references.
    _createdAreas.clear()
    _listerAreas.clear()

    if _prevActiveSection:
        context.preferences.active_section = _prevActiveSection.pop()

    core.tagListerRedraw(context)


# Screen name -> window kind, captured on file load for the reopen timer.
_savedScreenKinds: dict[str, str] = {}
# Which of those screens were hosting a docked lister
_savedDockedScreens: set[str] = set()


@bpy.app.handlers.persistent
def _onLoadClearListerFlags(_e):
    """ The per-window lister markers live on window.screen, which is saved into the .blend,
        but the in-memory draw hijack is not carried across a file load. Clear stale markers on
        load so a reloaded Preferences window is treated as an ordinary one - otherwise the
        depsgraph redraw handler would keep poking it and 'open lister' would latch onto it
        instead of opening a fresh window.
    """
    _savedScreenKinds.clear()
    _savedDockedScreens.clear()
    # Every screen and area is replaced by the load, so these hold dead references only.
    _createdAreas.clear()
    _prevAreaUiTypes.clear()
    _listerAreas.clear()
    for screen in bpy.data.screens:
        for kind, flag in _WINDOW_FLAGS.items():
            if flag in screen:
                _savedScreenKinds[screen.name] = kind
                del screen[flag]
        if VRAY_DOCKED_FLAG in screen:
            _savedDockedScreens.add(screen.name)
            del screen[VRAY_DOCKED_FLAG]

    # New file references different files; drop cached file-existence results
    relink.refreshAssetCache()

    # Drop runtime-only view state (solo target, open material) from the old file
    core.resetRuntimeState()

    # Reopen the lister if the loaded file had it open. Deferred via a timer so the
    # open operator runs with a settled window context.
    bpy.app.timers.register(_reopenListerIfFlagged, first_interval=0.1)


def _claimPreferencesWindow(context: bpy.types.Context, kind: str):
    """ A Preferences-editor window to reuse for this lister kind: prefer the one whose
        screen carried this kind's saved marker, then one no kind has claimed, or None. """
    candidates = [w for w in context.window_manager.windows
                  if _windowKind(w) is None
                  and all(a.ui_type == 'PREFERENCES' or a.type == 'PREFERENCES' for a in w.screen.areas)]
    if kind == 'LISTER':
        # The Scene Lister needs a single-area screen.
        candidates = [w for w in candidates if len([a for a in w.screen.areas if a.type == 'PREFERENCES']) == 1]
    for window in candidates:
        if _savedScreenKinds.get(window.screen.name) == kind:
            return window
    # Don't steal a window the other lister kind saved for itself
    unsaved = [w for w in candidates if w.screen.name not in _savedScreenKinds]
    return unsaved[0] if unsaved else (candidates[0] if candidates else None)


def _restoreListerMarkers(screen: bpy.types.Screen, kind: str):
    """ Put back the markers the load stripped off a screen that hosted this kind. Without
        the docked one, closing the lister leaves the area as a bare Preferences editor
        instead of handing it back. """
    screen[_WINDOW_FLAGS[kind]] = True
    if screen.name in _savedDockedScreens:
        screen[VRAY_DOCKED_FLAG] = True


def _savedDockedScreen(kind: str):
    """ The screen this kind was docked in, when no window is showing it. A docked lister
        sits in one workspace, and another one can be active when the file is saved. """
    for screenName in _savedDockedScreens:
        if _savedScreenKinds.get(screenName) != kind:
            continue
        screen = bpy.data.screens.get(screenName)
        if screen is not None and any(a.type == 'PREFERENCES' for a in screen.areas):
            return screen
    return None


def _reopenListerIfFlagged():
    """ One-shot timer: reopen the listers the loaded scene had open, reusing restored windows. """
    try:
        context = bpy.context
        view = core.getListerView(context)
        if view is None:
            return None
        openOps = {'LISTER': bpy.ops.vray.lister_open, 'MATERIALS': bpy.ops.vray.material_lister_open}
        reclaimed = False
        opened = False
        for kind in _WINDOW_FLAGS:
            if not getattr(view, _OPEN_PROPS[kind]):
                continue
            if any(_windowKind(w) == kind for w in context.window_manager.windows):
                continue  # a window of this kind already exists
            prefsWindow = _claimPreferencesWindow(context, kind)
            if prefsWindow is not None:
                _restoreListerMarkers(prefsWindow.screen, kind)
                if kind == 'MATERIALS':
                    _setupMaterialListerLayout(context, prefsWindow)
                _registerListerAreas(prefsWindow.screen)
                reclaimed = True
            elif (dockedScreen := _savedDockedScreen(kind)) is not None:
                # Docked in a workspace that is not the active one. Mark it in place rather
                # than open a window over it. It draws as the lister again when the user
                # switches back to that workspace.
                _restoreListerMarkers(dockedScreen, kind)
                _registerListerAreas(dockedScreen)
                reclaimed = True
            else:
                openOps[kind]()
            opened = True
        if reclaimed:
            _hijackPreferences(context)
        if opened:
            # Headers may be hidden/at-bottom right after load; flip them to the top
            bpy.app.timers.register(_flipListerHeaderTop, first_interval=0.3)
    except Exception as ex:
        debug.printError(f"V-Ray Scene Lister: reopen-on-load failed: {ex}")
    return None  # one-shot


def _flipListerHeaderTop():
    """ One-shot timer: move each lister window's header region to the top if it is at
        the bottom (the header is already un-hidden by the open operator). """
    try:
        for window in bpy.context.window_manager.windows:
            if _windowKind(window) is None:
                continue
            areas = [a for a in window.screen.areas if a.type == 'PREFERENCES'] or list(window.screen.areas)
            for area in areas:
                header = next((r for r in area.regions if r.type == 'HEADER'), None)
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

        if _windowKind(context.window) == 'MATERIALS':
            # The material list has no columns and no width of ours to set.
            _drawMaterialOptions(layout, prefs)
            core.drawOptions(layout, prefs)
            return

        layout.label(text="Layout")
        layout.prop(prefs, 'layout_mode', expand=True)
        layout.prop(prefs, 'geometry_combined')
        layout.prop(prefs, 'max_visible_rows')

        # Column alignment only applies in Stacked mode
        layout.label(text="Column Alignment")
        alignRow = layout.row(align=True)
        alignRow.enabled = prefs.layout_mode == 'STACKED'
        alignRow.prop(prefs, 'alignment_mode', expand=True)

        # 'Hide Single-Type Columns' only affects the Unified Grid; disable it elsewhere
        compactRow = layout.row()
        compactRow.enabled = prefs.layout_mode == 'STACKED' and prefs.alignment_mode == 'UNIFIED'
        compactRow.prop(prefs, 'unified_hide_exclusive')

        core.drawOptions(layout, prefs)

        _drawColumnPicker(layout, context, prefs, view)


def _drawMaterialOptions(layout, prefs):
    """ Material Lister options: selection sync, thumbnail size and a manual preview refresh. """
    layout.prop(prefs, 'material_sync_selection')

    layout.separator()
    # Resizing only scales the already-rendered preview, so it needs no warning of its own.
    layout.label(text="Thumbnails")
    layout.prop(prefs, 'material_thumbnail_size', expand=True)

    layout.separator()
    layout.operator("vray.lister_refresh_previews", icon='FILE_REFRESH')
    warning = layout.row()
    warning.active = False
    warning.label(text="Re-renders every material")


def _drawColumnPicker(layout, context, prefs, view):
    # A category with no table has no columns.
    active = core.getCategory(view.active_category) if view is not None else None
    if active is not None and not active.hasTable:
        return

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
    combined = bool(prefs.geometry_combined)
    row = layout.row(align=True)
    for cat in core.getCategories():
        if cat.hasTable and core.isCategoryVisible(cat, combined):
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
        extra = [c for c in core.collectCategoryColumns(category) if c.id not in shown]
        if extra:
            layout.label(text="Lister")
            _drawPickerColumns(layout.box().column(align=True), prefs, category, extra)
        return

    cols = core.collectCategoryColumns(category)
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
    """ Open the window that will host a lister, at the size of the active editor. Returns
        the window.

        We open a plain new window and switch its area to the Preferences editor -
        deliberately NOT screen.userpref_show. screen.userpref_show opens/focuses
        Blender's single 'temp' Preferences window; if the lister used that, then
        Edit > Preferences would just focus the lister and the real Preferences would
        be unreachable. A regular window stays distinct from the temp Preferences
        window, so the real Preferences still open normally alongside the lister.

        The size is whatever wm.window_new gives, since Blender offers no way to set it.
        Sizing a staging window by splitting it cannot work. area.width updates only in the
        next event loop's screen refresh, so the area a split creates still measures 0, and
        duplicating that one opened a 200x150 window on displays above ~1440p.
    """
    before = set(context.window_manager.windows)
    bpy.ops.wm.window_new()
    window = next((w for w in context.window_manager.windows if w not in before), None)
    if window is None:
        return None

    area = window.screen.areas[0]
    _prevAreaUiTypes[area] = area.ui_type
    area.ui_type = 'PREFERENCES'
    # Regular window's header is already at the top, so no un-hide/flip
    return window


def _setupMaterialListerLayout(context: bpy.types.Context, window: bpy.types.Window):
    """ Split the window's Preferences area into the list and parameters panes, navbars hidden. """
    areas = [a for a in window.screen.areas if a.type == 'PREFERENCES']
    if len(areas) != 1:
        return
    before = set(window.screen.areas)
    try:
        with context.temp_override(window=window, area=areas[0]):
            bpy.ops.screen.area_split(direction='VERTICAL', factor=_MATERIAL_LISTER_SPLIT)
    except RuntimeError as ex:
        debug.printError(f"V-Ray Material Lister: could not split the editor area: {ex}")
        return
    # Remember the pane the split added.
    _createdAreas.update(set(window.screen.areas) - before)
    for area in window.screen.areas:
        if area.type != 'PREFERENCES':
            continue
        # Blender 5.0 moved the Preferences sections to a regular sidebar region ('UI'); before
        # that they lived in 'NAVIGATION_BAR'. region_toggle reports FINISHED for a region type
        # the area does not have, so ask the area which one it is instead of guessing.
        navbar = next((r for r in area.regions if r.type in {'UI', 'NAVIGATION_BAR'}), None)
        if navbar is None:
            continue
        # region_toggle toggles, so it must only be called on a navbar that is actually showing.
        # Re-splitting a pane that was hidden and shown again gives areas whose navbars are
        # already hidden (1px wide), and toggling those puts a blank 160px column back in.
        if navbar.width <= 1:
            continue
        try:
            with context.temp_override(window=window, area=area):
                bpy.ops.screen.region_toggle(region_type=navbar.type)
        except RuntimeError:
            pass  # navbar already hidden


############################################################
# Operators
############################################################

def _undockScreen(context: bpy.types.Context, screen: bpy.types.Screen):
    """ Give a docked host window back: close the panes we added, then put back the ui_type
        of the area we converted. """
    window = next((w for w in context.window_manager.windows if w.screen == screen), None)

    for area in [a for a in screen.areas if a in _createdAreas]:
        _createdAreas.discard(area)
        _prevAreaUiTypes.pop(area, None)
        if window is None:
            continue  # no window to override with; leave the area alone
        try:
            with context.temp_override(window=window, area=area):
                bpy.ops.screen.area_close()
        except RuntimeError as ex:
            debug.printError(f"V-Ray lister: could not close the added pane: {ex}")

    # All that is left to go on when the dock happened before a file load.
    savedUiType = screen.get(VRAY_PREV_UI_TYPE)
    try:
        del screen[VRAY_PREV_UI_TYPE]
    except KeyError:
        pass

    for area in screen.areas:
        prevUiType = _prevAreaUiTypes.pop(area, None)
        if prevUiType is None and area.type == 'PREFERENCES':
            prevUiType = savedUiType
        if prevUiType is not None:
            try:
                area.ui_type = prevUiType
            except (TypeError, AttributeError) as ex:
                debug.printError(f"V-Ray lister: could not restore the docked area: {ex}")


def _closeListerKind(context: bpy.types.Context, kind: str):
    """ Take this lister kind down: hand a docked window back to the user, close one we
        opened ourselves. """
    flag = _WINDOW_FLAGS[kind]
    _setListerOpen(context, kind, False)

    for window in list(context.window_manager.windows):
        screen = window.screen
        if not screen.get(flag, False):
            continue
        # Anything but Preferences panes means the user's own window - hand it back, never close it.
        docked = bool(screen.get(VRAY_DOCKED_FLAG, False)) or any(a.type != 'PREFERENCES' for a in screen.areas)
        _listerAreas.pop(screen.name, None)
        for key in (flag, VRAY_DOCKED_FLAG):
            try:
                del screen[key]
            except KeyError:
                pass
        if docked:
            _undockScreen(context, screen)
        else:
            # The window (and every area in it) is about to be freed. Drop the bookkeeping
            # first, or the dicts keep dangling Area keys that a later restore would match
            # against a recycled address.
            for area in screen.areas:
                _prevAreaUiTypes.pop(area, None)
                _createdAreas.discard(area)
            with context.temp_override(window=window):
                bpy.ops.wm.window_close()

    # Keep the draw hijack while a window of the other lister kind is still open
    if not any(_windowKind(w) for w in context.window_manager.windows):
        restorePreferences(context)
    else:
        core.tagListerRedraw(context)


def _dockListerKind(op, context, kind: str, window: bpy.types.Window, area):
    """ Move this lister into `area` of `window`. Releases the current host first. """
    hostKind = _windowKind(window)
    if hostKind is not None and hostKind != kind:
        # One screen can host one kind only.
        op.report({'WARNING'}, "That window already hosts the other V-Ray lister")
        return {'CANCELLED'}

    # Releasing the host reshuffles its areas. Re-find the target by pointer, do not hold the reference.
    areaPtr = area.as_pointer()
    screenPtr = window.screen.as_pointer()
    _closeListerKind(context, kind)

    window = next((w for w in context.window_manager.windows if w.screen.as_pointer() == screenPtr), None)
    area = next((a for a in window.screen.areas if a.as_pointer() == areaPtr), None) if window is not None else None
    if window is None or area is None:
        op.report({'WARNING'}, "That editor is gone")
        return {'CANCELLED'}

    if area.type != 'PREFERENCES':
        _prevAreaUiTypes[area] = area.ui_type
        # _prevAreaUiTypes is in-memory only. Keep it on the screen too, which the file
        # saves, or a dock that outlives a load can never give the area back.
        window.screen[VRAY_PREV_UI_TYPE] = area.ui_type
        area.ui_type = 'PREFERENCES'

    window.screen[_WINDOW_FLAGS[kind]] = True
    window.screen[VRAY_DOCKED_FLAG] = True
    _setListerOpen(context, kind, True)
    if kind == 'MATERIALS':
        _setupMaterialListerLayout(context, window)
    _registerListerAreas(window.screen)
    _hijackPreferences(context)
    return {'FINISHED'}


def _openListerKind(op, context, kind: str):
    """ Open (or re-focus) the singular window of this lister kind. """
    flag = _WINDOW_FLAGS[kind]
    _setListerOpen(context, kind, True)

    # Re-focus an already-open window of this kind instead of opening a second one - and put it
    # back together first. Blender's own Area Options > Close Area can take a pane out from
    # under us, and the marker lives on the screen, so without this the window still counts as
    # open while holding one pane, or none at all in a docked lister, and the lister could never
    # be opened again.
    for window in [w for w in context.window_manager.windows if _windowKind(w) == kind]:
        panes = _listerPaneAreas(window.screen)
        if not panes:
            try:
                del window.screen[flag]  # nothing of the lister is left in it
            except KeyError:
                pass
            continue
        if kind == 'MATERIALS' and len(panes) < 2:
            _setupMaterialListerLayout(context, window)
        _registerListerAreas(window.screen)
        _hijackPreferences(context)
        return {'FINISHED'}

    window = _openListerWindow(context)
    if window is None:
        op.report({'ERROR'}, f"Could not open the {op.bl_label} window")
        return {'CANCELLED'}

    window.screen[flag] = True
    if kind == 'MATERIALS':
        _setupMaterialListerLayout(context, window)
    _registerListerAreas(window.screen)
    _hijackPreferences(context)
    return {'FINISHED'}


class VRAY_OT_lister_open(VRayOperatorBase):
    bl_idname = "vray.lister_open"
    bl_label = "V-Ray Object Lister"
    bl_description = "Open the V-Ray Object Lister in a new window"

    def execute(self, context):
        return _openListerKind(self, context, 'LISTER')


class VRAY_OT_material_lister_open(VRayOperatorBase):
    bl_idname = "vray.material_lister_open"
    bl_label = "V-Ray Material Manager"
    bl_description = "Open the V-Ray Material Manager in a new window"

    def execute(self, context):
        return _openListerKind(self, context, 'MATERIALS')


class VRAY_OT_lister_toggle_params(ListerOperatorBase):
    bl_idname = "vray.lister_toggle_params"
    bl_label = "Show Parameters"
    bl_description = "Show or hide the material parameters pane"

    _listerKinds = ('MATERIALS',)

    def execute(self, context):
        window = next((w for w in context.window_manager.windows
                       if _windowKind(w) == 'MATERIALS'), None)
        if window is None:
            return {'CANCELLED'}

        # The pane is an editor area, and an area edge stops at AREAMINX - so hiding it means
        # closing it, and showing it means splitting the list again at _MATERIAL_LISTER_SPLIT.
        panes = _materialListerPaneAreas(window)
        if len(panes) < 2:
            _setupMaterialListerLayout(context, window)
            return {'FINISHED'}

        params = panes[-1]
        _createdAreas.discard(params)
        _prevAreaUiTypes.pop(params, None)
        try:
            with context.temp_override(window=window, area=params):
                bpy.ops.screen.area_close()
        except RuntimeError as ex:
            debug.printError(f"V-Ray Material Lister: could not hide the parameters pane: {ex}")
            return {'CANCELLED'}
        return {'FINISHED'}


class VRAY_OT_lister_close(ListerOperatorBase):
    bl_idname = "vray.lister_close"
    bl_label = "Close Lister Window"
    bl_description = "Close this V-Ray lister window. A docked window is handed back instead"

    _listerKinds = ('LISTER', 'MATERIALS')

    kind: bpy.props.StringProperty(options={'HIDDEN'}, default='LISTER')  # 'LISTER' or 'MATERIALS'

    def execute(self, context):
        if self.kind not in _WINDOW_FLAGS:
            return {'CANCELLED'}
        _closeListerKind(context, self.kind)
        return {'FINISHED'}


############################################################
# Dock picker
############################################################

# Pick state, shared across windows and draw callbacks. 'generation' stands down stale siblings.
_pickState = {'active': False, 'areaPtr': 0, 'generation': 0}

# Space type -> POST_PIXEL handle, one per space type in use while a pick runs.
_pickHandlers: dict = {}

# Wash over the hovered area. Effective alpha over the viewport is about twice this value.
_PICK_TINT = (1.0, 1.0, 1.0, 0.16)


def _drawPickTint():
    """ Tint the area under the pointer, in whichever window it is. """
    if not _pickState['active']:
        return
    context = bpy.context
    area, region = context.area, context.region
    if area is None or region is None or region.type != 'WINDOW':
        return
    if area.as_pointer() != _pickState['areaPtr']:
        return

    width, height = region.width, region.height
    shader = gpu_overlay.colorShader()
    # Keep depth testing off here or the viewport rejects the quad. Restore it afterwards.
    prevDepthTest = gpu.state.depth_test_get()
    gpu.state.depth_test_set('NONE')
    gpu.state.blend_set('ALPHA')
    try:
        batch = batch_for_shader(shader, 'TRIS', {"pos": [
            (0, 0), (width, 0), (width, height),
            (0, 0), (width, height), (0, height),
        ]})
        shader.uniform_float("color", _PICK_TINT)
        batch.draw(shader)
    finally:
        gpu.state.blend_set('NONE')
        gpu.state.depth_test_set(prevDepthTest)


def _addPickHandlers(context: bpy.types.Context):
    """ One POST_PIXEL handler per space type on screen - handlers are registered per space
        type, not per area, so this covers every editor the pointer might land on. """
    for window in context.window_manager.windows:
        for area in window.screen.areas:
            space = area.spaces.active
            spaceType = type(space) if space is not None else None
            if spaceType is None or spaceType in _pickHandlers:
                continue
            try:
                _pickHandlers[spaceType] = spaceType.draw_handler_add(_drawPickTint, (), 'WINDOW', 'POST_PIXEL')
            except (AttributeError, TypeError, RuntimeError) as ex:
                # No highlight over that editor; picking it still works.
                debug.printError(f"V-Ray lister: no dock highlight for {spaceType}: {ex}")


def _removePickHandlers():
    for spaceType, handle in _pickHandlers.items():
        try:
            spaceType.draw_handler_remove(handle, 'WINDOW')
        except (AttributeError, ValueError, RuntimeError):
            pass
    _pickHandlers.clear()


def _redrawAllAreas(context: bpy.types.Context):
    for window in context.window_manager.windows:
        for area in window.screen.areas:
            area.tag_redraw()


def _isListerPane(window: bpy.types.Window, area) -> bool:
    """ Whether `area` is one of a lister's own panes. Only those are barred as dock targets. """
    return _windowKind(window) is not None and area.type == 'PREFERENCES'


def _areaUnderMouse(window: bpy.types.Window, event):
    """ The area of `window` the pointer is in, or None if there is none or it is a lister's
        own pane. """
    if window is None:
        return None
    x, y = event.mouse_x, event.mouse_y
    for area in window.screen.areas:
        if area.x <= x <= area.x + area.width and area.y <= y <= area.y + area.height:
            return None if _isListerPane(window, area) else area
    return None


class VRAY_OT_lister_dock_pick(VRayOperatorBase):
    """ Click-an-editor picker for the dock button: the hovered area lights up, clicking it
        moves the lister there. One instance per window, spawned by the primary. """
    bl_idname = "vray.lister_dock_pick"
    bl_label = "Dock Lister In Editor"
    bl_description = "Click an editor to move this lister into it. Right-click or Esc cancels"
    bl_options = {'INTERNAL'}

    # Set on the spawned siblings; the primary takes it from the window it was clicked in.
    kind: bpy.props.StringProperty(options={'HIDDEN', 'SKIP_SAVE'}, default='')

    _instances = []
    _kind = 'LISTER'

    def invoke(self, context, event):
        cls = type(self)
        isPrimary = not cls._instances

        if isPrimary:
            kind = self.kind or _windowKind(context.window)
            if kind not in _WINDOW_FLAGS:
                return {'CANCELLED'}
            if not any(not _isListerPane(w, a) for w in context.window_manager.windows for a in w.screen.areas):
                self.report({'WARNING'}, "No editor available to dock into")
                return {'CANCELLED'}
            cls._kind = kind
            _pickState.update({'active': True, 'areaPtr': 0, 'generation': _pickState['generation'] + 1})
            _addPickHandlers(context)

        self._generation = _pickState['generation']
        self._win = context.window
        self._cursorSet = False
        try:
            self._win.cursor_modal_set('PICK_AREA')
            self._cursorSet = True
        except (RuntimeError, TypeError):
            pass

        cls._instances.append(self)
        context.window_manager.modal_handler_add(self)

        if isPrimary:
            for window in context.window_manager.windows:
                if window != context.window:
                    with context.temp_override(window=window):
                        bpy.ops.vray.lister_dock_pick('INVOKE_DEFAULT', kind=cls._kind)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        cls = type(self)
        # Stale instance from a finished or earlier pick.
        if not _pickState['active'] or self._generation != _pickState['generation']:
            self._finish()
            return {'CANCELLED'}

        if event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
            _endPick(context)
            return {'CANCELLED'}

        area = _areaUnderMouse(self._win, event)

        if event.type == 'MOUSEMOVE':
            areaPtr = area.as_pointer() if area is not None else 0
            if areaPtr != _pickState['areaPtr']:
                _pickState['areaPtr'] = areaPtr
                _redrawAllAreas(context)
            return {'RUNNING_MODAL'}

        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            if area is None:
                return {'RUNNING_MODAL'}
            window = self._win
            _endPick(context)
            return _dockListerKind(self, context, cls._kind, window, area)

        # Swallow everything else while picking.
        return {'RUNNING_MODAL'}

    def _finish(self):
        if self._cursorSet:
            try:
                self._win.cursor_modal_restore()
            except (ReferenceError, AttributeError, RuntimeError):
                pass
            self._cursorSet = False
        try:
            type(self)._instances.remove(self)
        except ValueError:
            pass

    def cancel(self, context):
        # Blender force-cancels modals on window close without calling modal(). Keep this hook.
        self._finish()


def _endPick(context: bpy.types.Context):
    """ Stop the pick everywhere: drop the highlight, release every instance's cursor. """
    _pickState['active'] = False
    _pickState['areaPtr'] = 0
    _removePickHandlers()
    for instance in list(VRAY_OT_lister_dock_pick._instances):
        instance._finish()
    VRAY_OT_lister_dock_pick._instances.clear()
    _redrawAllAreas(context)


def _setListerOpen(context: bpy.types.Context, kind: str, isOpen: bool):
    """ Record (per-file, on the scene) whether this lister kind is open, so it can be
        reopened when the .blend is loaded again. Only written on a real change - the flag
        lives in the .blend, so a redundant write would mark the file dirty for nothing. """
    view = core.getListerView(context)
    prop = _OPEN_PROPS[kind]
    if view is not None and getattr(view, prop) != isOpen:
        setattr(view, prop, isOpen)


@bpy.app.handlers.persistent
def _onSaveSyncListerFlags(_e):
    """ Sync the per-file open flags with the lister markers on the screens before the file
        is written. Closing a lister window by its title bar never runs vray.lister_close,
        and the flag left set behind it pops the window open on every later load.

        A marker counts on a screen a window is showing, and on a docked host, whose
        workspace need not be the active one at save time. Nowhere else. Closing a Material
        Lister window leaves its split screen in bpy.data.screens with the marker still on
        it, which must not read as open.
    """
    windowScreens = {window.screen.name for window in bpy.context.window_manager.windows}

    openKinds = set()
    for screen in bpy.data.screens:
        if screen.name not in windowScreens and not screen.get(VRAY_DOCKED_FLAG, False):
            continue
        for kind, flag in _WINDOW_FLAGS.items():
            if screen.get(flag, False):
                openKinds.add(kind)

    for kind in _OPEN_PROPS:
        _setListerOpen(bpy.context, kind, kind in openKinds)


def getRegClasses():
    return (
        VRAY_PT_lister_options,
        VRAY_OT_lister_open,
        VRAY_OT_material_lister_open,
        VRAY_OT_lister_close,
        VRAY_OT_lister_toggle_params,
        VRAY_OT_lister_dock_pick,
    )


def onUnregister():
    """ Make sure no Preferences window is left with hijacked draw methods. """
    try:
        restorePreferences(bpy.context)
    except Exception as ex:
        debug.printError(f"V-Ray Scene Lister: failed to restore Preferences on unregister: {ex}")
