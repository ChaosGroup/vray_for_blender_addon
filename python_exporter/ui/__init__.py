# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later


__all__ = [ 'classes' ]


def _get_physics_panels():
    import bpy

    panels = []
    for panel in bpy.types.Panel.__subclasses__():
        if hasattr(panel, 'COMPAT_ENGINES') and 'BLENDER_RENDER' in panel.COMPAT_ENGINES:
            if panel.__name__.startswith('PHYSICS'):
                panels.append(panel)

    return panels



def _getRegPackages():
    from vray_blender.ui import classes
    from vray_blender.ui import icons
    from vray_blender.ui import properties_data_fur
    from vray_blender.ui import properties_data_geometry
    from vray_blender.ui import properties_data_camera
    from vray_blender.ui import properties_data_lamp
    from vray_blender.ui import properties_data_empty
    from vray_blender.ui import properties_material
    from vray_blender.ui import properties_object
    from vray_blender.ui import properties_output
    from vray_blender.ui import properties_particles
    from vray_blender.ui import properties_render
    from vray_blender.ui import properties_render_layers
    from vray_blender.ui import properties_texture
    from vray_blender.ui import properties_world
    from vray_blender.ui import properties_view_3d
    from vray_blender.ui import menus
    from vray_blender.ui import preferences

    return (
        icons,
        classes,
        properties_data_fur,
        properties_data_geometry,
        properties_data_camera,
        properties_data_lamp,
        properties_data_empty,
        properties_material,
        properties_object,
        properties_output,
        properties_particles,
        properties_render,
        properties_render_layers,
        properties_texture,
        properties_world,
        properties_view_3d,
        menus,
        preferences
    )

def register():
    # draw_callbacks can't be imported when using blender's background mode because of the blender gpu api limitations
    import bpy
    if bpy.app.background == False:
        from vray_blender.ui import draw_callbacks

    for p in _getRegPackages():
        p.register()

    if bpy.app.background == False:
        draw_callbacks.register()

    from vray_blender.ui import classes
    for panel in _get_physics_panels():
        for vrayEngine in classes.VRayEngines:
            panel.COMPAT_ENGINES.add(vrayEngine)


def unregister():

    # draw_callbacks can't be imported when using blender's background mode because of the blender gpu api limitations
    import bpy
    if bpy.app.background == False:
        from vray_blender.ui import draw_callbacks

    for p in reversed(_getRegPackages()):
        p.unregister()

    if bpy.app.background == False:
        draw_callbacks.unregister()

    from vray_blender.ui import classes
    for panel in _get_physics_panels():
        for vrayEngine in classes.VRayEngines:
            if vrayEngine in panel.COMPAT_ENGINES:
                panel.COMPAT_ENGINES.remove(vrayEngine)
