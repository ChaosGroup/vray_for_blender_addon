from __future__ import annotations
import bpy
from mathutils import Matrix


from vray_blender import debug
from vray_blender.lib import export_utils, blender_utils
from vray_blender.lib.camera_utils import CameraParams, ViewParams, Rect, Size
from vray_blender.lib import camera_utils as ct
from vray_blender.lib.defs import AttrPlugin, ExporterContext, ExporterBase, PluginDesc
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
_RENDER_CAMERA_BASE_NAME = 'renderCamera'

def getActiveCamera(ctx: ExporterContext):
    """ Return the view-local camera, if active. Otherwise return the scene camera. 
        NOTE: The view camera is not necessarily of type Camera, may be any object.
    """
    view3D = blender_utils.getSpaceView3D(ctx.ctx) if ctx.viewport else None

    if view3D and (view3dCamera := view3D.camera):
        return view3dCamera
    
    return ctx.dg.scene.camera


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
        assert self.production or self.preview, "Method should only be called for prod and preview renders"

        # Export all cameras in the scene. This is necessary for subsequent renderings with V-Ray Standalone
        # where the user can select which one of the exported cameras to use.
        cameras = list([inst.object for inst in self.dg.object_instances if inst.object.type == 'CAMERA'])
        
        # If an object other than a camera has been set as the active camera, add it to the cameras' list.
        # A camera with suitable parameters will be exported for it.
        if self.activeCamera and (not blender_utils.isCamera(self.activeCamera)):
            cameras.append(self.activeCamera)

        perCameraViewParams: dict[str, ViewParams] = {}

        for cameraObj in cameras:
            cameraName = cameraObj.name_full
            viewParams = prevPerCameraViewParams.get(cameraName)
            perCameraViewParams[cameraName] = self._exportProdCamera(cameraObj, viewParams)
        
        if self.isAnimation and prevPerCameraViewParams:
            self._checkForIncompatibleCameras(prevPerCameraViewParams, perCameraViewParams)

        return perCameraViewParams
    

    def exportViewportView(self, view3D: bpy.types.SpaceView3D, prevViewParams: ViewParams = None):
        assert self.interactive, "Method should only be called for viewport renders"
        assert view3D, "view3D parameter should not be empty"
        self.view3D = view3D

        viewParams: ViewParams = self._getInteractiveViewParams()

        # Some plugins depend on SettingsOutput and have to be exported AFTER it, so export
        # it before anything else
        SettingsOutputExporter(self, viewParams, prevViewParams).export()

        cameraObj = viewParams.cameraObject
        self._exportCamera(cameraObj, viewParams, prevViewParams, isRenderCamera=True)
        
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
            self._exportCamera(viewParams.cameraObject, viewParams, prevViewParams, isRenderCamera=True)
        
        # Regardless of whether this is the active render camera, export current camera settings in its own 
        # set of plugins and mark them as inactive (dont_affect_settings = True). This will allow the camera 
        # to be selected as the render camera in V-Ray Standalone. 
        self._exportCamera(viewParams.cameraObject, viewParams, prevViewParams, isRenderCamera=False)

        return viewParams


    def _exportCamera(self, cameraObj: bpy.types.Object, viewParams: ViewParams, prevViewParams: ViewParams|None, isRenderCamera: bool):
        """ Export a scene object as a camera.

            Along with the camera objects, Blender allows to export any scene object as a camera. Such a camera
            is positioned at the object's coordinates and is looking at the coordinate system origin. 
        """
        if self.interactive and prevViewParams:
            self._removeIncompatibleCameras(viewParams, prevViewParams, isRenderCamera)
        

        if blender_utils.isCamera(cameraObj):
            if viewParams.usePhysicalCamera:
                self._exportCameraPhysical(viewParams, isRenderCamera)
            elif viewParams.useDomeCamera:
                self._exportCameraDome(viewParams, isRenderCamera)
            else:
                if not viewParams.renderView.ortho:
                    self._exportCameraDof(cameraObj, isRenderCamera)
                
                # The CameraDefault plugin cannot be set per-camera, so use the settings of the active camera
                if self._isActiveCamera(viewParams.cameraObject):
                    self._exportCameraDefault(viewParams)

            # The below plugins are valid for any camera type
            self._exportSettingsCamera(cameraObj, isRenderCamera)
            self._exportSettingsMotionBlur(cameraObj)

        self._exportRenderView(viewParams, isRenderCamera)


    # Export RenderView plugin
    def _exportRenderView(self, viewParams: ViewParams, isRenderCamera: bool):
        pluginName = self._getCameraPluginUniqueName(viewParams.cameraObject, "RenderView", isRenderCamera)
        viewDesc = PluginDesc(pluginName, "RenderView")

        viewDesc.setAttribute("transform", viewParams.renderView.tm)
        viewDesc.setAttribute("fov", viewParams.renderView.fov)
        viewDesc.setAttribute("clipping", viewParams.renderView.use_clip_start or viewParams.renderView.use_clip_end)
        viewDesc.setAttribute("clipping_near", viewParams.renderView.clip_start)
        viewDesc.setAttribute("clipping_far", viewParams.renderView.clip_end)
        viewDesc.setAttribute("orthographic", viewParams.renderView.ortho)
        viewDesc.setAttribute("orthographicWidth", viewParams.renderView.ortho_width)

        if blender_utils.isCamera(viewParams.cameraObject):
            viewDesc.setAttribute("focalDistance", ct.getCameraDofDistance(viewParams.cameraObject))
            viewDesc.setAttribute("scene_name", self._getCameraBaseName(viewParams.cameraObject, isRenderCamera))
        else:
            viewDesc.setAttribute("scene_name", None)
        
        viewDesc.setAttribute("use_scene_offset", not self.interactive)
        viewDesc.setAttribute("dont_affect_settings", not isRenderCamera)

        export_utils.exportPlugin(self, viewDesc)

        # Export stereoscopic cameras only for production renderings. Currently, we can't show
        # both cameras in the same view, as anaglyph mode is not implemented in VRay
        if not self.interactive:
            if self.vrayScene.VRayStereoscopicSettings.use:
                self._exportCameraStereoscopic(viewParams)
            

    def _exportSettingsCamera(self, cameraObj: bpy.types.Camera, isRenderCamera: bool):
        assert (cameraObj is not None) and (cameraObj.type == 'CAMERA'), "Invalid camera object"

        vrayCamera = cameraObj.data.vray
        pluginName = self._getCameraPluginUniqueName(cameraObj, "SettingsCamera", isRenderCamera)

        cameraType = vrayCamera.SettingsCamera.type
        if cameraObj.data.type == 'ORTHO':
            cameraType = 7

        settingsCameraGlobal = self.scene.vray.SettingsCameraGlobal
        plDesc = PluginDesc(pluginName, "SettingsCamera")
        plDesc.vrayPropGroup = vrayCamera.SettingsCamera

        plDesc.setAttributes({
            'scene_name': self._getCameraBaseName(cameraObj, isRenderCamera),
            'dont_affect_settings': not isRenderCamera,
            'fov'    : -1,
            'type'   : cameraType,
            'height' : cameraObj.data.ortho_scale,
            "auto_exposure": settingsCameraGlobal.auto_exposure,
            "auto_corrections_mode": settingsCameraGlobal.auto_corrections_mode,
            "auto_white_balance": "2" if settingsCameraGlobal.auto_white_balance else "0"
        })

        export_utils.exportPlugin(self, plDesc)
        self.cameraTracker.trackPlugin(getObjTrackId(cameraObj), pluginName)
        

    def _exportCameraDefault(self, viewParams: ViewParams):
        # This plugin can only be used as a singleton, hence the fixed name
        pluginName = Names.singletonPlugin("CameraDefault")
        
        plDesc = PluginDesc(pluginName, "CameraDefault")
        plDesc.setAttribute("orthographic", viewParams.renderView.ortho)

        export_utils.exportPlugin(self, plDesc)


    def _exportCameraPhysical(self, viewParams: ViewParams, isRenderCamera: bool):
        assert (viewParams.cameraObject is not None) and (viewParams.cameraObject.type == 'CAMERA'), "ViewParams should have a valid camera object"

        pluginType = "CameraPhysical"
        pluginName = self._getCameraPluginUniqueName(viewParams.cameraObject, pluginType, isRenderCamera)
        
        plDesc = PluginDesc(pluginName, pluginType)
        self._fillPhysicalCameraSettings(plDesc, viewParams)
        plDesc.setAttribute("scene_name", self._getCameraBaseName(viewParams.cameraObject, isRenderCamera))
        plDesc.setAttribute("dont_affect_settings", not isRenderCamera)
        plDesc.vrayPropGroup = viewParams.cameraObject.data.vray.CameraPhysical
        
        export_utils.exportPlugin(self, plDesc)
        self.cameraTracker.trackPlugin(getObjTrackId(viewParams.cameraObject), pluginName)
    

    def _exportCameraDome(self, viewParams: ViewParams, isRenderCamera: bool):
        assert (viewParams.cameraObject is not None) and (viewParams.cameraObject.type == 'CAMERA'), "ViewParams should have a valid camera object"

        pluginType = "CameraDome"
        pluginName = self._getCameraPluginUniqueName(viewParams.cameraObject, pluginType, isRenderCamera)
        
        plDesc = PluginDesc(pluginName, pluginType)
        plDesc.setAttribute("fov", viewParams.cameraObject.data.vray.fov)
        plDesc.setAttribute("scene_name", self._getCameraBaseName(viewParams.cameraObject, isRenderCamera))
        plDesc.setAttribute("dont_affect_settings", not isRenderCamera)
        plDesc.vrayPropGroup = viewParams.cameraObject.data.vray.CameraDome
        
        export_utils.exportPlugin(self, plDesc)
        self.cameraTracker.trackPlugin(getObjTrackId(viewParams.cameraObject), pluginName)


    def _exportCameraDof(self, cameraObj: bpy.types.Camera, isRenderCamera: bool):
        """ SettingsCameraDof plugin is only used with the default camera. Physical 
            cameras define their own DoF flag.
        """
        assert (cameraObj is not None) and (cameraObj.type == 'CAMERA'), "Invalid camera object"

        pluginName = self._getCameraPluginUniqueName(cameraObj, "SettingsCameraDof", isRenderCamera)
        
        plDesc = PluginDesc(pluginName, "SettingsCameraDof")
        plDesc.setAttributes({
            'scene_name': self._getCameraBaseName(cameraObj, isRenderCamera),
            'dont_affect_settings' : not isRenderCamera,
            'focal_dist': ct.getCameraDofDistance(cameraObj)
        })

        plDesc.vrayPropGroup = cameraObj.data.vray.SettingsCameraDof
        
        export_utils.exportPlugin(self, plDesc)
        self.cameraTracker.trackPlugin(getObjTrackId(cameraObj), pluginName)
    

    def _exportSettingsMotionBlur(self, cameraObj: bpy.types.Camera):
        """ SettingsMotionBlur plugin is exported for all camera types. 
            In case of a physical or a dome camera, only some of its members are used
            by VRay but are safe to be exported. 
        """
        assert (cameraObj is not None) and (cameraObj.type == 'CAMERA'), "Invalid camera object"
        
        plDesc = PluginDesc(Names.singletonPlugin("SettingsMotionBlur"), "SettingsMotionBlur")
        plDesc.vrayPropGroup = cameraObj.data.vray.SettingsMotionBlur

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
        
        # - CameraDefault, CameraPhysical and CameraDome are mutially exclusive
        # - DOF is not compatible with CameraPhysical
        if viewParams.usePhysicalCamera:
            vray.pluginRemove(self.renderer, Names.singletonPlugin("CameraDefault"))
            vray.pluginRemove(self.renderer, self._getCameraPluginUniqueName(prevViewParams.cameraObject, "SettingsCameraDof", isRenderCamera))
            vray.pluginRemove(self.renderer, self._getCameraPluginUniqueName(prevViewParams.cameraObject, "CameraDome", isRenderCamera))
        elif viewParams.useDomeCamera:
            vray.pluginRemove(self.renderer, Names.singletonPlugin("CameraDefault"))
            vray.pluginRemove(self.renderer, self._getCameraPluginUniqueName(prevViewParams.cameraObject, "CameraPhysical", isRenderCamera))
        else:
            vray.pluginRemove(self.renderer, self._getCameraPluginUniqueName(prevViewParams.cameraObject, "CameraPhysical", isRenderCamera))
            vray.pluginRemove(self.renderer, self._getCameraPluginUniqueName(prevViewParams.cameraObject, "CameraDome", isRenderCamera))

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
            self._fillViewFromProdCamera(cameraObj, viewParams)
            viewParams.usePhysicalCamera = ct.isPhysicalCamera(cameraObj)
            viewParams.useDomeCamera = ct.isDomeCamera(cameraObj)
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
        viewParams.renderView.use_clip_start = not isOrtho
        viewParams.renderView.use_clip_end   = not isOrtho

        viewParams.renderView.tm = Matrix(region3d.view_matrix).inverted()
        
        # This param is only needed for checking if the view3d has valid camera
        viewParams.cameraObject =  view3d.camera

    def _fillViewFromObject(self, obj: bpy.types.Object, viewParams: ViewParams):
        """ Fill ViewParams from a look-through non-camera object

            @param obj - a non-camera object
            @param viewParams -    
        """
        ViewExporter._fillCameraDataFromObject(obj, viewParams)

        viewParams.renderSize.w = self.scene.render.resolution_x
        viewParams.renderSize.h = self.scene.render.resolution_y

        viewParams.viewportW = self.scene.render.resolution_x
        viewParams.viewportH = self.scene.render.resolution_y

        viewParams.crop = False    
        viewParams.renderView.ortho = False
        viewParams.renderView.use_clip_start = False
        viewParams.renderView.use_clip_end   = False

        # This param is only needed for checking if the view3d has valid camera
        viewParams.cameraObject =  obj

    def _fillCroppedCameraViewportRenderRegion(self, renderSettings, viewParams: ViewParams, rectView, v3d, region):
        """ Fills render region ViewParams for 'Camera' viewport view that's cropped
        """

        # Compute the camera view rectangle inside the view area. The calculated coordinates
        # will be used later by the drawing procedure to crop 
        r = renderSettings
        camParams = CameraParams()
        camParams.setFromObject(v3d.camera)
        rectCamera = camParams.computeViewplane(r.resolution_x, r.resolution_y, r.pixel_aspect_x, r.pixel_aspect_y)
        
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


    def _fillCameraViewportRenderRegion(self, viewParams: ViewParams, v3dParams, rectView, rv3d, region):
        """ Fills render region ViewParams for 'Camera' viewport view
        """
        # The ratio of the whole view to the visible area. Its calculated by comparing
        # the size of the Blender 3D viewport in pixels to its size in "camera mode" (also in pixels).
        WHOLE_VIEW_SIZE_FACTOR = 1.66257 

        # Maximum view zoom factor (the biggest value generated by screenView3dZoomToFac(rv3d.view_camera_zoom))
        MAX_VIEW_3D_ZOOM_FACTOR = 6.03369

        if not viewParams.renderView.ortho and self.interactive:
            # Recompute camera FOV so that the new view encompassed the whole viewport area, but the 
            # camera view rectangle still showed exactly what the original camera would see 
            viewParams.renderView.fov = ct.fov(sensorSize=rectView.width(), focalDist=v3dParams.clip_start/WHOLE_VIEW_SIZE_FACTOR)
        else:
            # RenderViewParams.ortho_width is a scale factor and is populated from
            # and equal to CameraParams.ortho_scale
            viewParams.renderView.ortho_width *= v3dParams.zoom

        # region size
        viewParams.regionSize.w = region.width
        viewParams.regionSize.h = region.height

        # render size
        viewParams.renderSize.w = region.width * WHOLE_VIEW_SIZE_FACTOR
        viewParams.renderSize.h = region.height * WHOLE_VIEW_SIZE_FACTOR

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
            startPos = (regionSideLength * WHOLE_VIEW_SIZE_FACTOR - regionSideLength) / 2 # Render region start position
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
        v3dParams = CameraParams()
        v3dParams.setFromView3d(self.dg, v3d, rv3d)
        rectView = v3dParams.computeViewplane(region.width, region.height, 1.0, 1.0)

        # Set calculated view parameters to the camera
        if cameraObject.type == 'CAMERA':
            self._fillCameraData(cameraObject, viewParams)
            viewParams.usePhysicalCamera = ct.isPhysicalCamera(cameraObject)
            viewParams.useDomeCamera = ct.isDomeCamera(cameraObject)
        else:
            # Blender allows any object to act as a camera. For some object types like spot lights
            # the notion of a camera is straightforward. For the rest, the fake camera is positioned
            # at the object position and looks down object's local z-axis
            ViewExporter._fillCameraDataFromObject(cameraObject, viewParams)

        #viewParams.crop = self.scene.render.use_crop_to_border
        if scene.render.use_border:
            self._fillCroppedCameraViewportRenderRegion(scene.render, viewParams, rectView, v3d, region)
        else:
            self._fillCameraViewportRenderRegion(viewParams, v3dParams, rectView, rv3d, region)


    def _fillViewFromProdCamera(self, cameraObject, viewParams):
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
            viewParams.crop = renderSettings.use_crop_to_border

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

        aspect = float(viewParams.renderSize.w) / float(viewParams.renderSize.h)

        horizontalOffset = -camera.shift_x
        verticalOffset   = -camera.shift_y
        if aspect < 1.0:
            offsetFix   = 1.0 / aspect
            horizontalOffset *= offsetFix
            verticalOffset *= offsetFix

        lensShift = blender_utils.getLensShift(viewParams.cameraObject) if physicalCamera.auto_lens_shift else physicalCamera.lens_shift

        focusDistance = ct.getCameraDofDistance(viewParams.cameraObject)
        if focusDistance < 0.001:
            focusDistance = 5.0

        plDesc.setAttributes({
            # Always use the FOV setting for interactive renders, so that the calculations 
            # for the physical and non-physical cameras viewport were the same
            "specify_fov"        : self.viewport or physicalCamera.specify_fov,
            
            # FOV may have been recomputed for viewport camera view, don't take it verbatim from the UI.
            # In contrast, focal_length is never recomputed
            "fov"                : physicalCamera.fov if physicalCamera.specify_fov else viewParams.renderView.fov,
            
            # Focus distance only makes sense when using DOF
            "specify_focus"      : physicalCamera.use_dof,
            "focus_distance"     : focusDistance,

            "horizontal_offset"  : horizontalOffset,
            "vertical_offset"    : verticalOffset,
            "lens_shift"         : lensShift
        })


    def _fillCameraData(self, cameraObject, viewParams: ViewParams):
        cameraData: bpy.types.Camera = cameraObject.data

        vrayCamera = cameraData.vray
        renderView = vrayCamera.RenderView

        viewParams.renderView.fov = vrayCamera.fov if vrayCamera.override_fov else cameraData.angle 

        viewParams.renderView.ortho = (cameraData.type == 'ORTHO')
        viewParams.renderView.ortho_width = cameraData.ortho_scale

        viewParams.renderView.use_clip_start = renderView.clip_near
        viewParams.renderView.use_clip_end   = renderView.clip_far

        viewParams.renderView.clip_start = cameraData.clip_start
        viewParams.renderView.clip_end   = cameraData.clip_end

        viewParams.renderView.tm = cameraObject.matrix_world
        Matrix(viewParams.renderView.tm).normalize()
        viewParams.cameraObject = cameraObject
        


    @staticmethod
    def _fillCameraDataFromObject(obj, viewParams: ViewParams):
        """ Fill parameters for an object that is not a camera but has some camera-like settings ( e.g. cone light )
        """
        cameraParams = CameraParams()

        cameraParams.setFromObject(obj)
        viewParams.renderView.fov = ct.fov(cameraParams.sensorX, cameraParams.lens)
        
        tm = obj.matrix_world.copy()
        
        if obj.type == 'LIGHT' and obj.data.type == 'AREA':
            # When looking through a V-Ray  Rect light, the camera is z-fighting with the light gizmo
            # and may render the gizmo itself. Move the camera along its forward axis by an epsilon
            # in order to prevent the z-fight.
            tm = ct.fixLookThroughObjectCameraZFight(tm)
            
        viewParams.renderView.tm = tm
        Matrix(viewParams.renderView.tm).normalize()
        viewParams.cameraObject = None
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
        return _RENDER_CAMERA_BASE_NAME if isRenderCamera else Names.object(cameraObj)

def run(ctx: ExporterContext):
    return ViewExporter(ctx).export()