

def register():
    from . import image_texture, uvw_mapping
    image_texture.register()
    uvw_mapping.register()


def unregister():
    from . import image_texture, uvw_mapping
    image_texture.unregister()
    uvw_mapping.unregister()