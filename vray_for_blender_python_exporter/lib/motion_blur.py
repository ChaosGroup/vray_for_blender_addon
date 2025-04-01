from vray_blender.lib.blender_utils import geometryObjectIt

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
    frameType = FrameType.FROM_OBJECT_SAMPLES  # type of the frame
    objects = set() # Objects that have to be exported at that frame
    lastInInterval = False # Indicates if the frame is the last one from motion blur interval

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

    def initialize(self, settingsMotionBlur, scene, commonSettings):
        anim = commonSettings.animation
        
        # Filling a dictionary with all objects with overridden motion blur samples
        # different from the default ones.
        objectSamples: dict[int, str] = {}
        for obj in scene.objects:
            objProperties = obj.vray.VRayObjectProperties
            objMbSamples = objProperties.motion_blur_samples
            if objProperties.override_motion_blur_samples and commonSettings.mbSamples != objMbSamples:
                objectSamples.setdefault(objMbSamples, set()).add(obj.vray.unique_id)
                self._objsWithOverriddenSamples.add(obj.vray.unique_id)


        # Save the number of the first frame. It will be used as the first keyframe for exporting the geometry data.
        self._firstFrame = anim.frameStart + settingsMotionBlur.interval_center - settingsMotionBlur.duration / 2

        # Calculating the interval of frames needed for rendering a frame with motion blur.
        for baseFrame in range(anim.frameStart, anim.frameEnd + 1, anim.frameStep):
            """
                The interval is defined by: duration = "settingsMotionBlur.duration", 
                center = "settingsMotionBlur.interval_center", and number of samples = "commonSettings.mbSamples".
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
            frameStart = baseFrame + settingsMotionBlur.interval_center - settingsMotionBlur.duration / 2
            
            mBlurStep = settingsMotionBlur.duration / commonSettings.mbSamples
            for i in range(0, commonSettings.mbSamples + 1):
                frameData = self._getFrameData(frameStart + i * mBlurStep)
                frameData.frameType = FrameType.FROM_GLOBAL_SAMPLES

            # Calculating any other additional subframes for objects with overridden number of samples
            for objsMbSamples, objects in objectSamples.items():
                objsMbStep = settingsMotionBlur.duration / objsMbSamples
                for i in range(0, objsMbSamples + 1):
                    self._getFrameData(frameStart + i * objsMbStep).objects = objects

            # Tagging the frame for rendering
            self._getFrameData(baseFrame).frameType = FrameType.FOR_RENDERING
            
            # The last frame must be identified to signal that rendering can begin once it is reached.
            frameEnd = baseFrame + settingsMotionBlur.interval_center + settingsMotionBlur.duration / 2
            self._getFrameData(frameEnd).lastInInterval = True
    
    def getFrames(self):
        for frame in sorted(self._frameData):
            self._currentFrame = frame
            yield frame

    def isFirstFrame(self, frame: float):
        return self._firstFrame == frame
    
    def isLastFrame(self):
        return self._getFrameData(self._currentFrame).lastInInterval

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