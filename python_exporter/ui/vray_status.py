# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy

from vray_blender import debug
from vray_blender.bin import VRayBlenderLib as vray


# The last dispatched state. Redispatching the same state would tear down
# a healthy viewport render, throwing away everything it has converged so far.
_lastDispatchedReady = None

def getVRayStartupMsg():
    """ Return what V-Ray is waiting on, or None if it is ready to accept commands. """
    if not vray.hasLicense():
        return "Obtaining V-Ray license..."
    if not vray.isInitialized():
        return "Initializing V-Ray..."
    return ""


def _showServiceNotStartedMsg(msg: str):
    if bpy.app.background or not bpy.context.window:
        # No window for the dialog to open in.
        debug.report('WARNING', msg)
    else:
        bpy.ops.vray.message_box('INVOKE_DEFAULT', 
                message=msg+"\nPlease try again in a few moments.", icon='INFO', width=400)

def isServiceRunning(serviceName):
    if vray.isRunning():
        return True
    _showServiceNotStartedMsg(f"{serviceName} is not currently running.")

    return False


def isVRayRunning():
    """ Return True if V-Ray is ready, otherwise notify the user and return False. """
    if vray.isRunning() and vray.isInitialized():
        return True

    _showServiceNotStartedMsg("V-Ray is not currently running.")

    return False


def onVRayStatusChanged(ready: bool, force=False):
    """ Called from a ZmqServer worker thread when V-Ray becomes (un)available. The work is
        deferred to the main thread, the only place bpy data access is valid.

        @param force - dispatch even if the state has not changed. Set when replaying the status
        after a server (re)start, where the previously dispatched state no longer applies.
    """
    def _apply():
        global _lastDispatchedReady

        if (ready == _lastDispatchedReady) and not force:
            return None  # one-shot timer

        _lastDispatchedReady = ready

        debug.printInfo(f"V-Ray is {'ready' if ready else 'not available'}")

        # Refresh the menus and panels gated on vray.isInitialized(). Without this they stay
        # stale until something else happens to trigger a redraw.
        for window in getattr(bpy.context.window_manager, 'windows', []):
            for area in window.screen.areas:
                area.tag_redraw()

        if ready:
            from vray_blender.engine.renderer_ipr_viewport import VRayRendererIprViewport
            from vray_blender.engine.renderer_preview import scheduleStuckPreviewCheck

            VRayRendererIprViewport.onViewportRendererAvailable()
            scheduleStuckPreviewCheck()

        return None  # one-shot timer

    bpy.app.timers.register(_apply)
