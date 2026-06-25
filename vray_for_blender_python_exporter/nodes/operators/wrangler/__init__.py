# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

""" Node-editor convenience operators for V-Ray trees.
    Poll only on V-Ray tree types so they coexist with other node addons.

    Several modules contain code derived from Blender's Node Wrangler
    add-on (GPL-2.0-or-later, Blender Foundation); see each file header.
"""


__all__ = []


def _getModules():
    # keymap and menu must register last, they reference operator idnames
    # that the other modules need to have registered first.
    from . import (
        structural,
        layout,
        labels,
        lazy_connect,
        preview_node,
        settings,
        merge,
        pbr_import,
        presets,
        quick_setup,
        menu,
        keymap,
        nw_suppress,
    )
    return (
        structural,
        layout,
        labels,
        lazy_connect,
        preview_node,
        settings,
        merge,
        pbr_import,
        presets,
        quick_setup,
        menu,
        keymap,
        nw_suppress,
    )


def register():
    for mod in _getModules():
        mod.register()


def unregister():
    for mod in reversed(_getModules()):
        mod.unregister()
