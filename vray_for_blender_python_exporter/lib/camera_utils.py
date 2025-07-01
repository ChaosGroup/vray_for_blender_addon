from __future__ import annotations

import bpy
import math

from mathutils import Matrix

from vray_blender import debug
from vray_blender.lib import blender_utils
from vray_blender.lib.settings_defs import StereoOutputLayout, StereoViewMode

# Values copied from Blender source
DEFAULT_SENSOR_WIDTH = 36.0 # This value is hardcoded in Blender
CAMERA_PARAM_ZOOM_INIT_CAMOB = 1.0
CAMERA_PARAM_ZOOM_INIT_PERSP = 2.0
M_SQRT2   = 1.41421356237309504880
DEFAULT_LENS_FOCAL_LENGTH = 35.0

class Rect:
    """ Floating-size rectangle """
    def __init__(self, xmin = 0.0, xmax = 0.0, ymin = 0.0, ymax = 0.0):
        self.xmin = xmin
        self.xmax = xmax
        self.ymin = ymin
        self.ymax = ymax

    def width(self):
        return self.xmax - self.xmin

    def height(self):
        return self.ymax - self.ymin

    def isEqualTo(self, s: Rect):
        return (
            self.xmin == s.xmin 
            and self.xmax == s.xmax
            and self.ymin == s.ymin
            and self.ymax == s.ymax
        )
    
class Size:
    """ Floating-size 2d size """
    def __init__(self, w = 0.0,  h = 0.0):
        self.w = w
        self.h = h

    def isEqualTo(self, s: Size):
        return self.w == s.w and self.h == s.h


class RenderViewParams:
    def __init__(self):
        self.fov            = 0.785398
        self.ortho          = False
        self.ortho_width    = 1.0
        self.clip_start     = 0.0
        self.clip_end       = 1.0
        self.clipping       = True
        self.tm             = Matrix()


class ViewParams:
    """ Viewport and region size data """
    def __init__(self):
        self.viewportW = 0
        self.viewportH = 0
        self.viewportOffsX = 0
        self.viewportOffsY = 0

        self.renderSize = Size()
        self.renderView = RenderViewParams()
        self.regionStart = Size()
        self.regionSize = Size()
        self.crop = False
        self.canDrawWithOffset = True # Indicates if the image rendered with these view parameters can be drawn using gl_draw with an offset

        self.usePhysicalCamera  = False
        self.useDomeCamera      = False
        self.isCameraView       = True          # True if the viewport is in camera view mode
        self.cameraObject: bpy.types.Object = None
        self.cameraParams       = CameraParams()
        self.renderSizes        = RenderSizes()
        self.isActiveCamera     = False         # Set to true if this is the active scene camera which should be rendered


    def calcRenderSizes(self):
        rs = self.renderSizes

        # For a detailed description of the render regions and sizes, see the comment here
        # https://git.chaosgroup.com/core/cgrepo/-/blob/master/vraysl/vray_Settings/settings_output.cpp#L11

        # Bmp size is the size of the camera viewplane quad
        rs.bmpWidth = round(self.renderSize.w)
        rs.bmpHeight = round(self.renderSize.h)

        isCropped = self.crop

        # Img size is the size of the output image - may be less than camera viewplane quad if 
        # a crop region is active. Output img boundaries always map to the crop region boundaries. 
        rs.imgWidth = round(self.regionSize.w) if isCropped else rs.bmpWidth
        rs.imgHeight = round(self.regionSize.h) if isCropped else rs.bmpHeight

        # The crop region specifies which part of the full view to actually render.
        # The crop quad from the rendered image is 'projected' onto the output img, possibly
        # resizing it if the img and crop sizes don't match 
        rs.cropRgnLeft = self.regionStart.w if isCropped else 0.0
        rs.cropRgnTop = self.regionStart.h if isCropped else 0.0
        rs.cropRgnWidth = self.regionSize.w if isCropped else self.renderSize.w
        rs.cropRgnHeight = self.regionSize.h if isCropped else self.renderSize.h

        # The output region is also used for croppping, but inside the output image (whereas the 
        # crop region does define cropping inside the rendered image). 
        # We are never rendering to just a part of the output image, so output region is always
        # equal to the output image size
        rs.rgnLeft = 0
        rs.rgnTop = 0
        rs.rgnWidth = rs.imgWidth
        rs.rgnHeight = rs.imgHeight 

        # Don't make assumptions about the previous state, always set all values
        rs.bitmask  = RenderSizes.Bitmask.ImgSize | RenderSizes.Bitmask.CropRgn

        

class RenderSizes:
    """ Implementation of VRayMessage::RenderSizes ZMQ protocol object """
    from enum import IntEnum

    class Bitmask(IntEnum):
        ImgSize = 1
        RenderRgn = 2
        CropRgn = 4

    def __init__(self):
        self.bitmask        = 0
        self.bmpWidth       = 0
        self.bmpHeight      = 0
        self.imgWidth       = 0
        self.imgHeight      = 0
        self.cropRgnLeft    = 0
        self.cropRgnTop     = 0
        self.cropRgnWidth   = 0
        self.cropRgnHeight  = 0
        self.rgnLeft        = 0
        self.rgnTop         = 0
        self.rgnWidth       = 0
        self.rgnHeight      = 0


    def isEqualTo(self, other: RenderSizes):
        return (
            self.imgWidth == other.imgWidth 
            and self.imgHeight == other.imgHeight
            and self.bmpWidth == other.bmpWidth
            and self.bmpHeight == other.bmpHeight
            and self.rgnLeft == other.rgnLeft
            and self.rgnTop == other.rgnTop
            and self.rgnWidth == other.rgnWidth
            and self.rgnHeight == other.rgnHeight
            and self.cropRgnLeft == other.cropRgnLeft
            and self.cropRgnTop == other.cropRgnTop
            and self.cropRgnWidth == other.cropRgnWidth
            and self.cropRgnHeight == other.cropRgnHeight
        )


    def multiply(self, mult: Size):
        self.imgWidth *= mult.w
        self.imgHeight *= mult.h

        self.bmpWidth *= mult.w
        self.bmpHeight *= mult.h

        self.rgnWidth *= mult.w
        self.rgnHeight *= mult.h

        self.cropRgnWidth *= mult.w
        self.cropRgnHeight *= mult.h


class CameraParams:
    """ Generic camera params"""
    def __init__(self):
        self._setDefaultParams()

    def _setDefaultParams(self):
        self.isOrtho    = False
        self.lens       = DEFAULT_LENS_FOCAL_LENGTH
        self.orthoScale = 1.0
        self.zoom       = 1.0
        
        self.shiftx   = 0.0
        self.shifty   = 0.0
        self.offsetx  = 0.0
        self.offsety  = 0.0

        self.sensorX = DEFAULT_SENSOR_WIDTH
        self.sensorY = DEFAULT_SENSOR_WIDTH
        self.sensorFit = 'AUTO'

        self.clip_start = 0.1
        self.clip_end = 100.0


    def setFromObject(self, cameraObj: bpy.types.Object):
        """ Set params from a camera-like object in the scene """
        self._setDefaultParams()
        if not cameraObj:
            return
        
        if cameraObj.type == "CAMERA":
            camera: bpy.types.Camera = cameraObj.data
            
            self.isOrtho = isOrthographicCamera(camera)
            self.orthoScale = camera.ortho_scale if self.isOrtho else 1

            self.shiftx = camera.shift_x
            self.shifty = camera.shift_y

            self.sensorX = camera.sensor_width
            self.sensorY = camera.sensor_height
            self.sensorFit = str(camera.sensor_fit)

            self.clip_start = camera.clip_start
            self.clip_end = camera.clip_end
            
            settingsCamera = cameraObj.data.vray.SettingsCamera

            if settingsCamera.override_camera_settings and settingsCamera.override_fov:
                if settingsCamera.fov >= math.pi:
                    # Blender does not support camera angles > 180 degrees.
                    # Set focal distance to a negative value. This will cause the FOV 
                    # to be negative and to not override the FOV for the render view 
                    # with a value computed from the focal distance.
                    self.lens = -1.0
                else:
                    self.lens = focalDist(settingsCamera.fov, getCameraSensorSize(self.sensorFit, self.sensorX, self.sensorY))
            else:          
                # In case FOV is not overridden, Blender has already computed the focal distance for us
                self.lens = camera.lens
            return
        
        if (cameraObj.type == "LIGHT") and hasattr(cameraObj.data, "vray"):
            match cameraObj.data.type:
                case 'SPOT':
                    light: bpy.types.SpotLight = cameraObj.data
                    self.lens = 16.0 / math.tan(light.spot_size * 0.5)
                # The following adjustments to the focal length have been determined empirically. 
                # TODO: Find where those values are set in Blender code and add a reference.
                case 'POINT': 
                    self.lens = DEFAULT_LENS_FOCAL_LENGTH - 14
                case 'AREA':
                    self.lens = DEFAULT_LENS_FOCAL_LENGTH + 3.5
                case 'SUN':
                    self.lens = DEFAULT_LENS_FOCAL_LENGTH + 3.5
                case _:
                    debug.printWarning(f"Unknowm light type: {cameraObj.data.type}")


    def setFromView3d(self, dg: bpy.types.Depsgraph, v3d: bpy.types.SpaceView3D, rv3d: bpy.types.RegionView3D):
        """ Set params from a viewport's settings """
        self._setDefaultParams()
        self.lens = v3d.lens
        self.clip_start = v3d.clip_start
        self.clip_end = v3d.clip_end

        if rv3d.view_perspective == 'CAMERA':
            camObj = dg.id_eval_get(v3d.camera)
            self.setFromObject(camObj)

            self.zoom = screenView3dZoomToFac(rv3d.view_camera_zoom)

            self.offsetx = 2.0 * rv3d.view_camera_offset[0] * self.zoom
            self.offsety = 2.0 * rv3d.view_camera_offset[1] * self.zoom

            self.shiftx *= self.zoom
            self.shifty *= self.zoom
            
            self.zoom = CAMERA_PARAM_ZOOM_INIT_CAMOB / self.zoom
            return
        
        if rv3d.view_perspective == 'ORTHO':
            sensorSize = getCameraSensorSize(self.sensorFit, self.sensorX, self.sensorY)
            self.clip_end *= 0.5
            self.clip_start = self.clip_end
            
            self.isOrtho = True
            self.orthoScale = rv3d.view_distance * sensorSize / v3d.lens
            
            self.zoom = CAMERA_PARAM_ZOOM_INIT_PERSP
            return
        
        # Perspective camera
        self.zoom = CAMERA_PARAM_ZOOM_INIT_PERSP


    def computeViewplane(self, winX: int, winY: int, aspX: float, aspY: float):
        """ For the camera view, compute the camera view rectangle position in the viewport"""
        viewplane = Rect()
        pixSize, viewFac, sensorSize, dx, dy = 0.0, 0.0, 0.0, 0.0, 0.0
        
        ycor = aspY / aspX

        if self.isOrtho:
            # Orthographic camera
            # scale == 1.0 means exact 1 to 1 mapping
            pixSize = self.orthoScale
        else:
            # Perspective camera
            sensorSize = getCameraSensorSize(self.sensorFit, self.sensorX, self.sensorY)
            pixSize = (sensorSize * self.clip_start) / self.lens

        # Determine sensor fit and compute the pixel size of the viewplane. The choice
        # of H or V direction for the pixel size depends on the aspect ratio of the viewport rectangle
        sensorFit = getCameraSensorFit(self.sensorFit, aspX * winX, aspY * winY)
        viewFac = winX if sensorFit == 'HORIZONTAL' else ycor * winY
        pixSize /= viewFac
        # Extra zoom factor
        pixSize *= self.zoom

        # Compute viewplane coordinates centered around (0,0)
        viewplane.xmin = -0.5 * float(winX)
        viewplane.ymin = -0.5 * ycor * float(winY)
        viewplane.xmax = 0.5 * float(winX)
        viewplane.ymax = 0.5 * ycor * float(winY)

        # Compute lens shift and offset deltas
        dx = self.shiftx * viewFac + winX * self.offsetx
        dy = self.shifty * viewFac - winY * self.offsety # The Y axis in V-Ray is opposite to Blender's, so flip the offset direction.

        viewplane.xmin += dx
        viewplane.ymin += dy
        viewplane.xmax += dx
        viewplane.ymax += dy

        # Translate sizes from normalized to pixels
        viewplane.xmin *= pixSize
        viewplane.xmax *= pixSize
        viewplane.ymin *= pixSize
        viewplane.ymax *= pixSize

        return viewplane



#########################################################
########## FREE FUNCTIONS 
#########################################################


# The comment below was copied verbatim from Blender source:
#   ---
#   Magic zoom calculation, no idea what it signifies, if you find out, tell me! -zr
#
#   Simple, its magic dude! Well, to be honest,
#   this gives a natural feeling zooming with multiple keypad presses (ton). */

def screenView3dZoomToFac(cameraZoom):
    return math.pow((M_SQRT2 + cameraZoom / 50.0), 2.0) / 4.0


def getCameraSensorSize(sensorFit: str, sensorX: float, sensorY: float):
    return sensorY if sensorFit == 'VERTICAL' else sensorX


def getCameraSensorFit(sensorFit: str, sizeX: float, sizeY: float):
    if sensorFit == 'AUTO':
        return 'HORIZONTAL' if sizeX >= sizeY else 'VERTICAL'

    return sensorFit


def fov(sensorSize: float, focalDist: float):
    return 2.0 * math.atan((0.5 * sensorSize) / focalDist)


def focalDist(fov: float, sensorSize: float):
    return sensorSize / (2 * math.tan(fov / 2))


# Return render border as Rect
def getView3dRenderBorder(v3d: bpy.types.SpaceView3D):
    return Rect(v3d.render_border_min_x, v3d.render_border_max_x, v3d.render_border_min_y, v3d.render_border_max_y)

# Return border as Rect
def getRenderSettingsBorder(rs: bpy.types.RenderSettings):
    return Rect(rs.border_min_x, rs.border_max_x, rs.border_min_y, rs.border_max_y)


def isPhysicalCamera(obj):
    if blender_utils.isCamera(obj):
        camera: bpy.types.Camera = obj.data
        return camera.vray.CameraPhysical.use
    
    return False


def isDomeCamera(obj):
    if blender_utils.isCamera(obj):
        camera: bpy.types.Camera = obj.data
        return camera.vray.CameraDome.use
    
    return False


def isOrthographicCamera(camera: bpy.types.Camera):
    settingsCamera = camera.vray.SettingsCamera
    return  (camera.type == 'ORTHO') or (settingsCamera.override_camera_settings and settingsCamera.type == '7' )
    

def isSameCamera(camera1: bpy.types.Object, camera2: bpy.types.Object):
    return (camera1 is not None) and (camera2 is not None) and (camera1.name_full == camera2.name_full)


def getPhysicalCamera(obj):
    return obj.data.vray.CameraPhysical if blender_utils.isCamera(obj) else None


def aspectCorrectForFovOrtho(vp: ViewParams):
    aspect = float(vp.renderSize.w) / float(vp.renderSize.h)
    if aspect < 1.0:
        vp.renderView.fov = 2.0 * math.atan(math.tan(vp.renderView.fov / 2.0) * aspect)
        vp.renderView.ortho_width  *= aspect


def setRegionBorder(vp: ViewParams, borderRect: Rect):
    vp.regionStart.w = int(borderRect.xmin * vp.viewportW)
    # Coordinate system origin is bottom-left
    vp.regionStart.h = vp.viewportH * (1.0 - borderRect.ymax)

    vp.regionSize.w = int(borderRect.width() * vp.viewportW)
    vp.regionSize.h = int(borderRect.height() * vp.viewportH)
    vp.crop = True


def getCameraDofDistance(cameraObj: bpy.types.Object):
    camera: bpy.types.Camera = cameraObj.data
    dofObject = camera.dof.focus_object
    return blender_utils.getDistanceObOb(cameraObj, dofObject) if dofObject else camera.dof.focus_distance


def hasRenderSizeChanged(prev: ViewParams, curr: ViewParams):
    return (
        not prev.renderSize.isEqualTo(curr.renderSize)
        or not prev.regionStart.isEqualTo(curr.regionStart)
        or not prev.regionSize.isEqualTo(curr.regionSize)
    )

def getStereoscopicSizeMult(vrayScene):
    stereo = vrayScene.VRayStereoscopicSettings
    if stereo.use and (int(stereo.view) == StereoViewMode.Both):
        if int(stereo.output_layout) == StereoOutputLayout.Horizontal:
            return Size(2, 1)
        else:
            return Size(1, 2)
            
    return Size(1, 1)


def fixLookThroughObjectCameraZFight(tmObject: Matrix):
    """ Move an object a small amount along its 'forward' axis to avoid z-fight with
        objects positioned at the same place.

        Returns:
        The calculated transform for the camera. 
    """
    
    EPSILON = 0.00001 # 1/100 mm
    
    forwardVec  = tmObject.inverted().normalized().row[2]
    forwardVec *= EPSILON

    tmObject[0][3] -= forwardVec.x
    tmObject[1][3] -= forwardVec.y
    tmObject[2][3] -= forwardVec.z

    return tmObject


def fixOverrideCameraType():
    """ This function is called from the onDepsgraphPre event. It keeps in sync 
        Blender and V-Ray camera type selections.
    """
    if (camera := bpy.context.scene.camera) and blender_utils.isCamera(camera):
        # Orthographic camera mode should always be either ON or OFF for BOTH Blender camera 
        # and V-Ray Camera Override in order for the object selection outline to be correct.
        # Keep them in sync.
        if camera.data.type == 'ORTHO':
            # Set to Orthogonal
            camera.data.vray.SettingsCamera.type = '7'
        elif camera.data.vray.SettingsCamera.type == '7':
            # Set to Standard
            camera.data.vray.SettingsCamera.type = '0'

