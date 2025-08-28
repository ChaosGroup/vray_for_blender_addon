

__all__ = []


def register():
    from vray_blender.utils import cosmos_handler
    from vray_blender.utils import utils_bake
    cosmos_handler.register()
    utils_bake.register()


def unregister():
    from vray_blender.utils import cosmos_handler
    from vray_blender.utils import utils_bake
    cosmos_handler.unregister()
    utils_bake.unregister()
