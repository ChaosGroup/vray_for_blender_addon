# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy

from vray_blender.engine import NODE_TRACKERS, OBJ_TRACKERS

from vray_blender.exporting.plugin_tracker import ObjTracker, FakeScopedNodeTracker
from vray_blender.exporting.tools import FakeTimeStats
from vray_blender.exporting import obj_export, mtl_export, light_export, settings_export, view_export, world_export, fur_export

from vray_blender import debug
from vray_blender.lib import path_utils
from vray_blender.lib.blender_utils import TestBreak
from vray_blender.lib.camera_utils import ViewParams
from vray_blender.lib.common_settings import CommonSettings
from vray_blender.lib.defs import ExporterContext, ExporterContext, RendererMode, PersistedState
from vray_blender.lib.plugin_utils import updateValue

from vray_blender.bin import VRayBlenderLib as vray


# Use the no-op implementations of the plugin trackers defined below
_fakeNodeTrackers = dict([(t, FakeScopedNodeTracker()) for t in NODE_TRACKERS])

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

        # RenderResult will be passed to the C++ library to be updated with the image data
        # as it is received from VRay, so we need to keep it alive between render
        # methods invocations
        self.renderResult: bpy.types.RenderResult = None

        self.cbUpdateImage = None
        self.cbUpdateVfbLayers = None

        self.objTrackers = dict([(t, ObjTracker(t)) for t in OBJ_TRACKERS])

        # State to carry on to the next render cycle
        self.persistedState = PersistedState()


    def _reportInfo(self, engine: bpy.types.RenderEngine, msg: str):
        engine.report({'INFO'}, f"V-Ray: {msg}")
        debug.printInfo(msg)


    def _reportError(self, engine: bpy.types.RenderEngine, msg: str):
        engine.report({'ERROR'}, f"V-Ray: {msg}")
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
            if self.isPreview:
                vray.deletePreviewRenderer(self.renderer)
            self.renderer = None
            self.renderResult = None

    def _export(self, engine: bpy.types.RenderEngine, exporterCtx: ExporterContext):
        """ Perform a full export of the scene. The depsgraph will be re-evaluated
            in order to pick up any animated values.
        """
        try:
            # Has to be called before syncObjVisibility for proper update of the visibility of the fur objects.
            fur_export.syncFurInfo(exporterCtx)
            
            exporterCtx.calculateObjectVisibility()
            obj_export.GeometryExporter(exporterCtx).syncObjVisibility()

            light_export.syncLightMeshInfo(exporterCtx)
            light_export.collectLightMixInfo(exporterCtx)

            self._exportObjects(exporterCtx);       TestBreak.check(exporterCtx)
            self._exportMaterials(exporterCtx);     TestBreak.check(exporterCtx)
            self._exportLights(exporterCtx);        TestBreak.check(exporterCtx)

            if not exporterCtx.bake:
                self.viewParams = self._exportCameras(exporterCtx, self.viewParams)

                if not any(p.isActiveCamera for p in self.viewParams.values()):
                    raise Exception("No cameras selected for production rendering. Render aborted.")
            else:
                # Baking textures does not require a camera setup. Only export basic view configuration.
                self._exportBakeView(exporterCtx)

            self._exportWorld(exporterCtx)
            if exporterCtx.fullExport:
                self._exportSettings(exporterCtx)

                self._linkRenderChannels(exporterCtx)
            else:
                settings_export.SettingsExporter(exporterCtx).exportPlugin("SettingsLightLinker")

            # Call descendant's interface
            self._exportSceneAdjustments(exporterCtx)
            TestBreak.check(exporterCtx)
        finally:
            # Wait for all async export tasks to finish before proceeding.
            # This should be done even if an error occured during export
            vray.finishExport(self.renderer, interactive = False)
            
        TestBreak.check(exporterCtx)


    def _persistState(self, exporterCtx: ExporterContext):
        self.persistedState.activeInstancers = exporterCtx.activeInstancers
        self.persistedState.activeGizmos = exporterCtx.activeGizmos
        self.persistedState.exportedMtls = exporterCtx.exportedMtls
        self.persistedState.activeFurInfo = exporterCtx.activeFurInfo
        self.persistedState.activeMeshLightsInfo = exporterCtx.activeMeshLightsInfo


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


    def _getExporterContext(self, engine: bpy.types.RenderEngine, dg: bpy.types.Depsgraph, commonSettings: CommonSettings):
        context = ExporterContext()
        context.engine          = engine
        context.commonSettings  = commonSettings
        context.objTrackers     = self.objTrackers
        context.nodeTrackers    = _fakeNodeTrackers
        context.ctx             = bpy.context
        context.dg              = dg
        context.fullExport      = True
        context.ts              = FakeTimeStats()
        context.persistedState  = self.persistedState

        if self.isPreview:
            context.rendererMode = RendererMode.Preview
        elif dg.scene.vray.Exporter.isBakeMode:
            context.rendererMode = RendererMode.Bake
        else:
            context.rendererMode = RendererMode.Production

        return context


    def _createRenderer(self, exporterType):
        from vray_blender.lib.export_utils import setupDistributedRendering
        from vray_blender.engine.render_engine import VRayRenderEngine

        exporter = bpy.context.scene.vray.Exporter

        settings = vray.ExporterSettings()
        settings.exporterType     = exporterType
        settings.renderThreads    = exporter.custom_thread_count if exporter.use_custom_thread_count=='FIXED' else -1

        setupDistributedRendering(settings, exporterType)

        if self.isPreview:
            settings.previewDir = path_utils.getPreviewDir()
            return vray.createPreviewRenderer(settings)
        else:
            from vray_blender.plugins.system.compute_devices import updateEnabledComputeDevices
            updateEnabledComputeDevices(bpy.context)
            return vray.getMainRenderer(settings)


    def _linkRenderChannels(self, exporterCtx: ExporterContext):
        """ In order for certain plugins to affect the render channels, those render channels
            must be explicitly listed in a parameter of the plugin. This function updates
            the relevant plugin properties.
        """
        for pluginData, channelsList in exporterCtx.pluginRenderChannels.items():
            updateValue(self.renderer, pluginData[0], pluginData[1], channelsList)


    def _exportObjects(self, exporterCtx: ExporterContext):
        obj_export.run(exporterCtx)


    def _exportLights(self, exporterCtx: ExporterContext):
        light_export.LightExporter(exporterCtx).export()


    def _exportMaterials(self, exporterCtx: ExporterContext):
        mtl_export.run(exporterCtx)


    def _exportSettings(self, exporterCtx: ExporterContext):
        settings_export.SettingsExporter(exporterCtx).export()


    def _exportCameras(self, exporterCtx: ExporterContext, prevViewParams = dict[str, ViewParams]):
        return view_export.ViewExporter(exporterCtx).exportProdCameras(prevViewParams)


    def _exportBakeView(self, exporterCtx: ExporterContext):
        return view_export.ViewExporter(exporterCtx).exportBakeView()


    def _exportWorld(self, exporterCtx: ExporterContext):
        world_export.WorldExporter(exporterCtx).export()

