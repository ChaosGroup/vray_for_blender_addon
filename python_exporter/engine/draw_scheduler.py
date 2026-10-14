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
    # Returns True if the property was actually written.
    if getattr(obj, attr, None) != value:
        setattr(obj, attr, value)
        return True
    return False

# The view settings from the Color Management panel of Cycles and EEVEE. display_device is saved too.
_VIEW_SETTINGS = ('view_transform', 'exposure', 'gamma', 'look', 'use_curve_mapping', 'use_white_balance')

# The scene's color management from before the viewport render switched it. An ID property on the
# scene, so it is saved with the file for _onLoadPost to restore. Writing one fires no updates, and
# the leading underscore hides it in the UI.
_SAVED_COLOR_MANAGEMENT = '_vray_saved_color_management'


def switchViewTransformToStandard():
    """ Force the scene's color management to a neutral state for the duration of the viewport
        render, saving what was there before. Returns True if anything was actually written.

        Blender applies its view transform on top of the images drawn in the 3D viewport, which
        would alter the appearance of an image V-Ray has already color corrected.

        Called when the viewport renderer is created, i.e. from inside the first view_update, so
        that the caller knows whether these writes have tagged the Scene - see
        VRayRendererIprViewport._initRenderer().
    """
    scene = bpy.context.scene
    if _SAVED_COLOR_MANAGEMENT in scene:
        return False

    saved = {attr: getattr(scene.view_settings, attr) for attr in _VIEW_SETTINGS}
    saved['display_device'] = scene.display_settings.display_device
    scene[_SAVED_COLOR_MANAGEMENT] = saved

    changed = False

    # Note that this can fail with certain OCIO configs.
    try:
        changed |= _setIfChanged(scene.display_settings, 'display_device', "sRGB")
        changed |= _setIfChanged(scene.view_settings, 'view_transform', 'Standard')
    except:
        printError("Failed to reset Blender color corrections. The viewport and VFB may differ.")

    changed |= _setIfChanged(scene.view_settings, 'exposure', 0.0)
    changed |= _setIfChanged(scene.view_settings, 'gamma', 1.0)
    changed |= _setIfChanged(scene.view_settings, 'look', "None")
    changed |= _setIfChanged(scene.view_settings, 'use_curve_mapping', False)
    changed |= _setIfChanged(scene.view_settings, 'use_white_balance', False)

    return changed


def restoreViewTransform():
    """ Put back what switchViewTransformToStandard() replaced, so the user's color management
        is intact when the viewport render ends or the render engine is switched. Restores
        every switched scene, not just the current one.
    """
    for scene in bpy.data.scenes:
        if not scene.library and (saved := scene.pop(_SAVED_COLOR_MANAGEMENT, None)):
            for attr in _VIEW_SETTINGS:
                _setIfChanged(scene.view_settings, attr, saved[attr])
            _setIfChanged(scene.display_settings, 'display_device', saved['display_device'])


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

    def modal(self, context, event):
        if event.type == 'TIMER':
            from vray_blender.engine.renderer_ipr_viewport import VRayRendererIprViewport

            if not VRayRendererIprViewport.isActive():
                # Returning the color correction (view transform) setting to its previous state.
                # The switch itself is made by the renderer when it is created; only the restore
                # is driven from here, as this is the context in which the render is seen to end.
                restoreViewTransform()
                # Blender calls cancel() only on cancel, so FINISHED has to remove the timer itself.
                context.window_manager.event_timer_remove(self._timer)
                return {'FINISHED'}

            renderer = VRayRendererIprViewport.getActiveRenderer()
            instance = VRayRendererIprViewport.getActiveInstance()

            if vray.imageWasUpdated(renderer):
                if instance is not None:
                    instance._imageUpdatePending = True

                    # imageWasUpdated() consumes the flag, so a lost redraw leaves the image
                    # unshown until something else repaints. context.area follows the mouse in a
                    # modal operator, so it is None as soon as the cursor leaves the viewport -
                    # tagging it would drop the last frame of every render.
                    instance.tagRedraw()
            elif instance is not None and instance.drawData is None:
                # No images arrive during long stages, so nothing else would trigger the redraw
                # that refreshes the status text.
                now = time.perf_counter()
                if now - self._lastStatusRedraw >= STATUS_REDRAW_INTERVAL:
                    self._lastStatusRedraw = now
                    instance.tagRedraw()

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
