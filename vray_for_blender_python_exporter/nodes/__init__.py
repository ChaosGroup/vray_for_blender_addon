__all__ = [ 'importing' ]

def _getModules():
    """ Modules requiring registration/unregistration """
    from vray_blender.nodes import meta
    from vray_blender.nodes import sockets
    from vray_blender.nodes import specials
    from vray_blender.nodes import nodes
    from vray_blender.nodes import operators
    from vray_blender.nodes import tree
    from vray_blender.nodes import docs

    return (
        operators,
        tree,
        sockets,
        specials,
        meta,
        nodes,
        docs,
    )

def register():
    for module in _getModules():
        module.register()


def unregister():
    for module in reversed(_getModules()):
        module.unregister()
