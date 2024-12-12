
__all__ = [ 'classes' ]


def _get_physics_panels():
    import bpy

    panels = []
    for panel in bpy.types.Panel.__subclasses__():
        if hasattr(panel, 'COMPAT_ENGINES') and 'BLENDER_RENDER' in panel.COMPAT_ENGINES:
            if panel.__name__.startswith('PHYSICS'):
                panels.append(panel)

    return panels


def drawVRayInteractiveRenderMenu(self, context):
    """ Draws buttons for V-Ray settings in the 3d viewport bar """

    from vray_blender.ui.classes import pollEngine
    if pollEngine(context):

        # TODO This is temporary look of this layout. It will be redesigned in near future
        from vray_blender.engine.render_engine import VRayRenderEngine
        from vray_blender.ui.icons import getUIIcon
        from vray_blender import operators as ops
        from vray_blender.bin import VRayBlenderLib as vray

        iprRow = self.layout.row()
        iprRow.enabled = vray.isInitialized()
        if not VRayRenderEngine.iprRenderer:
            op = ops.VRAY_OT_render_interactive
            iprRow.operator(op.bl_idname, text='', icon_value=getUIIcon(op))
        else:
            op = ops.VRAY_OT_render_interactive_stop
            iprRow.operator(op.bl_idname, text='', icon_value=getUIIcon(op))

        if not vray.isInitialized():
            message = "V-Ray initializing..." if vray.hasLicense() else "Obtaining V-Ray license..."
            self.layout.label(text=message, icon="INFO")





def _getRegPackages():
    from vray_blender.ui import classes
    from vray_blender.ui import icons
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
    from vray_blender.ui import menus

    return (
        classes,
        icons,
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
        menus
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

    bpy.types.VIEW3D_HT_header.append(drawVRayInteractiveRenderMenu)


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
    
    bpy.types.VIEW3D_HT_header.remove(drawVRayInteractiveRenderMenu)
  