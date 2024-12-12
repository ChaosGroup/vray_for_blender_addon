import bpy
from pathlib import Path

from vray_blender.lib import blender_utils
from vray_blender.lib import settings_defs as defs

from vray_blender import debug
from vray_blender.bin import VRayBlenderLib as vray


class AnimationSettings:
    def __init__(self):
        self.mode         = defs.AnimationMode.SingleFrame
        self.use          = False
        self.frameStart   = 0
        self.frameEnd     = 0
        self.frameCurrent = 0
        self.frameStep    = 0
    
class FileOutputSettings:
    def __init__(self):
        self.useSeparate    = False
        self.folderType     = ""
        self.outputDir      = ""
        self.outputUnique   = False
        self.projectPath    = ""


def collectExportSceneSettings(scene: bpy.types.Scene, scenePath=""):
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
            
        if not Path(filePath).suffix:
            filePath = f"{filePath}.vrscene"
        
        return filePath, None


    exporter = scene.vray.Exporter
    
    if (not exporter.export_scene_file_path) and (not scenePath):
        return None, None
    
    settings = vray.ExportSceneSettings()
    
    settings.compressed    = exporter.export_scene_compressed
    settings.hexArrays     = exporter.export_scene_hex_meshes
    settings.hexTransforms = exporter.export_scene_hex_transforms
    settings.separateFiles = exporter.export_scene_separate_files
    settings.pluginTypes   = exporter.export_scene_plugin_types
    settings.hostAppString = getHostAppVersionString()
    
    filePath, errMsg = fixPath(scenePath if scenePath else exporter.export_scene_file_path)

    if errMsg:
        return None, errMsg
    
    settings.filePath = filePath
    return settings, None


class CommonSettings:
    """ This class is used to gather, once per update cycle, the UI settings that are  
        used by different exporters but not necessarily exported by them. 
    """
    def __init__(self, scene: bpy.types.Scene, renderEngine: bpy.types.RenderEngine, isInteractive):

        # renderEngine will be None in case of interactive rendering.
        self.renderEngine = renderEngine
        self.isPreview = renderEngine and renderEngine.is_preview
        
        # Viewport and Interactive renders are both "interactive",
        # so isInteractive will be true on both cases
        self._interactive = isInteractive
        self.scene = scene
        self.vrayScene = self.scene.vray
        self.vrayExporter = self.vrayScene.Exporter
        
        self.animation   = AnimationSettings()
        self.files       = FileOutputSettings()
        
        # settingsDR = []
        
    def updateFromScene(self):
        vrayExporter = self.vrayExporter

        self.backgroundScene              = self.scene
        self.calculateInstancerVelocity   = vrayExporter.calculate_instancer_velocity
        self.useSubsurfToOsd              = vrayExporter.subsurf_to_osd
        self.autoSaveRender               = vrayExporter.auto_save_render
        self.exportMeshes                 = True
        self.selectedObjects              = set()
        self.loopCameras                  = []

        self.exportFileFormat  = vrayExporter.data_format
        if self.isPreview:
            # force zip for preview so it can be faster if we are writing to file
            self.exportFileFormat = defs.ExportFormat.ZIP
                            
        self._updateFileOutput()
        self._updateAnimation()
        self._updateRenderMode()
        self._updateViewport()
        self._updateLogLevels()

        if self.useStereoCamera:
            self._updateStereoCameraObjectsList(self.leftStereoCamName, self.rightStereoCamName)

        # TODO: export material overrides
        # overrideName = ""
        # settingsOptions = vrayScene.SettingsOptions
        # if settingsOptions.mtl_override_on:
        #     overrideName = settingsOptions.mtl_override

        
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

    def _updateAnimation(self):

        if self.isPreview or self._interactive or self.vrayExporter.isBakeMode:
            self.animation.mode = defs.AnimationMode.SingleFrame
        else:
            self.animation.mode = self.vrayExporter.animation_mode
        
        self.animation.use = (self.animation.mode != defs.AnimationMode.SingleFrame)
        self.animation.frameStart   = self.scene.frame_start
        self.animation.frameEnd     = self.scene.frame_end
        self.animation.frameCurrent = self.scene.frame_current
        self.animation.frameStep    = self.scene.frame_step
        
        # scene.render.fps is of type int. In order to have non-int fps, Blender uses 
        # the float divisor fps_base. E.g. FPS 20.5 may be represented as
        # fps = 41, fps_base = 2.0
        self.animation.fps = self.scene.render.fps / self.scene.render.fps_base

        self.useStereoCamera    = False
        self.useMotionBlur      = False
        self.usePhysicalCamera  = False

        cameraObject = self.scene.camera
        if blender_utils.isCamera(cameraObject):
            camera: bpy.types.Camera = cameraObject.data

            vrayCamera = camera.vray
            physCamera = vrayCamera.CameraPhysical

            self.useHideFromView = vrayCamera.hide_from_view
            mbSettings = vrayCamera.SettingsMotionBlur
            self.mbSamples = mbSettings.geom_samples

            self.usePhysicalCamera = physCamera.use
            if self.usePhysicalCamera:
                self.useMotionBlur = physCamera.use_moblur

                cameraType = int(physCamera.type)
                
                frameDuration = 1.0 / self.animation.fps

                match cameraType:
                    case defs.PhysicalCameraType.Still:
                        self.mbDuration = 1.0 / (physCamera.shutter_speed * frameDuration)
                        self.mbOffset = self.mbDuration * 0.5
                    case defs.PhysicalCameraType.Cinematic:
                        self.mbDuration = physCamera.shutter_angle / 360.0
                        self.mbOffset = physCamera.shutter_offset / 360.0 + self.mbDuration * 0.5
                    case defs.PhysicalCameraType.Video:
                        self.mbDuration = 1.0 + physCamera.latency / frameDuration
                        self.mbOffset = -self.mbDuration * 0.5
                    case _:
                        self.useMotionBlur = False
            else:
                self.useMotionBlur = mbSettings.on
                self.mbDuration = mbSettings.duration

        # Disable motion blur for bake render
        self.useMotionBlur = self.useMotionBlur and not self.vrayExporter.isBakeMode and not self.isPreview


    def _updateStereoCameraObjectsList(self, leftCamName, rightCamName):
        self.useHideFromView = False
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
        self.verbosityLevel = self.vrayExporter.verbose_level
        
   
    def _getRenderMode(self):

        deviceType = self.vrayExporter.device_type
        renderMode = defs.RenderMode.Production

        match deviceType:
            case 'CPU':
                renderMode = defs.RenderMode.RtCpu if self._interactive else defs.RenderMode.Production
            case 'GPU':
                deviceGpuType = self.vrayExporter.device_gpu_type

                match deviceGpuType:
                    case 'CUDA':
                        renderMode = defs.RenderMode.RtGpuCUDA if self._interactive else defs.RenderMode.ProductionGpuCUDA
                    case 'OPTIX':
                        renderMode = defs.RenderMode.RtGpuOptiX if self._interactive else defs.RenderMode.ProductionGpuOptiX

        return renderMode
