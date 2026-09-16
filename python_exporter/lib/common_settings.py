# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
import sys
import math
from pathlib import Path
from typing import Optional

from vray_blender.lib import blender_utils
from vray_blender.lib import settings_defs as defs
from vray_blender.lib.defs import ProdRenderMode
from vray_blender.lib.lib_utils import parseFramesToFlatList

from vray_blender import debug
from vray_blender.bin import VRayBlenderLib as vray

from vray_blender.lib import camera_utils


class AnimationSettings:
    def __init__(self):
        self.use          = False
        self.frameCurrent = 0
        self.frames       = []

class FileOutputSettings:
    def __init__(self):
        self.useSeparate    = False
        self.folderType     = ""
        self.outputDir      = ""
        self.outputUnique   = False
        self.projectPath    = ""


def collectExportSceneSettings(scene: bpy.types.Scene, scenePath="", viewLayerName=""):
    """ Collect settings related to .vrscene export.

    Args:
        scene (bpy.types.Scene): the current scene

    Returns:
        vray.ExportSceneSettings: the collected data.
        string: error message, if any. If both return values are None, file should not be exported.
    """
    from vray_blender.version import getHostAppVersionString

    def fixPath(filePath):
        """"
            Returns:
                filePath, errorMessage: if errorMessage is None, filePath is valid and vice-versa
        """
        if not filePath:
            return None, "The path to the .vrscene is empty."

        if filePath.startswith("//"):
            # This is a path relative to the Blender scene, convert to regular path
            if bpy.context.blend_data.filepath:
                filePath = bpy.path.abspath(filePath)
            else:
                return None, "Cannot use scene-relative paths before the scene is saved."

        if not Path(filePath).is_absolute():
            return None, "Path to the .vrscene file should be absolute."

        # When exporting multiple view layers, add the view layer name to the scene file path
        if len(scene.view_layers) > 1 and viewLayerName:
            filePath = f"{Path(filePath).with_suffix('')!s}_{viewLayerName}.vrscene"

        if not Path(filePath).suffix:
            filePath = f"{filePath}.vrscene"

        return filePath, None


    preferences = blender_utils.getVRayPreferences()

    if (not preferences.export_scene_file_path) and (not scenePath):
        return None, None

    settings = vray.ExportSceneSettings()

    settings.compressed    = preferences.export_scene_compressed
    settings.hexArrays     = preferences.export_scene_hex_meshes
    settings.hexTransforms = preferences.export_scene_hex_transforms
    settings.separateFiles = preferences.export_scene_separate_files
    settings.pluginTypes   = preferences.export_scene_plugin_types
    settings.hostAppString = getHostAppVersionString()

    filePath, errMsg = fixPath(scenePath if scenePath else preferences.export_scene_file_path)

    if errMsg:
        return None, errMsg

    settings.filePath = filePath
    return settings, None


def _frameRange(start: int, end: int, step: int) -> list[int]:
    """ An inclusive frame range. Blender's RNA hard min for frame_step is 0 (only the UI
        soft min is 1), so a step of 0 can reach us from a .blend and range() would reject it.
    """
    return list(range(start, end + 1, max(step, 1)))


def getAnimationFrames(scene: bpy.types.Scene, viewLayerName: str = "") -> (list[int] | None, str | None):
    """ Returns (frames, errMsg) - the frames to render for a normal (non-vrscene-export)
        animation render job. Uses use_frame_range to choose between the scene frame range and
        a custom frame list expression. Filters by the view layer's enabled state when
        viewLayerName is provided.

        frames is None, with a message in errMsg, if the frame expression is empty or invalid.
        An empty list means that the expression was valid but no frame passed the view layer
        filter - the renderer reports that case separately.

        Note: frame_step is applied only for the scene range; custom frame list expressions
        specify frames explicitly so frame_step is not relevant there.
    """
    vrayExporter = scene.vray.Exporter
    if vrayExporter.use_frame_range:
        frames = _frameRange(scene.frame_start, scene.frame_end, scene.frame_step)
    else:
        frames, err = parseFramesToFlatList(vrayExporter.frames_list)
        if frames is None:
            return None, err
        if not frames:
            # An expression that parses but selects nothing, e.g. "" or " , , "
            return None, "Empty frames list"

    if viewLayerName:
        if vlFCurve := blender_utils.getViewLayerUseFCurve(viewLayerName):
            frames = [f for f in frames if vlFCurve.evaluate(f)]

    return frames, None


class CommonSettings:
    """ This class is used to gather, once per update cycle, the UI settings that are
        used by different exporters but not necessarily exported by them.
    """
    def __init__(self, scene: bpy.types.Scene,
                    isInteractive: bool, isPreview: bool = False,
                    viewLayerName: str = "", exportOnly: bool = False,
                    forceAnimationMode: str = 'AUTO'):

        self.isPreview = isPreview

        # Viewport and Interactive renders are both "interactive",
        # so isInteractive will be true on both cases
        self._interactive = isInteractive
        self.scene = scene
        self.vrayScene = self.scene.vray
        self.vrayExporter = self.vrayScene.Exporter
        self.viewLayerName = viewLayerName

        self.exportOnly   = exportOnly
        self.animation   = AnimationSettings()
        self.files       = FileOutputSettings()
        self.forceAnimationMode = forceAnimationMode


    def updateFromScene(self):
        vrayExporter = self.vrayExporter

        self.backgroundScene              = self.scene
        self.calculateInstancerVelocity   = vrayExporter.calculate_instancer_velocity
        self.useSubsurfToOsd              = vrayExporter.subsurf_to_osd
        self.autoSaveRender               = vrayExporter.auto_save_render
        self.exportMeshes                 = True
        self.selectedObjects              = set()

        self.exportFileFormat  = vrayExporter.data_format
        if self.isPreview:
            # force zip for preview so it can be faster if we are writing to file
            self.exportFileFormat = defs.ExportFormat.ZIP

        self._updateFileOutput()
        self._updateRenderMode()
        self._updateAnimation()
        self._updateViewport()
        self._updateLogLevels()

        if self.useStereoCamera:
            self._updateStereoCameraObjectsList(self.leftStereoCamName, self.rightStereoCamName)


    def _updateRenderMode(self):
        self.renderMode = self._getRenderMode()
        self.isGpu = self.renderMode not in (defs.RenderMode.Production, defs.RenderMode.RtCpu)

        self.vfbFlags = defs.VfbFlags.NoFlags
        self.displayVfbOnTop = self.vrayExporter.display_vfb_on_top

        if self.displayVfbOnTop:
            self.vfbFlags |= defs.VfbFlags.AlwaysOnTop


    def _updateFileOutput(self):
        self.useSeparate  = self.vrayExporter.useSeparateFiles
        self.projectPath  = bpy.data.filepath


    def _updateViewport(self):
        self.viewportResolution     = 1.0
        self.viewportImageQuality   = 93
        self.viewportImageType      = defs.ImageType.RgbaReal


    def isCloudSubmit(self):
        from vray_blender.engine.render_engine import VRayRenderEngine
        prodRenderer = VRayRenderEngine.prodRenderer
        return prodRenderer and prodRenderer.renderMode == ProdRenderMode.CLOUD_SUBMIT

    def _updateAnimation(self):
        isProductionRendering=not (self.isPreview or self._interactive or self.vrayExporter.isBakeMode)

        self.animation.frameCurrent = self.scene.frame_current

        animationMode = 'FRAME'
        if isProductionRendering:
            animSettings = blender_utils.getVRayPreferences().animationSettingsVrsceneExport
            vrsceneExport = self.exportOnly and not self.isCloudSubmit()

            if self.forceAnimationMode != 'AUTO':
                animationMode = self.forceAnimationMode
            elif vrsceneExport:
                if animSettings.exportAnimation:
                    animationMode = 'ANIMATION'
            else:
                animationMode = self.vrayExporter.animation_mode

            if animationMode == 'ANIMATION':
                assert self.viewLayerName, "Rendering animation without view layer information"

                if vrsceneExport:
                    match animSettings.frameRangeMode:
                        case "CUSTOM_RANGE":
                            frames = _frameRange(animSettings.customFrameStart, animSettings.customFrameEnd, animSettings.customFrameStep)
                        case "CUSTOM_FRAMES":
                            frames, err = parseFramesToFlatList(animSettings.customFramesList)
                            if frames is None:
                                raise Exception(err)
                            if not frames:
                                raise Exception("Empty frames list")
                        case "SCENE_RANGE":
                            frames = _frameRange(self.scene.frame_start, self.scene.frame_end, self.scene.frame_step)
                        case _:
                            assert False, "Invalid frame range mode"
                    if vlFCurve := blender_utils.getViewLayerUseFCurve(self.viewLayerName):
                        frames = [f for f in frames if vlFCurve.evaluate(f)]
                    self.animation.frames = frames
                else:
                    frames, err = getAnimationFrames(self.scene, self.viewLayerName)
                    if frames is None:
                        raise Exception(err)
                    
                    # An empty list is not an error here - the view layer may be disabled for
                    # all the selected frames. The renderer reports that case separately.
                    self.animation.frames = frames

 
        self.animation.use = (animationMode != 'FRAME')
        
        # scene.render.fps is of type int. In order to have non-int fps, Blender uses 
        # the float divisor fps_base. E.g. FPS 20.5 may be represented as
        # fps = 41, fps_base = 2.0
        self.animation.fps = self.scene.render.fps / self.scene.render.fps_base

        self.useStereoCamera    = False

        mbSettings = self.scene.vray.SettingsMotionBlur
        self.mbSamples = mbSettings.geom_samples

        if self.exportOnly:
            allCameras = [obj for obj in self.scene.objects if obj.type == 'CAMERA']
        else:
            markerCameras = [marker.camera for marker in self.scene.timeline_markers if marker.camera]
            allCameras = markerCameras if markerCameras else [self.scene.camera]

        # Motion blur that will be visible in the render result. Kept separate from
        # exportMotionData so that the SettingsMotionBlur export can tell it apart from the
        # velocity-only case. Bake renders and previews never render motion blur.
        self.hasCameraMotionBlur = any(camera_utils.camObjUsesMotionBlur(camObj, mbSettings) for camObj in allCameras) \
                                    and not self.vrayExporter.isBakeMode and not self.isPreview

        # A Velocity render element is computed from object motion, which V-Ray only has if we
        # export subframes for it. Other V-Ray integrations do the same, so an enabled Velocity
        # element is its own reason to export motion data, regardless of motion blur.
        from vray_blender.engine.render_elements import isVelocityWired
        self.velocityMotionData = isVelocityWired(self.scene.world) \
                                    and not self.vrayExporter.isBakeMode and not self.isPreview \
                                    and not self._interactive \
                                    and not self.isGpu

        # Subframe motion data has to be exported. NOT the same as rendering motion blur.
        self.exportMotionData = self.hasCameraMotionBlur or self.velocityMotionData

        # If we are rendering single frame but we have motion blur - override to animation rendering
        # and set the start/end frame to the current. That way we'll export frames for motion blur
        # override only for production rendering. IPR does not export animation frames yet so
        # it won't handle animation being turned on
        if isProductionRendering and not self.animation.use and self.exportMotionData:
            self.animation.use = True
            self.animation.frames = [self.scene.frame_current]


    def _updateStereoCameraObjectsList(self, leftCamName, rightCamName):
        self.selectedObjects = set()
        self.cameraStereoLeft = None
        self.cameraStereoRight = None

        for ob in self.scene.objects:
            if self.useStereoCamera:
                if ob.name == leftCamName:
                    self.cameraStereoLeft = ob
                elif ob.name == rightCamName:
                    self.cameraStereoRight = ob

            activeLayer = ob.select_get()
            if activeLayer:
                self.selectedObjects.add(ob)

        if self.useStereoCamera:
            if not self.cameraStereoLeft or not self.cameraStereoRight:
                self.useStereoCamera = False
                debug.printError("Failed to find cameras for stereo camera!")


    def _updateLogLevels(self):
        self.verbosityLevel = blender_utils.getVRayPreferences().verbose_level


    def _getRenderMode(self):

        deviceType = self.vrayExporter.device_type
        renderMode = defs.RenderMode.Production

        match deviceType:
            case 'CPU':
                renderMode = defs.RenderMode.RtCpu if self._interactive else defs.RenderMode.Production
            case 'GPU':
                if sys.platform == "darwin":
                    return defs.RenderMode.RtGpuMetal if self._interactive else defs.RenderMode.ProductionGpuMetal
                else:
                    match self.vrayExporter.gpu_device_type:
                        case 'RTX':
                            renderMode = defs.RenderMode.RtGpuOptiX if self._interactive else defs.RenderMode.ProductionGpuOptiX
                        case 'HIP' if sys.platform == 'win32':
                            renderMode = defs.RenderMode.RtGpuHIP if self._interactive else defs.RenderMode.ProductionGpuHIP
                        case _:
                            # Includes 'CUDA' and 'HIP' on non-Windows hosts (HIP is only
                            # valid on Windows; the value is preserved on other platforms
                            # for round-trip safety but rendered using CUDA).
                            renderMode = defs.RenderMode.RtGpuCUDA if self._interactive else defs.RenderMode.ProductionGpuCUDA

        return renderMode
