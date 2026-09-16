# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later


__all__ = []


def register():
    from . import add_tree
    from . import import_vrscene
    from . import misc
    from . import wrangler

    add_tree.register()
    import_vrscene.register()
    misc.register()
    wrangler.register()


def unregister():
    from . import add_tree
    from . import import_vrscene
    from . import misc
    from . import wrangler

    wrangler.unregister()
    add_tree.unregister()
    import_vrscene.unregister()
    misc.unregister()
