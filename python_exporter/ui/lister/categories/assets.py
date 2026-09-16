# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

# Assets category: one row per external-file reference (bitmaps, proxies, scenes, splats,
# scanned / VRmat materials, OCIO, IES, camera files), grouped by kind. Each row shows the
# file's status, where it is used, and its editable path, with per-row and bulk relinking
# and open-file actions. Driven by the same scan as the relinker (relink.collectAssetRefs),
# so any new file-bearing V-Ray plugin appears here automatically.

from vray_blender.ui.lister.core import ListerCategory, ColumnSpec
from vray_blender.ui.lister import relink


_GROUP_ORDER = ['BITMAP', 'PROXY', 'SCENE', 'SPLAT', 'SCANNED', 'VRMAT', 'OCIO', 'IES', 'LUMINAIRE', 'CAMERA']


def _drawSelect(row, rec, propGroup):
    selected = relink.isAssetSelected(rec.locator)
    op = row.operator("vray.asset_select", text="",
                      icon='CHECKBOX_HLT' if selected else 'CHECKBOX_DEHLT',
                      emboss=False, depress=selected)
    op.mode = 'TOGGLE'
    op.ref = rec.locator


def _drawStatus(row, rec, propGroup):
    # The status icon IS the relink button: red error = missing, green check = found.
    # Clicking either opens the file picker, so there is no separate relink column.
    if rec.isMissing:
        cell = row.row()
        cell.alert = True
        op = cell.operator("vray.relink_asset", text="", icon='ERROR')
    else:
        op = row.operator("vray.relink_asset", text="", icon='CHECKMARK', emboss=False)
    op.ref = rec.locator
    op.kind = rec.kind


def _drawType(row, rec, propGroup):
    row.label(text="", icon=relink.kindIcon(rec.kind))


def _drawElement(row, rec, propGroup):
    row.label(text=rec.element)


def _drawPath(row, rec, propGroup):
    if rec.image is not None:
        row.prop(rec.image, 'filepath', text="")
    elif rec.propGroup is not None and rec.attr in rec.propGroup.bl_rna.properties:
        row.prop(rec.propGroup, rec.attr, text="")
    else:
        row.label(text=rec.path)


def _drawOpen(row, rec, propGroup):
    line = row.row(align=True)
    # No action in this cell can do anything while the file is missing from disk -
    # opening it, opening its folder and reading a proxy back all need it there.
    line.enabled = not rec.isMissing
    op = line.operator("vray.asset_open_folder", text="", icon='FILE_FOLDER')
    op.ref = rec.locator
    if rec.kind == 'PROXY':
        # A .vrmesh has no viewer; convert it to a Blender mesh instead.
        op = line.operator("vray.proxy_to_mesh", text="", icon='MESH_DATA')
        op.object_name = rec.objName
    elif rec.kind == 'BITMAP':
        # Only bitmaps have a default app; other kinds are V-Ray formats - VBLD-2615.
        op = line.operator("vray.asset_open_file", text="", icon='WINDOW')
        op.ref = rec.locator


# Status / Type are icon-only columns (no header text, no sorting).
_COL_SELECT     = ColumnSpec('select',  "",              draw=_drawSelect,  fixedWidth=1.2, center=True)
_COL_STATUS     = ColumnSpec('status',  "",              draw=_drawStatus,  fixedWidth=1.4, center=True)
_COL_TYPE       = ColumnSpec('type',    "",              draw=_drawType,    fixedWidth=1.4, center=True)
_COL_ELEMENT    = ColumnSpec('element', "Scene Element", draw=_drawElement, width=2.5)
_COL_PATH       = ColumnSpec('path',    "Path",          draw=_drawPath,    width=4.0)
# Bitmaps and splats both expose 'rgb_color_space'; the generic prop cell edits it.
_COL_COLORSPACE = ColumnSpec('rgb_color_space', "Color Space", attr='rgb_color_space', width=1.8)
_COL_OPEN       = ColumnSpec('open',    "",              draw=_drawOpen,    fixedWidth=2.8, center=True)

# Kinds whose propgroup carries an editable color space.
_COLORSPACE_KINDS = frozenset({'BITMAP', 'SPLAT'})


class AssetsCategory(ListerCategory):
    id = 'ASSETS'
    label = "Assets"
    icon = 'FILE'
    enumIndex = 11
    selectable = False  # rows are file references, not scene objects
    ownsHeaderRow = True       # draws its own full-width status + actions header
    hasFakeUserColumn = True
    hasCleanup = True
    supportsRename = False      # rows are file references, not renamable datablocks

    def enumerate(self, context):
        return relink.collectAssetRefs(context)

    def emptyMessage(self, state):
        if getattr(state, 'show_missing_assets_only', False):
            return "No missing assets in the scene."
        return super().emptyMessage(state)

    def entityName(self, rec):
        return rec.element

    def groupKey(self, rec):
        return rec.kind

    def groupLabel(self, key):
        return relink.kindLabel(key)

    def groupOrder(self):
        return _GROUP_ORDER

    def getPropGroup(self, rec):
        # None for image-backed bitmaps; the cell draws read the record directly
        return rec.propGroup

    def visibilityObject(self, rec, key, context):
        # Only file references owned by a single scene object (proxies, splats, V-Ray
        # scenes, IES lights, cameras) follow that object's viewport / render visibility;
        # texture / material / world references have no single owner (objName == '') and
        # stay unfiltered.
        if not rec.objName:
            return None
        return context.scene.objects.get(rec.objName)

    def columns(self, key):
        cols = [_COL_SELECT, _COL_STATUS, _COL_TYPE, _COL_ELEMENT, _COL_PATH]
        if key in _COLORSPACE_KINDS:
            cols.append(_COL_COLORSPACE)
        cols.append(_COL_OPEN)
        return cols

    def drawHeader(self, layout, context, entities):
        # 'entities' is the already-enumerated list; the cached file-existence check
        # keeps the missing count cheap on big scenes.
        missing = sum(1 for r in entities if r.isMissing)

        bar = layout.row()
        info = bar.row(align=True)
        icon = 'ERROR' if missing else 'CHECKMARK'
        info.label(text=f"{len(entities)} asset(s), {missing} missing", icon=icon)
        info.operator("vray.asset_refresh", text="", icon='FILE_REFRESH')
        bar.separator_spacer()

        # Selection group, a gap, then the action menus (Relink / Paths), separating the
        # bulk actions from the selection helpers.
        selectionGroup = bar.row(align=True)
        op = selectionGroup.operator("vray.asset_select", text="Select Missing")
        op.mode = 'MISSING'
        op = selectionGroup.operator("vray.asset_select", text="Clear")
        op.mode = 'NONE'

        bar.separator()

        actionGroup = bar.row(align=True)
        actionGroup.menu("VRAY_MT_asset_relink", text="Relink", icon='FILE_FOLDER')
        actionGroup.menu("VRAY_MT_asset_paths", text="Paths")


category = AssetsCategory()
