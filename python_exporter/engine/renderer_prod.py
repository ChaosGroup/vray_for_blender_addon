# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import threading
import time
import bpy
from typing import Optional

from vray_blender.engine.renderer_prod_base import VRayRendererProdBase
from vray_blender.exporting import view_export
from vray_blender.exporting.update_tracker import UpdateTracker

from vray_blender import debug
from vray_blender.lib.blender_utils import TestBreak, preserveInitialFrame, setFloatFrame, getVRayPreferences
from vray_blender.lib.camera_utils import isSameCamera
from vray_blender.lib.path_utils import getV4BTempDir
from vray_blender.lib.common_settings import CommonSettings, collectExportSceneSettings
from vray_blender.lib.defs import ExporterContext, ExporterType, ProdRenderMode
from vray_blender.lib.lib_utils import framesToSequences
from vray_blender.lib.names import syncUniqueNames

from vray_blender.bin import VRayBlenderLib as vray

#############################
## VRayRendererProd
#############################

FRAME_EXPORT_SLEEP_TIME = 0.02

class VRayRendererProd(VRayRendererProdBase):
    """ Final (or 'production' in VRay lingo) renderer implementation.
        It is used for non-interactive production renders of both single frames
        and animations.
    """

    # The prod renderer runs in a dedicated thread. Blender needs to access it from the main
    # thread, this is why synchronization is necessary.
    _instanceLock = threading.Lock()
    _instance = None  # a reference to the VRayRendererProd instance

    # Type of job ("Scene export", "Render" or "Cloud submit")
    renderMode = ProdRenderMode.RENDER 

    # Per-job override of the scene's animation mode. 'AUTO' means "use Exporter.animation_mode".
    forceAnimationMode: str = 'AUTO'

    # Fake view layer use keyframe that will be set before the rendering starts.    
    fakeViewLayerKeyframe: float | None  = None

    # Upper bound for the wait in abort(). Only ever reached if the job thread is stuck, and
    # giving up on it is safe: the native side then hands the main exporter over to that thread
    # instead of destroying it (see relinquishMainExporter).
    ABORT_TIMEOUT = 10.0
    

    def __init__(self):
        super().__init__(isPreview=False)
        VRayRendererProd._instance = self

        self.exporterCtx: ExporterContext = None
        self.cbRenderStopped = None
        self.aborted = False

        # Cleared while render() is in flight, set once it has released the native renderer.
        self._renderFinished = threading.Event()
        self._renderFinished.set()

        # The job thread inside render(), so the abort() it issues on TestBreak does not wait
        # for itself.
        self._renderThreadId = None


    @staticmethod
    def isActive():
        """ Return True if the renderer is currently active, i.e. rendering """
        with VRayRendererProd._instanceLock:
            if prodRenderer := VRayRendererProd._instance:
                return prodRenderer.renderer is not None
            return False

    @staticmethod
    def isAborted():
        """ Return True if the renderer is currently active, i.e. rendering """
        with VRayRendererProd._instanceLock:
            if prodRenderer := VRayRendererProd._instance:
                return prodRenderer.aborted
            return False

    @staticmethod
    def testBreak(engine: bpy.types.RenderEngine):
        with VRayRendererProd._instanceLock:
            if prodRenderer := VRayRendererProd._instance:
                if engine.test_break() or prodRenderer.aborted:
                    raise TestBreak.Exception()

    @staticmethod
    def clearFakeViewLayerKeyframe(viewLayerName: str):
        from vray_blender.lib.blender_utils import getViewLayerUseFCurve
        import math

        if (fakeVLayerKf := VRayRendererProd.fakeViewLayerKeyframe) is not None and \
            (vlFCurve := getViewLayerUseFCurve(viewLayerName)):
            
            if (fakeKf := next((kf for kf in vlFCurve.keyframe_points
                                if math.isclose(kf.co.x, fakeVLayerKf, abs_tol=1e-5)), None)):
                vlFCurve.keyframe_points.remove(fakeKf)


    def abort(self):
        """ Abort the rendering job if it is running, and wait for the render job thread to
            release the renderer. Can be called from any context; called from that thread
            itself, it does not wait.
        """
        with VRayRendererProd._instanceLock:
            self.aborted = True

            if self.renderer:
                # Abort the job in vray. The cleanup will be performed when the job
                # has finished.
                vray.abortRender(self.renderer)

        # Deliberately outside the lock - the job thread takes it in testBreak() on every poll.
        if self._renderThreadId == threading.get_ident():
            return

        if not self._renderFinished.wait(VRayRendererProd.ABORT_TIMEOUT):
            debug.printError("Timed out waiting for the production render to abort")


    def render(self, engine: bpy.types.RenderEngine, depsgraph: bpy.types.Depsgraph):
        self._renderThreadId = threading.get_ident()
        self._renderFinished.clear()

        try:
            self._renderJob(engine, depsgraph)
        finally:
            # A throw past _initRenderJob()'s renderer creation would skip _finalizeRenderJob()
            # and leave the renderer claimed, which also keeps isActive() true for good.
            # A no-op once the renderer has been released.
            with VRayRendererProd._instanceLock:
                self._renderEnd(engine, success=False)

            # Must be last - abort() reads it as 'the native renderer is no longer in use'.
            self._renderThreadId = None
            self._renderFinished.set()


    def _renderJob(self, engine: bpy.types.RenderEngine, depsgraph: bpy.types.Depsgraph):
        if not __class__._shouldRenderJob():
            return

        scene = bpy.context.scene
        if not self._initRenderJob(engine, depsgraph):
            return

        success  = True

        try:
            match __class__.renderMode:
                case ProdRenderMode.CLOUD_SUBMIT:
                    self._submitToCloud(engine)
                case ProdRenderMode.EXPORT_VRSCENE:
                    scenePath = self._writeVrscene(scene, engine)
                    self._packExportedScene(scenePath, engine)
                case ProdRenderMode.EXPORT_PROXY:
                    success = self._exportProxy(scene, engine)
                case ProdRenderMode.RENDER:
                    errMsg = self._render(scene, engine)
                    success = not bool(errMsg)
                case _:
                    assert False, f"Invalid render mode in PROD renderer: {__class__.renderMode}"

        except TestBreak.Exception:
            debug.printInfo("Interrupted by user")
            self.abort()
        except Exception as ex:
            success = False
            self._reportError(engine, f"{str(ex)} See log for details")
            debug.printExceptionInfo(ex, "VRayRendererProd::render()")

        # Finalize the rendering regardless of whether it has been successful in order to
        # free any associated resources and let Blender know that state has changed.
        self._finalizeRenderJob(engine, success)


    def _render(self, scene: bpy.types.Scene, engine: bpy.types.RenderEngine):
        if self.exporterCtx.isAnimation:
            self.exporterCtx.commonSettings.updateFromScene()

            with preserveInitialFrame(scene):

                # Determine if the rendering process has started.
                # Note: Don't replace it with "frame == self.exporterCtx.commonSettings.animation.startFrame" if-statement,
                # because in 'Single Frame' render mode with motion blur enabled, this statement won't be valid.
                renderingStarted = False
                mbBuilder = self.exporterCtx.motionBlurBuilder
                exportMotionData = self.exporterCtx.commonSettings.exportMotionData

                # The camera whose values currently sit at each exported subframe. All camera
                # plugins share one name during a production render, so their values form a
                # single time-indexed track that every camera writes into.
                subframeCameras: dict[float, bpy.types.Object] = {}

                for frame in self._getFrameRange(scene):

                    self._exportAnimationFrame(engine, frame)
                    self.exporterCtx.fullExport = False

                    if exportMotionData:
                        subframeCameras[frame] = view_export.getActiveCamera(self.exporterCtx)

                        # During motion blur animation export rendering is started only when
                        # the entire interval of required frames is exported. Intervals of
                        # consecutive frames may end out of order or on the same subframe, so
                        # the builder decides which frames this subframe completes - possibly
                        # more than one, possibly none.
                        if not (renderFrames := mbBuilder.popFramesReadyForRender(frame)):
                            continue

                        # Everything below the earliest subframe an unrendered frame may still
                        # read is no longer needed. The frames just released are part of that:
                        # they are handed over below, and one of them may have been held back
                        # past its own interval end, so the subframe reached here does not bound
                        # what they need.
                        frameForClearing = mbBuilder.earliestSampleTime(renderFrames)
                        self._clearFrameData(upToTime=frameForClearing)

                        # Keep the record of which camera owns which subframe in step with the
                        # data V-Ray still holds.
                        subframeCameras = {f: c for f, c in subframeCameras.items()
                                           if f >= frameForClearing}
                    else:
                        self._clearFrameData(upToTime=frame)
                        renderFrames = [frame]

                    for renderFrame in renderFrames:
                        if exportMotionData:
                            self._restoreFrameCamera(engine, renderFrame, subframeCameras)

                        # With motion blur this is the base frame the interval was calculated
                        # from, not the subframe just exported. Without it, the frame is the one
                        # _exportAnimationFrame() already set.
                        vray.setRenderFrame(self.renderer, renderFrame)

                        if not renderingStarted:
                            self._startRenderSequence(engine)
                            renderingStarted = True
                        else:
                            # Proceed to the next frame
                            vray.continueRenderSequence(self.renderer)

                        self._waitFrameRenderEnd(engine, renderFrame)
                        self._postRender(engine)
                        self._persistState(self.exporterCtx)

                if renderingStarted:
                    engine.update_progress(1.0)
                    self._reportInfo(engine, "Animation exported.")
                else:
                    return "No frames selected for rendering"

        else:
            self._renderSingleFrame(engine, scene.frame_current)

        return None


    def _writeVrscene(self, scene: bpy.types.Scene, engine: bpy.types.RenderEngine, baseScenePath="", isCloudExport=False):
        """ Export the full animation sequence to V-Ray and write a .vrscene file """
        self._exportFullScene(scene, engine)
        exportSettings, errMsg = collectExportSceneSettings(scene, baseScenePath, self.exporterCtx.dg.view_layer_eval.name)

        if exportSettings:
            exportSettings.cloudExport = isCloudExport

            if not vray.writeVrscene(self.renderer, exportSettings):
                self._reportError(engine, "Scene export failed")
                return

            # Writing the scene is an asynchronous task during which we need to keep the renderer alive.
            while vray.exportJobIsRunning(self.renderer):
                __class__.testBreak(engine)
                time.sleep(FRAME_EXPORT_SLEEP_TIME)

            self._reportInfo(engine, f"Exported scene: {exportSettings.filePath}")

            return exportSettings.filePath
        elif errMsg:
            self._reportError(engine, f"Export scene: {errMsg}")

        return ""

    def _packExportedScene(self, scenePath: str, engine: bpy.types.RenderEngine):
        """ If enabled, collect the exported scene's assets via Chaos Cloud and optionally zip them. """
        if not scenePath:
            return

        preferences = getVRayPreferences()
        if not preferences.export_scene_pack:
            return

        self._reportInfo(engine, "Packing scene assets...")

        from vray_blender.lib.export_utils import packExportedScene
        if errMsg := packExportedScene(scenePath, archive=preferences.export_scene_zip):
            self._reportError(engine, f"Scene packing: {errMsg}")
        else:
            self._reportInfo(engine, "Scene packed.")

    def _initRenderJob(self, engine: bpy.types.RenderEngine, depsgraph: bpy.types.Depsgraph):
        
        scene = bpy.context.scene

        commonSettings = CommonSettings(scene,
                                        isInteractive = False,
                                        viewLayerName = depsgraph.view_layer_eval.name,
                                        exportOnly = __class__._exportOnly(),
                                        forceAnimationMode = __class__.forceAnimationMode)

        try:
            commonSettings.updateFromScene()
        except Exception as ex:
            # The reason is already in the log, e.g. which part of the frames list is invalid
            self._reportError(engine, str(ex))
            return False

        self.exporterCtx = self._getExporterContext(engine, depsgraph, commonSettings)
        # Value was overridden in the vray.render operator
        self.exporterCtx.forceAnimationMode = __class__.forceAnimationMode

        if self.exporterCtx.isAnimation and (len(commonSettings.animation.frames) == 0):
            self._reportInfo(engine, f"View layer '{depsgraph.view_layer_eval.name}' is not enabled for the selected frames")
            return False

        with VRayRendererProd._instanceLock:
            if not self.renderer:
                isProxyAnimationExport = (
                    __class__.renderMode == ProdRenderMode.EXPORT_PROXY
                    and getVRayPreferences().export_proxy_animation_range == 'FRAME_RANGE'
                )
                exporterType = ExporterType.ANIMATION if (self.exporterCtx.isAnimation or isProxyAnimationExport) else ExporterType.PROD
                self.renderer = self._createRenderer(exporterType)

                def onStopped():
                    self.aborted = True

                self.cbRenderStopped = lambda isAborted: onStopped()
                vray.setRenderStoppedCallback(self.renderer, self.cbRenderStopped)

        self.exporterCtx.renderer = self.renderer

        vray.startExport(self.renderer, bpy.context.scene.vray.Exporter.debug_threads)

        # In production mode we always perform a full export which only adds data to the scene.
        # Clearing the scene will ensure that no remnants of a previous scene are left around.
        vray.clearScene(self.renderer)
        UpdateTracker.clear()
        
        syncUniqueNames()

        VRayRendererProdBase._syncView(self.exporterCtx)

        return True


    def _renderSingleFrame(self, engine: bpy.types.RenderEngine, frame):
        """ Export and render а single frame. """
        vray.setRenderFrame(self.renderer, frame)
        self._export(engine, self.exporterCtx)

        debug.printDebug("Start single-frame render.")

        self._renderStart(self.exporterCtx)
        vray.renderFrame(self.renderer)

        while vray.renderJobIsRunning(self.renderer):
            __class__.testBreak(engine)

            progress = vray.getRenderProgress(self.renderer)
            engine.update_progress(progress)
            time.sleep(FRAME_EXPORT_SLEEP_TIME)

        # Render data has fully arrived; finalize cryptomatte metadata and
        # dynamically-registered passes (e.g. Effects Result) before end_result.
        self._postRender(engine)

        debug.printDebug("End single-frame render.")


    def _submitToCloud(self, engine):
        from vray_blender.exporting.cloud_job import VCloudJob
        import os, tempfile

        # Create temporary vrscene file used only for cloud submissions.
        # Cleanup of tempDir is owned by the background submit thread in VCloudJob,
        # because submitToCloud() spawns a subprocess and returns immediately.
        tempDir = tempfile.mkdtemp(dir=getV4BTempDir())
        baseScenePath = os.path.join(tempDir, "cloud_export.vrscene").replace("\\", "/")
        scenePath = self._writeVrscene(bpy.context.scene, engine, baseScenePath, isCloudExport=True)
        if not scenePath:
            self._reportError(engine, "Failed to export .vrscene for cloud submission")
            return

        job = VCloudJob(bpy.context.scene, scenePath)
        job.submitToCloud()

    def _exportProxy(self, scene: bpy.types.Scene, engine: bpy.types.RenderEngine):
        from vray_blender.proxy import runProxyFileExport
        return runProxyFileExport(scene, self.exporterCtx, engine)



    def _startRenderSequence(self, engine: bpy.types.RenderEngine):
        assert self.exporterCtx.isAnimation
        
        __class__.testBreak(engine)

        debug.printDebug("Start animation render.")

        # Obtain a rendering target from Blender and set it to the C++ renderer
        self._renderStart(self.exporterCtx)

        # CommonSettings already resolved the frame set once, including the view layer filter.
        # Deriving the sequence from it is what keeps it index-matched with the export loop
        # and with _waitFrameRenderEnd.
        frames = self.exporterCtx.commonSettings.animation.frames
        assert frames

        flatSequenceList = [item for sequence in framesToSequences(frames) for item in sequence]
        vray.renderSequenceStart(self.renderer, flatSequenceList)


    def _waitFrameRenderEnd(self, engine: bpy.types.RenderEngine, currentFrame: float):
        """ Waits until rendering of the current frame is complete or the render job finishes """
        # The next frame in the sequence. When vray.getLastRenderedFrame() returns this value,
        # it indicates that rendering of "currentFrame" has completed.
        frames = self.exporterCtx.commonSettings.animation.frames
        
        try:
            currentIdx = frames.index(currentFrame)
            nextFrame = frames[currentIdx + 1] if currentIdx + 1 < len(frames) else None
        except ValueError:
            nextFrame = None

        while vray.renderJobIsRunning(self.renderer):
            __class__.testBreak(engine)

            if nextFrame is not None:
                lastRenderedFrame = vray.getLastRenderedFrame(self.renderer)

                if nextFrame == lastRenderedFrame:
                    engine.update_progress((currentIdx + 1) / len(frames))
                    return

                if lastRenderedFrame > nextFrame:
                    # V-Ray reports the frame it will render next, so it has already moved past
                    # the one this wait was keyed to and the condition above can never be met.
                    # Carry on with the job instead of blocking it forever on what is only a
                    # progress-tracking mismatch.
                    debug.printError(f"Render sequence out of sync: waiting for frame {nextFrame}, "
                                     f"V-Ray has already reached {lastRenderedFrame}")
                    return

            time.sleep(FRAME_EXPORT_SLEEP_TIME)


    def _restoreFrameCamera(self, engine: bpy.types.RenderEngine, renderFrame: float,
                            subframeCameras: dict[float, bpy.types.Object]):
        """ Restore values possibly written by a differemt subframe camera.

            All camera plugins share a single name during a production render, so every camera
            writes into one time-indexed track. The export walks subframes in time order and
            exports whichever camera the scene has at each of them, so a switch inside a frame's
            shutter leaves the next camera's values in the previous frame's interval and V-Ray
            interpolates the view across the cut. Restoring the frame's own camera right before
            the frame is handed over keeps each frame's blur to one camera. Frames render in
            ascending order, so each one re-exports over the previous one's leftovers in turn.

            The range restored is subframesInSampleRange(), which also covers the shutter
            SettingsMotionBlur describes - it need not be the one the subframes came from.

            Costs nothing when no switch falls inside the range: nothing is re-exported.
        """
        ctx = self.exporterCtx
        frameCamera = ctx.motionBlurBuilder.cameraForFrame(renderFrame)

        if frameCamera is None:
            return

        # Only subframes already exported can be read by this frame's render. Ones further along
        # the sample range are still to come, and the walk will export them in their own time.
        framesWithStaleCamera = [
            f for f in ctx.motionBlurBuilder.subframesInSampleRange(renderFrame)
            if (f in subframeCameras) and not isSameCamera(subframeCameras[f], frameCamera)]

        if not framesWithStaleCamera:
            return

        debug.printDebug(f"Restoring camera '{frameCamera.name}' for frame {renderFrame} "
                         f"at subframes {framesWithStaleCamera}")

        savedFrame = ctx.currentFrame

        try:
            for subframe in framesWithStaleCamera:
                __class__.testBreak(engine)

                setFloatFrame(engine, subframe)
                ctx.currentFrame = subframe
                vray.setRenderFrame(self.renderer, subframe)

                # Currently, the change tracker in ZmqServer does not track its values per time. 
                # Setting the last set value for a different frame will not work. Sending the camera 
                # the subframe already holds first - the values in effect there now - makes the 
                # one that follows it a change again.
                for camera in (subframeCameras[subframe], frameCamera):
                    ctx.cameraOverride = camera

                    # Deliberately discards the returned ViewParams: self.viewParams has to keep
                    # describing the scene's own camera for the next frame's full export.
                    self._exportCameras(ctx, self.viewParams, camerasOnly=True)

                subframeCameras[subframe] = frameCamera
        finally:
            ctx.cameraOverride = None
            ctx.currentFrame = savedFrame

            # The scene frame has to go back with it: the caller carries on exporting and
            # rendering from where it was, and nothing else moves it back.
            setFloatFrame(engine, savedFrame)


    def _exportAnimationFrame(self, engine: bpy.types.RenderEngine, frame: float):
        """ Export animation sequence """

        __class__.testBreak(engine)

        self._reportInfo(engine, f"Export animation frame {frame}")

        setFloatFrame(engine, frame)

        vray.setRenderFrame(self.renderer, frame)

        # TODO: ideally this should go to _getExporterContext()
        self.exporterCtx.currentFrame    = frame
        self.exporterCtx.persistedState  = self.persistedState
        self.exporterCtx.objTrackers     = self.objTrackers

        self._export(engine, self.exporterCtx)

        return True


    def _getFrameRange(self, scene):
        if self.exporterCtx.commonSettings.exportMotionData:
            self.exporterCtx.motionBlurBuilder.initialize(scene, self.exporterCtx)
            
            return self.exporterCtx.motionBlurBuilder.getFrames()

        return (f for f in self.exporterCtx.commonSettings.animation.frames)


    def _exportFullScene(self, scene: bpy.types.Scene, engine: bpy.types.RenderEngine):
        """ Export the complete frame sequence. This is only called in order to export a .vrscene
            file. Normal render jobs will be exported frame by frame.
        """
        self.exporterCtx.exportOnly = __class__._exportOnly()

        if not self.exporterCtx.isAnimation:
            # When animation mode is off, the procedure for exporting animation will not work
            # because Blender will not generate depsgraph updates when the frame is changed.
            # This is why we cannot just use a frame range of 1 and reuse the code in the else:
            # block below.
            frameStart = self.exporterCtx.commonSettings.animation.frameCurrent
            vray.setRenderFrame(self.renderer, frameStart)
            self.exporterCtx.currentFrame = frameStart

            self.exporterCtx.exportProgress.setTotalObjectsAndFrames(self.exporterCtx)
            self._export(engine, self.exporterCtx)
        else:

            with preserveInitialFrame(scene):
                allFrames = list(self._getFrameRange(scene))
                for i, frame in enumerate(allFrames):
                    self._exportAnimationFrame(engine, frame)
                    self.exporterCtx.fullExport = False
                    engine.update_progress((i + 1) / len(allFrames))

        self._reportInfo(engine, "Animation exported.")


    def _finalizeRenderJob(self, engine: bpy.types.RenderEngine, success: bool):
        """ Free the resources associated with a render job """

        debug.printInfo("Finalize render job.")

        if self.aborted:
            self._reportInfo(engine, "Render job aborted. Check console log for details.")

        # Let Blender know that we are finished using the renderResult object
        with VRayRendererProd._instanceLock:
            self._renderEnd(engine, success)

        # Return to the fake view layer use keyframe to ensure that the rendering procedure for the next view layer will start.
        if fakeVLayerKf := __class__.fakeViewLayerKeyframe:
            setFloatFrame(engine, fakeVLayerKf)

    def _exportSceneAdjustments(self, exporterCtx: ExporterContext):
        # No adjustments to export
        pass


    def _clearFrameData(self, upToTime: float):
        """ Clear the data for the previous animation frame which is not needed to render the next frame.

        Args:
            upToTime (float): the start frame of the current range
        """
        commonSettings = self.exporterCtx.commonSettings
        clearUpTo = upToTime

        if commonSettings.exportMotionData:
            # Depending on the center of the interval, motion blur time ranges may overlap between consecutive
            # frames. To make sure no necessary data is deleted, leave one frame worth of data when calculating
            # the range to clear.
            epsilon = 1.0 / commonSettings.animation.fps
            clearUpTo -= epsilon

        vray.clearFrameData(self.renderer, upToTime=clearUpTo)

    @staticmethod
    def _shouldRenderJob():
        if not bpy.app.background:
            return True

        # In headless mode, Blender will create a new renderer for each animation frame. We are rendering
        # the whole animation using just 1 renderer, so skip all render requests after the first one
        scene = bpy.context.scene
        if scene.vray.Exporter.animation_mode == 'FRAME' or (scene.frame_current == scene.frame_start):
            return True

        # Also accept the frame this renderer itself moved the scene to. startProdRenderSync()
        # jumps to a fake 'view_layer.use' keyframe one frame before the first real one, so that a
        # view layer disabled on frame_start still starts rendering. That frame is not frame_start,
        # so the check above would refuse every request of a headless multi-view-layer animation
        # render and the job would produce no image and no .vrscene at all.
        if (fakeVLayerKf := __class__.fakeViewLayerKeyframe) is not None:
            import math
            return scene.frame_current == math.floor(fakeVLayerKf)

        return False


    @staticmethod
    def _exportOnly():
        return __class__.renderMode in (ProdRenderMode.EXPORT_VRSCENE, ProdRenderMode.CLOUD_SUBMIT, ProdRenderMode.EXPORT_PROXY)
