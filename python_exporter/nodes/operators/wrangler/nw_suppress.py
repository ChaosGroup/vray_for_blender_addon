# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

""" Suppress Node Wrangler bindings that collide with V-Ray wrangler shortcuts.

    NW binds ops to the same keys as V-Ray's wrangler (Alt+S, Alt+X, ...) and
    wins keymap dispatch on some Blender versions, corrupting V-Ray links
    because NW doesn't understand V-Ray's socket types.

    Only NW's own bindings are touched (`node.nw_*` ops, `wm.call_menu` ->
    `NODE_MT_nw_*`). Native Blender ops and other addons are left alone: addon
    keymaps dispatch before the default keymap, so V-Ray's poll-gated kmi wins
    in V-Ray editors and falls through natively everywhere else. (An earlier
    version also disabled native ops and broke macros like
    `node.select_link_viewer`.)

    Strategy:
      * Disable matched NW kmis in the addon keyconfig only (rebuilt fresh
        each launch, never saved), capturing combo -> idname/props to forward.
      * Register a generic proxy op `vray.nw_proxy` bound to each captured
        combo. It polls false in V-Ray editors; elsewhere it replays the
        captured NW op via bpy.ops.
      * Heal: re-enable native bindings an earlier version left disabled in
        the saved user keyconfig.

    Hooked into `addon_utils.enable` and `load_post` so this survives NW
    being toggled mid-session or on startup.
"""

import bpy
import addon_utils

from vray_blender.nodes.operators.wrangler.poll import isVrayEditor
from vray_blender.nodes.operators.wrangler.keymap import _KEY_DEFS


_OWNED_COMBOS = frozenset(
    (key, value, ctrl, shift, alt)
    for (_, key, value, ctrl, shift, alt, _) in _KEY_DEFS
)

# (type, value, ctrl, shift, alt) -> (target_idname, captured_props_dict)
_PROXY_TABLE: dict[tuple, tuple[str, dict]] = {}
_DISABLED: list[bpy.types.KeyMapItem] = []
_ADDED: list[tuple[bpy.types.KeyMap, bpy.types.KeyMapItem]] = []
_originalEnable = None


def _captureProps(kmi) -> dict:
    """ Read kmi.properties into a dict of bpy.ops-passable kwargs.
        Vectors/colors become tuples; anything unreadable is skipped.
    """
    out = {}
    for name in kmi.properties.keys():
        try:
            value = getattr(kmi.properties, name)
            # Macro sub-op props aren't bpy.ops-passable - skip, forward uses defaults.
            if isinstance(value, bpy.types.OperatorProperties):
                continue
            if hasattr(value, '__iter__') and not isinstance(value, str):
                value = tuple(value)
            out[name] = value
        except Exception:
            pass
    return out


def _opRegistered(idname: str) -> bool:
    """ True if `idname`'s operator is still registered. NW's ops unregister
        when it's disabled mid-session, so this lets the proxy fall through to
        the native binding instead of swallowing the event. Uses
        get_rna_type() rather than a bpy.types lookup so it also catches
        C builtins like wm.call_menu (bpy.types only sees Python-registered ops).
    """
    module, _, name = idname.partition('.')
    try:
        getattr(getattr(bpy.ops, module), name).get_rna_type()
        return True
    except Exception:
        return False


class VRAY_OT_nw_proxy(bpy.types.Operator):
    """ Forward an NW key event to the original NW op in non-V-Ray editors. """
    bl_idname = "vray.nw_proxy"
    bl_label = "Forward Node Wrangler"
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return not isVrayEditor(context)

    def invoke(self, context, event):
        # PASS_THROUGH (not CANCELLED) on a miss, so the native op still fires.
        combo = (event.type, event.value, event.ctrl, event.shift, event.alt)
        entry = _PROXY_TABLE.get(combo)
        if entry is None:
            return {'PASS_THROUGH'}
        target, props = entry
        if not _opRegistered(target):
            return {'PASS_THROUGH'}  # NW disabled mid-session
        try:
            module, name = target.split('.', 1)
            result = getattr(getattr(bpy.ops, module), name)('INVOKE_DEFAULT', **props)
        except Exception as e:
            self.report({'WARNING'}, f"Forward to {target} failed: {e}")
            return {'PASS_THROUGH'}
        # NW menu class gone (NW disabled) -> wm.call_menu returns CANCELLED.
        if 'CANCELLED' in result:
            return {'PASS_THROUGH'}
        return {'FINISHED'}


def _isLiveKmi(kmi) -> bool:
    try:
        kmi.active
        return True
    except (RuntimeError, ReferenceError):
        return False


def _isNodeWranglerKmi(kmi) -> bool:
    """ True only for NW's own bindings: `node.nw_*` ops and `wm.call_menu`
        menus named `NODE_MT_nw_*`. Leaves native/other-addon bindings intact.
    """
    if kmi.idname.startswith('node.nw_'):
        return True
    if kmi.idname == 'wm.call_menu':
        try:
            return kmi.properties.name.startswith('NODE_MT_nw_')
        except Exception:
            return False
    return False


def _healUserKeyconfig():
    """ Re-enable native (non-V-Ray, non-NW) kmis on V-Ray-owned combos that an
        earlier over-broad version left disabled in the saved user keyconfig.
        NW's own entries are skipped - re-enabling them would override the
        addon-keyconfig disable below.
    """
    user = bpy.context.window_manager.keyconfigs.user
    km = user and user.keymaps.get('Node Editor')
    if not km:
        return
    for kmi in km.keymap_items:
        if kmi.active or kmi.idname.startswith('vray.') or _isNodeWranglerKmi(kmi):
            continue
        if (kmi.type, kmi.value, kmi.ctrl, kmi.shift, kmi.alt) in _OWNED_COMBOS:
            kmi.active = True


def _applySuppression():
    """ Disable NW's kmis on V-Ray combos and bind proxy kmis to forward them.
        Idempotent.
    """
    # Drop refs invalidated by NW disable/re-enable cycles - keeps _DISABLED bounded.
    _DISABLED[:] = [k for k in _DISABLED if _isLiveKmi(k)]

    _healUserKeyconfig()

    # Disable NW in the addon keyconfig only - it's never saved, so the user
    # keyconfig stays clean; the keymap merge still propagates the disable.
    addon = bpy.context.window_manager.keyconfigs.addon
    if not addon:
        return
    km = addon.keymaps.get('Node Editor') or addon.keymaps.new(
        name='Node Editor', space_type='NODE_EDITOR'
    )
    for kmi in km.keymap_items:
        if not kmi.active or not _isNodeWranglerKmi(kmi):
            continue
        combo = (kmi.type, kmi.value, kmi.ctrl, kmi.shift, kmi.alt)
        if combo not in _OWNED_COMBOS:
            continue
        if combo not in _PROXY_TABLE:
            _PROXY_TABLE[combo] = (kmi.idname, _captureProps(kmi))
        kmi.active = False
        _DISABLED.append(kmi)

    # Bind proxy kmis for each captured combo (addon keyconfig).
    bound = {(k.type, k.value, k.ctrl, k.shift, k.alt) for _, k in _ADDED}
    for combo in _PROXY_TABLE.keys():
        if combo in bound:
            continue
        keyType, value, ctrl, shift, alt = combo
        kmi = km.keymap_items.new(
            VRAY_OT_nw_proxy.bl_idname, keyType, value,
            ctrl=ctrl, shift=shift, alt=alt,
        )
        _ADDED.append((km, kmi))


def _patchedEnable(*args, **kwargs):
    result = _originalEnable(*args, **kwargs)
    _applySuppression()
    return result


@bpy.app.handlers.persistent
def _onFileLoad(*args):
    _applySuppression()


def register():
    global _originalEnable

    bpy.utils.register_class(VRAY_OT_nw_proxy)

    # Wrap addon_utils.enable once - a double register() re-wrapping our own
    # wrapper would recurse infinitely.
    if addon_utils.enable is not _patchedEnable:
        _originalEnable = addon_utils.enable
        addon_utils.enable = _patchedEnable
    if _onFileLoad not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_onFileLoad)

    _applySuppression()


def unregister():
    global _originalEnable

    # Only restore if we're still the active wrapper - another addon may have
    # wrapped it again on top of ours.
    if _originalEnable is not None and addon_utils.enable is _patchedEnable:
        addon_utils.enable = _originalEnable
    _originalEnable = None

    if _onFileLoad in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.remove(_onFileLoad)

    for km, kmi in _ADDED:
        try:
            km.keymap_items.remove(kmi)
        except (RuntimeError, ReferenceError):
            pass
    _ADDED.clear()

    for kmi in _DISABLED:
        try:
            kmi.active = True
        except (RuntimeError, ReferenceError):
            pass
    _DISABLED.clear()
    _PROXY_TABLE.clear()

    bpy.utils.unregister_class(VRAY_OT_nw_proxy)
