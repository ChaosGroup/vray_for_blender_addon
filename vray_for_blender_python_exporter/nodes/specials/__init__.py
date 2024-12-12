
from . import outputs
from . import selector
from . import environment
from . import geometry
from . import brdf
from . import texture
from . import material
from . import effects
from . import renderchannels
from . import inputlist
from . import transform
from . import debug
from . import object_properties


def register():
    outputs.register()
    selector.register()
    environment.register()
    geometry.register()
    brdf.register()
    texture.register()
    material.register()
    effects.register()
    renderchannels.register()
    inputlist.register()
    transform.register()
    debug.register()
    object_properties.register()


def unregister():
    outputs.unregister()
    selector.unregister()
    environment.unregister()
    geometry.unregister()
    brdf.unregister()
    texture.unregister()
    material.unregister()
    effects.unregister()
    renderchannels.unregister()
    inputlist.unregister()
    transform.unregister()
    debug.unregister()
    object_properties.unregister()
