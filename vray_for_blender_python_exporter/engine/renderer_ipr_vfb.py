# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
import time

from vray_blender import debug
from vray_blender.lib import blender_utils
from vray_blender.lib.defs import RendererMode, ExporterType, IprContext
from vray_blender.engine.renderer_ipr_base import VRayRendererIprBase, exportViewportView
from vray_blender.engine.vfb_event_handler import VfbEventHandler
from vray_blender.exporting.update_tracker import UpdateTracker

from vray_blender.bin import VRayBlenderLib as vray


#############################
## VRayRendererIprVfb
#############################

class VRayRendererIprVfb(VRayRendererIprBase):
    """ Interactive render engine implementation """

    # A static ref to the currently active renderer. We need it in order to be able to 
    # determine if the viewport renderer is active from any context.
    _activeRenderer = None

    def __init__(self, iprContext: IprContext):
        super().__init__(False, iprContext, bpy.context)

    @staticmethod
    @bpy.app.handlers.persistent
    def _exportOnIprUpdatePost(e):
        """ Exporting of scene on every depsgrpah update """
        from vray_blender.engine.render_engine import VRayRenderEngine
        if iprRenderer := VRayRenderEngine.iprRenderer:
            iprRenderer.exportScene()

    @staticmethod
    def isActive():
        """ Return True if an interactive renderer is currently active """
        return  VRayRendererIprVfb._activeRenderer != None   

    @staticmethod
    def getActiveRenderer():
        return VRayRendererIprVfb._activeRenderer
    

    @staticmethod
    def reset():
        """ Flags the renderer to clear the scene, re-export it, and perform a fresh render. """
        if VRayRendererIprVfb.isActive():
            VRayRendererIprBase._resetRenderer = True

    def start(self):
        # Adding export event on every depsgraph update
        blender_utils.addEvent(bpy.app.handlers.depsgraph_update_post, VRayRendererIprVfb._exportOnIprUpdatePost)
        blender_utils.addEvent(bpy.app.handlers.frame_change_post, VRayRendererIprVfb._exportOnIprUpdatePost)

    def stop(self, block = True):
        if self.renderer is not None:
            vray.renderEnd(self.renderer)

            if block:
                while vray.renderJobIsRunning(self.renderer):
                    time.sleep(0.01)

            self.renderer = None
            VRayRendererIprVfb._activeRenderer = None
            
            blender_utils.delEvent(bpy.app.handlers.depsgraph_update_post, VRayRendererIprVfb._exportOnIprUpdatePost)
            blender_utils.delEvent(bpy.app.handlers.frame_change_post, VRayRendererIprVfb._exportOnIprUpdatePost)
            

    def exportScene(self):
        context = bpy.context
        depsgraph = bpy.context.evaluated_depsgraph_get()

        # For interactive renders, do a full export the first time only. Subsequent exports will be partial.
        isFullExport = not self.renderer

        if not self.renderer:
            self._initRenderer()
            if not self.renderer:
                raise Exception("Failed to create VRay renderer object.")
            vray.clearScene(self.renderer)

        isFullExport = self._clearSceneOnReset(isFullExport, self.renderer)

        if context.scene.vray.Exporter.debug_log_times:
            vray.startStatsCollection(self.renderer)

        if not (exporterCtx := self._createExporterContext(depsgraph, isFullExport)):
            return None
        
        if isFullExport:
            # Updates may have accumulated from a previous renderer session.
            UpdateTracker.clear()

        self._export(exporterCtx)


    def exportViewport(self):
        depsgraph = bpy.context.evaluated_depsgraph_get()

        if not (ctx := self._createExporterContext(dg = depsgraph, isFullExport = False)):
            return None
        
        region3d = ctx.iprContext.region3d
        
        # If this condition is true, there is change in the viewport
        renderSizesOnly = region3d.view_matrix == self.persistedState.prevRegion3dViewMatrix
        self.viewParams = exportViewportView(ctx, self.viewParams, renderSizesOnly)

        vray.finishExport(self.renderer, interactive=True)
        self.persistedState.prevRegion3dViewMatrix = region3d.view_matrix.copy()


    def _initRenderer(self):
        self._createRenderer(ExporterType.IPR_VFB)

        if self.renderer == 0:
            # ZmqServer still starting
            return
        
        def onStopped(isAborted):
            if isAborted:
                bpy.app.timers.register(lambda: debug.reportError("Connection to renderer lost. Restart interactive rendering."))
            VfbEventHandler.stopInteractiveRender()

        self.cbRenderStopped = lambda isAborted: onStopped(isAborted)
        vray.setRenderStoppedCallback(self.renderer, self.cbRenderStopped)

        # Set a static renderer ref that can be accessed from Blender operators
        VRayRendererIprVfb._activeRenderer = self.renderer


    def _createExporterContext(self, dg: bpy.types.Depsgraph, isFullExport: bool):
        ctx = self._getExporterContext(bpy.context, RendererMode.Interactive, dg, isFullExport)
        if ctx.iprContext is None:
            # There is no active viewport to export from. Stop the VFB renderer as we can
            # no longer control the view from the UI.
            VfbEventHandler.stopInteractiveRender()
            return None
        
        return ctx