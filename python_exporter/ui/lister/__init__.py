# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

# V-Ray Scene Lister: a category-driven, table-style scene management window
# (lights, cameras, proxies, ...) hosted in a floating Preferences window.

import bpy

from vray_blender.lib import blender_utils
from vray_blender.ui.lister import core, ops, window, relink, assign_drag
from vray_blender.ui.lister.state import VRayListerState
from vray_blender.ui.lister.categories import lights, cameras, geometry, clippers, displacement, materials, assets


@bpy.app.handlers.persistent
def _onDepsgraphRedraw(scene, depsgraph):
    """ The Preferences editor that hosts the lister does not refresh on scene
        changes, so an object added/removed while the lister is open would not
        appear until the user switched tabs. Redraw any open lister window on every
        depsgraph update (cheap: it only flags a redraw, and only when one is open).
    """
    for wm in bpy.data.window_managers:
        for win in wm.windows:
            screen = win.screen
            if screen.get(window.VRAY_LISTER_FLAG) or screen.get(window.VRAY_MATERIAL_LISTER_FLAG):
                for area in screen.areas:
                    if area.type == 'PREFERENCES':
                        area.tag_redraw()


def _getCategoryModules():
    # Order here defines the order of the category tabs.
    return (lights, cameras, geometry, clippers, displacement, materials, assets)


def _moduleCategories(mod):
    """ A module may expose a single 'category' or a 'categories' sequence. """
    cats = getattr(mod, 'categories', None)
    return list(cats) if cats is not None else [mod.category]


def _moduleRegClasses(mod):
    """ A category module may expose its own bpy classes (menus, operators) via an
        optional getRegClasses() - e.g. the Lights tab's Convert-to-V-Ray menu. """
    fn = getattr(mod, 'getRegClasses', None)
    return tuple(fn()) if fn is not None else ()


def _allRegClasses():
    classes = ops.getRegClasses() + window.getRegClasses() + relink.getRegClasses() + assign_drag.getRegClasses()
    for mod in _getCategoryModules():
        classes += _moduleRegClasses(mod)
    return classes


def register():
    from vray_blender import features
    from vray_blender.features import Feature

    # The whole Scene Lister subsystem (classes, the Scene.vray_lister property and the
    # depsgraph/load handlers) is gated behind the OBJECT_LISTER feature flag.
    if not features.isEnabled(Feature.OBJECT_LISTER):
        return

    core.clearCategories()
    for mod in _getCategoryModules():
        for category in _moduleCategories(mod):
            core.registerCategory(category)

    bpy.utils.register_class(VRayListerState)
    for regClass in _allRegClasses():
        bpy.utils.register_class(regClass)

    # Per-file lister state lives on the Scene (saved in the .blend); the global
    # layout settings live on the addon preferences (see ui/preferences.py).
    bpy.types.Scene.vray_lister = bpy.props.PointerProperty(type=VRayListerState)

    blender_utils.addEvent(bpy.app.handlers.depsgraph_update_post, _onDepsgraphRedraw)
    blender_utils.addEvent(bpy.app.handlers.load_post, window._onLoadClearListerFlags)
    blender_utils.addEvent(bpy.app.handlers.save_pre, window._onSaveSyncListerFlags)


def unregister():
    from vray_blender import features
    from vray_blender.features import Feature

    # Nothing was registered when the feature is disabled (see register()).
    if not features.isEnabled(Feature.OBJECT_LISTER):
        return

    blender_utils.delEvent(bpy.app.handlers.depsgraph_update_post, _onDepsgraphRedraw)
    blender_utils.delEvent(bpy.app.handlers.load_post, window._onLoadClearListerFlags)
    blender_utils.delEvent(bpy.app.handlers.save_pre, window._onSaveSyncListerFlags)

    window.onUnregister()

    if hasattr(bpy.types.Scene, 'vray_lister'):
        del bpy.types.Scene.vray_lister

    for regClass in reversed(_allRegClasses()):
        bpy.utils.unregister_class(regClass)
    bpy.utils.unregister_class(VRayListerState)

    core.clearCategories()
