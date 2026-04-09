# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import threading
import time
import bpy

from vray_blender.engine.renderer_prod_base import VRayRendererProdBase
from vray_blender.exporting.update_tracker import UpdateTracker

from vray_blender import debug
from vray_blender.lib.blender_utils import TestBreak, setFloatFrame
from vray_blender.lib.path_utils import getV4BTempDir
from vray_blender.lib.common_settings import CommonSettings, collectExportSceneSettings
from vray_blender.lib.defs import ExporterContext, ExporterType, ProdRenderMode
from vray_blender.lib.lib_utils import parseFramesToSequences, filterSequencesByViewLayerUse
from vray_blender.lib.names import syncUniqueNames

from vray_blender.bin import VRayBlenderLib as vray

#############################
## VRayRendererProd
#############################

FRAME_EXPORT_SLEEP_TIME = 0.1

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

    # This field will be set to the value of vray.render operator's 'animation' property.
    forceAnimation = False

    # Fake view layer use keyframe that will be set before the rendering starts.    
    fakeViewLayerKeyframe: float | None  = None
    

    def __init__(self):
        super().__init__(isPreview=False)
        VRayRendererProd._instance = self

        self.exporterCtx: ExporterContext = None
        self.cbRenderStopped = None
        self.aborted = False


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
        """ Abort the rendering job if it is running. This method can be called from
            any context.
        """
        with VRayRendererProd._instanceLock:
            if self.renderer:
                # Abort the job in vray. The cleanup will be performed when the job
                # has finished.
                self.aborted = True
                vray.abortRender(self.renderer)


    def render(self, engine: bpy.types.RenderEngine, depsgraph: bpy.types.Depsgraph):
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
                    self._writeVrscene(scene, engine)
                case ProdRenderMode.RENDER:
                    errMsg = self._render(scene, engine)
                    success = not bool(errMsg)
                case _:
                    assert False, "Invalid render mode in PROD renderer: {__class__.renderMode}"

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

            # Save the current render frame since VRayRendererProd._exportAnimationFrame() modifies it.
            currentFrame = bpy.context.scene.frame_current

            # Determine if the rendering process has started.
            # Note: Don't replace it with "frame == self.exporterCtx.commonSettings.animation.startFrame" if-statement,
            # because in 'Single Frame' render mode with motion blur enabled, this statement won't be valid.
            renderingStarted = False
            for frame in self._getFrameRange(scene):

                self._exportAnimationFrame(engine, frame)
                self.exporterCtx.fullExport = False

                if self.exporterCtx.commonSettings.useMotionBlur:
                    # During motion blur animation export rendering is started only when
                    # the entire interval of required frames is exported.
                    if self.exporterCtx.motionBlurBuilder.isLastFrame():
                        # The frames before the maximum motion blur duration are no longer needed.
                        frameForClearing = frame - self.exporterCtx.commonSettings.maxMBlurDuration
                        self._clearFrameData(upToTime=frameForClearing)

                        # Setting the frame to be the original one from which the interval is calculated.
                        frame = self.exporterCtx.motionBlurBuilder.getFrameReadyForRender()
                        vray.setRenderFrame(self.renderer, frame)
                    else:
                        continue
                else:
                    self._clearFrameData(upToTime=frame)

                if not renderingStarted:
                    self._startRenderSequence(engine)
                    renderingStarted = True
                else:
                    # Proceed to the next frame
                    vray.continueRenderSequence(self.renderer)

                self._waitFrameRenderEnd(engine, frame)
                self._persistState(self.exporterCtx)

            if renderingStarted:
                self._reportInfo(engine, "Animation exported.")
            else:
                return "No frames selected for rendering"

            engine.frame_set(currentFrame, 0) # Resetting to the render frame, selected by the user

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

    def _initRenderJob(self, engine: bpy.types.RenderEngine, depsgraph: bpy.types.Depsgraph):
        
        scene = bpy.context.scene

        commonSettings = CommonSettings(scene, engine,
                                        isInteractive = False,
                                        viewLayerName = depsgraph.view_layer_eval.name,
                                        exportOnly = __class__._exportOnly(),
                                        forceAnimation = __class__.forceAnimation)

        commonSettings.updateFromScene()

        self.exporterCtx = self._getExporterContext(engine, depsgraph, commonSettings)
        # Value was overridden in the vray.render operator
        self.exporterCtx.forceAnimation = __class__.forceAnimation

        if self.exporterCtx.isAnimation and (len(commonSettings.animation.frames) == 0):
            self._reportInfo(engine, f"View layer '{depsgraph.view_layer_eval.name}' is not enabled for the selected frames")
            return False

        with VRayRendererProd._instanceLock:
            if not self.renderer:

                exporterType =  ExporterType.ANIMATION if self.exporterCtx.isAnimation else ExporterType.PROD
                self.renderer = self._createRenderer(exporterType)

                def onStopped():
                    self.aborted = True

                self.cbRenderStopped = lambda isAborted: onStopped()
                vray.setRenderStoppedCallback(self.renderer, self.cbRenderStopped)

        self.exporterCtx.renderer = self.renderer

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

        debug.printDebug("End single-frame render.")


    def _submitToCloud(self, engine):
        from vray_blender.exporting.cloud_job import VCloudJob
        import os, tempfile

        # Create temporary vrscene file used only for cloud submissions
        tempDir = tempfile.mkdtemp(dir=getV4BTempDir())
        baseScenePath = os.path.join(tempDir, "cloud_export.vrscene").replace("\\", "/")
        scenePath = self._writeVrscene(bpy.context.scene, engine, baseScenePath, isCloudExport=True)
        if not scenePath:
            self._reportError(engine, "Failed to export .vrscene for cloud submission")
            return

        job = VCloudJob(bpy.context.scene, scenePath)
        job.submitToCloud() # This function waits for the submission to finish



    def _startRenderSequence(self, engine: bpy.types.RenderEngine):
        assert self.exporterCtx.isAnimation
        
        __class__.testBreak(engine)

        debug.printDebug("Start animation render.")

        # Obtain a rendering target from Blender and set it to the C++ renderer
        self._renderStart(self.exporterCtx)

        scene = self.exporterCtx.dg.scene
        vrayExporter = scene.vray.Exporter

        if vrayExporter.use_frame_range:
            sequences = [[scene.frame_start, scene.frame_end, scene.frame_step]]
        else:
            sequences = parseFramesToSequences(vrayExporter.frames_list)

        sequences = filterSequencesByViewLayerUse(self.exporterCtx.dg.view_layer_eval.name, sequences)
 
        flatSequenceList =  [item for sublist in sequences for item in sublist]
        assert flatSequenceList and (len(flatSequenceList) % 3 == 0)
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

            if nextFrame is not None and nextFrame == vray.getLastRenderedFrame(self.renderer):
                totalFrames = len(frames)
                if totalFrames > 1:
                    engine.update_progress(currentIdx / (totalFrames - 1))
                return

            time.sleep(FRAME_EXPORT_SLEEP_TIME)


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

        # Clear per-frame caches.
        self.exporterCtx.exportedMtls.clear()


    def _getFrameRange(self, scene):
        if self.exporterCtx.commonSettings.useMotionBlur:
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
            for frame in self._getFrameRange(scene):
                self.exporterCtx.exportProgress.setTotalObjectsAndFrames(self.exporterCtx)
                self._exportAnimationFrame(engine, frame)
                self.exporterCtx.fullExport = False

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

        if commonSettings.useMotionBlur:
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
        return scene.vray.Exporter.animation_mode == 'FRAME' or (scene.frame_current == scene.frame_start)


    @staticmethod
    def _exportOnly():
        return __class__.renderMode in (ProdRenderMode.EXPORT_VRSCENE, ProdRenderMode.CLOUD_SUBMIT)
