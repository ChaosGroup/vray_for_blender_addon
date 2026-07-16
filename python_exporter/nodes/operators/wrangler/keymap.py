# SPDX-FileCopyrightText: Blender Foundation
# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Default shortcuts mirror Blender's Node Wrangler add-on (GPL-2.0-or-later).

""" Keymap registration for the V-Ray node-editor operators.
    Every bound operator polls V-Ray tree types only, so stock node addons
    keep owning the same shortcut in their own editors.
"""

import bpy


# (idname, key, value, ctrl, shift, alt, props-or-None)
_KEY_DEFS = (
    # Preview Node - Ctrl+Shift+LMB
    ("vray.wr_preview_node",          'LEFTMOUSE',   'PRESS', True,  True,  False, None),
    # Lazy Connect - Alt+RMB (autolink) and Shift+Alt+RMB (socket picker).
    # Mirrors Blender Node Wrangler's bindings.
    ("vray.wr_lazy_connect",          'RIGHTMOUSE',  'PRESS', False, False, True,  (('with_menu', False),)),
    ("vray.wr_lazy_connect",          'RIGHTMOUSE',  'PRESS', False, True,  True,  (('with_menu', True),)),
    # Link to Output - O
    ("vray.wr_link_out",              'O',           'PRESS', False, False, False, None),
    # Link Active to Selected - K
    ("vray.wr_link_active_to_selected", 'K',          'PRESS', False, False, False, None),
    # Delete Unused - Alt+X
    ("vray.wr_del_unused",            'X',           'PRESS', False, False, True,  None),
    # Swap Links - Alt+S (forward cycle) and Shift+Alt+S (reverse cycle)
    ("vray.wr_swap_links",            'S',           'PRESS', False, False, True,  (('reverse', False),)),
    ("vray.wr_swap_links",            'S',           'PRESS', False, True,  True,  (('reverse', True),)),
    # Detach Outputs - Shift+Alt+D
    ("vray.wr_detach_outputs",        'D',           'PRESS', False, True,  True,  None),
    # Add Reroutes - / (ALL), Shift+/ (LOOSE), Alt+/ (LINKED)
    ("vray.wr_add_reroutes",          'SLASH',       'PRESS', False, False, False, (('option', 'ALL'),)),
    ("vray.wr_add_reroutes",          'SLASH',       'PRESS', False, True,  False, (('option', 'LOOSE'),)),
    ("vray.wr_add_reroutes",          'SLASH',       'PRESS', False, False, True,  (('option', 'LINKED'),)),
    # Align Nodes - Shift+=
    ("vray.wr_align_nodes",           'EQUAL',       'PRESS', False, True,  False, None),
    # Select Parent/Children - ] / [
    ("vray.wr_select_parent_child",   'RIGHT_BRACKET', 'PRESS', False, False, False, (('option', 'CHILD'),)),
    ("vray.wr_select_parent_child",   'LEFT_BRACKET',  'PRESS', False, False, False, (('option', 'PARENT'),)),
    # Labels
    ("vray.wr_copy_label",            'V',           'PRESS', False, True,  False, (('option', 'FROM_ACTIVE'),)),
    ("vray.wr_clear_label",           'L',           'PRESS', False, False, True,  (('option', False),)),
    ("vray.wr_modify_labels",         'L',           'PRESS', False, True,  True,  None),
    # Wrangler menu popup - Shift+W (wrapper op has a real poll so Blender
    # falls through to Node Wrangler's Shift+W in non-V-Ray editors)
    ("vray.wr_menu_popup",            'W',           'PRESS', False, True,  False, None),
    # Reset Selected - Backspace
    ("vray.wr_reset_nodes",           'BACK_SPACE',  'PRESS', False, False, False, None),
    # Merge Nodes - numpad shortcuts for the common blend ops
    ("vray.wr_merge_nodes",           'NUMPAD_0',    'PRESS', True,  False, False, (('mode', 'MIX'),)),
    ("vray.wr_merge_nodes",           'NUMPAD_PLUS', 'PRESS', True,  False, False, (('mode', 'ADD_COLOR'),)),
    ("vray.wr_merge_nodes",           'NUMPAD_MINUS','PRESS', True,  False, False, (('mode', 'SUB_COLOR'),)),
    ("vray.wr_merge_nodes",           'NUMPAD_ASTERIX','PRESS', True, False, False, (('mode', 'MUL_COLOR'),)),
    ("vray.wr_merge_nodes",           'NUMPAD_SLASH', 'PRESS', True, False, False, (('mode', 'DIV_COLOR'),)),
    # Same numpad keys with Shift = float variants
    ("vray.wr_merge_nodes",           'NUMPAD_PLUS', 'PRESS', True,  True,  False, (('mode', 'ADD_FLOAT'),)),
    ("vray.wr_merge_nodes",           'NUMPAD_MINUS','PRESS', True,  True,  False, (('mode', 'SUB_FLOAT'),)),
    ("vray.wr_merge_nodes",           'NUMPAD_ASTERIX','PRESS', True, True,  False, (('mode', 'MUL_FLOAT'),)),
    ("vray.wr_merge_nodes",           'NUMPAD_SLASH','PRESS', True,  True,  False, (('mode', 'DIV_FLOAT'),)),
    # Add PBR Texture Setup - Ctrl+Shift+T
    ("vray.wr_add_pbr_setup",         'T',           'PRESS', True,  True,  False, None),
    # Copy Settings to Selected - Shift+C
    ("vray.wr_copy_settings",         'C',           'PRESS', False, True,  False, None),
    # Lazy Mix - Ctrl+Shift+RMB
    ("vray.wr_lazy_mix",              'RIGHTMOUSE',  'PRESS', True,  True,  False, None),
)


_registered: list = []


def register():
    if bpy.app.background:
        return
    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon
    if not kc:
        return

    km = kc.keymaps.get('Node Editor') or kc.keymaps.new(name='Node Editor', space_type='NODE_EDITOR')

    for idname, key, value, ctrl, shift, alt, props in _KEY_DEFS:
        kmi = km.keymap_items.new(idname, key, value, ctrl=ctrl, shift=shift, alt=alt)
        if props:
            for pName, pVal in props:
                setattr(kmi.properties, pName, pVal)
        _registered.append((km, kmi))


def unregister():
    for km, kmi in _registered:
        try:
            km.keymap_items.remove(kmi)
        except (RuntimeError, ReferenceError):
            pass
    _registered.clear()
