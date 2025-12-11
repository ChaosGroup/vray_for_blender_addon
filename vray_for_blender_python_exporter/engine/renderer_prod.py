import threading
import time
import bpy
from math import modf

from vray_blender.engine.renderer_prod_base import VRayRendererProdBase
from vray_blender.exporting.update_tracker import UpdateTracker

from vray_blender import debug
from vray_blender.lib.common_settings import CommonSettings, collectExportSceneSettings
from vray_blender.lib.defs import ExporterContext, ExporterType, ProdRenderMode
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

    renderMode = ProdRenderMode.RENDER # Type of job ("Scene export", "Render" or "Cloud submit")

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


    def abort(self):
        """ Abort the rendering job if it is running. This method can be called from
            any context.
        """
        with VRayRendererProd._instanceLock:
            if self.renderer:
                # Abort the job in vray. The cleanup will be performed when the job
                # has finished.
                self.aborted = True
                vray.renderEnd(self.renderer)



    def render(self, engine: bpy.types.RenderEngine, depsgraph: bpy.types.Depsgraph):
        if not __class__._shouldRenderJob():
            return

        scene = bpy.context.scene
        self._initRenderJob(engine, depsgraph)

        success  = True

        try:
            match __class__.renderMode:
                case ProdRenderMode.CLOUD_SUBMIT:
                    self._submitToCloud(engine)
                case ProdRenderMode.EXPORT_VRSCENE:
                    self._writeVrscene(scene, engine)
                case ProdRenderMode.RENDER:
                    self._render(scene, engine)
                case _:
                    assert False, "Invalid render mode in PROD renderer: {__class__.renderMode}"

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
            animationExported = True

            # Save the current render frame since VRayRendererProd._exportAnimationFrame() modifies it.
            currentFrame = bpy.context.scene.frame_current

            # Determine if the rendering process has started.
            # Note: Don't replace it with "frame == self.exporterCtx.commonSettings.animation.startFrame" if-statement,
            # because in 'Single Frame' render mode with motion blur enabled, this statement won't be valid.
            renderingStarted = False

            for frame in self._getFrameRange(scene):

                if not self._exportAnimationFrame(engine, frame):
                    animationExported = False
                    break
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
                    if not (renderingStarted := self._startRenderSequence(engine)):
                        # Rendering sequence couldn't be started
                        break
                else:
                    # Proceed to the next frame
                    vray.continueRenderSequence(self.renderer)

                if not self._waitFrameToBeRendered(engine, frame):
                    # Render job has been aborted
                    animationExported = False
                    break

                self._persistState(self.exporterCtx)

            if animationExported:
                self._reportInfo(engine, "Animation exported.")

            engine.frame_set(currentFrame, 0) # Resetting to the render frame, selected by the user

        else:
            self._renderSingleFrame(engine, scene.frame_current)


    def _writeVrscene(self, scene: bpy.types.Scene, engine: bpy.types.RenderEngine, scenePath="", isCloudExport=False):
        """ Export the full animation sequence to V-Ray and write a .vrscene file """
        self._exportFullScene(scene, engine)
        exportSettings, errMsg = collectExportSceneSettings(scene, scenePath)

        if exportSettings:
            exportSettings.cloudExport = isCloudExport

            if not vray.writeVrscene(self.renderer, exportSettings):
                self._reportError(engine, "Scene export failed")
                return

            # Writing the scene is an asynchronous task during which we need to keep the renderer alive.
            while vray.exportJobIsRunning(self.renderer):
                time.sleep(FRAME_EXPORT_SLEEP_TIME)

            self._reportInfo(engine, f"Exported scene: {exportSettings.filePath}")
        elif errMsg:
            self._reportError(engine, f"Export scene: {errMsg}")


    def _initRenderJob(self, engine: bpy.types.RenderEngine, depsgraph):

        scene = bpy.context.scene

        commonSettings = CommonSettings(scene, engine,
                                        isInteractive = False,
                                        exportOnly = __class__._exportOnly())
        
        commonSettings.updateFromScene()


        with VRayRendererProd._instanceLock:
            if not self.renderer:

                exporterType =  ExporterType.ANIMATION if commonSettings.animation.use else ExporterType.PROD
                self.renderer = self._createRenderer(exporterType)

                def onStopped():
                    self.aborted = True

                self.cbRenderStopped = lambda isAborted: onStopped()
                vray.setRenderStoppedCallback(self.renderer, self.cbRenderStopped)

        # In production mode we always perform a full export which only adds data to the scene. 
        # Clearing the scene will ensure that no remnants of a previous scene are left around.
        vray.clearScene(self.renderer)
        UpdateTracker.clear()

        self.exporterCtx = self._getExporterContext(self.renderer, depsgraph, commonSettings)
        syncUniqueNames()

        VRayRendererProdBase._syncView(self.exporterCtx)


    def _renderSingleFrame(self, engine: bpy.types.RenderEngine, frame):
        """ Export and render а single frame. """
        vray.setRenderFrame(self.renderer, frame)
        self._export(engine, self.exporterCtx)

        debug.printDebug("Start single-frame render.")

        self._renderStart(self.exporterCtx)
        vray.renderFrame(self.renderer)

        while vray.renderJobIsRunning(self.renderer):
            if self._checkRenderJobCancelled(engine):
                vray.abortRender(self.renderer)
                debug.printDebug("Render job cancelled.")
                return
            else:
                progress = vray.getRenderProgress(self.renderer)
                engine.update_progress(progress)

            time.sleep(FRAME_EXPORT_SLEEP_TIME)

        debug.printDebug("End single-frame render.")


    def _submitToCloud(self, engine):
        from vray_blender.lib.path_utils import getV4BTempDir
        from vray_blender.exporting.cloud_job import VCloudJob
        import os

        # Create temporary vrscene file used only for cloud submissions
        scenePath = os.path.join(getV4BTempDir(), f"cloud{os.getpid()}.vrscene").replace("\\", "/")
        self._writeVrscene(bpy.context.scene, engine, scenePath=scenePath, isCloudExport=True)

        job = VCloudJob(bpy.context.scene, scenePath)
        job.submitToCloud() # This function waits for the submission to finish


    def _startRenderSequence(self, engine: bpy.types.RenderEngine):
        if self._checkRenderJobCancelled(engine):
            debug.printDebug("Render job cancelled.")
            return False

        debug.printDebug("Start animation render.")

        # Obtain a rendering target from Blender and set it to the C++ renderer
        self._renderStart(self.exporterCtx)

        anim = self.exporterCtx.commonSettings.animation

        # No need for sequence rendering, when there is only a single frame for rendering
        if (bpy.context.scene.vray.Exporter.animation_mode == 'FRAME') \
                or (anim.frameStart == anim.frameEnd):

            vray.renderFrame(self.renderer) # Rendering only the current frame
        else:
            vray.renderSequenceStart(self.renderer, anim.frameStart, anim.frameEnd, anim.frameStep)

        return True


    def _waitFrameToBeRendered(self, engine: bpy.types.RenderEngine, currentFrame: float):
        """ Waits until rendering of the current frame is complete or the render job finishes """
        # The next frame in the sequence. When vray.getLastRenderedFrame() returns this value,
        # it indicates that rendering of "currentFrame" has completed.
        nextFrame = currentFrame + self.exporterCtx.commonSettings.animation.frameStep

        while vray.renderJobIsRunning(self.renderer):
            if self._checkRenderJobCancelled(engine):
                # The mechanism for checking for aborted jobs assumes that this could be done using a signal 
                # other than the stop from the VFB. This used to be the case when we ran the vray.render operator 
                # asynchronously - the job could be stopped from Blender. 
                vray.abortRender(self.renderer)
                debug.printDebug("Render job cancelled.")
                return False
            elif nextFrame == vray.getLastRenderedFrame(self.renderer):
                frameStart = self.exporterCtx.commonSettings.animation.frameStart
                frameEnd = self.exporterCtx.commonSettings.animation.frameEnd
                engine.update_progress(1.0 / (frameEnd - frameStart + 1) * (currentFrame - frameStart))
                return True

            time.sleep(FRAME_EXPORT_SLEEP_TIME)

        # Rendering finished
        return True


    def _exportAnimationFrame(self, engine: bpy.types.RenderEngine, frame: float):
        """ Export animation sequence """

        if self._checkRenderJobCancelled(engine):
            self._reportInfo(engine, "Render job cancelled.")
            return False

        self._reportInfo(engine, f"Export animation frame {frame}")

        frac, whole = modf(frame)
        engine.frame_set(int(whole), frac)

        vray.setRenderFrame(self.renderer, frame)

        # TODO: ideally this should go to _getExporterContext()
        self.exporterCtx.currentFrame    = frame
        self.exporterCtx.persistedState  = self.persistedState
        self.exporterCtx.objTrackers     = self.objTrackers

        self.exporterCtx.commonSettings.updateFromScene()

        self._export(engine, self.exporterCtx)

        # Clear per-frame caches.
        self.exporterCtx.exportedMtls.clear()

        return True


    def _getFrameRange(self, scene):
        if self.exporterCtx.commonSettings.useMotionBlur:
            self.exporterCtx.motionBlurBuilder.initialize(scene, self.exporterCtx.commonSettings, self.exporterCtx.exportOnly)
            return self.exporterCtx.motionBlurBuilder.getFrames()

        anim = self.exporterCtx.commonSettings.animation
        return range(anim.frameStart, anim.frameEnd + 1, anim.frameStep)


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
            frameStart = self.exporterCtx.commonSettings.animation.frameStart
            vray.setRenderFrame(self.renderer, frameStart)
            self.exporterCtx.currentFrame = frameStart

            self._export(engine, self.exporterCtx)
        else:
            for frame in self._getFrameRange(scene):
                if not self._exportAnimationFrame(engine, frame):
                    break
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


    def _checkRenderJobCancelled(self, engine: bpy.types.RenderEngine):
        if engine.test_break() or self.aborted:
            self._reportInfo(engine, "Render job cancelled.")
            return True
        return False


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

        
