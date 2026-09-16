# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bisect

from collections import deque

from vray_blender.lib.blender_utils import geometryObjectIt, isCamera
from vray_blender.lib import camera_utils
import bpy

# Subframe times are computed in several different ways, so the final result for the same frame
# may not be exactly the same depending on which algorithm was used. This may create phantom 
# subframes. Quantizing every time to a precision finer than any usable subframe step collapses 
# them back into one.
_FRAME_QUANT_DIGITS = 9
_FRAME_EPSILON = 10 ** -_FRAME_QUANT_DIGITS

def _quantizeFrame(frame: float):
    """ Snap a computed subframe time onto the common time grid. """
    return round(float(frame), _FRAME_QUANT_DIGITS)


def _sampleFrame(intervalStart: float, intervalEnd: float, sample: int, numSamples: int):
    """ The time of motion blur sample 'sample' of 'numSamples' evenly spaced steps over
        [intervalStart, intervalEnd]. Both ends are returned exactly, so whatever the sample
        count, the last sample of an interval and the interval end are the same time.
    """
    if sample == 0:
        return intervalStart
    if sample == numSamples:
        return intervalEnd

    return _quantizeFrame(intervalStart + (intervalEnd - intervalStart) * (sample / numSamples))


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
        self._intervalStartFrames: set[float] = set() # Frames that open a motion blur interval
        self._intervalEndFrames: set[float] = set()   # Frames that close a motion blur interval

        # The base frames whose motion blur interval has not been fully exported yet, in
        # ascending order, each paired with the subframe at which its interval closes. Frames
        # are only ever released from the front - see popFramesReadyForRender().
        self._pendingFrames: deque[tuple[float, float]] = deque()

        # Per base frame: the camera its motion blur interval was calculated from. This is what
        # the renderer restores over the frame's sample range - see cameraForFrame().
        self._frameCameras: dict[float, bpy.types.Object] = {}

        # Per base frame: the time range V-Ray may sample when rendering it. Not necessarily the
        # interval the subframes were generated from - see _sampleRange().
        self._frameSampleRanges: dict[float, tuple[float, float]] = {}

        # All exported subframes in ascending order, for the interval lookups.
        self._sortedFrames: list[float] = []

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

            Returns the interval as (start, end). 'end' is the last subframe that has to be
            exported before 'baseFrame' can be rendered.
        """
        mbSettings = commonSettings.scene.vray.SettingsMotionBlur
        mbSamples = commonSettings.mbSamples

        # Tagging the frame for rendering
        self._getFrameData(baseFrame).frameType = FrameType.FOR_RENDERING

        # There could be a mix of cameras with and without motion blur to be considered in the calculations.
        if camera_utils.camObjUsesMotionBlur(cameraObj, mbSettings):
            intervalCenter, mbDuration = camera_utils.getMBlurIntCenterAndDuration(cameraObj.data, commonSettings)
        elif commonSettings.velocityMotionData:
            # No motion blur to render, but the Velocity render element still needs object motion.
            intervalCenter = camera_utils.VELOCITY_MB_INTERVAL_CENTER
            mbDuration = camera_utils.VELOCITY_MB_DURATION
        else:
            # If the camera does not use motion blur, the frame is the last one in the interval.
            self._intervalEndFrames.add(baseFrame)
            return baseFrame, baseFrame

        frameStart = _quantizeFrame(baseFrame + intervalCenter - mbDuration / 2)
        frameEnd = _quantizeFrame(frameStart + mbDuration)

        for i in range(0, mbSamples + 1):
            frameData = self._getFrameData(_sampleFrame(frameStart, frameEnd, i, mbSamples))
            frameData.frameType = FrameType.FROM_GLOBAL_SAMPLES

        # Calculating any other additional subframes for objects with overridden number of samples
        for objsMbSamples, objects in objectSamples.items():
            for i in range(0, objsMbSamples + 1):
                self._getFrameData(_sampleFrame(frameStart, frameEnd, i, objsMbSamples)).objects.update(objects)

        # The interval end is always the last of the global samples, so it is one of the exported
        # subframes. That is what lets popFramesReadyForRender() release the frame while the
        # export loop walks them.
        self._intervalEndFrames.add(frameEnd)
        return frameStart, frameEnd


    @staticmethod
    def _sampleRange(baseFrame: int, interval: tuple[float, float], commonSettings):
        """ The time range V-Ray may sample when rendering 'baseFrame', as the union of the
            interval its subframes were generated from and the one SettingsMotionBlur describes.

            The two are not the same. The subframes come from the active camera - a physical
            camera derives duration and interval center from its own shutter - but nothing
            writes those back into the SettingsMotionBlur plugin, which keeps the scene-wide
            UI values. So V-Ray's shutter can be shorter, longer or offset from the interval the
            subframes cover, and taking the union is what makes the camera restore in the
            renderer cover every value V-Ray can actually read for the frame, whichever of the
            two ends up driving the blur.

            With no camera motion blur in the scene there is no shutter to account for: the
            subframes are exported for the Velocity render element alone and V-Ray samples
            nothing outside the interval.
        """
        if not commonSettings.hasCameraMotionBlur:
            return interval

        mbSettings = commonSettings.scene.vray.SettingsMotionBlur
        globalStart = _quantizeFrame(baseFrame + mbSettings.interval_center - mbSettings.duration / 2)
        globalEnd = _quantizeFrame(globalStart + mbSettings.duration)

        return min(interval[0], globalStart), max(interval[1], globalEnd)


    def initialize(self, scene, exporterCtx):
        commonSettings = exporterCtx.commonSettings
        exportOnly = exporterCtx.exportOnly

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

        # Blender activates a marker's camera on every frame from that marker onwards, so the
        # camera of a base frame is the one on the last marker at or before it. Testing for a
        # marker exactly on the frame misses every switch that does not land on a rendered frame
        # - a frame step above 1, a custom frame list, or a view layer filtered range. Before the
        # first marker Blender leaves the active camera alone, which is what scene.camera holds.
        cameraMarkers = sorted((m for m in scene.timeline_markers if m.camera), key=lambda m: m.frame)
        markerFrames = [m.frame for m in cameraMarkers]

        def cameraAtFrame(frame):
            markerIdx = bisect.bisect_right(markerFrames, frame) - 1
            return cameraMarkers[markerIdx].camera if markerIdx >= 0 else scene.camera

        anim = commonSettings.animation
        for baseFrame in anim.frames:
            # If the scene is not going to be rendered (when writing to a .vrscene file), interval frames are calculated
            # for all cameras. This ensures that all of them have the necessary frames for motion blur generation.
            if exportOnly:
                intervalStart, intervalEnd = baseFrame, baseFrame
                for camera in allCameras:
                    camStart, camEnd = self._calculateMBInterval(camera, baseFrame, commonSettings, objectSamples)
                    intervalStart = min(intervalStart, camStart)
                    intervalEnd = max(intervalEnd, camEnd)

                # No single camera owns the frame, and nothing is rendered anyway.
                frameCamera = None
            else:
                frameCamera = cameraAtFrame(baseFrame)
                intervalStart, intervalEnd = self._calculateMBInterval(frameCamera, baseFrame,
                                                                       commonSettings, objectSamples)

            # Only the latest end matters for the render trigger: the frame is renderable
            # once every camera's interval for it has been exported.
            self._pendingFrames.append((baseFrame, intervalEnd))
            self._frameSampleRanges[baseFrame] = __class__._sampleRange(
                    baseFrame, (intervalStart, intervalEnd), commonSettings)

            if frameCamera is not None:
                self._frameCameras[baseFrame] = frameCamera

        # Save the number of the first frame. It will be used as the first keyframe for exporting the geometry data.
        self._firstFrame = min(self._frameData.keys())

        self._sortedFrames = sorted(self._frameData)

        # Derive this here, not in getFrames(): _exportFullScene() materializes that generator up front.
        prevEndedInterval = True
        for frame in self._sortedFrames:
            if prevEndedInterval:
                self._intervalStartFrames.add(frame)
            prevEndedInterval = frame in self._intervalEndFrames

    def isIntervalStart(self, frame: float):
        """ Whether the frame opens a motion blur interval. True when no frame data has been built. """
        return (not self._intervalStartFrames) or (frame in self._intervalStartFrames)

    def getFrames(self):
        for frame in self._sortedFrames:
            self._currentFrame = frame
            yield frame

    def isFirstFrame(self, frame: float):
        return self._firstFrame == frame

    def cameraForFrame(self, baseFrame: float):
        """ The camera the base frame's motion blur interval was calculated from, or None when
            no single camera owns the frame (a .vrscene export builds intervals for all of them).
        """
        return self._frameCameras.get(baseFrame)

    def subframesInSampleRange(self, baseFrame: float):
        """ Every exported subframe that V-Ray may sample when rendering the base frame, in
            ascending order. These are the times whose plugin values have to belong to this
            frame's camera. See _sampleRange() for why this is wider than the frame's interval.
        """
        rangeStart, rangeEnd = self._frameSampleRanges[baseFrame]
        first = bisect.bisect_left(self._sortedFrames, rangeStart - _FRAME_EPSILON)
        last = bisect.bisect_right(self._sortedFrames, rangeEnd + _FRAME_EPSILON)

        return self._sortedFrames[first:last]


    def popFramesReadyForRender(self, frame: float):
        """ Return the base frames whose motion blur interval is fully exported as of 'frame',
            in ascending order, and stop tracking them.

            V-Ray renders an animation sequence in ascending frame order and cannot be
            repositioned once the sequence has started - VRayRenderer::setCurrentFrame() fails
            during renderSequence() - so the frames have to be handed to it in exactly that
            order. Interval ends do not follow the frame order: switching to a camera with a
            shorter shutter makes the new camera's first interval end before the previous
            camera's last one, and two intervals may also end on the same subframe. Releasing
            only from the front of the queue keeps the render order equal to the frame order in
            both cases, and never drops a frame.
        """
        readyFrames = []

        while self._pendingFrames and (self._pendingFrames[0][1] <= frame + _FRAME_EPSILON):
            readyFrames.append(self._pendingFrames.popleft()[0])

        return readyFrames

    def earliestSampleTime(self, renderFrames: list[float]):
        """ The earliest subframe that any frame which has not been rendered yet may still read:
            the frames in 'renderFrames', about to be rendered, and everything still queued
            behind them. Nothing below it is needed any more.

            The subframe the export has reached is not a bound on its own. Frames are released
            from the front of the queue, so one can be handed over at a subframe well past its
            own interval end - see popFramesReadyForRender() - and a frame whose sample range
            starts earlier than the range of the frame before it would then have already lost
            subframes it needs.
        """
        assert renderFrames, "There is nothing left to render"

        pendingFrames = [f for f, _ in self._pendingFrames]

        return min(self._frameSampleRanges[f][0] for f in (renderFrames + pendingFrames))

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