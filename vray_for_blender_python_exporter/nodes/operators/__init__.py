# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later


__all__ = []


def register():
    from . import add_tree
    from . import misc

    add_tree.register()
    misc.register()


def unregister():
    from . import add_tree
    from . import misc

    add_tree.unregister()
    misc.unregister()
