# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy

from vray_blender.engine import NODE_TRACKERS, OBJ_TRACKERS

from vray_blender.exporting.plugin_tracker import ObjTracker, FakeScopedNodeTracker
from vray_blender.exporting.tools import FakeTimeStats
from vray_blender.exporting import obj_export, mtl_export, light_export, settings_export, view_export, world_export, fur_export, instancer_export

from vray_blender import debug
from vray_blender.lib import path_utils
from vray_blender.lib.blender_utils import TestBreak
from vray_blender.lib.camera_utils import ViewParams
from vray_blender.lib.common_settings import CommonSettings
from vray_blender.lib.defs import ExporterContext, UIRegionContext, RendererMode, PersistedState, ProdRenderMode
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
        # Scene captured at _renderStart; used by _postRender so batch renders that
        # switch scenes between view layers resolve the right world tree.
        self.renderScene: bpy.types.Scene = None
        # Stamp-data keys already written to the persistent RenderResult; cleared on
        # _renderEnd. Prevents per-frame duplication during animation rendering.
        self._stampedCryptomatteKeys: set[str] = set()

        self.cbUpdateImage = None
        self.cbUpdateVfbLayers = None

        self.objTrackers = dict([(t, ObjTracker(t)) for t in OBJ_TRACKERS])

        # State to carry on to the next render cycle
        self.persistedState = PersistedState()

        # Active region context at the moment of render operator invocation.
        self.uiRegionContext: UIRegionContext = None



    def _reportInfo(self, engine: bpy.types.RenderEngine, msg: str):
        engine.report({'INFO'}, f"V-Ray: {msg}")
        debug.printInfo(msg)


    def _reportError(self, engine: bpy.types.RenderEngine, msg: str):
        engine.report({'ERROR'}, f"V-Ray: {msg}")
        debug.printError(msg)


    @staticmethod
    def getActiveUIRegionContext():
        activeWindow = bpy.context.window_manager.windows[0]
        if not (activeScreen := activeWindow.screen):
            return None

        if (space3D := bpy.context.space_data) and (space3D.type == 'VIEW_3D'):
            return UIRegionContext(bpy.context.space_data, activeWindow)

        view3DSpaces = [space for area in activeScreen.areas if area and area.type == 'VIEW_3D'
                            for space in area.spaces if space.type == 'VIEW_3D' ]

        if len(view3DSpaces) != 1:
            # 3D View space not found or cannot tell which one is active.
            # Returning None here will force the caller to use global scene data instead.
            return None

        return UIRegionContext(view3DSpaces[0], activeWindow)


    def _renderStart(self, exporterCtx: ExporterContext):
        """ Start the rendering sequence in vray. This method must be matched by
            a call to _renderEnd. Cryptomatte metadata is finalized in _postRender,
            after the render wait loop completes (getMetadata isn't valid until then).
        """
        scene = exporterCtx.commonSettings.scene
        engine = exporterCtx.engine

        self.renderScene = scene

        renderW =  scene.render.resolution_x * scene.render.resolution_percentage / 100
        renderH =  scene.render.resolution_y * scene.render.resolution_percentage / 100

        viewLayerName = exporterCtx.dg.view_layer_eval.name
        assert viewLayerName != "", "Rendering scene without View Layers"

        imageToBlender = scene.vray.Exporter.image_to_blender

        if imageToBlender:
            renderResult = engine.begin_result(0, 0, int(renderW), int(renderH), layer=viewLayerName, view="")
            assert renderResult, "Failed to obtain RenderResult for prod rendering"

            self.renderResult = renderResult
            combinedPass = self._setupElementPasses(viewLayerName, scene)
            # VRay will keep a weakref to the callback, so make sure we keep it alive
            # after the end of this function
            self.cbUpdateImage = None if exporterCtx.bake else (lambda: self._updateImage(engine))
        else:
            # No RenderResult, no element subscriptions, no callback. The server is
            # told via MsgRendererStart.imageToBlender to suppress all emits, so V-Ray
            # renders to its own VFB and nothing crosses the wire.
            self.renderResult = None
            combinedPass = None
            self.cbUpdateImage = None

        # Configure resumable rendering before starting. SettingsOutput.img_file is already
        # exported to V-Ray as a plugin attribute, so V-Ray derives the .vrprog path from it
        # when outputFileName is "".
        # Resumable Rendering is not supported in the Community Edition; force-disable it
        # there so saved scenes with the option enabled do not accidentally turn it on.
        settingsOutput = scene.vray.SettingsOutput
        if settingsOutput.resumable_rendering and not vray.isCommunityEdition():
            if not settingsOutput.img_file:
                raise RuntimeError("Resumable rendering requires an output file path to be set in V-Ray Output settings.")
            autosaveSeconds = int(settingsOutput.resumable_autosave_interval * 60)
            vray.setResumableRendering(self.renderer, True, "", autosaveSeconds, settingsOutput.resumable_delete_on_success)
        else:
            vray.setResumableRendering(self.renderer, False)

        # Hand pointers to the C++ side. Actual rendering is kicked off later via
        # vray.renderFrame / vray.renderSequenceStart by the descendant. A 0 pointer
        # signals the native side that there's no Blender buffer to write into; the
        # imageToBlender flag is also bundled into MsgRendererStart from here.
        combinedPassPtr = combinedPass.as_pointer() if combinedPass else 0
        vray.renderStart(self.renderer, combinedPassPtr, self.cbUpdateImage, imageToBlender)


    def _setupElementPasses(self, viewLayerName: str, scene: bpy.types.Scene):
        """ Build (channelType, instanceName, subIndex) routing for each non-Combined pass,
            request the channels from the server, and return the Combined pass.

            `scene` is taken from the exporter context, not bpy.context, so batch renders
            that switch scenes between view layers still resolve the right world tree.
        """
        from vray_blender.engine.render_elements import (
            CRYPTOMATTE_CHANNEL_TYPE, OBJECT_SELECT_CHANNEL_TYPE,
            OBJECT_SELECT_SUBINDEX_MATTE, OBJECT_SELECT_SUBINDEX_FILTER, OBJECT_SELECT_SUBINDEX_ALPHA,
            enumerateCryptomatteNodes, cryptomattePassName,
            enumerateObjectSelectNodes, objectSelectPassName,
            enumerateGenericChannelNodes, enumerateSpecialPasses,
        )
        layer = self.renderResult.layers[viewLayerName]
        combinedPass = layer.passes["Combined"]

        # Pass name -> (channelType, instanceName, subIndex). The server matches plugins by
        # instanceName (their `name` attribute), enabling multi-instance for every channel type.
        passMap: dict[str, tuple[int, str, int]] = {}

        def assign(passName: str, channelType: int, instanceName: str, subIndex: int):
            """ Write into passMap, warning on collisions so the user knows two channel
                nodes share the same `name` (default V-Ray names collide trivially).
            """
            entry = (channelType, instanceName, subIndex)
            if (existing := passMap.get(passName)) is not None and existing != entry:
                debug.report(severity="WARNING",
                             msg=f"Two render channel nodes both map to pass '{passName}' "
                                 f"({existing} vs {entry}); rename one of them or only the "
                                 f"first will receive data.")
                return
            passMap[passName] = entry

        for _cryptoNode, instanceName, typePrefix, _idType, numPasses in enumerateCryptomatteNodes(scene.world):
            for layerIdx in range(numPasses):
                assign(cryptomattePassName(typePrefix, instanceName, layerIdx),
                       CRYPTOMATTE_CHANNEL_TYPE, instanceName, layerIdx)

        for _osNode, instanceName in enumerateObjectSelectNodes(scene.world):
            for subIdx in (OBJECT_SELECT_SUBINDEX_MATTE, OBJECT_SELECT_SUBINDEX_FILTER, OBJECT_SELECT_SUBINDEX_ALPHA):
                assign(objectSelectPassName(instanceName, subIdx),
                       OBJECT_SELECT_CHANNEL_TYPE, instanceName, subIdx)

        for _node, channelType, instanceName, _reType in enumerateGenericChannelNodes(scene.world):
            assign(instanceName, channelType, instanceName, 0)

        # Effects Result + Denoised. The Denoised pass needs the plugin's actual `name`
        # attr as instanceName (default "Denoiser") - the server matches plugins by that.
        for passName, channelType, instanceName in enumerateSpecialPasses(scene.world):
            assign(passName, channelType, instanceName, 0)

        # Request once per (type, instance); the server enumerates internal layers itself.
        requestedInstances: set[tuple[int, str]] = set()
        for channelType, instanceName, _subIdx in passMap.values():
            key = (channelType, instanceName)
            if key in requestedInstances:
                continue
            requestedInstances.add(key)
            vray.requestRenderChannel(self.renderer, channelType, instanceName, 0)

        elementPasses = []
        for rpass in layer.passes:
            if rpass.name == "Combined":
                continue
            if entry := passMap.get(rpass.name):
                channelType, instanceName, subIndex = entry
                elementPasses.append((rpass.as_pointer(), channelType, instanceName, subIndex))

        if elementPasses:
            vray.setElementPasses(self.renderer, elementPasses)

        return combinedPass


    def _postRender(self, engine: bpy.types.RenderEngine):
        """ Bookkeeping that depends on actual render results being available.
            Call AFTER the render-job wait loop completes and BEFORE end_result.
            getMetadata isn't valid until V-Ray has finished and sent the final image.
        """
        if not self.renderResult:
            return
        self._setCryptomatteMetadata(engine)


    def _setCryptomatteMetadata(self, engine: bpy.types.RenderEngine):
        """ Stamp Cryptomatte manifest fields onto the persistent RenderResult.

            Stamp data must go on get_result(), not begin_result()'s temporary: only pixel
            data is merged on end_result, so stamps written to the temporary are dropped.
            The persistent result accumulates across an animation, so we dedupe keys and
            skip subsequent frames once the (scene-static) manifest has been stamped.
        """
        if self._stampedCryptomatteKeys:
            return  # Manifest is scene-static; one frame's worth of stamps is enough.

        from vray_blender.engine.render_elements import enumerateCryptomatteNodes
        persistentResult = None
        scene = self.renderScene or bpy.context.scene

        def writeStampData(metadata):
            nonlocal persistentResult
            if not metadata:
                return
            if persistentResult is None:
                persistentResult = engine.get_result()
            for entry in metadata.split(';'):
                key, sep, value = entry.partition('=')
                if sep and key not in self._stampedCryptomatteKeys:
                    persistentResult.stamp_data_add_field(key, value)
                    self._stampedCryptomatteKeys.add(key)

        for _cryptoNode, instanceName, *_ in enumerateCryptomatteNodes(scene.world):
            writeStampData(vray.getMetadata(self.renderer, f"cryptomatte.{instanceName}"))

        # Single-instance manifest (empty pluginInstanceName path on the server).
        writeStampData(vray.getMetadata(self.renderer, "cryptomatte"))


    def _renderEnd(self, engine: bpy.types.RenderEngine, success: bool):
        """ Free the resources associated with a render job

            @param engine - maybe None if this method is invoked from a cancellation request
            @param success - report the job as successful to Blender
        """
        # Let Blender know that we are finished using the renderResult object. When
        # image_to_blender is off we never called begin_result, so there's no result
        # to end - skip the call entirely.
        if engine is not None and self.renderResult is not None:
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
            self.renderScene = None
            self._stampedCryptomatteKeys.clear()

    def _export(self, engine: bpy.types.RenderEngine, exporterCtx: ExporterContext):
        """ Perform a full export of the scene. The depsgraph will be re-evaluated
            in order to pick up any animated values.
        """
        try:
            # Has to be called before syncObjVisibility for proper update of the visibility of the fur objects.
            fur_export.syncFurInfo(exporterCtx)

            exporterCtx.calculateObjectVisibility()
            mtl_export.syncMtlExportCache(exporterCtx)

            obj_export.GeometryExporter(exporterCtx).syncObjVisibility()

            light_export.syncLightMeshInfo(exporterCtx)
            light_export.collectLightMixInfo(exporterCtx)

            geomExporter = self._exportObjects(exporterCtx);   TestBreak.check(exporterCtx)
            self._exportMaterials(exporterCtx);                 TestBreak.check(exporterCtx)
            lightExporter = self._exportLights(exporterCtx);   TestBreak.check(exporterCtx)
            self._exportInstances(exporterCtx, geomExporter, lightExporter); TestBreak.check(exporterCtx)

            if not exporterCtx.bake:
                self.viewParams = self._exportCameras(exporterCtx, self.viewParams)

                if not any(p.isActiveCamera for p in self.viewParams.values()):
                    raise Exception("No cameras selected for production rendering. Render aborted.")
            else:
                # Baking textures does not require a camera setup. Only export basic view configuration.
                self._exportBakeView(exporterCtx)

            self._exportWorld(exporterCtx)
            self._exportSettings(exporterCtx)

            if exporterCtx.fullExport:
                self._linkRenderChannels(exporterCtx)

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
        context.uiRegionContext = self.uiRegionContext

        if self.isPreview:
            context.rendererMode = RendererMode.Preview
        elif getattr(self.__class__, "renderMode", None) == ProdRenderMode.EXPORT_PROXY:
            context.rendererMode = RendererMode.ProxyExport
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
        return obj_export.run(exporterCtx)


    def _exportLights(self, exporterCtx: ExporterContext):
        lightExporter = light_export.LightExporter(exporterCtx)
        lightExporter.export()
        return lightExporter


    def _exportInstances(self, exporterCtx: ExporterContext, geomExporter, lightExporter):
        instancer_export.run(exporterCtx, geomExporter, lightExporter)


    def _exportMaterials(self, exporterCtx: ExporterContext):
        mtl_export.run(exporterCtx)


    def _exportSettings(self, exporterCtx: ExporterContext):
        settings_export.SettingsExporter(exporterCtx).export()


    def _exportCameras(self, exporterCtx: ExporterContext, prevViewParams: dict[str, ViewParams]):
        return view_export.ViewExporter(exporterCtx).exportProdCameras(prevViewParams)


    def _exportBakeView(self, exporterCtx: ExporterContext):
        return view_export.ViewExporter(exporterCtx).exportBakeView()


    def _exportWorld(self, exporterCtx: ExporterContext):
        world_export.WorldExporter(exporterCtx).export()

