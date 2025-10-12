from __future__ import annotations
import bpy
import math
from mathutils import Matrix

from vray_blender import debug
from vray_blender.lib import export_utils, blender_utils
from vray_blender.lib.blender_utils import isCamera
from vray_blender.lib.camera_utils import ViewParams, Rect, Size, isOrthographicCamera
from vray_blender.lib import camera_utils as ct
from vray_blender.lib.defs import AttrPlugin, ExporterContext, ExporterBase, PluginDesc, ProdRenderMode
from vray_blender.lib.names import Names
from vray_blender.plugins.settings.SettingsOutput import SettingsOutputExporter
from vray_blender.exporting.plugin_tracker import getObjTrackId, log as trackerLog
from vray_blender.utils.utils_bake import VRayBatchBakeItem

from vray_blender.bin import VRayBlenderLib as vray

# In production, multiple cameras can be exported but only one can be active at any given time.
# The switching of cameras is not supported during a single render job, so the way to animate
# camera switch is to export a dedicated camera which is enabled for rendering and whose parameters
# are taken from the active camera for each frame. This camera is also used for interactive jobs
# as we cannot have more than one camera in IPR.
RENDER_CAMERA_BASE_NAME = 'renderCamera'

VISIBLE_TO_WHOLE_VIEW_RATIO = 1.66257 # The ratio of the whole view to the visible area of 3d viewport in camera mode.
# Example:
#    _______________________________________________
#   |    ________________________________           |
#   |   |           Visible 3D          |           |
#   |   |          viewport area        |           |
#   |   |            ________           |           |
#   |   |           | Camera |          |           |
#   |   |           |  rect  |          |           |
#   |   |           |________|          |           |
#   |   |                               |           | VISIBLE_TO_WHOLE_VIEW_RATIO = Whole-view-width / Viewport-width
#   |   |_______________________________|           |
#   |   <--------Viewport-width--------->           |
#   |                                               |
#   |          Area outside the viewport            |
#   |_______________________________________________|
#   <-----------------Whole-view-width-------------->
#
# Steps used for its calculation:
# 1. Switch to camera viewport mode
# 2. Zoom out at maximum.
# 3. Move the camera rectangle to the most right position of the 3d viewport
# 4. Measure the pixels from the left edge of the 3d viewport to the center of the camera rectangle
# 5. Multiply the pixels by 2 to get the whole view size
# 6. Divide the whole view size by the viewport width to get the ratio


# Returns the ratio between the viewport width and the camera view rectangle width, based on the sensor fit.
def _getCameraToViewportRatio(viewParams: ViewParams, region: bpy.types.Region, renderer: bpy.types.RenderSettings):
    rectView = viewParams.cameraParams.computeViewplane(region.width, region.height, 1.0, 1.0)
    rectCamera = viewParams.cameraParams.computeViewplane(renderer.resolution_x, renderer.resolution_y, 1.0, 1.0)

    sensorFit = ct.getCameraSensorFit(viewParams.cameraParams.sensorFit, renderer.resolution_x, renderer.resolution_y)

    return rectView.width() / (rectCamera.height() if sensorFit == 'VERTICAL' else  rectCamera.width())


def getActiveCamera(ctx: ExporterContext):
    """ Return the view-local camera, if active. Otherwise return the scene camera. 
        NOTE: The view camera is not necessarily of type Camera, may be any object.
    """
    view3D = blender_utils.getSpaceView3D(ctx.ctx) if ctx.viewport else None

    if view3D and (view3dCamera := view3D.camera):
        return view3dCamera

    return ctx.dg.scene.camera

def _exportCameraDefaultUVWGen(ctx: ExportedContext):
    """ Export a UVWGenChannel plugin for use in camera aperture/distortion.

        @returns A newly exported plugin on the first invocation, a cached copy after that.
    """

    DEFAULT_PLUGIN_TYPE = 'UVWGenChannel_camera'
    if not (plugin := ctx.defaultPlugins.get(DEFAULT_PLUGIN_TYPE)):
        uvwGenChannel = PluginDesc('cameraDefaultUVWGenChannel', 'UVWGenChannel')
        uvwGenChannel.setAttribute('uvw_channel', 0)
        plugin = export_utils.exportPlugin(ctx, uvwGenChannel)
        ctx.defaultPlugins[DEFAULT_PLUGIN_TYPE] = plugin

    return plugin

class ViewExporter(ExporterBase):
    """ Export all view-related settings
    """
    def __init__(self, ctx: ExporterContext):
        super().__init__(ctx)
        self.scene = self.dg.scene
        self.vrayScene = self.scene.vray
        self.vrayExporter = self.vrayScene.Exporter
        self.activeCamera = getActiveCamera(ctx)
        self.cameraTracker = ctx.objTrackers['CAMERA']
        self.view3D: bpy.types.SpaceView3D = None


    def exportProdCameras(self, prevPerCameraViewParams: dict[str, ViewParams]):
        """ Export all cameras in the scene, prodiction only.

            Parameters:
            perCameraViewParams: camera object full name => last evaluated ViewParams
        """
        from vray_blender.engine.renderer_prod import VRayRendererProd

        assert self.production or self.preview, "Method should only be called for prod and preview renders"

        exportOnly = self.production and (VRayRendererProd.renderMode == ProdRenderMode.EXPORT_VRSCENE)

        cameras = []

        # When writing a .vrscene file, export all cameras in the scene.
        # This is necessary for subsequent renderings with V-Ray Standalone
        # where the user can select which one of the exported cameras to use.
        # During production rendering, only the active camera is needed (or the marked cameras, if any, in animation mode).
        if exportOnly or self.preview:
            cameras = list([inst.object for inst in self.dg.object_instances if inst.object.type == 'CAMERA'])
            assert (not self.preview) or len(cameras) == 1, "Exactly 1 camera should exist in the material preview scene"
        elif self.isAnimation:
            # Camera objects marked in the timeline
            markedCameras = set(m.camera.name for m in self.scene.timeline_markers if m.camera and (m.frame <= self.scene.frame_end))
            
            # Evaluated marked camera objects
            cameras = list([inst.object for inst in self.dg.object_instances if inst.object.name in markedCameras])

        # If an object other than a camera has been set as the active camera, add it to the cameras' list.
        # A camera with suitable parameters will be exported for it.
        if self.activeCamera and ((not blender_utils.isCamera(self.activeCamera)) or (not cameras)):
            cameras.append(self.activeCamera)

        perCameraViewParams: dict[str, ViewParams] = {}

        for cameraObj in (c.evaluated_get(self.dg) for c in cameras):
            cameraName = cameraObj.name_full
            viewParams = prevPerCameraViewParams.get(cameraName)
            perCameraViewParams[cameraName] = self._exportProdCamera(cameraObj, viewParams)
        
        if self.isAnimation and prevPerCameraViewParams:
            self._checkForIncompatibleCameras(prevPerCameraViewParams, perCameraViewParams)

        # The active camera settings have been exported to a dedicated set of 
        # camera plugins with the RENDER_CAMERA_BASE_NAME scene name. Tell V-Ray 
        # to use it for the actual rendering.
        vray.setCameraName(self.renderer, RENDER_CAMERA_BASE_NAME)
        return perCameraViewParams
    

    def exportViewportView(self, view3D: bpy.types.SpaceView3D, prevViewParams: ViewParams = None):
        assert self.interactive, "Method should only be called for viewport renders"
        assert view3D, "view3D parameter should not be empty"
        self.view3D = view3D

        viewParams: ViewParams = self._getInteractiveViewParams()

        # Some plugins depend on SettingsOutput and have to be exported AFTER it, so export
        # it before anything else
        SettingsOutputExporter(self, viewParams, prevViewParams).export()

        self._exportCamera(viewParams, prevViewParams, isRenderCamera=True)
        
        return viewParams


    def exportBakeView(self):
        assert self.bake, "Method should only be called for bake renders"

        viewParams: ViewParams = self._getBakeViewParams()
        SettingsOutputExporter(self, viewParams, prevViewParams=None).export()
        
        self._exportBakeView(viewParams)
        self._exportCameraDefault(viewParams)
        
        return viewParams


    # Remove the plugins associated with deleted cameras
    # Use only for the interactive viewport
    def prunePlugins(self):
        assert(self.interactive)
 
        cameraTracker = self.cameraTracker
        objectIds = [getObjTrackId(o) for o in self.sceneObjects if o.type == "CAMERA"]

        diff = cameraTracker.diff(objectIds)
        for objTrackId in diff:
            for pluginName in cameraTracker.getOwnedPlugins(objTrackId):
                vray.pluginRemove(self.renderer, pluginName)
                trackerLog(f"REMOVE: {objTrackId} => {pluginName}")
            cameraTracker.forget(objTrackId) 


    def _exportProdCamera(self, camera: bpy.types.Camera, prevViewParams: ViewParams = None):
        """ Exports the camera in the selected mode ( normal or bake ) """
        assert not self.viewport, "Method shouldn't be called for viewport renders"

        viewParams: ViewParams = self._getProdViewParams(camera)
        
        if self._isActiveCamera(viewParams.cameraObject):
            # Some plugins depend on SettingsOutput and have to be exported AFTER it, so export
            # it before anything else
            SettingsOutputExporter(self, viewParams, prevViewParams=None).export()
            viewParams.isActiveCamera = True
        
        if self._useCamera(viewParams.cameraObject):
            # This is the active render camera. Export as a dedicated set of plugins with fixed names
            # and mark all of them as active (dont_affect_settings = False)
            self._exportCamera(viewParams, prevViewParams, isRenderCamera=True)
        
        # Regardless of whether this is the active render camera, export current camera settings in its own 
        # set of plugins and mark them as inactive (dont_affect_settings = True). This will allow the camera 
        # to be selected as the render camera in V-Ray Standalone. 
        self._exportCamera(viewParams, prevViewParams, isRenderCamera=False)

        return viewParams


    def _exportCamera(self, viewParams: ViewParams, prevViewParams: ViewParams|None, isRenderCamera: bool):
        """ Export a scene object as a camera.

            Along with the camera objects, Blender allows to export any scene object as a camera. Such a camera
            is positioned at the object's coordinates and is looking at the coordinate system origin. 
        """
        if self.interactive and prevViewParams:
            self._removeIncompatibleCameras(viewParams, prevViewParams, isRenderCamera)
        

        if blender_utils.isCamera(viewParams.cameraObject):
            if viewParams.usePhysicalCamera:
                self._exportCameraPhysical(viewParams, isRenderCamera)
            elif viewParams.useDomeCamera:
                self._exportCameraDome(viewParams, isRenderCamera)
            else:
                # The CameraDefault plugin cannot be set per-camera, so use the settings of the active camera
                if self._isActiveCamera(viewParams.cameraObject):
                    self._exportCameraDefault(viewParams)

            
        self._exportSettingsCamera(isRenderCamera, viewParams)
        self._exportRenderView(viewParams, isRenderCamera)


    # Export RenderView plugin
    def _exportRenderView(self, viewParams: ViewParams, isRenderCamera: bool):
        """ Export RenderView plugin for the specified camera.

            @param viewParams - a filled-in ViewParams structure.
            @param isRenderCamera - True if this is the active camera in the scene through which to render. 
                                    False if this is a camera that is to be exported, but is not currently active.  
            @param cameraObj -  In Prespective View - the object to export(may be non-camera). 
                                In Camera view - a camera object or None for non-camera objects.
        """
        # cameraObj will be None if there is no camera in the scene in perspective mode or
        # when a non-camera object is used as a local camera in CameraView mode
        cameraObj = viewParams.cameraObject
        pluginName = self._getCameraPluginUniqueName(viewParams.cameraObject, "RenderView", isRenderCamera)
        viewDesc = PluginDesc(pluginName, "RenderView")

        cameraOverridesEnabled = isCamera(cameraObj) and cameraObj.data.vray.SettingsCamera.override_camera_settings
        
        viewDesc.setAttribute("transform", viewParams.renderView.tm)
        viewDesc.setAttribute("fov", viewParams.renderView.fov)
        viewDesc.setAttribute("clipping", viewParams.renderView.clipping if cameraOverridesEnabled else False)
        viewDesc.setAttribute("clipping_near", viewParams.renderView.clip_start)
        viewDesc.setAttribute("clipping_far", viewParams.renderView.clip_end)
        viewDesc.setAttribute("orthographic", viewParams.renderView.ortho)
        viewDesc.setAttribute("orthographicWidth", viewParams.renderView.ortho_width)

        if blender_utils.isCamera(cameraObj):
            settingsDof = self.ctx.scene.vray.SettingsCameraDof
            focalDistance = ct.getCameraDofDistance(cameraObj) if settingsDof.use_camera_focus else settingsDof.focal_dist
            viewDesc.setAttribute("focalDistance", focalDistance)
        
        viewDesc.setAttribute("scene_name", self._getCameraBaseName(cameraObj, isRenderCamera))
        
        viewDesc.setAttribute("use_scene_offset", not self.interactive)
        viewDesc.setAttribute("dont_affect_settings", not isRenderCamera)

        export_utils.exportPlugin(self, viewDesc)

        # Export stereoscopic cameras only for production renderings. Currently, we can't show
        # both cameras in the same view, as anaglyph mode is not implemented in VRay
        if not self.interactive:
            if self.vrayScene.VRayStereoscopicSettings.use:
                self._exportCameraStereoscopic(viewParams)
            

    def _exportSettingsCamera(self, isRenderCamera: bool, viewParams: ViewParams):
        cameraObj = viewParams.cameraObject

        settingsCameraGlobal = self.scene.vray.SettingsCameraGlobal

        pluginName = self._getCameraPluginUniqueName(cameraObj, "SettingsCamera", isRenderCamera)
        plDesc = PluginDesc(pluginName, "SettingsCamera")
        

        plDesc.setAttributes({
            'scene_name'            : self._getCameraBaseName(cameraObj, isRenderCamera),
            'dont_affect_settings'  : not isRenderCamera,
            'fov'                   : -1, # Special value to indicate that FOV from RenderView should be used instead
            "auto_exposure"         : settingsCameraGlobal.auto_exposure,
            "auto_corrections_mode" : settingsCameraGlobal.auto_corrections_mode,
            "auto_white_balance"    : "2" if settingsCameraGlobal.auto_white_balance else "0"
        })

        if not viewParams.isCameraView:
            # If we are not in camera view mode, switch to Standard camera type because the current mode
            # does not support special cameras.
            plDesc.resetAttribute('type')

        if isCamera(cameraObj):
            vrayCamera = cameraObj.data.vray
            settingsCamera = vrayCamera.SettingsCamera
            plDesc.vrayPropGroup = settingsCamera

            if settingsCamera.override_camera_settings:
                # Height is defined differently for cylindrical and spherical cameras
                if settingsCamera.type == '3':   # Cylindrical
                    plDesc.setAttribute('height', settingsCamera.height)
                elif settingsCamera.type == '9': # Spherical panorama
                    plDesc.setAttribute('height', math.degrees(settingsCamera.vertical_fov))
            elif not isOrthographicCamera(cameraObj.data):
                # If camera override has been switched off, reset the type in order to return to the
                # standard camera. Do not do this if the current camera is orthographic because
                # it should match the camera type set in Blender.
                plDesc.resetAttribute('type')
            
            # Only camera object plugins are getting tracked and pruned.
            self.cameraTracker.trackPlugin(getObjTrackId(cameraObj), pluginName)

        export_utils.exportPlugin(self, plDesc)
        

    def _exportCameraDefault(self, viewParams: ViewParams):
        # This plugin can only be used as a singleton, hence the fixed name
        pluginName = Names.singletonPlugin("CameraDefault")
        
        plDesc = PluginDesc(pluginName, "CameraDefault")
        plDesc.setAttribute("orthographic", viewParams.renderView.ortho)

        export_utils.exportPlugin(self, plDesc)


    def _exportCameraPhysical(self, viewParams: ViewParams, isRenderCamera: bool):
        assert (viewParams.cameraObject is not None) and (viewParams.cameraObject.type == 'CAMERA'), "ViewParams should have a valid camera object"

        vrayCamera = viewParams.cameraObject.data.vray

        pluginType = "CameraPhysical"
        pluginName = self._getCameraPluginUniqueName(viewParams.cameraObject, pluginType, isRenderCamera)

        plDesc = PluginDesc(pluginName, pluginType)
        plDesc.vrayPropGroup = vrayCamera.CameraPhysical
        self._fillPhysicalCameraSettings(plDesc, viewParams)
        plDesc.setAttribute("scene_name", self._getCameraBaseName(viewParams.cameraObject, isRenderCamera))
        plDesc.setAttribute("dont_affect_settings", not isRenderCamera)
        plDesc.setAttribute("fov", viewParams.renderView.fov)

        aperturePath = vrayCamera.CameraPhysical.bmpaperture_tex_path
        distortionPath = vrayCamera.CameraPhysical.distortion_tex_path
        if aperturePath or distortionPath:
            uvwgen = _exportCameraDefaultUVWGen(self)
        apertureTex = export_utils.exportRenderMaskBitmap(self, aperturePath, uvwgen, pluginName+"|aperture") if aperturePath else AttrPlugin()
        plDesc.setAttribute("bmpaperture_tex", apertureTex)
        distortionTex = export_utils.exportRenderMaskBitmap(self, distortionPath, uvwgen, pluginName+"|distortion") if distortionPath else AttrPlugin()
        plDesc.setAttribute("distortion_tex", distortionTex)

        export_utils.exportPlugin(self, plDesc)
        self.cameraTracker.trackPlugin(getObjTrackId(viewParams.cameraObject), pluginName)


    def _exportCameraDome(self, viewParams: ViewParams, isRenderCamera: bool):
        assert (viewParams.cameraObject is not None) and (viewParams.cameraObject.type == 'CAMERA'), "ViewParams should have a valid camera object"

        pluginType = "CameraDome"
        pluginName = self._getCameraPluginUniqueName(viewParams.cameraObject, pluginType, isRenderCamera)

        plDesc = PluginDesc(pluginName, pluginType)
        plDesc.setAttribute("scene_name", self._getCameraBaseName(viewParams.cameraObject, isRenderCamera))
        plDesc.setAttribute("dont_affect_settings", not isRenderCamera)
        plDesc.vrayPropGroup = viewParams.cameraObject.data.vray.CameraDome

        export_utils.exportPlugin(self, plDesc)
        self.cameraTracker.trackPlugin(getObjTrackId(viewParams.cameraObject), pluginName)


    def _exportSettingsMotionBlur(self, cameraObj: bpy.types.Camera):
        """ SettingsMotionBlur plugin is exported for all camera types. 
            In case of a physical or a dome camera, only some of its members are used
            by VRay but are safe to be exported. 
        """
        assert (cameraObj is not None) and (cameraObj.type == 'CAMERA'), "Invalid camera object"

        plDesc = PluginDesc(Names.singletonPlugin("SettingsMotionBlur"), "SettingsMotionBlur")
        plDesc.vrayPropGroup = self.scene.vray.SettingsMotionBlur

        export_utils.exportPlugin(self, plDesc)


    def _exportBakeView(self, viewParams: ViewParams):
        plUVWChannelDesc = PluginDesc("bakeUvwGenChannel", "UVWGenChannel")
        plUVWChannelDesc.setAttribute("uvw_channel", self.vrayScene.BakeView.uv_channel)
        plUVWChannelDesc.setAttribute("uvw_transform",Matrix())

        pluginUVWChannel = export_utils.exportPlugin(self, plUVWChannelDesc)

        activeBakeItem: VRayBatchBakeItem = self.vrayScene.BatchBake.active_item
        assert activeBakeItem, "Active bake item not set"

        plDescBakeView = PluginDesc(Names.singletonPlugin("BakeView"), "BakeView")
        plDescBakeView.attrs = {
            "fov"         : viewParams.renderView.fov,
            "bake_node"   : AttrPlugin(Names.vrayNode(Names.object(activeBakeItem.ob))),
            "bake_uvwgen" : pluginUVWChannel,
            "dilation"    : activeBakeItem.dilation,
            "flip_derivs" : activeBakeItem.flip_derivs
        }

        plDescBakeView.vrayPropGroup = self.vrayScene.BakeView

        export_utils.exportPlugin(self, plDescBakeView)


    def _exportCameraStereoscopic(self, viewParams: ViewParams):
        pluginName = Names.singletonPlugin("VRayStereoscopicSettings")
        
        plDesc = PluginDesc(pluginName, "VRayStereoscopicSettings")
        plDesc.vrayPropGroup = self.vrayScene.VRayStereoscopicSettings
        
        export_utils.exportPlugin(self, plDesc)


    def _removeIncompatibleCameras(self, viewParams: ViewParams, prevViewParams: ViewParams, isRenderCamera: bool):
        """ If the type of camera has been changed, remove the plugins associated 
            with the old camera type. Used in interactive mode only.
        """
        assert self.interactive, "Camera type cannot change during non-interactive rendering"

        if viewParams.usePhysicalCamera == prevViewParams.usePhysicalCamera \
                and viewParams.useDomeCamera == prevViewParams.useDomeCamera:
            # No change from the current state, nothing more to do
            return
        
        # Generally, the enabling/disabling of cameras cannot be animated and are not applied correctly
        # to the IPR unless the plugins associated with them are recreated.
        vray.pluginRemove(self.renderer, Names.singletonPlugin("CameraDefault"))
        vray.pluginRemove(self.renderer, self._getCameraPluginUniqueName(prevViewParams.cameraObject, "CameraDome", isRenderCamera))
        vray.pluginRemove(self.renderer, self._getCameraPluginUniqueName(prevViewParams.cameraObject, "CameraPhysical", isRenderCamera))
        vray.pluginRemove(self.renderer, self._getCameraPluginUniqueName(prevViewParams.cameraObject, "SettingsCameraDof", isRenderCamera))
        
        # When the type of camera has changed (specifically when a dome camera is removed from the scene), 
        # VRay does not always apply the correct view settings until either SettingsOutput of RenderView plugins 
        # are re-exported. We are exporting SettingsOutput before everything else because the order of export for it
        # is important for some scenarions (e.g. stereoscopic cameras). That leaves us with RenderView to re-export
        vray.pluginRemove(self.renderer, self._getCameraPluginUniqueName(prevViewParams.cameraObject, "RenderView", isRenderCamera))


    def _checkForIncompatibleCameras(self, prevPerCameraViewParams: dict[str, ViewParams], perCameraViewParams: dict[str, ViewParams]):
        """ When animating a switch between cameras, all the cameras must be of the same type. """ 
        assert self.isAnimation

        vp  = next((vp for vp in perCameraViewParams.values() if vp.isActiveCamera), None)
        prevVp  = next((vp for vp in prevPerCameraViewParams.values() if vp.isActiveCamera), None)

        if (vp is None):
            raise Exception(f"No active camera defined in frame {self.currentFrame}")

        if (vp.usePhysicalCamera != prevVp.usePhysicalCamera) or (vp.useDomeCamera != prevVp.useDomeCamera):
            raise Exception(f"Camera defined in frame {self.currentFrame} incompatible with the previously defined camera.")


    def _isActiveCamera(self, cameraObj: bpy.types.Object):
        """ Return True if the camera is the currently selected camera"""
        return ct.isSameCamera(cameraObj, self.activeCamera)


    def _useCamera(self, cameraObj: bpy.types.Object):
        """ Return True if the camera should be used to render the scene """
        # Camera object isn't set to ViewParams when the default scene camera is used, or when 
        # a non-camera object is used as a camera  
        return (cameraObj is None) or self._isActiveCamera(cameraObj)


    def _getViewFromViewport(self):
        """ Create ViewParams for the currently active viewport. This may be 
            either the 'Camera' view, or the default leayout view.
        """
        viewParams = ViewParams()
        region3d = blender_utils.getRegion3D(self.ctx) if self.viewport else blender_utils.getFirstAvailableRegion3D()
        assert region3d, "No Region3D in the context"


        if region3d.view_perspective == 'CAMERA':

            if self.viewport:
                self._fillViewFromCameraViewport(viewParams)
            else:
                # Export the IPR view with prod camera settings
                viewParams = self._getProdViewParams(self.activeCamera)
        else:
            self._fillViewFromScene(viewParams, region3d)

        return viewParams


    def _getInteractiveViewParams(self):
        """ Return a ViewParams struct, filled from either the camera selected in the 
            camera view or, if no camera is selected, from the viewport settings.
        """
        
        viewParams = self._getViewFromViewport()
        viewParams.calcRenderSizes()
        
        return viewParams


    def _getProdViewParams(self, cameraObj: bpy.types.Object): 
        viewParams = ViewParams()
        
        if blender_utils.isCamera(cameraObj):
            viewParams.usePhysicalCamera = ct.isPhysicalCamera(cameraObj)
            viewParams.useDomeCamera = ct.isDomeCamera(cameraObj)
            viewParams.cameraParams.setFromObject(cameraObj)
            self._fillViewFromProdCamera(cameraObj, viewParams)
        else:
            self._fillViewFromObject(cameraObj, viewParams)
            viewParams.calcRenderSizes()

        return viewParams
    

    def _getBakeViewParams(self): 
        viewParams: ViewParams = self._getViewFromBakeSettings()
        viewParams.usePhysicalCamera = False
        viewParams.useDomeCamera = False
        viewParams.cameraObject = None

        return viewParams
    
    def _fillRenderSizeFromScene(self, viewParams: ViewParams):
        """ Fill ViewParams.renderSize, ViewParams.viewportW and ViewParams.viewportH 
            from the scene view settings, not from a specific camera object 
        """
        if self.viewport:
            region = self.ctx.region
            
            viewParams.renderSize.w = region.width
            viewParams.renderSize.h = region.height

            viewParams.viewportW = region.width
            viewParams.viewportH = region.height
        else:
            renderSettings = self.scene.render
            viewParams.renderSize.w = renderSettings.resolution_x * renderSettings.resolution_percentage / 100
            viewParams.renderSize.h = renderSettings.resolution_y * renderSettings.resolution_percentage / 100

            viewParams.viewportW = renderSettings.resolution_x
            viewParams.viewportH = renderSettings.resolution_y

    def _fillViewFromScene(self, viewParams: ViewParams, region3d):
        """ Fill ViewParams from the scene view settings, not from a specific camera object 
        """
        DEFAULT_SENSOR_WIDTH = 36.0 # This value is hardcoded in Blender
        VIEW_PERSPECTIVE_ORTHO = "ORTHO"

        sensorSize = DEFAULT_SENSOR_WIDTH
        view3d = self.view3D

        lens = view3d.lens / (2.0 if self.viewport else 1)

        self._fillRenderSizeFromScene(viewParams)

        isOrtho = (region3d.view_perspective == VIEW_PERSPECTIVE_ORTHO)

        if isOrtho:
            viewParams.renderView.ortho_width = region3d.view_distance * sensorSize / lens
        else:
            viewParams.renderView.fov = ct.fov(sensorSize, lens)
            viewParams.renderView.clip_start = view3d.clip_start
            viewParams.renderView.clip_end = view3d.clip_end

        ct.aspectCorrectForFovOrtho(viewParams)

        if view3d.use_render_border:
            # Only the region within the border should be rendered
            ct.setRegionBorder(viewParams, ct.getView3dRenderBorder(view3d))
            viewParams.crop = True

        viewParams.renderView.ortho = isOrtho
        viewParams.renderView.clipping = not isOrtho

        viewParams.renderView.tm = Matrix(region3d.view_matrix).inverted()
        
        # This param is only needed for checking if the view3d has valid camera
        viewParams.cameraObject =  view3d.camera
        viewParams.isCameraView = False


    def _fillViewFromObject(self, obj: bpy.types.Object, viewParams: ViewParams):
        """ Fill ViewParams from a look-through non-camera object

            @param obj - a non-camera object
            @param viewParams -    
        """
        ViewExporter._fillCameraDataFromNonCameraObject(obj, viewParams)

        viewParams.renderSize.w = self.scene.render.resolution_x
        viewParams.renderSize.h = self.scene.render.resolution_y

        viewParams.viewportW = self.scene.render.resolution_x
        viewParams.viewportH = self.scene.render.resolution_y

        viewParams.crop = False    
        viewParams.renderView.ortho = False
        viewParams.renderView.clipping = False

        # This param is only needed for checking if the view3d has valid camera
        viewParams.cameraObject =  obj

    def _fillCroppedCameraViewportRenderRegion(self, renderSettings, viewParams: ViewParams, rectView, v3d, region):
        """ Fills render region ViewParams for 'Camera' viewport view that's cropped
        """

        # Compute the camera view rectangle inside the view area. The calculated coordinates
        # will be used later by the drawing procedure to crop 
        r = renderSettings
        viewParams.cameraParams.setFromObject(v3d.camera)
        rectCamera = viewParams.cameraParams.computeViewplane(r.resolution_x, r.resolution_y, r.pixel_aspect_x, r.pixel_aspect_y)
        
        viewBorder = Rect()

        viewBorder.xmin = ((rectCamera.xmin - rectView.xmin) / rectView.width()) * region.width
        viewBorder.xmax = ((rectCamera.xmax - rectView.xmin) / rectView.width()) * region.width
        viewBorder.ymin = ((rectCamera.ymin - rectView.ymin) / rectView.height()) * region.height
        viewBorder.ymax = ((rectCamera.ymax - rectView.ymin) / rectView.height()) * region.height

        # The border width is hardcoded and cannot be changed
        CAMERA_VIEW_BORDER = 2

        viewParams.viewportW = int(viewBorder.xmax - viewBorder.xmin) + CAMERA_VIEW_BORDER
        viewParams.viewportH = int(viewBorder.ymax - viewBorder.ymin) + CAMERA_VIEW_BORDER
        viewParams.viewportOffsX = int(viewBorder.xmin)
        viewParams.viewportOffsY = int(viewBorder.ymin)

        
        # There is a crop region defined. Render only what is inside the camera view rect.
        ct.setRegionBorder(viewParams, ct.getRenderSettingsBorder(renderSettings))

        viewParams.renderSize.w = viewParams.viewportW
        viewParams.renderSize.h = viewParams.viewportH

        # The fov needs to be adjusted renderSize.h > renderSize.w 
        aspect = viewParams.renderSize.w / viewParams.renderSize.h
        if aspect < 1.0:
            viewParams.renderView.fov = 2 * math.atan(math.tan(viewParams.renderView.fov/2.0) * aspect)


    def _fillCameraViewportRenderRegion(self, viewParams: ViewParams, v3dParams, rectView, rv3d, region):
        """ Fills render region ViewParams for 'Camera' viewport view
        """
        # Maximum view zoom factor (the biggest value generated by screenView3dZoomToFac(rv3d.view_camera_zoom))
        MAX_VIEW_3D_ZOOM_FACTOR = 6.03369

        if not viewParams.renderView.ortho and self.interactive:
            # Recompute camera FOV so that the new view encompassed the whole viewport area, but the 
            # camera view rectangle still showed exactly what the original camera would see 
            fov = ct.fov(sensorSize=rectView.width(), focalDist=v3dParams.clip_start/VISIBLE_TO_WHOLE_VIEW_RATIO)
            if fov > 0.0:
                viewParams.renderView.fov = fov
        else:
            # RenderViewParams.ortho_width is a scale factor and is populated from
            # and equal to CameraParams.orthoScale
            viewParams.renderView.ortho_width *= v3dParams.zoom

            if viewParams.cameraObject:
                # Multiplying WHOLE_VIEW_SIZE_FACTOR in Camera View in order to compensate for the different field of view
                viewParams.renderView.ortho_width *= VISIBLE_TO_WHOLE_VIEW_RATIO

        # region size
        viewParams.regionSize.w = region.width
        viewParams.regionSize.h = region.height

        # render size
        viewParams.renderSize.w = region.width * VISIBLE_TO_WHOLE_VIEW_RATIO
        viewParams.renderSize.h = region.height * VISIBLE_TO_WHOLE_VIEW_RATIO

        # When rendering in camera viewport mode, Blender visualizes only part of the 3D viewport region.
        # We want to set the render size to match the entire region, because users may drag the visible 
        # area, causing the render result to become misaligned. To address this, we export the whole 3D 
        # viewport as viewParams.renderSize and render only the visible area as viewParams.regionSize, 
        # adjusting the start position with the offset from the dragging (rv3d.view_camera_offset).
        #
        #    _____whole_3D_viewport_region__
        #   |    ____________               | A 
        #   |   |            | A            | |
        #   |   | region     | |            | |
        #   |   | visible in | regionSize.h | |
        #   |   | Blender    | |            | renderSize.h
        #   |   |____________| V            | |
        #   |    <---------->               | |
        #   |    regionSize.W               | |
        #   |_______________________________| V
        #    <--------renderSize.w---------->
        #
        # info about "renderSize" and "regionSize": https://git.chaosgroup.com/core/cgrepo/-/blob/master/vraysl/vray_Settings/settings_output.cpp#L11

        zoomFactor = MAX_VIEW_3D_ZOOM_FACTOR / v3dParams.zoom # How much the view has been zoomed

        def getRegionStartPos(regionSideLength, cameraOffset, flipOffsetDir=False):
            startPos = (regionSideLength * VISIBLE_TO_WHOLE_VIEW_RATIO - regionSideLength) / 2 # Render region start position
            offset = startPos * cameraOffset * zoomFactor # Offset of the visible region with applied zoom
            
            # The Y axis in V-Ray is opposite to Blender's, so the offset direction should be flipped when setting viewParams.regionStart.h
            return startPos + offset * (-1 if flipOffsetDir else 1)
        
        viewParams.regionStart.w = getRegionStartPos(region.width, rv3d.view_camera_offset[0])
        viewParams.regionStart.h = getRegionStartPos( region.height, rv3d.view_camera_offset[1], True)

        viewParams.crop = True
        viewParams.canDrawWithOffset = False

        

    def _fillViewFromCameraViewport(self, viewParams: ViewParams):
        """ Fill ViewParams for the 'Camera' viewport view. This is different from
            the camera for the production view, because we need to render a larger
            area with the camera view in a 'box'
        """
        scene: bpy.types.Scene = self.scene
        region: bpy.types.Region = self.ctx.region
        v3d = self.view3D
        rv3d = blender_utils.getRegion3D(self.ctx)
        cameraObject: bpy.types.Camera = getActiveCamera(self)

        if not cameraObject:
            debug.printError("Viewport camera not found")
            return

        # Viewport camera shows the camera view as a rectangle inside the whole area view.
        # If there is an active crop region, everything outside the camera view rect will be empty.
        # If there is no crop region, the part outside the camera vew rect should show the rest of the scene
        # which would otherwise be invisible to the camera.
        
        # Below, we compute the view parameters needed to match the actual camera view
        # to the camera view rectangle
        
        # Compute the camera params for rendering the whole viewport area
        viewParams.cameraParams.setFromView3d(self.dg, v3d, rv3d)
        v3dParams = viewParams.cameraParams
        rectView = v3dParams.computeViewplane(region.width, region.height, 1.0, 1.0)

        # Set calculated view parameters to the camera
        if cameraObject.type == 'CAMERA':
            viewParams.usePhysicalCamera = ct.isPhysicalCamera(cameraObject)
            viewParams.useDomeCamera = ct.isDomeCamera(cameraObject)
            self._fillCameraData(cameraObject, viewParams)
        else:
            # Blender allows any object to act as a camera. For some object types like spot lights
            # the notion of a camera is straightforward. For the rest, the fake camera is positioned
            # at the object position and looks down object's local z-axis
            ViewExporter._fillCameraDataFromNonCameraObject(cameraObject, viewParams)

        if scene.render.use_border:
            self._fillCroppedCameraViewportRenderRegion(scene.render, viewParams, rectView, v3d, region)
        else:
            self._fillCameraViewportRenderRegion(viewParams, v3dParams, rectView, rv3d, region)


    def _fillViewFromProdCamera(self, cameraObject: bpy.types.Object, viewParams: ViewParams):
        """ Get view parameters for the production camera. It is different from the viewport 'Camera' view,
            this is why fillViewFromCamera cannot be used
        """
        camera: bpy.types.Camera = cameraObject.data
        if not camera:
            return

        renderSettings: bpy.types.RenderSettings = self.scene.render

        viewParams.renderSize.w = renderSettings.resolution_x * renderSettings.resolution_percentage / 100
        viewParams.renderSize.h = renderSettings.resolution_y * renderSettings.resolution_percentage / 100

        if renderSettings.use_border:
            viewParams.crop = True

            viewParams.regionStart.w = renderSettings.border_min_x * viewParams.renderSize.w
            viewParams.regionStart.h = viewParams.renderSize.h - renderSettings.border_max_y * viewParams.renderSize.h

            viewParams.regionSize.w = (renderSettings.border_max_x - renderSettings.border_min_x) * viewParams.renderSize.w
            viewParams.regionSize.h = (renderSettings.border_max_y - renderSettings.border_min_y) * viewParams.renderSize.h

        else:
            viewParams.regionSize = viewParams.regionStart = Size()
            viewParams.crop = False
        
        viewParams.calcRenderSizes()

        self._fillCameraData(cameraObject, viewParams)
        ct.aspectCorrectForFovOrtho(viewParams)

        return viewParams


    def _getViewFromBakeSettings(self):
        assert self.bake, "This method should only be called in bake mode"""
        activeItem = self.vrayScene.BatchBake.active_item

        viewParams = ViewParams()
        viewParams.renderSize.w = activeItem.width
        viewParams.renderSize.h = activeItem.height
        viewParams.regionSize = viewParams.regionStart = Size()
        viewParams.crop = False

        viewParams.calcRenderSizes()
        return viewParams
    

    def _fillPhysicalCameraSettings(self, plDesc: PluginDesc, viewParams: ViewParams):
        """ Returns an 'overrides' dictionary with physical camera settings
        """
        assert(viewParams.usePhysicalCamera)
        assert(viewParams.cameraObject)

        camera: bpy.types.Camera = viewParams.cameraObject.data
        
        vrayCamera = camera.vray
        physicalCamera = vrayCamera.CameraPhysical
        cameraParams = viewParams.cameraParams

        lensShift = blender_utils.getLensShift(viewParams.cameraObject) if physicalCamera.auto_lens_shift else physicalCamera.lens_shift

        # Orthographic view doesn't work with DoF and for that "focus_distance"
        # is calculated only when its disabled.
        focusDist = 1
        if not viewParams.renderView.ortho:
            focusDist = ct.getCameraDofDistance(viewParams.cameraObject)
            
            if self.viewport:
                # In viewport rendering, 'fov' includes a zoom factor.  
                # Adjust the focus distance based on the ratio of the production FOV to the viewport FOV  
                # to ensure the depth of field matches the one generated by production render.
                prodFov = ct.fov(cameraParams.sensorX, cameraParams.lens)
                focusDist *= prodFov / viewParams.renderView.fov

        filmWidth = physicalCamera.film_width # The same as cameraParams.sensorX/Y and camera.sensor_width/height
        if self.viewport: # Applying viewport corrections
            if not self.scene.render.use_border: # Applying whole size factor when camera border is disabled.

                # Get the ratio between viewport and camera view rectangle sizes to adjust the film width
                # This ensures the viewport camera view matches the production render camera view
                cameraToViewportRatio = _getCameraToViewportRatio(viewParams, self.ctx.region, self.scene.render)

                filmWidth *= VISIBLE_TO_WHOLE_VIEW_RATIO * cameraToViewportRatio

            filmWidth *= cameraParams.zoom # Applying the viewport zoom factor.

        plDesc.setAttributes({
            # Matching the FOV calculated by V-Ray with Blender's is really difficult.
            # To overcome this, we use Blender's FOV during viewport rendering by setting specify_fov to True
            # to prevent V-Ray from calculating own FOV value.
            "specify_fov"        : True if self.viewport else physicalCamera.specify_fov,

            # FOV may have been recomputed for viewport camera view, don't take it verbatim from the UI.
            # In contrast, focal_length is never recomputed
            "fov"                : viewParams.renderView.fov,
            "film_width"         : filmWidth,
            "lens_shift"         : lensShift,
            "focus_distance"     : focusDist
        })


    def _fillCameraData(self, cameraObject: bpy.types.Object, viewParams: ViewParams):
        camera: bpy.types.Camera = cameraObject.data

        vrayCamera = camera.vray
        renderView = vrayCamera.RenderView
        settingsCamera = vrayCamera.SettingsCamera

        fov = settingsCamera.fov if settingsCamera.override_camera_settings and settingsCamera.override_fov else camera.angle
        viewParams.renderView.fov = fov

        viewParams.renderView.ortho = isOrthographicCamera(camera)
        viewParams.renderView.ortho_width = camera.ortho_scale if viewParams.renderView.ortho else 1

        viewParams.renderView.clipping = renderView.clipping
        viewParams.renderView.clip_start = camera.clip_start
        viewParams.renderView.clip_end   = camera.clip_end

        viewParams.renderView.tm = cameraObject.matrix_world
        viewParams.cameraObject = cameraObject


    @staticmethod
    def _fillCameraDataFromNonCameraObject(obj, viewParams: ViewParams):
        """ Fill parameters for an object that is not a camera but has some camera-like settings ( e.g. cone light )
        """
        assert not isCamera(obj)

        viewParams.cameraParams.setFromObject(obj)
        viewParams.renderView.fov = ct.fov(viewParams.cameraParams.sensorX, viewParams.cameraParams.lens)

        tm = obj.matrix_world.copy()

        if obj.type == 'LIGHT' and obj.data.type == 'AREA':
            # When looking through a V-Ray  Rect light, the camera is z-fighting with the light gizmo
            # and may render the gizmo itself. Move the camera along its forward axis by an epsilon
            # in order to prevent the z-fight.
            tm = ct.fixLookThroughObjectCameraZFight(tm)

        viewParams.renderView.tm = tm
        Matrix(viewParams.renderView.tm).normalize()
        viewParams.cameraObject = obj
        viewParams.usePhysicalCamera = False
        viewParams.useDomeCamera = False


    def _getCameraPluginUniqueName(self, cameraObj, pluginType, isRenderCamera: bool):
        """ Return plugin name depending on whether we are exporting one (interactive) 
            or multilpe (prod render) cameras. In the former case, use a fixed name. 
            Otherwise create a camera-specific name
        """

        if (cameraObj is not None) and (not self.interactive):
            cameraName = self._getCameraBaseName(cameraObj, isRenderCamera)
            return Names.pluginObject(pluginType, cameraName)
        else:
            return Names.singletonPlugin(pluginType)


    def _getCameraBaseName(self, cameraObj, isRenderCamera: bool):
        # Non-camera object may be exported as a cameras only when it is the active selected camera, hence
        # the current render camera.
        return RENDER_CAMERA_BASE_NAME if isRenderCamera or not isCamera(cameraObj) else Names.object(cameraObj)
        

def run(ctx: ExporterContext):
    return ViewExporter(ctx).export()