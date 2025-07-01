import bpy

from vray_blender.engine.renderer_ipr_base import VRayRendererIprBase, exportViewportView

from vray_blender.lib import blender_utils
from vray_blender.lib.defs import RendererMode, ExporterType
from vray_blender.exporting.update_tracker import UpdateTracker
from vray_blender.engine.vfb_event_handler import VfbEventHandler

from vray_blender.bin import VRayBlenderLib as vray


#############################
## VRayRendererIprVfb
#############################

class VRayRendererIprVfb(VRayRendererIprBase):
    """ Interactive render engine implementation """

    # A static ref to the currently active renderer. We need it in order to be able to 
    # determine if the viewport renderer is active from any context.
    _activeRenderer = None

    def __init__(self):
        super().__init__(False)

        # This variable holds the value of view3D.region_3d.view_matrix before calling of self.exportViewport()
        # It is used to ensure that there is a change in the 3D viewport, indicating that the scene should be redrawn.
        self._prevRegion3dViewMatrix = None

    @staticmethod
    @bpy.app.handlers.persistent
    def _onIprRenderUpdatePost(e):
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
        blender_utils.addEvent(bpy.app.handlers.depsgraph_update_post, VRayRendererIprVfb._onIprRenderUpdatePost)
        blender_utils.addEvent(bpy.app.handlers.frame_change_post, VRayRendererIprVfb._onIprRenderUpdatePost)

    def stop(self):
        if self.renderer is not None:
            vray.deleteRenderer(self.renderer)
            self.renderer = None
            VRayRendererIprVfb._activeRenderer = None
            # Removing the export event
            blender_utils.delEvent(bpy.app.handlers.depsgraph_update_post, VRayRendererIprVfb._onIprRenderUpdatePost)
            blender_utils.delEvent(bpy.app.handlers.frame_change_post, VRayRendererIprVfb._onIprRenderUpdatePost)
    
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
        
        exporterCtx = self._getExporterContext(context, RendererMode.Interactive, depsgraph, isFullExport)
        
        view3d = None
        if isFullExport:
            # Updates may have accumulated from a previous renderer session.
            UpdateTracker.clear()
            # Find an available 3D view from any screen. May be the current or another one
            view3d = blender_utils.getFirstAvailableView3D()
        else:
            # We are reexporting - get the view3D only from the current screen or None if the
            # current screen has no View3D (can happen). If we use the available here -
            # the view will change even if we switch to some screen with no visible View3D
            view3d=blender_utils.getActiveView3D()

        self._export(exporterCtx, view3D=view3d)

    def exportViewport(self):
        view3D = blender_utils.getFirstAvailableView3D()

        if view3D.region_3d.view_matrix != self._prevRegion3dViewMatrix: # If this condition is true, there is change in the viewport
            depsgraph = bpy.context.evaluated_depsgraph_get()
            
            ctx = self._getExporterContext(ctx = bpy.context, rendererMode=RendererMode.Interactive, dg = depsgraph, isFullExport = False)
            self.viewParams = exportViewportView(ctx, view3D, self.viewParams)
            
            vray.finishExport(self.renderer, interactive=True)
            self._prevRegion3dViewMatrix = view3D.region_3d.view_matrix.copy()


    def _initRenderer(self):
        self._createRenderer(ExporterType.IPR_VFB)
        
        self.cbRenderStopped = lambda: VfbEventHandler.stopInteractiveRender()
        vray.setRenderStoppedCallback(self.renderer, self.cbRenderStopped)
        
        # Set a static renderer ref that can be accessed from Blender operators
        VRayRendererIprVfb._activeRenderer = self.renderer
