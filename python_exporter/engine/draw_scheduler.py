# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
import time

from vray_blender.bin import VRayBlenderLib as vray
from vray_blender.lib.mixin import VRayOperatorBase
from vray_blender.debug import printError

# Redraw interval for refreshing the status text while no image is arriving.
STATUS_REDRAW_INTERVAL = 0.25

def _setIfChanged(obj, attr, value):
    # RNA updates fire even for same-value writes, and a display_device write retags every
    # material in the file, re-rendering all material previews (VBLD-2739). Only write real changes.
    if getattr(obj, attr, None) != value:
        setattr(obj, attr, value)

class ColorManagementSettings:
    """ A class that stores the Color Management settings visible
        in the Render property panel of Cycles and EEVEE.
    """

    def __init__(self, scene: bpy.types.Scene):
        self._fillViewSettings(self, scene.view_settings)
        self.display_device = scene.display_settings.display_device

    # Replaces the scene color settings with those saved in this class.
    def restore(self, scene: bpy.types.Scene):
        self._fillViewSettings(scene.view_settings, self)
        _setIfChanged(scene.display_settings, 'display_device', self.display_device)

    @staticmethod
    def _fillViewSettings(dest: bpy.types.ColorManagedViewSettings, src: bpy.types.ColorManagedViewSettings):
        _setIfChanged(dest, 'view_transform', src.view_transform)
        _setIfChanged(dest, 'exposure', src.exposure)
        _setIfChanged(dest, 'gamma', src.gamma)
        _setIfChanged(dest, 'look', src.look)
        _setIfChanged(dest, 'use_curve_mapping', src.use_curve_mapping)
        _setIfChanged(dest, 'use_white_balance', src.use_white_balance)


class VRay_OT_draw_viewport_timer(VRayOperatorBase):
    """ A modal operator which will periodically check for new rendered images and
        trigger a viewport redraw operation.
    """
    bl_idname   = "vray.draw_viewport_timer"
    bl_label    = "V-Ray Draw Viewport Timer Operator"
    bl_options  = {'INTERNAL'}

    _timer = None
    _fps = 30
    _lastStatusRedraw = 0.0

    def switchViewTransformToStandard(self):
        if not hasattr(self, "previousColorManagementSettings"):
            scene = bpy.context.scene

            self.previousColorManagementSettings = ColorManagementSettings(scene) # Saving current settings

            # Resetting the color management settings to prevent them from interfering
            # with V-Ray viewport rendering. Note that this can fail with certain OCIO configs.
            try:
                _setIfChanged(scene.display_settings, 'display_device', "sRGB")
                _setIfChanged(scene.view_settings, 'view_transform', 'Standard')
            except:
                printError("Failed to reset Blender color corrections. The viewport and VFB may differ.")

            _setIfChanged(scene.view_settings, 'exposure', 0.0)
            _setIfChanged(scene.view_settings, 'gamma', 1.0)
            _setIfChanged(scene.view_settings, 'look', "None")
            _setIfChanged(scene.view_settings, 'use_curve_mapping', False)
            _setIfChanged(scene.view_settings, 'use_white_balance', False)

    def clearViewTransform(self):
        if hasattr(self, "previousColorManagementSettings"):
            # Restore any saved settings to ensure they are available when switching the rendering engine.
            self.previousColorManagementSettings.restore(bpy.context.scene)


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
            renderer = VRayRendererIprViewport.getActiveRenderer()
            instance = VRayRendererIprViewport.getActiveInstance()

            if vray.imageWasUpdated(renderer):
                if instance is not None:
                    instance._imageUpdatePending = True
                if context.area:
                    context.area.tag_redraw()
            elif context.area and instance is not None and instance.drawData is None:
                # No images arrive during long stages, so nothing else would trigger the redraw
                # that refreshes the status text.
                now = time.perf_counter()
                if now - self._lastStatusRedraw >= STATUS_REDRAW_INTERVAL:
                    self._lastStatusRedraw = now
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
