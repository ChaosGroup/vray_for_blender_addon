# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import threading
import bpy
import mathutils
import numpy
import time

from vray_blender.engine.renderer_prod_base import VRayRendererProdBase

from vray_blender import debug
from vray_blender.lib.blender_utils import getVRayPreferences
from vray_blender.lib.common_settings import CommonSettings, collectExportSceneSettings
from vray_blender.lib.defs import ExporterContext, NodeContext, PluginDesc, ExporterType, ProdRenderMode
from vray_blender.lib.names import Names, syncUniqueNamesForPreview
from vray_blender.lib.plugin_utils import updateValue
from vray_blender.lib.export_utils import exportPlugin
from vray_blender.exporting import world_export

from vray_blender.bin import VRayBlenderLib as vray


def _retagStuckMaterialPreviews():
    """ Re-kick material previews whose icon render job was killed mid-queue.

        Blender kills all pending preview jobs when a production render starts or a RENDERED
        viewport session ends, and cancelled previews keep their 'rendering' flag forever -
        the UI shows a spinner and never reschedules them. Such previews are detectable by
        their allocated but never written pixel buffers. Tag them so they re-render on the
        next draw.
    """
    if bpy.app.is_job_running('RENDER') or bpy.app.is_job_running('RENDER_PREVIEW'):
        # A live job may legitimately not have written its pixels yet.
        return

    for mtl in bpy.data.materials:
        if ((preview := mtl.preview) is None) or preview.is_image_custom:
            continue
        for size, pixels in ((preview.image_size, preview.image_pixels), (preview.icon_size, preview.icon_pixels)):
            if pixelCount := size[0] * size[1]:
                buf = numpy.empty(pixelCount, dtype=numpy.int32)
                pixels.foreach_get(buf)
                if not buf.any():
                    # reload() clears the preview (BKE_previewimg_clear), which resets the stale
                    # 'rendering' flag and makes the missing buffer reschedule a render on the
                    # next draw. Works for materials outside the depsgraph too, unlike update_tag().
                    preview.reload()
                    break


def scheduleStuckPreviewCheck():
    """ Run the stuck-preview check once, after the transition that may have killed the
        preview jobs has settled.
    """
    if not bpy.app.timers.is_registered(_retagStuckMaterialPreviews):
        bpy.app.timers.register(_retagStuckMaterialPreviews, first_interval=1.0)


class VRayRendererPreview(VRayRendererProdBase):
    """ Material preview renderer implementation. """

    # Upper bound for the wait in abort(). A preview render takes ~150ms, so it is only ever
    # reached if the job thread is stuck - and giving up on it is safe, as the native side never
    # destroys a preview exporter a job thread may still be using.
    ABORT_TIMEOUT = 5.0

    def __init__(self):
        super().__init__(isPreview=True)
        self.lock = threading.Lock()

        # Cleared while render() is in flight, set once it has released the native renderer.
        self._renderFinished = threading.Event()
        self._renderFinished.set()

        # Set by abort(). Polled by the render loop so an abort is honoured even when it arrives
        # before the frame has been started, which vray.renderEnd() alone would not catch.
        self._aborted = False


    def abort(self):
        """ Abort the rendering job, if it is running, and wait for the preview job thread to
            release the renderer. Can be called from any context except that thread itself.
        """
        with self.lock:
            self._aborted = True

            if self.renderer:
                # Abort the job in vray. The cleanup will be performed when the job
                # has finished.
                vray.renderEnd(self.renderer)

        # Deliberately outside the lock - render() holds it while running _renderEnd().
        if not self._renderFinished.wait(VRayRendererPreview.ABORT_TIMEOUT):
            debug.printError("Timed out waiting for the material preview render to abort")


    def render(self, engine: bpy.types.RenderEngine, dg: bpy.types.Depsgraph):
        self._renderFinished.clear()

        try:
            self._render(engine, dg)
        finally:
            # A throw before _render()'s own cleanup would leave the native renderer registered
            # with nothing left to release it. A no-op once it has been released.
            with self.lock:
                self._renderEnd(engine, success=False)

            # Must be last - abort() reads it as 'the native renderer is no longer in use'.
            self._renderFinished.set()


    def _render(self, engine: bpy.types.RenderEngine, dg: bpy.types.Depsgraph):
        with self.lock:
            self.renderer = self._createRenderer(ExporterType.PREVIEW)

        # In production mode we always perform a full export which only adds data to the scene.
        # Clearing the scene will ensure that no remnants of a previous scene are left around.
        vray.clearScene(self.renderer)

        # Common settings don't change during the animation, collect up front
        commonSettings = CommonSettings(dg.scene, isInteractive = False, isPreview = True)
        commonSettings.updateFromScene()

        exporterCtx = self._getExporterContext(engine, dg, commonSettings)
        exporterCtx.renderer = self.renderer
        syncUniqueNamesForPreview(exporterCtx.dg)
        exporterCtx.syncSceneState()
        exporterCtx.syncActiveInstancers()

        # Obtain a rendering target from Blender and set it to the C++ renderer
        self._renderStart(exporterCtx)
        success = False

        try:
            VRayRendererPreview._syncView(exporterCtx)

            success = self._renderScene(engine, exporterCtx)
        except Exception as ex:
            self._reportError(engine, f"{str(ex)} See log for details")
            debug.printExceptionInfo(ex, "VRayRendererPreview::render()")

        with self.lock:
            self._renderEnd(engine, success)


    def _renderScene(self, engine: bpy.types.RenderEngine, exporterCtx: ExporterContext):
        """ Export and render the current frame."""
        scene = exporterCtx.dg.scene

        if self._aborted:
            return False

        vray.setRenderFrame(self.renderer, scene.frame_current)
        self._export(engine, exporterCtx)

        # Look up whether we need to export a vrscene for the material preview
        # in the original scene. This property is not set in the preview scene.
        originalScene = bpy.context.scene
        if getVRayPreferences().export_material_preview_scene:
            self._writeVrscene(originalScene, engine)

        vray.renderFrame(self.renderer)

        while vray.renderJobIsRunning(self.renderer):
            if self._aborted or engine.test_break():
                vray.abortRender(self.renderer)
                return False

            # It is usual for previews to be aborted so keep the abort check mechanism
            # responsive by sleeping for just a short interval. A preview render takes
            # ~150ms, so a 30ms interval used to add up to 20% to it in wait alone.
            # Anything below 2ms only burns CPU without returning sooner.
            time.sleep(0.002)

        return True


    def _exportSceneAdjustments(self, exporterCtx: ExporterContext):
        """ An override to some of the plugins's attributes is needed when exporting preview scene. """

        # The 'Floor' object is only present in material preview scenes, not world/light previews.
        dg = exporterCtx.dg
        if dg.objects.get('Floor') is None:
            return

        # The 'Floor' object in the scene doesn't have a V-Ray material attached to it
        # For that BRDFVRayMtl with TexChecker is attached to the Floor object node
        floor = dg.objects['Floor']

        nodeCtx = NodeContext(exporterCtx)
        nodeCtx.rootObj = floor.active_material

        # Create TexChecker texture
        texCheckerPluginName = Names.nextVirtualNode(nodeCtx, "TexChecker")
        texCheckerFloor = PluginDesc(texCheckerPluginName, "TexChecker")
        texCheckerFloor.setAttribute("placement_type", "1")
        texCheckerFloor.setAttribute("w", 10)
        texCheckerFloor.setAttribute("h", 10)
        texCheckerFloor.setAttribute("white_color", mathutils.Color((0.6,0.6,0.6)))
        texCheckerFloor.setAttribute("black_color", mathutils.Color((0.2,0.2,0.2)))
        texCheckerPlugin = exportPlugin(exporterCtx, texCheckerFloor)

        brdfPluginName = Names.nextVirtualNode(nodeCtx, "BRDFVRayMtl")
        brdfFloor = PluginDesc(brdfPluginName, "BRDFVRayMtl")
        brdfFloor.setAttribute("diffuse", texCheckerPlugin)
        brdfPlugin = exportPlugin(exporterCtx, brdfFloor)

        floorMtlName = Names.object(floor.active_material)
        mtlSingleBrdfFloor = PluginDesc(floorMtlName, "MtlSingleBRDF")
        mtlSingleBrdfFloor.setAttribute("brdf", brdfPlugin)
        floorMtlPlugin = exportPlugin(exporterCtx, mtlSingleBrdfFloor)

        # Attaching the newly created material to the Floor Object
        # WARNING: In future blender releases the floor object name could get changed (and respectively its V-Ray node)
        floorNodeName = Names.vrayNode(Names.object(floor))
        updateValue(exporterCtx.renderer, floorNodeName, "material", floorMtlPlugin)

        # The default intensity of the lights in the scene is too small and for that, it is increased
        if squaredLight := dg.objects.get('SquaredLight'):
            updateValue(exporterCtx.renderer, Names.objectData(squaredLight), "intensity", 20)
        if circularLight := dg.objects.get('CircularLight'):
            updateValue(exporterCtx.renderer, Names.objectData(circularLight), "intensity", 100)


    def _exportWorld(self, exporterCtx: ExporterContext):
        dg = exporterCtx.dg
        if dg.scene.world is None:
            return

        # For material previews, respect the "Preview World" toggle (material.use_preview_world).
        # If the flag is off on all materials in the scene, skip world export.
        if dg.objects.get('Floor') is not None:
            if not any(mtl.use_preview_world for obj in dg.objects for slot in obj.material_slots if (mtl := slot.material) is not None):
                return

        world_export.WorldExporter(exporterCtx).export()


    def _writeVrscene(self, scene: bpy.types.Scene, engine: bpy.types.RenderEngine, scenePath="", isCloudExport=False):
        """ Export the preview scene to a .vrscene file """
        from pathlib import Path

        preferences = getVRayPreferences()
        assert preferences.export_material_preview_scene

        # Reuse the path set for the .vrscene for the interactive scene, but
        # add the '_preview' suffix. This is only available in in debug mode, so
        # no need for other user-controllable options here.
        exportPath = Path(preferences.export_scene_file_path)
        vrsceneFile = exportPath.parent / f"{exportPath.stem}_preview.vrscene"

        exportSettings, errMsg = collectExportSceneSettings(scene, str(vrsceneFile))

        if exportSettings:
            if not vray.writeVrscene(self.renderer, exportSettings):
                self._reportError(engine, "Preview scene export failed")
                return

            # Writing the scene is an asynchronous task during which we need to keep the renderer alive.
            while vray.exportJobIsRunning(self.renderer):
                time.sleep(0.5)

            self._reportInfo(engine, f"Exported preview scene: {exportSettings.filePath}")
        elif errMsg:
            self._reportError(engine, f"Export preview scene: {errMsg}")