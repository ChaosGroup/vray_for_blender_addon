# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

from vray_blender.lib.blender_utils import geometryObjectIt, isCamera
from vray_blender.lib import camera_utils
import bpy

UNDEFINED_FRAME = -1

class FrameType:
    # Frame for rendering, all of the objects in the scene should be exported.
    FOR_RENDERING = 0

    # Frame calculated from the number of samples overridden by object,
    # only those objects should be exported.
    FROM_OBJECT_SAMPLES = 1

    # Frame calculated from the global motion blur settings.
    # Export all objects without overridden samples, as well as those producing the same frame.
    FROM_GLOBAL_SAMPLES = 2

class MbFrameData:
    def __init__(self):
        self.frameType = FrameType.FROM_OBJECT_SAMPLES  # type of the frame
        self.objects = set() # Objects that have to be exported at that frame
        self.frameForRenderRef = UNDEFINED_FRAME # The frame to be rendered at the end of an motion blur interval


# Class that calculates which frames should be exported for motion blur and
# holds additional information for them.
class MotionBlurBuilder:

    def _getFrameData(self, frame)->MbFrameData:
        return self._frameData.setdefault(frame, MbFrameData())
    
    def __init__(self):
        self._frameData: dict[float, MbFrameData] = {} # Additional motion blur data for frames
        self._currentFrame = 0 # Current frame
        self._firstFrame = 0   # The first frame in the sequence
        self._objsWithOverriddenSamples = set() # Objects with overridden "nsamples"

    def _calculateMBInterval(self, cameraObj: bpy.types.Object, baseFrame: int, commonSettings, objectSamples: dict[int, str]):
        """
            Motion blur requires additional interval of frames to be exported for its calculations.
            The interval is defined by: duration, interval center, and number of samples.
            The formula for the start and end of the interval is:
            frameStart | frameEnd = renderFrame + interval_center -|+ (duration / 2)
        
            For example when we have 6 motion blur samples the interval will look like this:

                    |<---------------duration-------------->|
                    |                                       |
                sample0 sample1 sample2 sample3 sample4 sample5
                    |       |       |       |       |       |
            ----||-------|-|-----|---||--|-------|-------||----> timeline
                ||         |         ||                  ||
                start      for     interval              end
                            render    center
            
            Every sample[i] is the time frame that have to be exported and the formula for its calculation is:
            sample[i] = frameStart + i * (duration / numSamples)
        """
        mbSamples = commonSettings.mbSamples
        mbSettings = commonSettings.scene.vray.SettingsMotionBlur
        camera: bpy.types.Camera = cameraObj.data
        
        # Tagging the frame for rendering
        self._getFrameData(baseFrame).frameType = FrameType.FOR_RENDERING

        # There could me a mix of cameras with and without motion blur to be considered in the calculations.
        if not camera_utils.camObjUsesMotionBlur(cameraObj, mbSettings):
            # If the camera does not use motion blur, the frame is the last one in the interval.
            self._getFrameData(baseFrame).frameForRenderRef = baseFrame
            return

        intervalCenter, mbDuration = camera_utils.getMBlurIntCenterAndDuration(camera, commonSettings)

        frameStart = baseFrame + intervalCenter - mbDuration / 2

        mBlurStep = mbDuration / mbSamples
        for i in range(0, mbSamples + 1):
            frameData = self._getFrameData(frameStart + i * mBlurStep)
            frameData.frameType = FrameType.FROM_GLOBAL_SAMPLES

        # Calculating any other additional subframes for objects with overridden number of samples
        for objsMbSamples, objects in objectSamples.items():
            objsMbStep = mbDuration / objsMbSamples
            for i in range(0, objsMbSamples + 1):
                self._getFrameData(frameStart + i * objsMbStep).objects = objects 
    
        # The last frame must be identified to signal that rendering can begin once it is reached.
        frameEnd = baseFrame + intervalCenter + mbDuration / 2
        self._getFrameData(frameEnd).frameForRenderRef = baseFrame


    def initialize(self, scene, commonSettings, exportOnly: bool = True):
        assert scene.camera and isCamera(scene.camera), "Motion blur interval cannot be calculated without a camera"
        
        # Filling a dictionary with all objects with overridden motion blur samples
        # different from the default ones.
        objectSamples: dict[int, str] = {}
        for obj in scene.objects:
            objProperties = obj.vray.VRayObjectProperties
            objMbSamples = objProperties.motion_blur_samples
            if objProperties.override_motion_blur_samples and commonSettings.mbSamples != objMbSamples:
                objectSamples.setdefault(objMbSamples, set()).add(obj.vray.unique_id)
                self._objsWithOverriddenSamples.add(obj.vray.unique_id)

        allCameras = [o for o in scene.objects if o.type == 'CAMERA']

        currentCamera = scene.camera
        anim = commonSettings.animation
        for baseFrame in range(anim.frameStart, anim.frameEnd + 1, anim.frameStep):
            # If the scene is not going to be rendered (when writing to a .vrscene file), interval frames are calculated for all cameras.
            # This ensures that all of them have the necessary frames for motion blur generation.
            if exportOnly:
                for camera in allCameras:
                    self._calculateMBInterval(camera, baseFrame, commonSettings, objectSamples)
                continue

            if markedCamera:= next((m.camera for m in scene.timeline_markers if m.frame == baseFrame), None):
                currentCamera = markedCamera

            self._calculateMBInterval(currentCamera, baseFrame, commonSettings, objectSamples)

        # Save the number of the first frame. It will be used as the first keyframe for exporting the geometry data.
        self._firstFrame = min(self._frameData.keys())

    def getFrames(self):
        for frame in sorted(self._frameData):
            self._currentFrame = frame
            yield frame

    def isFirstFrame(self, frame: float):
        return self._firstFrame == frame
    
    def isLastFrame(self):
        """ Checks if the current frame is the last one in the motion blur interval. """
        return self._getFrameData(self._currentFrame).frameForRenderRef != UNDEFINED_FRAME # If the frame refers to a frame ready for render, it is the last one in the interval.

    def getFrameReadyForRender(self):
        """ Returns the frame that is ready for render. """
        assert self.isLastFrame(), "MotionBlurBuilder.getFrameReadyForRender() returns invalid frame if MotionBlurBuilder.isLastFrame() is False"
        return self._getFrameData(self._currentFrame).frameForRenderRef

    def getObjectsForExport(self, allObjects):
        currentFrameData = self._getFrameData(self._currentFrame)
        match(currentFrameData.frameType):
            case FrameType.FOR_RENDERING:
                return allObjects
            case FrameType.FROM_GLOBAL_SAMPLES:
                objsNotForExport = self._objsWithOverriddenSamples - currentFrameData.objects
                return (ob for ob in allObjects if ob.vray.unique_id not in objsNotForExport)
            case FrameType.FROM_OBJECT_SAMPLES:
                return (ob for ob in allObjects if ob.vray.unique_id in currentFrameData.objects)
        return ()

    def getGeometryForExport(self, allObjects):
        yield from geometryObjectIt(self.getObjectsForExport(allObjects))