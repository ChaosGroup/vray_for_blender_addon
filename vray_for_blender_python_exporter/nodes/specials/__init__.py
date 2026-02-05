
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
    from vray_blender.nodes.specials import object_properties
    from vray_blender.nodes.specials import gradient_ramp

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
        object_properties,
        gradient_ramp,
    )


def register():
    for module in _getModules():
        module.register()


def unregister():
    for module in reversed(_getModules()):
        module.unregister()
