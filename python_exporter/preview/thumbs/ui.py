# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

""" The node editor's own thumbnail controls: an icon-only header toggle, plus the full set
    appended to the Overlays popover, so they sit where a Blender user looks for them instead
    of only in preferences.
"""

import bpy

from vray_blender.lib.blender_utils import getVRayPreferences

# Validated against the running Blender's icon set at register time: an unknown identifier raises
# inside a draw callback, the worst place to find out.
_TOGGLE_ICON = 'MATSPHERE'
_TOGGLE_ICON_FALLBACK = 'IMAGE_DATA'


def _isVRayNodeEditor(context) -> bool:
    space = context.space_data
    return ((space is not None) and (space.type == 'NODE_EDITOR')
            and (space.tree_type == 'VRayNodeTreeEditor'))


def _drawHeaderToggle(self, context):
    """ Appended to the node editor header, so it lands at the right-hand end. """
    if not _isVRayNodeEditor(context):
        return

    try:
        prefs = getVRayPreferences()
    except Exception:
        return

    self.layout.prop(prefs, 'node_thumbnails', text="", icon=_TOGGLE_ICON)


def _drawOverlayControls(self, context):
    if not _isVRayNodeEditor(context):
        return

    try:
        prefs = getVRayPreferences()
    except Exception:
        return

    layout = self.layout
    layout.separator()
    layout.label(text="V-Ray")
    col = layout.column(align=True)
    col.prop(prefs, 'node_thumbnails', text="Node Thumbnails")
    sub = col.column(align=True)
    sub.enabled = prefs.node_thumbnails
    sub.prop(prefs, 'node_thumbnail_size', text="Size")
    sub.prop(prefs, 'node_thumbnail_resolution', text="Resolution")


def register():
    global _TOGGLE_ICON

    icons = bpy.types.UILayout.bl_rna.functions['prop'].parameters['icon'].enum_items
    if _TOGGLE_ICON not in icons:
        _TOGGLE_ICON = _TOGGLE_ICON_FALLBACK

    if (panel := getattr(bpy.types, 'NODE_PT_overlay', None)) is not None:
        panel.append(_drawOverlayControls)
    if (header := getattr(bpy.types, 'NODE_HT_header', None)) is not None:
        header.append(_drawHeaderToggle)


def unregister():
    for owner, fn in ((getattr(bpy.types, 'NODE_PT_overlay', None), _drawOverlayControls),
                      (getattr(bpy.types, 'NODE_HT_header', None), _drawHeaderToggle)):
        if owner is not None:
            try:
                owner.remove(fn)
            except Exception:
                pass
