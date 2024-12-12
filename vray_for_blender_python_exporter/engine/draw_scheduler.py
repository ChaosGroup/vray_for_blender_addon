import bpy

from vray_blender.bin import VRayBlenderLib as vray

class VRay_OT_draw_viewport_timer(bpy.types.Operator):
    """ A modal operator which will periodically check for new rendered images and
        trigger a viewport redraw operation.
    """
    bl_idname   = "vray.draw_viewport_timer"
    bl_label    = "V-Ray Draw Viewport Timer Operator"
    bl_options  = {'INTERNAL'}

    _timer = None
    _fps = 20

    def switchViewTransformToStandard(self):
        if not hasattr(self, "previousViewTransform"):
            self.previousViewTransform = bpy.context.scene.view_settings.view_transform
            bpy.context.scene.view_settings.view_transform = 'Standard'
    
    def clearViewTransform(self):
        if hasattr(self, "previousViewTransform"):
            bpy.context.scene.view_settings.view_transform = self.previousViewTransform


    def modal(self, context, event):
        if event.type == 'TIMER':
            from vray_blender.engine.renderer_ipr_viewport import VRayRendererIprViewport
            
            if not VRayRendererIprViewport.isActive():
                # Returning the color correction (view transform) setting to its previous state
                self.clearViewTransform()     
                return {'FINISHED'}
            
            # Blender applies color correction (view transform) on top of the images displayed in the 3D viewport.
            # This can alter the appearance of images already color-corrected by V-Ray.
            # To prevent this, we switch Blender to standard view (no transform) until there is active VRayRendererIprViewport,
            # ensuring it doesn't alter the render result's appearance.
            self.switchViewTransformToStandard()
            if vray.imageWasUpdated(VRayRendererIprViewport.getActiveRenderer()):
                if context.area:
                    context.area.tag_redraw()
            
        return {'PASS_THROUGH'}
    

    def execute(self, context):
        wm = context.window_manager
        self._timer = wm.event_timer_add(1.0 / self._fps, window=context.window)
        wm.modal_handler_add(self)
        
        # Tell Blender the operator will continue execution
        return {'RUNNING_MODAL'}


    def cancel(self, context):
        wm = context.window_manager
        wm.event_timer_remove(self._timer)
