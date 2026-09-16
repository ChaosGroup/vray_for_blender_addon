# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

__all__ = [ 'importing' ]


def _getModules():
    """ Modules requiring registration/unregistration """
    from vray_blender.nodes import tree
    from vray_blender.nodes import meta
    from vray_blender.nodes import sockets
    from vray_blender.nodes import specials
    from vray_blender.nodes import group
    from vray_blender.nodes import nodes
    from vray_blender.nodes import operators
    from vray_blender.nodes import docs
    from vray_blender.nodes import tools

    return (
        operators,
        tree,
        # Descendants need to be registered before base classes (specials before sockets)
        specials,
        sockets,
        meta,
        group,  # After sockets (V-Ray socket types available), before nodes (menus need it)
        nodes,
        docs,
        tools,  # Registers the node-editor draw handler that refines imported layouts
    )


def register():
    for module in _getModules():
        module.register()


def unregister():
    for module in reversed(_getModules()):
        module.unregister()
