import bpy 
import time
import queue
import threading

from numpy import ndarray

from vray_blender.engine.renderer_ipr_base import VRayRendererIprBase, exportViewportView

from vray_blender import debug
from vray_blender.lib import gl_draw, blender_utils, plugin_utils, image_utils
from vray_blender.lib.camera_utils import Size
from vray_blender.lib.defs import RendererMode, ExporterType
from vray_blender.exporting.update_tracker import UpdateTracker
from vray_blender.engine.vfb_event_handler import VfbEventHandler

from vray_blender.bin import VRayBlenderLib as vray

#############################
## VRayRendererIprViewport
#############################

class VRayRendererIprViewport(VRayRendererIprBase):
    """ Viewport (interactive) render engine implementation """
    
    # A static ref to the currently active renderer. We need it in order to be able to 
    # determine if the viewport renderer is active from any context.
    _activeRenderer = None

    # When a viewport renderer is activated, it will store here an identifier of the Blender 3D space
    # it was invoked in. When a rendering is requested without a valid context (e.g. from an 
    # external event handler), we can use this identifier to find the last space for which the
    # V-Ray renderer was used and route the event to the same space.
    # NOTE: the value stored is the native pointer to the space structure. It is not unique, 
    # but in the unlikely case the value is reused the only consequence will be VFB manipulating
    # the wrong space. Spaces are not an ID type so currently there is no better way to identify them.
    _space3D = 0

    def __init__(self, context):
        super().__init__(True)
        # Current view settings. Blender does not provide view change notification, so 
        # we have to compare the old state to the new state on each draw operation.

        # Cache & draw the most recent viewport image.
        self.drawData: gl_draw.DrawData = None  

        # Store an identifier of the screen space it was invoked in.
        space = next((s for s in context.area.spaces if s.type == 'VIEW_3D'), None)
        assert space, "Cannot create renderer for a space that is not of type 'View3D'"
        VRayRendererIprViewport._space3D = space.as_pointer()

    @staticmethod
    def isActive():
        """ Return True if an interactive renderer is currently active """
        return VRayRendererIprViewport._activeRenderer != None   

    @staticmethod
    def getActiveRenderer():
        return VRayRendererIprViewport._activeRenderer

    @staticmethod
    def getLastActiveSpace(context):
        """ Try to find the space the last active V-Rau viewport renderer was invoked in.
            @return last active space or None if it has been destroyed.
        """
        return next((  space  
                        for area in context.screen.areas if area.type == 'VIEW_3D'
                        for space in area.spaces \
                            if space.type == 'VIEW_3D' and space.as_pointer() == VRayRendererIprViewport._space3D), None)
    
    @staticmethod
    def reset():
        """ Flags the renderer to clear the scene, re-export it, and perform a fresh render. """
        if VRayRendererIprViewport.isActive():
            VRayRendererIprBase._resetRenderer = True

    def stop(self):
        if self.renderer is not None:
            vray.deleteRenderer(self.renderer)
            self.renderer = None
            VRayRendererIprViewport._activeRenderer = None
            # Do not reset _space3D. It will be used by the VFB event handler to 
            # route events to the same 3D view.

        
    def _startDrawPoller(self):
        # Start a poller to trigger redraw operation when a new rendered image is received.
        bpy.ops.vray.draw_viewport_timer()


    # Invoked by RenderEngine.view_update
    def view_update(self, engine: bpy.types.RenderEngine, context: bpy.types.Context, depsgraph: bpy.types.Depsgraph):
        if not (view3D := blender_utils.getSpaceView3D(context)):
            # Skip rendering when update is called for spaces of type other that View3D.
            return

        # For viewport renders, do a full export the first time only. Subsequent exports will be partial.  
        isFullExport = not self.renderer
        engine.update_stats("", "View_update start")
        
        startTime = time.perf_counter()

        if not self.renderer:
            self._initRenderer(engine)
            if not self.renderer:
                raise Exception("Failed to create VRay renderer object.")
            vray.clearScene(self.renderer)

        isFullExport = self._clearSceneOnReset(isFullExport, self.renderer)

        if context.scene.vray.Exporter.debug_log_times:
            vray.startStatsCollection(self.renderer)
        
        exporterCtx = self._getExporterContext(context, RendererMode.Viewport, depsgraph, isFullExport)
        
        if isFullExport:
            # Updates may have accumulated from a previous renderer session.
            UpdateTracker.clear()
        if self._export(exporterCtx, engine, view3D):
            engine.update_stats("", "View_update POC export complete")

        # Total scene export time
        endTime = time.perf_counter()
        
        
        if context.scene.vray.Exporter.debug_log_times:
            vray.endStatsCollection(self.renderer, printStats=True, title="C++ exporter")
            exporterCtx.ts.printSummary()

            for s in exporterCtx.stats:
                print(s)

            print(f"TOTAL export scene time: {(endTime - startTime) * 1000:.3f}ms\n")


    # Invoked by RenderEngine.view_draw
    def view_draw(self, engine: bpy.types.RenderEngine, context: bpy.types.Context, depsgraph: bpy.types.Depsgraph):
        if not (view3D := blender_utils.getSpaceView3D(context)):
            # Update may be called for spaces other that 3DView (e.g. the FileOpen dialog). Skip rendering.
            return
        if self.renderer:
            if vray.isRenderReady(self.renderer):
                # We are not stopping to check for image here
                # because there could be color corrections applied from VFB
                engine.update_stats("", "Rendering finished")

            try:
                # Blender does not send a separate notification when the viewport camera changes.
                # This is why we have to export the view on each draw request
                ctx  = self._getExporterContext(ctx = context, rendererMode=RendererMode.Viewport, dg = depsgraph, isFullExport = False)
                
                self.viewParams = exportViewportView(ctx, view3D, self.viewParams)
                engine.update_stats("", vray.getEngineUpdateMessage(self.renderer))
                self._drawViewport(context)
            except Exception as ex:
                engine.error_set(f"Add-on error: {str(ex)}")
                debug.printExceptionInfo(ex, "VRayRendererIprViewport::view_draw()")
    
            vray.finishExport(self.renderer, interactive=True)

    def _initRenderer(self, engine: bpy.types.RenderEngine):
        self._createRenderer(ExporterType.IPR_VIEWPORT)
        
        self.cbRenderStopped = lambda: VfbEventHandler.stopViewportRender()
        vray.setRenderStoppedCallback(self.renderer, self.cbRenderStopped)

        # 'renderer' member of engine is only used by the draw polling thread  
        engine.renderer = self.renderer
        self._startDrawPoller()

        # Set a static renderer ref that can be accessed from Blender operators
        VRayRendererIprViewport._activeRenderer = self.renderer


    def _drawViewport(self, context: bpy.types.Context):
        region: bpy.types.Region = context.region

        # If there is a new image, update DrawData with it.
        image: ndarray = vray.getImage(self.renderer)
        if image is not None:
            self.drawData = gl_draw.DrawData(image, self.viewParams)

        if self.drawData is None:
            # Nothing to draw ( before the first image has arrived )
            return
        
        self.drawData.draw(Size(region.width, region.height))
