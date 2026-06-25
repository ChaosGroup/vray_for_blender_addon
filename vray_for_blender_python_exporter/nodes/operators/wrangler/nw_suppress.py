# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

""" Suppress Node Wrangler bindings that collide with V-Ray wrangler shortcuts.

    NW operators bound to the same keys as V-Ray's wrangler (Alt+S, Alt+X, ...)
    fire in V-Ray node trees on Blender versions where NW wins keymap dispatch.
    NW's ops don't understand V-Ray's socket types and silently destroy
    V-Ray-specific links.

    Strategy - generalises the original Shift+W workaround:
      * Disable every Node Editor kmi whose combo matches a V-Ray-owned
        shortcut and whose idname isn't ours. Capture (combo -> idname +
        props) so we can forward it.
      * Register one generic forwarder op `vray.nw_proxy`. Its poll returns
        False in V-Ray editors. In stock editors it looks up the event's
        combo and dispatches the captured target via bpy.ops.
      * Bind a proxy kmi for each captured combo to the addon keyconfig.

    Hooked into `addon_utils.enable` and `load_post` so suppression survives
    mid-session NW toggles and startup propagation.
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
            if hasattr(value, '__iter__') and not isinstance(value, str):
                value = tuple(value)
            out[name] = value
        except Exception:
            pass
    return out


class VRAY_OT_nw_proxy(bpy.types.Operator):
    """ Forward an NW key event to the original NW op in non-V-Ray editors. """
    bl_idname = "vray.nw_proxy"
    bl_label = "Forward Node Wrangler"
    bl_options = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return not isVrayEditor(context)

    def invoke(self, context, event):
        combo = (event.type, event.value, event.ctrl, event.shift, event.alt)
        entry = _PROXY_TABLE.get(combo)
        if entry is None:
            return {'CANCELLED'}
        target, props = entry
        try:
            module, name = target.split('.', 1)
            getattr(getattr(bpy.ops, module), name)('INVOKE_DEFAULT', **props)
        except Exception as e:
            self.report({'WARNING'}, f"Forward to {target} failed: {e}")
            return {'CANCELLED'}
        return {'FINISHED'}


def _isLiveKmi(kmi) -> bool:
    try:
        kmi.active
        return True
    except (RuntimeError, ReferenceError):
        return False


def _applySuppression():
    """ Disable NW kmis on V-Ray combos and bind proxy kmis. Idempotent. """
    # Drop refs invalidated by NW disable/re-enable cycles - keeps _DISABLED bounded.
    _DISABLED[:] = [k for k in _DISABLED if _isLiveKmi(k)]

    # Capture + disable. addon first so props come from the source kmi -
    # user-keyconfig copies can lose StringProperty values during propagation.
    for kcName in ('addon', 'user'):
        kc = getattr(bpy.context.window_manager.keyconfigs, kcName, None)
        km = kc and kc.keymaps.get('Node Editor')
        if not km:
            continue
        for kmi in km.keymap_items:
            if not kmi.active or kmi.idname.startswith('vray.'):
                continue
            combo = (kmi.type, kmi.value, kmi.ctrl, kmi.shift, kmi.alt)
            if combo not in _OWNED_COMBOS:
                continue
            if combo not in _PROXY_TABLE:
                _PROXY_TABLE[combo] = (kmi.idname, _captureProps(kmi))
            kmi.active = False
            _DISABLED.append(kmi)

    # Bind proxy kmis for each captured combo (addon keyconfig).
    addon = bpy.context.window_manager.keyconfigs.addon
    if not addon:
        return
    km = addon.keymaps.get('Node Editor') or addon.keymaps.new(
        name='Node Editor', space_type='NODE_EDITOR'
    )
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

    _originalEnable = addon_utils.enable
    addon_utils.enable = _patchedEnable
    if _onFileLoad not in bpy.app.handlers.load_post:
        bpy.app.handlers.load_post.append(_onFileLoad)

    _applySuppression()


def unregister():
    global _originalEnable

    if _originalEnable is not None:
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
