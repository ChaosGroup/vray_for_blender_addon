# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
import time
import blf

from numpy import ndarray

from vray_blender.engine.renderer_ipr_base import VRayRendererIprBase, exportViewportView

from vray_blender import debug
from vray_blender.lib import gl_draw
from vray_blender.lib.camera_utils import Size, getRegionWidth, getRegionHeight
from vray_blender.lib.defs import RendererMode, ExporterType, UIRegionContext
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

    def __init__(self, context: bpy.types.Context):
        uiRegionContext = UIRegionContext(context.space_data, context.window)
        super().__init__(True, uiRegionContext, context)

        # Current view settings. Blender does not provide view change notification, so
        # we have to compare the old state to the new state on each draw operation.

        # Cache & draw the most recent viewport image.
        self.drawData: gl_draw.DrawData = None
        VfbEventHandler.setLightMixSupported(False)

        # FPS measurement
        self.lastFpsTime = time.perf_counter()
        self.currentFps = 0.0

        # When viewport render is first started, the render engine's update callback
        # gets called twice - the first time with an empty depsgraph update list,
        # the second time with all scene objects in the update list. This is behavior
        # defined by Blender and we cannot alter it. To avoid exporting the whole scene
        # again but show the first rendered image as soon as possible, skip the second call.
        self.skipNextUpdate = False

        # The region dimensions of the last draw operation. Used to detect if the region dimensions have changed .
        self.lastRegionDimensions = (0, 0)

    @staticmethod
    def isActive():
        """ Return True if an interactive renderer is currently active """
        return VRayRendererIprViewport._activeRenderer != None

    @staticmethod
    def getActiveRenderer():
        return VRayRendererIprViewport._activeRenderer


    @staticmethod
    def reset():
        """ Flags the renderer to clear the scene, re-export it, and perform a fresh render. """
        if VRayRendererIprViewport.isActive():
            VRayRendererIprBase._resetRenderer = True

    def stop(self, block=True):
        if self.renderer:
            vray.renderEnd(self.renderer)

            if block:
                while vray.renderJobIsRunning(self.renderer):
                    time.sleep(0.01)

            self.renderer = None
            VRayRendererIprViewport._activeRenderer = None

    def _startDrawPoller(self):
        # Start a poller to trigger redraw operation when a new rendered image is received.
        bpy.ops.vray.draw_viewport_timer()


    # Invoked by RenderEngine.view_update
    def view_update(self, engine: bpy.types.RenderEngine, context: bpy.types.Context, depsgraph: bpy.types.Depsgraph):
        startTime = time.perf_counter()

        if self.skipNextUpdate:
            self.skipNextUpdate = False
            return

        isFullExport = False

        if not self.renderer:
            self._initRenderer(engine)
            if not self.renderer:
                raise Exception("Failed to create VRay renderer object.")
            vray.clearScene(self.renderer)
            isFullExport = True
            self.skipNextUpdate = True

        isFullExport = self._clearSceneOnReset(isFullExport, self.renderer)

        if context.scene.vray.Exporter.debug_log_times:
            vray.startStatsCollection(self.renderer)

        exporterCtx = self._getExporterContext(context, RendererMode.Viewport, depsgraph, isFullExport)
        if exporterCtx.uiRegionContext is None:
            # No active viewport to export from
            return

        if isFullExport:
            # Updates may have accumulated from a previous renderer session.
            UpdateTracker.clear()

        if self._export(exporterCtx, engine):
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
        if self.renderer:
            if vray.isRenderReady(self.renderer):
                # We are not stopping to check for image here
                # because there could be color corrections applied from VFB
                engine.update_stats("", "Rendering finished")

            try:
                ctx = self._getExporterContext(ctx = context, rendererMode=RendererMode.Viewport, dg = depsgraph, isFullExport = False)
                assert ctx.uiRegionContext is not None

                region3d = ctx.uiRegionContext.region3d

                # Blender does not send a separate notification when the viewport camera changes.
                # This is why we have to export the view on each draw request
                renderSizesOnly = region3d.view_matrix == self.persistedState.prevRegion3dViewMatrix
                self.viewParams = exportViewportView(ctx, self.viewParams, renderSizesOnly)

                engine.update_stats("", vray.getEngineUpdateMessage(self.renderer))

                self._drawViewport(context)
                self.persistedState.prevRegion3dViewMatrix = region3d.view_matrix.copy()

            except Exception as ex:
                engine.error_set(f"Add-on error: {str(ex)}")
                debug.printExceptionInfo(ex, "VRayRendererIprViewport::view_draw()")

            vray.finishExport(self.renderer, interactive=True)

    def _initRenderer(self, engine: bpy.types.RenderEngine):
        self._createRenderer(ExporterType.IPR_VIEWPORT)

        if self.renderer == 0:
            # ZmqServer still starting
            return

        def onStopped(isAborted: bool):
            if isAborted:
                bpy.app.timers.register(lambda: debug.reportError("Connection to renderer lost. Restart viewport rendering."))
            VfbEventHandler.stopViewportRender()

        self.cbRenderStopped = lambda isAborted: onStopped(isAborted)
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


        if self.lastRegionDimensions != (region.width, region.height) and \
            region.width != getRegionWidth(region): 
            
            debug.reportAsync(
                'WARNING',
                "The image was scaled down due to the viewport"
                " resolution exceeding the Community Edition's limit."
            )
        self.lastRegionDimensions = (region.width, region.height)

        self.drawData.draw(Size(getRegionWidth(region), getRegionHeight(region)))


        if vray.withProfiling:
            self._calculateFps()
            self._drawFps(region)

    def _calculateFps(self):
        receivedCount = vray.getReceivedImagesCount(self.renderer)

        currentTime = time.perf_counter()
        elapsed = currentTime - self.lastFpsTime
        if elapsed >= 1.0:
            deltaCount = receivedCount - getattr(self, 'lastReceivedCount', receivedCount)
            self.currentFps = deltaCount / elapsed
            self.lastFpsTime = currentTime
            self.lastReceivedCount = receivedCount
        elif not hasattr(self, 'lastReceivedCount'):
            self.lastReceivedCount = receivedCount

    def _drawFps(self, region):
        fontId = 0
        fontSize = 20
        blf.size(fontId, fontSize)

        fpsText = f"FPS: {self.currentFps:.2f}"
        width, height = blf.dimensions(fontId, fpsText)

        # position in bottom right
        padding = 20
        blf.position(fontId, region.width - width - padding, padding, 0)

        blf.color(fontId, 1.0, 0.8, 0.0, 1.0)
        blf.draw(fontId, fpsText)
