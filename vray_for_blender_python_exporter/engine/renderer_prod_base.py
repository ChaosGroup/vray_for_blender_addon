import bpy
from pathlib import Path

from vray_blender.engine import NODE_TRACKERS, OBJ_TRACKERS
from vray_blender.engine.vfb_event_handler import VfbEventHandler

from vray_blender.exporting.plugin_tracker import FakeScopedNodeTracker, FakeObjTracker
from vray_blender.exporting.tools import FakeTimeStats
from vray_blender.exporting import obj_export, mtl_export, light_export, settings_export, view_export, world_export

from vray_blender import debug
from vray_blender.lib import path_utils
from vray_blender.lib.camera_utils import ViewParams
from vray_blender.lib.common_settings import CommonSettings
from vray_blender.lib.defs import ExporterContext, ExporterContext, RendererMode

from vray_blender.engine.zmq_process import ZMQ
from vray_blender.bin import VRayBlenderLib as vray


# We are always exporting the full scene when rendering for production
# Use the no-op implementations of the plugin trackers defined below
_fakeNodeTrackers = dict([(t, FakeScopedNodeTracker()) for t in NODE_TRACKERS])
_fakeObjTrackers = dict([(t, FakeObjTracker()) for t in OBJ_TRACKERS])

_INFO_ANIMATION = "Animation is supported trough Render Image, with Animation set to not None"

#############################
## Module free functions
#############################
def _exportObjects(exporterCtx: ExporterContext):
    obj_export.run(exporterCtx) 


def _exportLights(exporterCtx: ExporterContext):
    light_export.LightExporter(exporterCtx).export()


def _exportMaterials(exporterCtx: ExporterContext):
    mtl_export.run(exporterCtx)


def _exportSettings(exporterCtx: ExporterContext):
    settings_export.SettingsExporter(exporterCtx).export()


def _exportWorld(ctx: ExporterContext):
    world_export.WorldExporter(ctx).export()


def _exportCameras(ctx: ExporterContext, prevViewParams = dict[str, ViewParams]):
    return view_export.ViewExporter(ctx).exportProdCameras(prevViewParams)


def _exportBakeView(ctx: ExporterContext):
    return view_export.ViewExporter(ctx).exportBakeView()


def _collectLightMeshInfo(exporterCtx: ExporterContext):
    """ Collect info about the visible and updated objects associated with LightMesh exports. """
    from vray_blender.plugins.light.LightMesh import collectLightMeshInfo
    active, updated = collectLightMeshInfo(exporterCtx)
    exporterCtx.activeMeshLightsInfo = active
    exporterCtx.updatedMeshLightsInfo = updated


#############################
## VRayRendererProd
#############################

class VRayRendererProdBase:
    """ Final (or 'production' in VRay lingo) renderer implementation.
        It is used for non-interactive rendering - e.g. production and material previews 
    """

    def __init__(self, isPreview: bool):
        # An opaque pointer to the VRay renderer object owned by the C++ library
        self.renderer = None 
        self.isPreview = isPreview
        self.viewParams: dict[str, ViewParams] = {}    # Per-camera ViewParams from the latest evaluation. 
                                                       # Used for difference comparison with the current state. 

        # RenderResult will be passed to the C++ library to be updated with the image data
        # as it is received from VRay, so we need to keep it alive between render
        # methods invocations
        self.renderResult: bpy.types.RenderResult = None

        self.cbUpdateImage = None
        self.cbUpdateVfbLayers = None

    def _reportInfo(self, engine: bpy.types.RenderEngine, msg: str):
        debug.printInfo(msg)


    def _reportError(self, engine: bpy.types.RenderEngine, msg: str):
        debug.printError(msg)
        

    def _renderStart(self, exporterCtx: ExporterContext):
        """ Start the rendering sequence in vray. This method must be matched by 
            a call to _renderEnd.
        """
        scene = exporterCtx.commonSettings.scene
        engine = exporterCtx.commonSettings.renderEngine

        renderW =  scene.render.resolution_x * scene.render.resolution_percentage / 100
        renderH =  scene.render.resolution_y * scene.render.resolution_percentage / 100

        viewLayerName = exporterCtx.dg.view_layer_eval.name
        assert viewLayerName != "", "Rendering scene without View Layers"

        renderResult = engine.begin_result(0, 0, int(renderW), int(renderH), layer=viewLayerName, view="")
        assert renderResult, "Failed to obtain RenderResult for prod rendering"

        # For the moment, we only support one render result and one pass - 'Combined'
        self.renderResult = renderResult
        
        renderPass = self.renderResult.layers[viewLayerName].passes["Combined"]

        # VRay will keep a weakref to the callback, so make sure we keep it alive
        # after the end of this function
        self.cbUpdateImage = None if exporterCtx.bake else (lambda: self._updateImage(engine))
        
        # This call will return only after the rendering job is finished, i.e. all frames
        # have been rendered. The vray class will
        vray.renderStart(self.renderer, renderPass.as_pointer(), self.cbUpdateImage)


    def _renderEnd(self, engine: bpy.types.RenderEngine, success: bool):
        """ Free the resources associated with a render job 

            @param engine - maybe None if this method is invoked from a cancellation request
            @param success - report the job as successful to Blender
        """
        # Let Blender know that we are finished using the renderResult object
        if engine is not None:
            if success:
                engine.end_result(self.renderResult, cancel=False, highlight=True, do_merge_results=True)
            else:
                engine.end_result(None)

        if self.renderer:
            vray.renderEnd(self.renderer)
            vray.deleteRenderer(self.renderer)
            self.renderer = None
            self.renderResult = None
                            

    def _export(self, engine: bpy.types.RenderEngine, exporterCtx: ExporterContext):
        """ Perform a full export of the scene. The depsgraph will be re-evaluated
            in order to pick up any animated values.
        """
        try:
            exporterCtx.calculateObjectVisibility()
            
            _collectLightMeshInfo(exporterCtx)
            _exportObjects(exporterCtx)
            _exportLights(exporterCtx)

            if not exporterCtx.bake:
                self.viewParams = _exportCameras(exporterCtx, self.viewParams)
                if not any(p.isActiveCamera for p in self.viewParams.values()):
                    raise Exception("No cameras selected for production rendering. Render aborted.")
            else:
                # Baking textures does not require a camera setup. Only export basic view configuration.
                _exportBakeView(exporterCtx)

            _exportSettings(exporterCtx)
            _exportMaterials(exporterCtx)
            _exportWorld(exporterCtx)

            # Call descendant's interface
            self._exportSceneAdjustments(exporterCtx)
        finally:
            # Wait for all async export tasks to finish before proceeding.
            # This should be done even if an error occured during export
            vray.finishExport(self.renderer, interactive = False)
        

    @staticmethod
    def _syncView(ctx: ExporterContext):
        viewSettings = vray.ViewSettings()
        viewSettings.renderMode = ctx.commonSettings.renderMode
        viewSettings.vfbFlags = ctx.commonSettings.vfbFlags
        viewSettings.viewportImageType = ctx.commonSettings.viewportImageType
        vray.syncViewSettings(ctx.renderer, viewSettings)


    def _updateImage(self, engine: bpy.types.RenderEngine):
        """ Callback invoked when a new image has been received from VRay """
        # Do not let any exeptions escape to the caller. If an exception handler 
        # is not installed on the native code, the app might crash with no error log
        try:
            if not self.renderResult:
                debug.printError("Invalid render result in drawing callback")
                return
        
            # renderResult has been updated by the caller. Push the updated data to the screen.
            engine.update_result(self.renderResult)
        except Exception as ex:
            debug.printExceptionInfo(ex, "VRayRendererProd::_updateImage() callback")


    def _getExporterContext(self, renderer: int, dg: bpy.types.Depsgraph, commonSettings: CommonSettings):
        context = ExporterContext()
        context.renderer        = renderer
        context.commonSettings  = commonSettings
        context.objTrackers     = _fakeObjTrackers
        context.nodeTrackers    = _fakeNodeTrackers
        context.ctx             = bpy.context
        context.dg              = dg
        context.fullExport      = True
        context.ts              = FakeTimeStats()

        if self.isPreview:
            context.rendererMode = RendererMode.Preview
        elif dg.scene.vray.Exporter.isBakeMode:
            context.rendererMode = RendererMode.Bake
        else:
            context.rendererMode = RendererMode.Production

        return context
    

    def _createRenderer(self, exporterType):
        settings = vray.ExporterSettings()
        settings.exporterType     = exporterType
        settings.closeVfbOnStop   = True
        
        if self.isPreview: 
            settings.previewDir = path_utils.getPreviewDir()
        
        return vray.createRenderer(settings)

