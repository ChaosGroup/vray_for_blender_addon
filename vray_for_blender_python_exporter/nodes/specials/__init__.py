
def _getModules():
    """ Modules requiring registration/unregistration """
    from vray_blender.nodes.specials import outputs
    from vray_blender.nodes.specials import selector
    from vray_blender.nodes.specials import environment
    from vray_blender.nodes.specials import geometry
    from vray_blender.nodes.specials import texture
    from vray_blender.nodes.specials import material
    from vray_blender.nodes.specials import effects
    from vray_blender.nodes.specials import renderchannels
    from vray_blender.nodes.specials import inputlist
    from vray_blender.nodes.specials import transform
    from vray_blender.nodes.specials import debug
    from vray_blender.nodes.specials import object_properties

    return (
        outputs,
        selector,
        environment,
        geometry,
        texture,
        material,
        effects,
        renderchannels,
        inputlist,
        transform,
        debug,
        object_properties,
    )

def register():
    for module in _getModules():
        module.register()


def unregister():
    for module in reversed(_getModules()):
        module.unregister()
