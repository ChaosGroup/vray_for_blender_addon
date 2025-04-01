import bpy
import json
import threading
from vray_blender.bin import VRayBlenderLib as vray
from vray_blender.engine.zmq_process import ZMQProcess
from vray_blender.lib import sys_utils
from vray_blender.lib.defs import ProdRenderMode
from vray_blender.nodes.utils import tagRedrawViewport

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


    def reset(self):
        with self._lock:
            self.eventQueue     = []
            self.vfbLayersJson  = ""
            self.vfSettingsJson = ""
            self._redrawViewport = False
            

    def stop(self):
        if bpy.app.timers.is_registered(_VfbEventHandler._onTimer): 
            bpy.app.timers.unregister(_VfbEventHandler._onTimer)


    def stopViewportRender(self):
        """ Set the viewport to SOLID mode """
        self.addEvent(_Event.RenderSolid)

    def startViewportRender(self):
        """ Set the viewport to RENDERED mode """
        self.addEvent(_Event.RenderViewport)

    def startInteractiveRender(self):
        """ Start interactive rendering """
        self.addEvent(_Event.RenderInteractive)

    def stopInteractiveRender(self):
        """ Stop interactive rendering """
        self.stopViewportRender()
        self.addEvent(_Event.RenderInteractiveStop)

    def startProdRender(self):
        """ Start production rendering """
        self.stopViewportRender()
        self.stopInteractiveRender()
        self.addEvent(_Event.RenderProd)

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
        """ Report to Belnder's status field """
        self.addEvent(_Event.ReportStatus, severity, msg)


    def updateVfbLayers(self, vfbLayersJson: str):
        """ Update VFB layer settings """
        with self._lock:
            # Do not directly change the vfb2_layers property of SettingsVFB here, because this will 
            # trigger an unwanted export of the scene. The property will be added to the export by a 
            # custom exporter function for SettingsVFB.
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

            # Exporting interactive viewport if there is Interactive renderer running
            self._exportInteractiveViewport()
            
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
                    processed = self.startProdRenderSync(renderMode=ProdRenderMode.RENDER)

                case _Event.ExportVrscene:
                    processed = self.startProdRenderSync(renderMode=ProdRenderMode.EXPORT_VRSCENE)

                case _Event.CloudSubmit:
                    processed = self.startProdRenderSync(renderMode=ProdRenderMode.CLOUD_SUBMIT)

                case _Event.RenderInteractive:
                    self._startInteractiveRender()
                
                case _Event.RenderInteractiveStop:
                    self._stopInteractiveRender()

                case _Event.ReportStatus:
                    debug.report(*event.args, **event.kwargs)


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
        from vray_blender.engine.renderer_ipr_viewport import VRayRendererIprViewport
        
        if not (space := VRayRendererIprViewport.getLastActiveSpace(bpy.context)):
            # We don't have information as to which space was last used by the user for V-Ray rendering.
            # Find any View3D space.
            if area := next((a for a in bpy.context.screen.areas if a.type == 'VIEW_3D'), None):
                space = next((space for space in area.spaces if space.type == 'VIEW_3D'), None)
                assert space, "No 3D viewport area found when ViewportModeSetter tries to switch shading type"

        if space:
            # The change to the shading type value will trigger a switch of the viewport
            # to the specified shading mode.
            space.shading.type = newMode  


    def _saveVfbSettings(self):
        #Save VFB settings in human-readable format
        with open(sys_utils.getVfbSettingsPath(), "w") as vfbConf:
            uiSettings = json.loads(self._vfbSettingsJson)
            # resize_on_res_change can't be gotten from the VFB API and for that is set explicitly
            uiSettings["RenderViewProps"]["resize_on_res_change"] = False
            vfbConf.write(json.dumps(uiSettings, indent=4))

    
    def startProdRenderSync(self, renderMode: bool):
        """ Synchronously invoke the production render operator """
        from vray_blender.engine.renderer_prod import VRayRendererProd

        if bpy.app.is_job_running('RENDER') or bpy.app.is_job_running('COMPOSITE'):
            # Wait for a previously started render job to finish. 
            # NOTE: This is check is a temporary debug tool used in an attempt to catch 
            # an intrmittently occuring situation where the context does not permit changes
            # to the scene from the timer handler.
            debug.printWarning("VfbEventHandler::startProdRenderSync(): Another job is running.")
            return False

        if not ZMQProcess.isRunning():
            debug.reportError('V-Ray Server is starting, try again in a few moments')
            return True

        # Lock the UI for the duration of the job. If the UI is not locked while a render job is running
        # in a timer callback, an access violation may occur in Blender.
        bpy.context.scene.render.use_lock_interface = True
        
        # Run the 'render' operator synchronously. 
        # NOTE: Running it asynchronously (using 'INVOKE_DEFAULT')
        # currently leads to an inconsistent state in Blender if there are animated materials in the scene.
        # This causes 'Can't write to ID classes in this context' type of errors when prod rendering or
        # loading/saving a scene.
        # ---
        # If inside a 3D viewport, the 'use_viewport' parameter will make the renderer use the layers 
        # and camera of the viewport. 
        VRayRendererProd.renderMode = renderMode
        bpy.ops.render.render('EXEC_DEFAULT', use_viewport=True)
        
        renderModeName = {
            ProdRenderMode.CLOUD_SUBMIT: "Submit to cloud",
            ProdRenderMode.EXPORT_VRSCENE: "Scene export",
            ProdRenderMode.RENDER: "Render"
        }[renderMode]
        
        debug.report('INFO', f"{renderModeName} finished")

        return True


    def _startInteractiveRender(self):
        from vray_blender.engine.render_engine import VRayRenderEngine
        VRayRenderEngine.startInteractiveRenderer()
        VRayRenderEngine.iprRenderer.exportScene()

    def _stopInteractiveRender(self):
        from vray_blender.engine.render_engine import VRayRenderEngine
        VRayRenderEngine.stopInteractiveRenderer()
    
    def _exportInteractiveViewport(self):
        """ Exporting of scene on every Viewport change """
        from vray_blender.engine.render_engine import VRayRenderEngine
        if iprRenderer := VRayRenderEngine.iprRenderer:
            iprRenderer.exportViewport()


# Export 'singleton' instance
VfbEventHandler = _VfbEventHandler()


    




