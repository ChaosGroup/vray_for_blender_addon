# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

# Generic, category-driven core of the V-Ray Scene Lister.
#
# A 'category' (Lights, Cameras, Geometry, ...) knows how to enumerate its entities,
# group them by type, which propgroup holds an entity's editable data, and which
# columns to show per group. The draw engine is category-agnostic: it filters, sorts
# and groups the entities and renders a table of live widgets drawn directly against
# the scene data, so every edit is immediate and undoable.

import re
from dataclasses import dataclass
from typing import Any, Callable, Optional

import bpy


############################################################
# Column and category descriptors
############################################################

@dataclass
class ColumnSpec:
    """ Description of a single lister column. """
    id: str
    label: str
    # Attribute on the row's propgroup, drawn as row.prop(propGroup, attr, text="");
    # skipped if absent on the group's propgroup
    attr: Optional[str] = None
    # Custom cell painter (row, entity, propGroup) used instead of 'attr'
    draw: Optional[Callable[[bpy.types.UILayout, Any, Any], None]] = None
    # Sort-key resolver (entity, propGroup) -> comparable value, for a column whose value is
    # type-dispatched in 'draw' and has no single 'attr' to sort by (e.g. merged light Intensity)
    sortValue: Optional[Callable[[Any, Any], Any]] = None
    # Relative column width (UILayout.scale_x)
    width: float = 2.0
    # Hidden by default; the user can still toggle it on per column from the Options menu
    defaultHidden: bool = False
    # Center the header and cells (for checkbox / icon-toggle columns whose widget isn't
    # an auto-detected boolean property)
    center: bool = False
    # If > 0, fixed width in UI units (UILayout.ui_units_x) instead of relative 'width';
    # for narrow icon columns (select, status) that should keep a constant size
    fixedWidth: float = 0.0
    # Name shown in the column picker when it should differ from the header label -
    # e.g. icon-only columns with an empty header label
    pickerLabel: Optional[str] = None


class ListerCategory:
    """ Base class for a lister category. Subclasses are registered as singletons. """
    id: str = ""
    label: str = ""
    icon: str = 'NONE'  # Blender icon enum or 0 for none
    # This category's value in the active-section EnumProperty. Blender stores the integer, so
    # it must stay fixed and unique: give a new category the next unused number, never renumber
    # an existing one, or a saved file reopens on the wrong section. -1 means unassigned.
    enumIndex: int = -1
    # True if entities are scene objects supporting selection / apply-to-selected
    selectable: bool = True
    # Role in the geometry combine/split toggle: 'split' categories (Proxies, Splats, ...) are
    # hidden when combined view is on; the 'combined' category (Geometry) when it is off.
    # Empty = always shown.
    geometryRole: str = ''
    # Per-category feature applicability. Datablock sections (Materials, Assets) get a fake-user
    # column and the Cleanup menu; every section but the read-only Assets file list can be renamed.
    hasFakeUserColumn: bool = False
    hasCleanup: bool = False
    supportsRename: bool = True
    # Draw the category's own full-width header (status + actions) on its own row, leaving the
    # shared row to the feature actions. Default: header and feature actions share one row.
    ownsHeaderRow: bool = False
    # False for a category with no column table (Materials). Such a category defines no columns.
    hasTable: bool = True

    def enumerate(self, context: bpy.types.Context) -> list:
        """ Return the entities belonging to this category. """
        return []

    def entityName(self, entity) -> str:
        """ Display name used for search and sorting (entities are usually scene
            objects with a .name; override when they are not).
        """
        return entity.name

    def groupKey(self, entity) -> str:
        """ Return a stable key identifying the type group an entity belongs to. """
        return ""

    def groupLabel(self, key: str) -> str:
        """ Human readable label for a group key. """
        return key

    def groupOrder(self) -> list[str]:
        """ Preferred order of group keys. Keys not listed are appended, sorted. """
        return []

    def getPropGroup(self, entity) -> Any:
        """ Return the live propgroup (bpy_struct) holding the editable data for
            the entity, or None if there is nothing to edit.
        """
        return None

    def columns(self, key: str) -> list[ColumnSpec]:
        """ Columns to show for the given group key. """
        return []

    def isGroupSelectable(self, key: str) -> bool:
        """ Whether the entities in this group are selectable scene objects - i.e. they
            get the auto-injected select/hide column and the selected/visible filters.
            Defaults to the category-wide 'selectable'; override for categories that mix
            object groups with non-object groups (e.g. Displacement: objects + materials). """
        return self.selectable

    def visibilityObject(self, entity, key: str, context: bpy.types.Context):
        """ The scene object whose viewport/render visibility gates this row in the
            'Viewport visible only' / 'Render visible only' filters, or None to leave the
            row unfiltered. Object sections gate on the row object itself; the Assets
            section maps a file reference to the object that owns it (texture / material /
            world references have no single owner and stay unfiltered). """
        return entity if self.isGroupSelectable(key) else None

    def supportsSolo(self, key: str = "") -> bool:
        """ Whether this group's rows offer the solo (isolate) feature, i.e. the solo column.
            Defaults to the group's selectability (only object rows can be soloed); override
            for categories whose rows drive an object-visibility solo without being objects
            themselves (Materials). """
        return self.isGroupSelectable(key)

    def soloScope(self, context: bpy.types.Context) -> list:
        """ The objects whose visibility solo toggles. Defaults to this category's own
            entities; override where the soloable rows are not themselves the objects to hide
            (a Material can be used by any object, so Materials scopes the whole view layer). """
        return self.enumerate(context)

    def soloMatches(self, obj, targetName: str) -> bool:
        """ Whether 'obj' (from soloScope) is the solo target and should stay visible.
            Defaults to a name match; override where the row identity is not the object name
            (Materials: an object matches when it uses the soloed material). """
        return obj.name == targetName

    def drawHeader(self, layout: bpy.types.UILayout, context: bpy.types.Context, entities: list):
        """ Optional category-level toolbar drawn above the table (e.g. bulk actions).
            'entities' is the already-enumerated, filtered list (so the header needs no
            second scan). Default: nothing. """
        pass

    def emptyMessage(self, state) -> str:
        """ Message shown when the section has no rows to display. Categories whose active
            filters change what "empty" means (e.g. Assets with 'missing only') override this. """
        return f"No {self.label.lower()} in the scene."

    def pickerSections(self, context: bpy.types.Context, state):
        """ Optional per-group breakdown for the column picker. Return a list of
            (sectionLabel, [ColumnSpec]) to show the picker split into labelled
            sub-sections (e.g. Materials: one heading per shader type); return None
            to use the default flat, deduplicated column list. """
        return None

    def pickerSectionsByGroup(self):
        """ A column picker split into one labelled section per group key (each group's
            columns minus the mandatory select/name). Categories whose groups have distinct
            column sets (Materials, Displacement) return this from pickerSections. """
        sections = []
        for key in self.groupOrder():
            cols = [c for c in self.columns(key) if c.id not in ('select', 'name', 'assign')]
            if cols:
                sections.append((self.groupLabel(key), cols))
        return sections


############################################################
# Category registry
############################################################

_CATEGORIES: list[ListerCategory] = []


def registerCategory(category: ListerCategory):
    if not any(c.id == category.id for c in _CATEGORIES):
        _CATEGORIES.append(category)


def clearCategories():
    _CATEGORIES.clear()


def getCategories() -> list[ListerCategory]:
    return list(_CATEGORIES)


def getCategory(categoryId: str) -> Optional[ListerCategory]:
    return next((c for c in _CATEGORIES if c.id == categoryId), None)


############################################################
# Settings storage: global layout in addon prefs, per-file view state on the scene
############################################################

# Settings kept on the addon preferences (global, persisted across sessions); everything
# else lives on the scene's lister state (per-file, saved in the .blend)
_PREFS_ATTRS = frozenset({'layout_mode', 'alignment_mode', 'geometry_combined',
                          'hidden_columns', 'unified_hide_exclusive', 'max_visible_rows'})


def getListerPrefs(context: bpy.types.Context):
    """ The lister's settings group (lister) on the addon preferences. """
    from vray_blender.lib.blender_utils import getVRayPreferences
    return getVRayPreferences(context).lister


def getListerView(context: bpy.types.Context):
    """ The scene's per-file lister state (open flag, active section, search, sort,
        selection filter, group collapse). """
    return getattr(context.scene, 'vray_lister', None)


class ListerState:
    """ Read/write facade over the two storage locations so the draw engine can use a
        single 'state' object: layout attributes resolve to the addon preferences,
        the rest to the scene's per-file state. NOTE: this is not a bpy_struct, so it
        must never be passed to layout.prop() - draw the real prefs/scene objects for
        that (window.py does).
    """
    __slots__ = ('_prefs', '_view')

    def __init__(self, context: bpy.types.Context):
        object.__setattr__(self, '_prefs', getListerPrefs(context))
        object.__setattr__(self, '_view', getListerView(context))

    def __getattr__(self, name):
        target = self._prefs if name in _PREFS_ATTRS else self._view
        return getattr(target, name)

    def __setattr__(self, name, value):
        if name in ('_prefs', '_view'):
            object.__setattr__(self, name, value)
            return
        target = self._prefs if name in _PREFS_ATTRS else self._view
        setattr(target, name, value)


def makeListerState(context: bpy.types.Context) -> 'ListerState':
    return ListerState(context)


def tagListerRedraw(context: bpy.types.Context):
    """ Request a redraw of all Preferences areas (where the lister is hosted). """
    for window in context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'PREFERENCES':
                area.tag_redraw()


def tagAllRedraw(context: bpy.types.Context):
    """ Redraw every area (e.g. after changing scene selection so the viewport
        and outliner update too, not just the lister window).
    """
    for window in context.window_manager.windows:
        for area in window.screen.areas:
            area.tag_redraw()


############################################################
# Reusable cell painters
############################################################

def drawSelectCell(row: bpy.types.UILayout, obj: bpy.types.Object, propGroup: Any):
    """ Selection toggle that mirrors and drives the scene selection. """
    selected = obj.select_get()
    op = row.operator(
        "vray.lister_select",
        text="",
        icon='RESTRICT_SELECT_OFF' if selected else 'RESTRICT_SELECT_ON',
        depress=selected,
    )
    op.object_name = obj.name


def drawNameCell(row: bpy.types.UILayout, obj, propGroup: Any):
    """ Editable entity name. """
    row.prop(obj, 'name', text="")


def drawMaterialUsersCell(row: bpy.types.UILayout, mtl, propGroup: Any):
    """ Select-the-users button for material rows (materials are not scene objects):
        selects every object that uses this material. """
    op = row.operator("vray.lister_select_material_users", text="", icon='RESTRICT_SELECT_OFF')
    op.material = mtl.name


def drawVisibilityCell(row: bpy.types.UILayout, obj, propGroup: Any):
    """ The object's three visibility toggles, in the same order and with the same glyphs as
        the Outliner's restriction columns:
          - eye     "Hide in Viewport"     - temporary, per view layer (hide_get/hide_set)
          - monitor "Disable in Viewports" - global, saved in the file (hide_viewport)
          - camera  "Disable in Renders"   - global, saved in the file (hide_render)
        The eye is the Outliner's default viewport toggle, so without it a row hidden from the
        Outliner looked unchanged here and vice versa (VBLD-2616).
    """
    line = row.row(align=True)
    # The eye is a Base (per-view-layer) flag, which Blender does not expose to Python as a
    # property, hence an operator instead of a prop; HIDE_ON/OFF are the Outliner's own glyphs
    hidden = obj.hide_get()
    op = line.operator("vray.lister_hide", text="",
                       icon='HIDE_ON' if hidden else 'HIDE_OFF', emboss=False)
    op.object_name = obj.name

    # Gaps so the three toggles read as separate controls
    line.separator(factor=0.4)
    # No explicit icon, so each toggle picks up its property's native RNA icon and on/off glyph
    # switching; hard-coding RESTRICT_VIEW_*/RESTRICT_RENDER_* showed the wrong glyph when off
    line.prop(obj, 'hide_viewport', text="", emboss=False)
    line.separator(factor=0.4)
    line.prop(obj, 'hide_render', text="", emboss=False)


# Shared visibility toggle column, auto-injected after the select column in every object
# section (see effectiveColumns). A touch wider than three icons so their gaps fit.
COL_VISIBILITY = ColumnSpec('hide', "", draw=drawVisibilityCell, fixedWidth=2.9, center=True,
                            pickerLabel="Visibility")


def makeStatusCell(kind: str, attr: str):
    """ Cell painter combining Chaos Cosmos origin and external-file status for an
        asset referenced by 'attr' on its propgroup:
          - from Cosmos, file present -> Cosmos icon
          - from Cosmos, file missing -> relink button (Cosmos relink icon)
          - local asset, file present -> check mark
          - local asset, file missing -> relink button (error icon)
        'kind' tells vray.relink_one how to resolve and relink the asset.
    """
    def draw(row: bpy.types.UILayout, obj, propGroup: Any):
        from vray_blender.ui import icons
        from vray_blender.ui.lister.relink import isFileMissing

        vrayData = getattr(getattr(obj, 'data', None), 'vray', None)
        isCosmos = bool(getattr(vrayData, 'cosmos_package_id', ""))
        path = getattr(propGroup, attr, "") if propGroup is not None else ""

        if not path:
            row.label(text="", icon='BLANK1')
            return

        if not isFileMissing(path):
            if isCosmos:
                row.label(text="", icon_value=icons.getIcon('COSMOS'))
            else:
                row.label(text="", icon='CHECKMARK')
            return

        sub = row.row()
        sub.alert = True
        if isCosmos:
            op = sub.operator("vray.relink_one", text="", icon_value=icons.getIcon('COSMOS_RELINK_ASSETS'))
        else:
            op = sub.operator("vray.relink_one", text="", icon='ERROR')
        op.object_name = obj.name
        op.kind = kind
    return draw


def makeFileCell(kind: str, attr: str):
    """ External-file path field with a browse button. Plain-string paths get the
        V-Ray file picker filtered to the asset kind's extensions (vray.relink_one);
        paths that are already FILE_PATH properties keep Blender's built-in browse
        button instead of showing a second one.
    """
    def draw(row: bpy.types.UILayout, obj, propGroup: Any):
        if propGroup is None or attr not in propGroup.bl_rna.properties:
            row.label(text="")
            return
        line = row.row(align=True)
        line.prop(propGroup, attr, text="")
        if propGroup.bl_rna.properties[attr].subtype != 'FILE_PATH':
            op = line.operator("vray.relink_one", text="", icon='FILE_FOLDER')
            op.object_name = obj.name
            op.kind = kind
    return draw


def drawTextureRef(row: bpy.types.UILayout, textureNode):
    """ Read-only reference to a texture node feeding a socket (materials, light colour,
        displacement, ...). A V-Ray Bitmap becomes a button whose tooltip shows the file
        path and whose click jumps to that texture in the Assets tab; any other (procedural)
        texture shows a plain labelled icon. """
    # The user's own label, else the node type. Never node.name: the .vrscene and Cosmos importers
    # name nodes '<Type>_<UUID>', so a raw name shows a UUID. The file name is deliberately left
    # out - this column is narrow and the button's tooltip already carries the path.
    name = textureNode.label or textureNode.bl_label
    from vray_blender.ui.lister import relink
    bitmap = relink.bitmapAssetRef(textureNode)
    if bitmap is None:
        row.label(text=name, icon='TEXTURE')
        return
    path, locator = bitmap
    op = row.operator("vray.lister_show_asset", text=name, icon='TEXTURE', emboss=False)
    op.path = path
    op.locator = locator


def isCategoryVisible(category: ListerCategory, combined: bool) -> bool:
    """ Whether a category appears in the navbar for the current combine toggle. """
    if category.id == 'MATERIALS':
        return False  # materials live in their own window, the Material Lister
    if combined and category.geometryRole == 'split':
        return False
    if (not combined) and category.geometryRole == 'combined':
        return False
    return True


def categoryEnumItems(self, context) -> list[tuple]:
    """ EnumProperty items callback for the active-category selector. The result
        is cached in a module global to avoid Blender's string-GC pitfall with
        dynamic enum callbacks. The geometry sections are shown either combined into
        a single 'Geometry' section or split per-kind, per the user's toggle.
    """
    # 'Combine Geometry' is a global layout setting on the addon preferences
    try:
        combined = bool(getListerPrefs(context).geometry_combined)
    except Exception:
        combined = False
    # Value is each category's fixed enumIndex, not its position in the visible list: Blender
    # stores the EnumProperty as that integer, so deriving it from the (filtered, sorted) list
    # would remap a saved selection to the wrong section. Sorted by label for display only.
    items = sorted(
        ((c.id, c.label, c.label, c.icon, c.enumIndex)
         for c in _CATEGORIES if isCategoryVisible(c, combined)),
        key=lambda it: it[1].lower())
    categoryEnumItems._cache = items
    return items


############################################################
# Column visibility / filtering
############################################################

def _attrExists(propGroup: Any, attr: str) -> bool:
    if propGroup is None:
        return False
    try:
        return attr in propGroup.bl_rna.properties
    except AttributeError:
        return False


def _columnToken(categoryId: str, colId: str) -> str:
    return f"{categoryId}::{colId}"


def _toggleToken(tokensStr: str, token: str) -> str:
    """ Add a token to (or remove it from) a space-separated set, returning the new
        string. Backs the per-section collapsed-group and hidden-column sets.
    """
    tokens = tokensStr.split()
    if token in tokens:
        tokens.remove(token)
    else:
        tokens.append(token)
    return " ".join(tokens)


def isColumnToggled(state, categoryId: str, colId: str) -> bool:
    """ True if the user flipped this column away from its default visibility. """
    return _columnToken(categoryId, colId) in state.hidden_columns.split()


def toggleColumn(state, categoryId: str, colId: str):
    """ Flip this column's visibility for the section (persisted in state). """
    state.hidden_columns = _toggleToken(state.hidden_columns, _columnToken(categoryId, colId))


def applyColumnPreset(state, category: 'ListerCategory', showAll: bool):
    """ Bulk-set a section's column visibility to a preset, by writing the per-column
        toggles directly (so the picker checkboxes match):
          - showAll=False ("Default"): drop the section's toggles -> default columns.
          - showAll=True  ("Show All"): toggle on every default-hidden column -> all visible.
    """
    prefix = f"{category.id}::"
    tokens = [t for t in state.hidden_columns.split() if not t.startswith(prefix)]
    if showAll:
        for col in collectCategoryColumns(category):
            if col.defaultHidden:
                tokens.append(_columnToken(category.id, col.id))
    state.hidden_columns = " ".join(tokens)


def isColumnVisible(state, categoryId: str, col: 'ColumnSpec') -> bool:
    # Visibility is the per-column toggle: visible unless default-hidden, with the user's
    # toggle flipping that
    visible = not col.defaultHidden
    if isColumnToggled(state, categoryId, col.id):
        visible = not visible
    return visible


def effectiveColumns(category: ListerCategory, key: str) -> list:
    """ A category's columns, with the shared hide/visibility toggle injected right
        after the select column for object (selectable) sections. Centralising it here
        means every object section gets it without each category repeating it. """
    cols = list(category.columns(key))
    if category.isGroupSelectable(key):
        insertAt = next((i + 1 for i, c in enumerate(cols) if c.id == 'select'), 0)
        cols.insert(insertAt, COL_VISIBILITY)
    # Add the lister's feature columns (pinned, solo, fake-user, ...)
    cols = injectColumns(category, key, cols)
    return cols


def _visibleColumns(category: ListerCategory, key: str, representativePropGroup: Any, state):
    cols = []
    for col in effectiveColumns(category, key):
        if not isColumnVisible(state, category.id, col):
            continue
        if col.attr is not None and not _attrExists(representativePropGroup, col.attr):
            continue
        cols.append(col)
    return cols


def collectCategoryColumns(category: ListerCategory):
    """ Deduplicated columns across all of the category's group kinds, for the per-
        section column picker. Uses the category's declared group order rather than
        only the groups present in the scene, so the picker still works when the
        section is empty. The mandatory select / name columns are excluded.
    """
    seen = set()
    ordered = []
    for key in category.groupOrder():
        for col in effectiveColumns(category, key):
            if col.id in ('select', 'name', 'assign') or col.id in seen:
                continue
            seen.add(col.id)
            ordered.append(col)
    return ordered


############################################################
# Grouping / sorting
############################################################

def _sortKey(value) -> tuple:
    """ Coerce an arbitrary property value into a comparable key. """
    if value is None:
        return (1, 0.0, "")
    if isinstance(value, (bool, int, float)):
        return (0, float(value), "")
    if isinstance(value, str):
        return (0, 0.0, value.lower())
    try:
        return (0, float(sum(value[:3])), "")  # color / vector
    except (TypeError, ValueError):
        return (0, 0.0, str(value).lower())


def _isSelected(entity) -> bool:
    getter = getattr(entity, 'select_get', None)
    return bool(getter()) if getter else False


def _collectGroups(category: ListerCategory, context: bpy.types.Context, state):
    """ Enumerate, filter, group and sort the entities. Returns an ordered list of
        (groupKey, [entities]).
    """
    # Raw (not lowercased): the matcher is case-insensitive and lowercasing would corrupt
    # regex escapes (see makeSearchMatcher)
    search = state.search.strip()
    selectedOnly = state.show_selected_only
    viewportOnly = state.show_viewport_visible_only
    renderOnly = state.show_render_visible_only
    missingOnly = state.show_missing_assets_only

    # Matched as a regex, falling back to a plain substring on an invalid pattern
    searchMatch = makeSearchMatcher(search)

    groups: dict[str, list] = {}
    for entity in category.enumerate(context):
        if search and not searchMatch(category.entityName(entity)):
            continue
        # Assets-only "missing files" filter: an AssetRef exposes isMissing (True/False); other
        # entities don't, so getattr yields None and this is a no-op outside the Assets section
        # (which is the only section that shows the toggle).
        if missingOnly and getattr(entity, 'isMissing', None) is False:
            continue
        key = category.groupKey(entity)
        # 'Selected only' applies only to selectable (scene-object) groups.
        if category.isGroupSelectable(key) and selectedOnly and not _isSelected(entity):
            continue
        # The visibility filters test the object a row maps to: the row object itself for
        # object sections, or an asset's owning object in the Assets section. None means the
        # row is not tied to a single object (a material / world reference), so it is exempt.
        if viewportOnly or renderOnly:
            visObj = category.visibilityObject(entity, key, context)
            if visObj is not None:
                if viewportOnly and not visObj.visible_get():
                    continue
                if renderOnly and visObj.hide_render:
                    continue
        groups.setdefault(key, []).append(entity)

    sortColId = state.sort_column
    reverse = state.sort_reverse
    for key, entities in groups.items():
        spec = next((c for c in category.columns(key) if c.id == sortColId), None)
        if spec is not None and spec.sortValue is not None:
            entities.sort(
                key=lambda e, f=spec.sortValue: (_sortKey(f(e, category.getPropGroup(e))),
                                                 category.entityName(e).lower()),
                reverse=reverse,
            )
        elif spec is not None and spec.attr is not None:
            entities.sort(
                key=lambda e, a=spec.attr: (_sortKey(getattr(category.getPropGroup(e), a, None)),
                                            category.entityName(e).lower()),
                reverse=reverse,
            )
        else:
            entities.sort(key=lambda e: category.entityName(e).lower(), reverse=reverse)
        # Stable-pin pinned rows to the front of the sorted group
        applyPinnedOrder(state, entities)

    order = category.groupOrder()
    orderedKeys = [k for k in order if k in groups]
    orderedKeys += sorted(k for k in groups if k not in order)
    return [(k, groups[k]) for k in orderedKeys]


############################################################
# Group collapse state (Stacked layout)
############################################################

def _groupToken(categoryId: str, key: str) -> str:
    return f"{categoryId}::{key}"


def isGroupCollapsed(state, categoryId: str, key: str) -> bool:
    return _groupToken(categoryId, key) in state.collapsed_groups.split()


def toggleGroup(state, categoryId: str, key: str):
    """ Collapse or expand this group for the section (persisted in state). """
    state.collapsed_groups = _toggleToken(state.collapsed_groups, _groupToken(categoryId, key))


############################################################
# Draw engine
############################################################

def drawLister(context: bpy.types.Context, layout: bpy.types.UILayout, category: ListerCategory, state):
    """ Render the lister for a category in the layout chosen by state.layout_mode. """
    # Only table categories get here. Materials has its own window, see drawMaterialLister*.
    groups = _collectGroups(category, context, state)

    # Header toolbar: the category's bulk actions and the feature actions (Copy to Selected,
    # Rename, ...) share one compact, right-aligned row. Entities are passed in (already
    # enumerated) so the header needs no second scan - matters for big scenes.
    headerEntities = [e for _k, ents in groups for e in ents]
    if category.ownsHeaderRow:
        # The category draws its own full-width header; only the feature actions move below
        category.drawHeader(layout, context, headerEntities)
        extrasRow = layout.row()
        extrasRow.alignment = 'RIGHT'
        drawHeaderExtras(extrasRow, context, category)
    else:
        headerRow = layout.row()
        headerRow.alignment = 'RIGHT'
        drawHeaderExtras(headerRow, context, category)
        category.drawHeader(headerRow, context, headerEntities)

    if not groups:
        box = layout.box()
        box.label(text=category.emptyMessage(state), icon='INFO')
        return

    if state.layout_mode == 'STACKED':
        _drawStacked(layout, category, groups, state)
    else:
        _drawTabbed(layout, category, groups, state)


def _drawTabbed(layout, category, groups, state):
    """ A row of type tabs on top and the table of the selected type below.
        Showing a single type keeps every column aligned.
    """
    keys = [k for k, _ in groups]
    active = state.active_group if state.active_group in keys else keys[0]

    tabs = layout.row(align=True)
    for key, entities in groups:
        op = tabs.operator(
            "vray.lister_set_group",
            text=f"{category.groupLabel(key)} ({len(entities)})",
            depress=(key == active),
        )
        op.group = key

    entities = next(e for k, e in groups if k == active)
    _drawGroupTable(layout, category, active, entities, state)


def _drawStacked(layout, category, groups, state):
    """ Every type stacked in collapsible group boxes. """
    # Columns visible in each group, computed once so sections can be lined up to a shared
    # layout per state.alignment_mode (otherwise each section sizes its columns independently)
    visibleByKey = {key: _visibleColumns(category, key, category.getPropGroup(entities[0]), state)
                    for key, entities in groups}

    alignMode = state.alignment_mode
    hideExclusive = state.unified_hide_exclusive
    unionSlots = _unionSlots(category, groups, visibleByKey, hideExclusive) if alignMode == 'UNIFIED' else None

    for key, entities in groups:
        box = layout.box()

        header = box.row(align=True)
        collapsed = isGroupCollapsed(state, category.id, key)
        op = header.operator(
            "vray.lister_toggle_group",
            text="",
            icon='DISCLOSURE_TRI_RIGHT' if collapsed else 'DISCLOSURE_TRI_DOWN',
            emboss=False,
        )
        op.category = category.id
        op.group = key
        header.label(text=f"{category.groupLabel(key)}  ({len(entities)})")

        if not collapsed:
            _drawGroupTable(box, category, key, entities, state, cols=visibleByKey[key],
                            alignMode=alignMode, unionSlots=unionSlots)


def _unionSlots(category: ListerCategory, groups: list, visibleByKey: dict,
                hideExclusive: bool = False) -> list[ColumnSpec]:
    """ The union of every group's visible columns, in first-seen order, merged into
        one shared set of column slots (widest width per id). 'Unified Grid' draws
        every section against these slots so all sections share an identical layout;
        a section with no value for a slot renders a blank cell.

        With hideExclusive, columns contributed by only one displayed group are dropped
        so the shared grid stays compact (no wide empty band for a type-only column like
        the IES file). The mandatory select/hide/name slots are always kept, and the
        compaction is skipped for single-group views (it would empty them). The dropped
        columns remain available in the Independent / Tabbed layouts and the picker.
    """
    order: list[str] = []
    byId: dict[str, ColumnSpec] = {}
    counts: dict[str, int] = {}
    for key, entities in groups:
        representative = category.getPropGroup(entities[0])
        for col in visibleByKey.get(key, []):
            counts[col.id] = counts.get(col.id, 0) + 1
            centered = _isBoolColumn(representative, col) or col.center
            slot = byId.get(col.id)
            if slot is None:
                order.append(col.id)
                byId[col.id] = ColumnSpec(col.id, col.label, width=col.width,
                                          center=centered, fixedWidth=col.fixedWidth)
            else:
                slot.width = max(slot.width, col.width)
                slot.fixedWidth = max(slot.fixedWidth, col.fixedWidth)
                slot.center = slot.center or centered
                if not slot.label and col.label:
                    slot.label = col.label
    if hideExclusive and len(groups) > 1:
        order = [i for i in order if counts[i] > 1 or i in ('select', 'hide', 'name')]
    return [byId[i] for i in order]


def _applyColumnWidth(uiCol, slot: ColumnSpec):
    """ Size one table column to the slot's own width (both Independent and Unified
        Grid use it; Unified Grid just feeds in shared union slots). """
    if slot.fixedWidth:
        uiCol.ui_units_x = slot.fixedWidth
    else:
        uiCol.scale_x = slot.width


def _drawGroupTable(layout: bpy.types.UILayout, category: ListerCategory, key: str,
                    entities: list, state, *, cols: list = None, alignMode: str = 'NONE',
                    unionSlots: list = None):
    representativePropGroup = category.getPropGroup(entities[0])
    ownCols = cols if cols is not None else _visibleColumns(category, key, representativePropGroup, state)
    if not ownCols:
        return

    box = layout.box()

    maxRows = state.max_visible_rows
    if len(entities) > maxRows:
        note = box.row()
        note.alert = True
        note.label(
            text=f"Showing first {maxRows} of {len(entities)} - use Search to narrow.",
            icon='INFO',
        )
        entities = entities[:maxRows]

    # The slots define the column layout: Unified Grid uses the shared union so all sections
    # line up, Independent uses this section's own columns. Cell content always comes from this
    # section's own column for the slot id (None -> blank cell).
    ownById = {c.id: c for c in ownCols}
    if alignMode == 'UNIFIED' and unionSlots:
        slots = unionSlots
        # The union slot already folded the center flag in across the sections
        centerFlags = [s.center for s in slots]
    else:
        slots = ownCols
        centerFlags = [_isBoolColumn(representativePropGroup, c) or c.center for c in slots]

    # Transposed layout: one vertical Blender column per lister column, each stacking a header
    # label then one cell per row, keeping cells aligned without manual split-factor bookkeeping
    tableRow = box.row()
    uiCols = []
    for slot, centered in zip(slots, centerFlags):
        uiCol = tableRow.column()
        _applyColumnWidth(uiCol, slot)
        uiCols.append(uiCol)
        _drawColumnHeader(uiCol, slot, state, centered)

    for entity in entities:
        propGroup = category.getPropGroup(entity)
        for uiCol, slot, centered in zip(uiCols, slots, centerFlags):
            cell = uiCol.row(align=True)
            if centered:
                cell.alignment = 'CENTER'
            col = ownById.get(slot.id)
            if col is None or (propGroup is None and col.attr is not None):
                cell.label(text="")  # column absent for this type, or nothing to edit
                continue
            if col.draw is not None:
                col.draw(cell, entity, propGroup)
            elif col.attr is not None:
                cell.prop(propGroup, col.attr, text="")


def _isBoolColumn(representativePropGroup: Any, col: ColumnSpec) -> bool:
    if col.attr is None or representativePropGroup is None:
        return False
    try:
        prop = representativePropGroup.bl_rna.properties.get(col.attr)
    except AttributeError:
        return False
    return prop is not None and prop.type == 'BOOLEAN'


def _drawColumnHeader(uiCol, col: ColumnSpec, state, centered: bool):
    """ Clickable header that sorts the rows by this column. """
    if not col.label:
        uiCol.label(text="")
        return
    isSorted = state.sort_column == col.id
    icon = 'NONE'
    if isSorted:
        icon = 'TRIA_DOWN' if state.sort_reverse else 'TRIA_UP'
    # Align the header to match the cell content below it: centered for checkbox/icon columns,
    # left otherwise so the label sits above the field's left edge, not floating over the column
    target = uiCol.row()
    target.alignment = 'CENTER' if centered else 'LEFT'
    op = target.operator("vray.lister_set_sort", text=col.label, icon=icon, emboss=False)
    op.column = col.id


############################################################
# Lister features: extra columns, search, pinning, solo state
#
# These add columns (injectColumns), a header action row (drawHeaderExtras) or change how the
# collector filters/orders rows (makeSearchMatcher / applyPinnedOrder). The operators they
# invoke live in ops.py; the shared runtime state (soloState, the editor material) lives here.
############################################################

def _entityId(entity) -> str:
    """ Stable per-entity key used for pinning (objects/materials use .name,
        asset rows their locator). """
    name = getattr(entity, 'name', None)
    if name:
        return name
    locator = getattr(entity, 'locator', None)
    if locator:
        return locator
    return getattr(entity, 'element', str(entity))


# Runtime-only solo state: category id -> {'targets': [name, ...], 'prev': {objName: hide_get}}.
# 'targets' is a list because solo is multi-select (Shift/Ctrl-click adds rows). Not persisted -
# solo is a momentary view state. Written by ops.VRAY_OT_lister_solo.
soloState: dict = {}


def soloTargets(categoryId: str) -> list:
    return soloState.get(categoryId, {}).get('targets', [])


def restoreSolo(category, context):
    """ Restore the pre-solo viewport visibility for a category, if it is soloed. """
    state = soloState.pop(category.id, None)
    if state is None:
        return
    for obj in category.soloScope(context):
        if obj.name in state['prev']:
            try:
                obj.hide_set(state['prev'][obj.name])
            except Exception:
                pass


def _soloColumn(category):
    categoryId = category.id

    def draw(row, obj, propGroup):
        soloed = getattr(obj, 'name', None) in soloTargets(categoryId)
        # Filled vs hollow circle, not the star (SOLO_ON/OFF), which is too easily confused
        # with the pin icon
        op = row.operator("vray.lister_solo", text="",
                          icon='RADIOBUT_ON' if soloed else 'RADIOBUT_OFF', emboss=False, depress=soloed)
        op.object_name = obj.name
        op.category = categoryId

    return ColumnSpec('solo', "", draw=draw, fixedWidth=1.0, center=True, pickerLabel="Solo")


def _fakeUserColumn(category):
    isAssets = category.id == 'ASSETS'

    def draw(row, entity, propGroup):
        datablock = getattr(entity, 'image', None) if isAssets else entity
        if datablock is None or not hasattr(datablock, 'use_fake_user'):
            row.label(text="")
            return
        row.prop(datablock, 'use_fake_user', text="",
                 icon='FAKE_USER_ON' if datablock.use_fake_user else 'FAKE_USER_OFF', emboss=False)

    return ColumnSpec('fake_user', "", draw=draw, fixedWidth=1.3, center=True, pickerLabel="Fake User")


def _renderColumn():
    def draw(row, obj, propGroup):
        op = row.operator("vray.lister_render_camera", text="", icon='RENDER_STILL')
        op.camera = obj.name

    return ColumnSpec('render', "", draw=draw, fixedWidth=1.5, center=True, pickerLabel="Render")


def makeSearchMatcher(search: str):
    """ A name-matcher for the collector: a case-insensitive regex, falling back to a
        case-insensitive substring test on an invalid pattern. The pattern is compiled with
        re.IGNORECASE and matched against the raw name - it must NOT be lowercased first, or
        regex escapes flip (e.g. '\\D' would become '\\d', matching the opposite class). """
    if not search:
        return lambda name: True
    try:
        pattern = re.compile(search, re.IGNORECASE)
        return lambda name: pattern.search(name) is not None
    except re.error:
        lowered = search.lower()
        return lambda name: lowered in name.lower()


def pinnedSet(view) -> set:
    raw = getattr(view, 'lister_pinned', "") if view is not None else ""
    return set(raw.splitlines())


def _pinnedColumn():
    # Build the pinned set once per column, not per cell, to avoid re-splitting the stored
    # string for every row
    pinned = pinnedSet(getListerView(bpy.context))

    def draw(row, entity, propGroup):
        eid = _entityId(entity)
        isPinned = eid in pinned
        op = row.operator("vray.lister_toggle_pinned", text="",
                          icon='PINNED' if isPinned else 'UNPINNED', emboss=False, depress=isPinned)
        op.entity = eid

    return ColumnSpec('pinned', "", draw=draw, fixedWidth=1.0, center=True, pickerLabel="Pinned")


def applyPinnedOrder(state, entities):
    """ Stable-pin pinned rows to the front of an already-sorted group. """
    pinned = pinnedSet(state)
    if pinned:
        entities.sort(key=lambda e: 0 if _entityId(e) in pinned else 1)


############################################################
# Material editor view (master-detail full draw)
############################################################

# Runtime-only "which material is open in the editor" (momentary view state). 'name' is what
# was clicked, 'displayed' what the parameters pane actually drew (it falls back to the first
# material when nothing was clicked). Write through setEditorMaterial().
_editorMaterial = {'name': ''}


def setEditorMaterial(name: str):
    """ Open a material in the editor. Drops 'displayed' too, so editorMaterialName() cannot
        keep reporting the previously shown material until the parameters pane redraws. """
    _editorMaterial['name'] = name
    _editorMaterial.pop('displayed', None)


def _drawNoShaderHint(layout, ntree):
    """ Name the actual fault. All three states look identical otherwise, and 'not connected'
        is plain wrong for a tree that has no output node to connect to. """
    from vray_blender.nodes import utils as NodesUtils

    if ntree is None or not ntree.nodes:
        layout.label(text="This material has no node tree.", icon='ERROR')
    elif NodesUtils.getOutputNode(ntree, 'MATERIAL') is None:
        layout.label(text="No V-Ray Material Output node in this material.", icon='ERROR')
    else:
        layout.label(text="No shader connected to the material output.", icon='INFO')


def _drawMaterialShader(layout, context, mat):
    """ The node UI (rollouts / widgets) of the material's shader, same as the Material Properties
        tab: it follows the user's node selection rather than being stuck on the BRDF, carries the
        navigation trail, and offers the texture pickers.

        The tree is passed explicitly instead of via navigation.getEditedTree(): the lister lives in
        a Preferences window, so scanning context.screen for a node editor is a guaranteed miss. """
    from vray_blender.lib import draw_utils
    from vray_blender.nodes import navigation as NodesNav
    from vray_blender.ui import classes, node_nav, node_slots
    from vray_blender.plugins import PLUGINS

    ntree = getattr(mat, 'node_tree', None)
    node = NodesNav.getPanelNode(ntree, 'MATERIAL')
    if node is None:
        _drawNoShaderHint(layout, ntree)
        return

    slotContext = node_slots.makeSlotContext(context, mat, ntree)

    if slotContext is not None:
        navCol = layout.column(align=True)
        navCol.use_property_split = False
        node_nav.drawNavigation(navCol, context, slotContext.ownerType, slotContext.ownerName,
                                ntree, 'MATERIAL', node)
        layout.separator()

    body = layout.column()
    body.use_property_split = True
    body.use_property_decorate = True
    try:
        with draw_utils.slotEditing(slotContext):
            classes.drawActiveNodePanel(context, body, node, PLUGINS)
    except Exception as ex:
        layout.label(text=f"Could not draw parameters: {ex}", icon='ERROR')


def _indentBody(layout):
    """ Indent a rollout body's content under its header by a small empty left column. Unlike
        draw_utils.subPanel it does NOT force use_property_split: the Output Options rollouts
        draw their enable checkbox + label in the header, and property-split squeezes those
        labels to a few characters. The shader body sets its own property split where needed. """
    split = layout.split(factor=0.05, align=True)
    split.column()
    return split.column()


############################################################
# Material parameters pane
############################################################

def _drawMaterialParams(layout, context, category, mat):
    """ The parameters pane: name, fake-user, then the Preview, Material and Output Options
        rollouts. Keep this pane in its own editor area - layout.panel() paints across the region. """
    from vray_blender.nodes import utils as NodesUtils

    header = layout.row(align=True)
    header.prop(mat, 'name', text="")
    header.prop(mat, 'use_fake_user', text="",
                icon='FAKE_USER_ON' if mat.use_fake_user else 'FAKE_USER_OFF')

    # Collapsible: template_preview() is what starts the preview render job (ED_preview_draw),
    # so a closed rollout costs nothing.
    previewHeader, previewBody = layout.panel("mateditor_preview", default_closed=False)
    previewHeader.label(text="Preview", icon='MATERIAL')
    if previewBody is not None:
        try:
            # The token changes only when a material no object uses was edited, which is the one
            # case a stable id cannot serve: see nodes.utils.tagMaterialPreview.
            previewBody.template_preview(mat, show_buttons=False,
                                         preview_id=f"mateditor_preview_{NodesUtils.loosePreviewToken['n']}")
        except Exception:
            previewBody.label(text="(preview unavailable)")

    if not getattr(getattr(mat, 'vray', None), 'is_vray_class', False):
        layout.label(text="Non-V-Ray material", icon='INFO')
        layout.prop(mat, 'diffuse_color', text="Viewport Color")
        return

    # Material rollout: the shader's grouped parameters, indented under the header
    materialHeader, materialBody = layout.panel("mateditor_material", default_closed=False)
    materialHeader.label(text=f"Material - {category.shaderLabel(mat)}", icon='NODE_MATERIAL')
    if materialBody is not None:
        _drawMaterialShader(_indentBody(materialBody), context, mat)

    # Output Options rollout: the per-material output plugins (Wrapper / ID / ...)
    outputHeader, outputBody = layout.panel("mateditor_output", default_closed=True)
    outputHeader.label(text="Output Options", icon='SETTINGS')
    if outputBody is not None:
        _drawMaterialOptions(_indentBody(outputBody), context, mat)


# Per-material option plugins (the "Material Options" subpanels of the Material tab):
# (propgroup attr on mtl.vray, plugin type, label)
_MATERIAL_OPTIONS = (
    ('MtlWrapper',     'MtlWrapper',     "Wrapper"),
    ('MtlMaterialID',  'MtlMaterialID',  "Material ID"),
    ('MtlRoundEdges',  'MtlRoundEdges',  "Round Edges"),
    ('MtlRenderStats', 'MtlRenderStats', "Render Stats"),
)


def _drawMaterialOptions(layout, context, mat):
    """ The per-material option plugins (Wrapper / Material ID / Round Edges / Render
        Stats), each a collapsible rollout with an enable checkbox in its header, gated
        on its own 'use' flag - mirroring the Material Options subpanels. """
    from vray_blender.ui import classes
    from vray_blender.lib import draw_utils
    from vray_blender.plugins import getPluginModule

    vrayMtl = getattr(mat, 'vray', None)
    if vrayMtl is None:
        return

    for attr, pluginType, label in _MATERIAL_OPTIONS:
        propGroup = getattr(vrayMtl, attr, None)
        if propGroup is None or 'use' not in propGroup.bl_rna.properties:
            continue
        # Draw through the shared rollout helper so each plugin's body is indented under its
        # enable-checkbox header (a raw layout.panel() would leave it flush with the header)
        body = draw_utils.rollout(layout, f"mateditor_opt_{attr}", label, defaultClosed=True,
                                  usePropDataSrc=propGroup, usePropName='use')
        if body is not None:
            body.enabled = propGroup.use
            classes.drawPluginUI(context, body, propGroup, getPluginModule(pluginType))


def _editorMaterials(context, category):
    """ The materials shown in the editor, honouring the Material Lister's own search. """
    view = getListerView(context)
    materials = category.enumerate(context)
    # Raw, not lowercased: makeSearchMatcher is case-insensitive already, and lowercasing
    # would corrupt regex escapes (see its docstring)
    search = (view.material_search if view is not None else '').strip()
    if search:
        match = makeSearchMatcher(search)
        materials = [m for m in materials if match(category.entityName(m))]
    return materials


def _editorSelectedMaterial(materials):
    """ The material open in the detail panel (selected one, or the first as a fallback). """
    selectedName = _editorMaterial.get('name', '')
    mat = bpy.data.materials.get(selectedName) if selectedName else None
    if mat is None or mat not in materials:
        mat = materials[0] if materials else None
    # Remember what the detail panel actually shows (may be the materials[0] fallback) so
    # editorMaterialName() reports it without re-enumerating materials itself
    _editorMaterial['displayed'] = mat.name if mat is not None else ''
    return mat


def isUnassigned(mat) -> bool:
    """ Nothing in the scene uses this material. Worth flagging: it is dropped on reload unless
        it has a fake user, and it is in no depsgraph (see nodes.utils.tagMaterialPreview).
        Also decides how long the panes keep repainting - see window._repaintWhileRendering. """
    return (mat.users - (1 if mat.use_fake_user else 0)) <= 0


def _drawUnassignedMark(row, mat):
    """ Subtle 'nothing uses this' marker. Always drawn - blank for a used material - so the
        name button keeps the same width and place whether or not the marker is showing. """
    mark = row.row()
    mark.active = False  # dim it; there is nothing to click here
    mark.label(text="", icon='UNLINKED' if isUnassigned(mat) else 'BLANK1')


def _drawMaterialSolo(row, matName):
    """ Per-material solo toggle. Shift/Ctrl-click adds to the solo set. """
    soloed = matName in soloTargets('MATERIALS')
    op = row.operator("vray.lister_solo", text="",
                      icon='RADIOBUT_ON' if soloed else 'RADIOBUT_OFF', emboss=False, depress=soloed)
    op.object_name = matName
    op.category = 'MATERIALS'


def editorMaterialName() -> str:
    """ The material the editor is showing. 'displayed' is what got drawn, 'name' what was clicked. """
    return _editorMaterial.get('displayed') or _editorMaterial.get('name', '')


# Thumbnail grid geometry, in UI units. The cell adds the box border around the preview. Large
# is Blender's own preview resolution; past it the same image is just drawn upscaled.
_THUMBNAIL_SCALES = {'SMALL': 3.0, 'MEDIUM': 5.0, 'LARGE': 6.5}
_THUMBNAIL_CELL_PADDING_UNITS = 1.4


def thumbnailScale(context) -> float:
    return _THUMBNAIL_SCALES[getListerPrefs(context).material_thumbnail_size]


def _drawMaterialThumbCell(layout, mat, selectedName: str, scale: float):
    """ One thumbnail cell: preview, then the solo toggle and name. """
    cell = layout.box().column(align=True)
    try:
        cell.template_icon(icon_value=mat.preview_ensure().icon_id, scale=scale)
    except Exception:
        cell.label(text="", icon='MATERIAL')
    nameRow = cell.row(align=True)
    _drawMaterialSolo(nameRow, mat.name)
    op = nameRow.operator("vray.lister_editor_select_material", text=mat.name,
                          depress=(mat.name == selectedName))
    op.material = mat.name
    _drawUnassignedMark(nameRow, mat)


def _drawMaterialThumbnailGrid(layout, materials, selectedName: str, listWidth: float):
    """ Grid of preview thumbnails, packed from the left at a fixed cell size. Built from nested
        split() calls - row() and grid_flow() stretch a lone cell and ignore ui_units_x. """
    widgetUnit = 20.0 * bpy.context.preferences.system.ui_scale
    scale = thumbnailScale(bpy.context)
    cellWidth = (scale + _THUMBNAIL_CELL_PADDING_UNITS) * widgetUnit
    avail = max(listWidth, cellWidth)
    columns = max(1, int(avail // cellWidth))

    grid = layout.column(align=True)
    container = None
    remaining = avail
    for i, mat in enumerate(materials):
        if (i % columns) == 0:
            container = grid.row(align=True)
            remaining = avail
        # Keep the factor below 1.0 - the split needs a remainder for the next cell.
        cellSplit = container.split(factor=min(0.999, cellWidth / max(remaining, 1.0)), align=True)
        _drawMaterialThumbCell(cellSplit.column(align=True), mat, selectedName, scale)
        container = cellSplit.column(align=True)
        remaining -= cellWidth


def _drawMaterialListInto(layout, materials, listWidth: float = 0.0):
    """ The clickable material list, as text rows or a thumbnail grid per the List Display
        preference. 'listWidth' is the pane's drawable width in pixels (0 = unknown). """
    col = layout.column(align=True)
    col.label(text=f"Materials  ({len(materials)})", icon='MATERIAL')
    # The name the parameters pane is showing, so the highlighted row matches it even when
    # nothing was clicked and the pane fell back to the first material
    selectedName = editorMaterialName()
    if not materials:
        col.label(text="No materials.", icon='INFO')
        return

    thumbnails = getListerPrefs(bpy.context).material_editor_list_display == 'THUMBNAILS'
    if thumbnails:
        _drawMaterialThumbnailGrid(col, materials, selectedName, listWidth)
        return

    listCol = col.column(align=True)
    for mat in materials:
        row = listCol.row(align=True)
        _drawMaterialSolo(row, mat.name)
        # Each row shows the material's own swatch, like Blender's material slot list. Beyond
        # looking right, the icon render job notifies NC_WINDOW, which is what repaints this
        # Preferences-hosted area - template_preview's NC_MATERIAL notifier is dropped here
        # (every SPACE_USERPREF region listener is a stub), so the big preview relies on it.
        try:
            iconId = mat.preview_ensure().icon_id
        except Exception:
            iconId = 0
        op = row.operator("vray.lister_editor_select_material", text=mat.name,
                          icon_value=iconId, depress=(mat.name == selectedName))
        op.material = mat.name
        _drawUnassignedMark(row, mat)


############################################################
# Material Lister panes
############################################################

# Approximate panel inset from the region edge, in UI units. Only feeds the thumbnail column count.
_PANEL_INSET_UNITS = 0.5

# Padding the list's box() eats out of the pane, in UI units. Approximate.
_LIST_BOX_PAD_UNITS = 1.0


def drawMaterialListerParams(context, layout):
    """ The Material Lister's right pane: the selected material's preview and parameters. """
    category = getCategory('MATERIALS')
    if category is None:
        return
    materials = _editorMaterials(context, category)
    mat = _editorSelectedMaterial(materials)
    if mat is None:
        layout.label(text="Select a material to edit.", icon='INFO')
    else:
        _drawMaterialParams(layout, context, category, mat)


def drawMaterialListerMain(context, layout):
    """ The Material Lister's left pane: the material list, as text rows or a thumbnail grid. """
    category = getCategory('MATERIALS')
    if category is None:
        return
    # The pane owns its whole region, less the inset either side and the box padding.
    widgetUnit = 20.0 * context.preferences.system.ui_scale
    listWidth = max(context.region.width - (2.0 * _PANEL_INSET_UNITS + _LIST_BOX_PAD_UNITS) * widgetUnit, 1.0)
    _drawMaterialListInto(layout, _editorMaterials(context, category), listWidth)


def resetRuntimeState():
    """ Drop the runtime-only view state (solo target, open material) on file load: the freshly
        loaded file has different objects and materials, so a stale solo target would keep hiding
        rows for a solo the new file knows nothing about. """
    soloState.clear()
    _editorMaterial.clear()
    _editorMaterial['name'] = ''


############################################################
# Column injection hook (core.effectiveColumns)
############################################################

def _insertColumns(cols, leftCols, afterNameCols, appendCols):
    result = list(cols)

    def indexAfter(ids):
        last = -1
        for i, c in enumerate(result):
            if c.id in ids:
                last = i
        return last + 1 if last >= 0 else 0

    for col in reversed(leftCols):
        result.insert(indexAfter({'select', 'hide'}), col)
    for col in reversed(afterNameCols):
        result.insert(indexAfter({'name', 'element'}), col)
    result.extend(appendCols)
    return result


def injectColumns(category, key, cols):
    """ Add the lister's extra feature columns to a group's column list. Called from
        effectiveColumns. """
    leftCols, afterNameCols, appendCols = [], [], []

    leftCols.append(_pinnedColumn())
    if category.supportsSolo(key):
        leftCols.append(_soloColumn(category))
    if category.id == 'CAMERAS':
        afterNameCols.append(_renderColumn())
    if category.hasFakeUserColumn:
        appendCols.append(_fakeUserColumn(category))

    if not (leftCols or afterNameCols or appendCols):
        return cols
    return _insertColumns(cols, leftCols, afterNameCols, appendCols)


############################################################
# Header toolbar hook (drawLister) + Options popover (window.py)
############################################################

def drawHeaderExtras(layout, context, category, iconOnly: bool = False):
    """ The lister's header actions (Copy to Selected, Rename, Cleanup) as a compact button group.
        iconOnly drops the button text for the material list's icon-only header. """
    showCopy = category.selectable
    showRename = category.supportsRename
    showCleanup = category.hasCleanup

    if not (showCopy or showRename or showCleanup):
        return

    bar = layout.row(align=True)
    if showCopy:
        bar.operator("vray.lister_copy_to_selected", text="" if iconOnly else "Copy to Selected",
                     icon='PASTEDOWN').category = category.id
    if showRename:
        bar.operator("vray.lister_batch_rename", text="" if iconOnly else "Rename",
                     icon='SORTALPHA').category = category.id
    if showCleanup:
        bar.menu("VRAY_MT_lister_cleanup", text="" if iconOnly else "Cleanup", icon='TRASH')


def drawOptions(layout, prefs):
    """ The lister Options popover's tail. List Display lives in the Material Lister's header. """
    layout.separator()
    layout.prop(prefs, 'assign_highlight', text="Highlight Drop Target")
