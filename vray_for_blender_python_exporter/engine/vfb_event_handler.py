# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
import json
import threading

from vray_blender.engine.zmq_process import ZMQProcess
from vray_blender.lib import sys_utils
from vray_blender.lib.defs import ProdRenderMode
from vray_blender.nodes.utils import tagRedrawViewport
from vray_blender.lib.camera_utils import renderCamerasHaveSameType

from vray_blender import debug

class _Event:
    Undefined               = 0
    RenderViewport          = 1     # Switch viewport to RENDERED mode
    RenderSolid             = 2     # Switch  viewport to SOLID mode
    RenderProd              = 3     # Start production rendering
    RenderProdEnd           = 5     # End a production render
    RenderInteractive       = 6     # Start IPR(Interactive) Rendering
    RenderInteractiveStop   = 7     # Stop IPR(Interactive) Rendering
    ExportVrscene           = 8     # Export the current scene to a .vrscene files
    CloudSubmit             = 9     # Submit scene for V-Ray Cloud rendering
    ReportStatus            = 10    # Report to Bleder status area
    UpgradeScene            = 11    # Run a scene version upgrade
    RenderVantageStart      = 12    # Start Vantage Live Link
    RenderVantageStop       = 13    # Stop Vantage Live Link

DrawHandlers = []

class _EventInfo:
    """ Encapsulates the event type and any parameters to the evennt handler procedure """
    def __init__(self, eventType, *args, **kwargs):
        self.eventType = eventType
        self.args = args
        self.kwargs = kwargs

class _VfbEventHandler:
    """ Processor for VFB events received in arbitrary thread / context.
        The events will be processed in a valid Blender context in FIFO order.
    """

    def __init__(self):
        self._lock = threading.Lock()

        self._eventQueue: list[_EventInfo] = []   # FIFO queue of events
        self._vfbLayersJson      = ""      # Last layer settings received fom VFB
        self._vfbSettingsJson    = ""      # Last received VFB settings
        self._fps                = 5       # Timer activation rate
        self._redrawViewport     = False   # Flag to request viewport redraw on the next timer activation

        self._lightMixSupported  = False   # Flag to indicate if LightMix settings are supported (After a valid RenderChannelLightMix plugin is exported and rendered)
        self._lightMixSettings   = None    # Stores LightMix settings until a valid RenderChannelLightMix plugin is exported and rendered


    def addEvent(self, eventType, *args, **kwargs):
        """ Add an event to the event queue. The event will be processed in a valid Blender context.
            This nethod is thread-safe.
        """
        with self._lock:
            # Only add the event if it is not a duplicate of the last one received.
            if (not self._eventQueue) or (self._eventQueue[-1] != eventType):
                self._eventQueue.append(_EventInfo(eventType, *args,  **kwargs))


    def ensureRunning(self, reset=False):
        """ Activate event handling. This method is idempotent.
            @param reset - clear data associated with the current scene. Should be set to True upon scene reload.
        """
        if reset:
            self.reset()

        if not bpy.app.timers.is_registered(_VfbEventHandler._onTimer):
            bpy.app.timers.register(_VfbEventHandler._onTimer)

        DrawHandlers.append(bpy.types.SpaceView3D.draw_handler_add(self._exportInteractiveViewport, (), 'WINDOW', 'POST_PIXEL'))

    def reset(self):
        with self._lock:
            self.eventQueue     = []
            self.vfbLayersJson  = ""
            self.vfSettingsJson = ""
            self._redrawViewport = False
            self._lightMixSupported = False
            self._lightMixSettings = None


    def stop(self):
        if bpy.app.timers.is_registered(_VfbEventHandler._onTimer):
            bpy.app.timers.unregister(_VfbEventHandler._onTimer)

        for handler in DrawHandlers:
            bpy.types.SpaceView3D.draw_handler_remove(handler, 'WINDOW')
        DrawHandlers.clear()


    def stopViewportRender(self):
        """ Set the viewport to SOLID mode """
        self.addEvent(_Event.RenderSolid)

    def startViewportRender(self):
        """ Set the viewport to RENDERED mode """
        self.addEvent(_Event.RenderViewport)

    def startInteractiveRender(self, iprContext = None):
        """ Start interactive rendering """
        self.addEvent(_Event.RenderInteractive, iprContext)

    def startVantageLiveLink(self):
        """ Start interactive rendering """
        self.addEvent(_Event.RenderVantageStart)

    def stopInteractiveRender(self):
        """ Stop interactive rendering """
        self.addEvent(_Event.RenderInteractiveStop)

    def startProdRender(self, forceAnimation: bool):
        """ Start production rendering """
        from vray_blender.engine.renderer_prod import VRayRendererProd

        if self._isEventInQueue(_Event.RenderProd) or VRayRendererProd.isActive():
            # This could happen if the user clicks several times in rapid succession on the
            # 'Start Prod Render' VFB button
            return
        self.stopViewportRender()
        self.stopInteractiveRender()
        self.addEvent(_Event.RenderProd, forceAnimation)

    def stopVantageLiveLink(self):
        self.addEvent(_Event.RenderVantageStop)

    def exportVrscene(self):
        """ Export .vrscene """
        self.stopViewportRender()
        self.stopInteractiveRender()
        self.addEvent(_Event.ExportVrscene)

    def cloudSubmit(self):
        """ Submit scene to Chaos cloud """
        self.stopViewportRender()
        self.stopInteractiveRender()
        self.addEvent(_Event.CloudSubmit)

    def reportStatus(self, severity: set, msg: str):
        """ Report to Blender's status field """
        self.addEvent(_Event.ReportStatus, severity, msg)


    def upgradeScene(self):
        """ Run a scene upgrade """
        self.addEvent(_Event.UpgradeScene)

    def setLightMixSupported(self, supported: bool):
        """ Mark LightMix settings as supported """
        with self._lock:
            self._lightMixSupported = supported

    def updateVfbLayers(self, vfbLayersJson: str, settingsAreFromScene: bool = False):
        """ Update VFB layer settings
            @param vfbLayersJson - the VFB layers JSON for storing in _vfbLayersJson
            @param settingsAreFromScene - if True, the settings are from the scene and not from the server
        """
        with self._lock:
            # Do not directly change the vfb2_layers property of SettingsVFB here, because this will
            # trigger an unwanted export of the scene. The property will be added to the export by a
            # custom exporter function for SettingsVFB.

            if vfbLayersJson:
                # LightMix VFB layers require render mode (Production or VFB IPR in our case)
                # in which the RenderChannelLightMix plugin can be exported and rendered
                # in order to function properly. Before the first render, or during viewport rendering,
                # these requirements are not met. As a result, if the server sends VFB layers JSON at that time,
                # the LightMix settings within it cannot be considered valid. To preserve valid settings,
                # they are stored in _lightMixSettings only when the requirements above are met.
                # When the requirements are not met, _lightMixSettings is used to update the
                # LightMix settings in the VFB layers JSON to ensure they remain valid.
                vfbLayersJsonDict = json.loads(vfbLayersJson)

                def findLayerByClass(data, className):
                    return next((l for l in data.get("sub-layers", []) if l.get("class") == className), None)

                if (displayCorrection := findLayerByClass(vfbLayersJsonDict, "chaos.displayCorrection")) and \
                    (sourceFolder := findLayerByClass(displayCorrection, "chaos.ref.sourcefolder")):

                    if lightMix := findLayerByClass(sourceFolder, "chaos.ref.lightmix"):
                        # If the settings are from the scene, we can use them directly, they are valid.
                        if self._lightMixSupported or settingsAreFromScene:
                            self._lightMixSettings = lightMix
                        elif self._lightMixSettings:
                            lightMix["properties"] = self._lightMixSettings["properties"]

                # Somehow the VFB layers get unselected after a render is started,
                # when the value of SettingsVFB.vfb2_layers property has spaces after "," and ":".
                # Using separators=(",", ":") instead of the default separators=(", ", ": ") prevents this.
                vfbLayersJson = json.dumps(vfbLayersJsonDict, separators=(",", ":"))

            self._vfbLayersJson = vfbLayersJson
            self._redrawViewport = True


    def updateVfbSettings(self, vfbSettingsJson: str):
        """ Update VFB settings """
        with self._lock:
            self._vfbSettingsJson = vfbSettingsJson
            self._saveVfbSettings()
            self._redrawViewport = True


    def getVfbLayers(self):
        with self._lock:
            return self._vfbLayersJson


    def _isEventInQueue(self, eventType: int):
        with self._lock:
            return any(e for e in self._eventQueue if e.eventType == eventType)


    @staticmethod
    def _onTimer():
        # bpy.app.timers.register works with non-static methods but such methods
        # are not reported as registered and cannot be unregistered later (although
        # the timer works fine).
        self = VfbEventHandler
        try:
            ev = None

            with self._lock:
                if self._eventQueue:
                    ev = self._eventQueue[0]

                if self._redrawViewport:
                    self._redrawViewport = False
                    tagRedrawViewport()

            if ev:
                self._handleQueuedEvent(ev)

        except Exception as ex:
            debug.printExceptionInfo(ex, "VfbEventHandler::onTimer()")

        return 1.0 / self._fps


    def _handleQueuedEvent(self, event: tuple):
        processed = True

        try:
            # Change the render mode

            match event.eventType:
                case _Event.RenderSolid:
                    self.changeViewportModeSync('SOLID')

                case _Event.RenderViewport:
                    self.changeViewportModeSync('RENDERED')

                case _Event.RenderProd:
                    self.setLightMixSupported(True)
                    processed = self.startProdRenderSync(ProdRenderMode.RENDER, *event.args)

                case _Event.ExportVrscene:
                    processed = self.startProdRenderSync(renderMode=ProdRenderMode.EXPORT_VRSCENE)

                case _Event.CloudSubmit:
                    processed = self.startProdRenderSync(renderMode=ProdRenderMode.CLOUD_SUBMIT)

                case _Event.RenderInteractive:
                    self.setLightMixSupported(True)
                    self._startInteractiveRender(*event.args, **event.kwargs)

                case _Event.RenderInteractiveStop:
                    self._stopInteractiveRender()

                case _Event.ReportStatus:
                    debug.report(*event.args, **event.kwargs)

                case _Event.UpgradeScene:
                    bpy.ops.vray.upgrade_scene('INVOKE_DEFAULT')

                case _Event.RenderVantageStart:
                    self._startVantageLiveLink()

                case _Event.RenderVantageStop:
                    self._stopVantageLiveLink()


        except Exception as ex:
            debug.printExceptionInfo(ex, "VfbEventHandler::_handleQueuedEvent()")
            # If the processing failed with an error, the event is still considered processed

        with self._lock:
            if processed:
                self._eventQueue.pop(0)


    def changeViewportModeSync(self, newMode: str):
        """ Synchronously change viewport mode between SOLID and RENDERED.

            @param newMode - the mode to set
            @param targetArea - the area for which the mode is changed. If None, then
                                the first found area of type View3D.
        """
        from vray_blender.engine.renderer_ipr_viewport import VRayRendererIprBase

        if not (iprContext := VRayRendererIprBase.getActiveIprContext()):
            # The command cannot be carried out because there are no 3d viewport views open in Blender.
            return

        space = iprContext.view3d
        if space and space.shading.type != newMode:
            # The change to the shading type value will trigger a switch of the viewport
            # to the specified shading mode.
            space.shading.type = newMode


    def _saveVfbSettings(self):
        #Save VFB settings in human-readable format
        with open(sys_utils.getVfbSettingsPath(), "w") as vfbConf:
            uiSettings = json.loads(self._vfbSettingsJson)
            vfbConf.write(json.dumps(uiSettings, indent=4))


    def startProdRenderSync(self, renderMode: int, forceAnimation = False, block = False):
        """ Invoke the production render operator

        Args:
            renderMode (int): one of the ProdRebderMode members
            forceAnimation (bool, optional): force renering animation even if the mode currently
                                             selected in the UI is not ANIMATION.
            block (bool, optional): Run the render operator in blocking mode.

        Returns:
            _type_: _description_
        """
        from vray_blender.engine.renderer_prod import VRayRendererProd

        # Blender would not let the 'Render' operator run without a camera. Check here in order
        # to avoid the exception log that would be printed otherwise.
        if not bpy.context.scene.camera:
           action = "export" if renderMode == ProdRenderMode.EXPORT_VRSCENE else "render"
           debug.reportError(f'Cannot {action} a scene without a camera. Add a camera to the scene and try again.')
           return True

        if bpy.app.is_job_running('RENDER') or bpy.app.is_job_running('COMPOSITE'):
            # This is check is a temporary debug tool used in an attempt to catch
            # an intermittently occurring situation where the context does not permit changes
            # to the scene from the timer handler.
            debug.printWarning("VfbEventHandler::startProdRenderSync(): Another job is running.")
            return False

        if not ZMQProcess.isRunning():
            from vray_blender.engine.render_engine import VRayRenderEngine
            debug.reportError(VRayRenderEngine.ERR_MSG_ZMQ_SERVER_DOWN)
            return True

        # Cancel rendering if there are cameras markers with different types in the part of the timeline that is for rendering.
        if (renderMode == ProdRenderMode.RENDER) and (not renderCamerasHaveSameType(forceAnimation)):
            return True

        # Lock the UI for the duration of the job. If the UI is not locked while a render job is running
        # in a timer callback, an access violation may occur in Blender.
        bpy.context.scene.render.use_lock_interface = True

        # Set render_display_type to None in order to hide the Blender render window and only render in the VFB.
        lastRenderDisplayType = bpy.context.preferences.view.render_display_type
        bpy.context.preferences.view.render_display_type = 'NONE'

        # If inside a 3D viewport, the 'use_viewport' parameter will make the renderer use the layers
        # and camera of the viewport.
        VRayRendererProd.renderMode = renderMode
        VRayRendererProd.forceAnimation = forceAnimation
        bpy.ops.render.render('EXEC_DEFAULT' if block else 'INVOKE_DEFAULT', use_viewport=True)

        renderModeName = {
            ProdRenderMode.CLOUD_SUBMIT: "Submit to cloud",
            ProdRenderMode.EXPORT_VRSCENE: "Scene export",
            ProdRenderMode.RENDER: "Render"
        }[renderMode]
        bpy.context.preferences.view.render_display_type = lastRenderDisplayType

        debug.report('INFO', f"{renderModeName} finished")

        return True


    def _startInteractiveRender(self, iprContext):
        from vray_blender.engine.render_engine import VRayRenderEngine
        from vray_blender.engine.renderer_ipr_vfb import VRayRendererIprVfb

        if not iprContext:
            iprContext = VRayRendererIprVfb.getActiveIprContext()

        if not iprContext:
            debug.report('WARNING', "No active viewport to render")
            return
        
        VRayRenderEngine.startInteractiveRenderer(iprContext)
        VRayRenderEngine.iprRenderer.exportScene()

    def _stopInteractiveRender(self):
        from vray_blender.engine.render_engine import VRayRenderEngine
        VRayRenderEngine.stopInteractiveRenderer()

    def _startVantageLiveLink(self):
        from vray_blender.engine.render_engine import VRayRenderEngine
        VRayRenderEngine.startVantageLiveLink()

    def _stopVantageLiveLink(self):
        from vray_blender.engine.render_engine import VRayRenderEngine
        VRayRenderEngine.stopVantageLiveLink()

    def _exportInteractiveViewport(self):
        """ Exporting of scene on every Viewport change """
        from vray_blender.engine.render_engine import VRayRenderEngine
        if iprRenderer := VRayRenderEngine.iprRenderer:
            iprRenderer.exportViewport()


# Export 'singleton' instance
VfbEventHandler = _VfbEventHandler()
