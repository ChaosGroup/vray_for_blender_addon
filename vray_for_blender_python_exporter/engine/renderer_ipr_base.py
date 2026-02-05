import bpy

from vray_blender.engine import NODE_TRACKERS, OBJ_TRACKERS

from vray_blender import debug
from vray_blender.lib import gl_draw
from vray_blender.lib.common_settings import CommonSettings
from vray_blender.lib.camera_utils import ViewParams
from vray_blender.lib.defs import (IprContext, ExporterContext, RendererMode, AttrPlugin, PersistedState, 
                                    RenderMaskState, IprContext, getObjTrackId, SceneStats)
from vray_blender.lib.names import syncObjectUniqueName, syncUniqueNames, Names
from vray_blender.lib.plugin_utils import updateValue, objectToAttrPlugin, stringToIntList
from vray_blender.exporting.instancer_export import InstancerExporter
from vray_blender.exporting.plugin_tracker import ObjTracker, ScopedNodeTracker
from vray_blender.exporting.settings_export import SettingsExporter
from vray_blender.exporting.tools import isObjectVrayProxy, isObjectVrayScene
from vray_blender.exporting.update_tracker import UpdateTracker, UpdateTarget
from vray_blender.exporting import tools, obj_export, mtl_export, view_export, settings_export, light_export, world_export, fur_export
from vray_blender.nodes.filters import filterRenderMasks
from vray_blender.plugins.system.compute_devices import updateEnabledComputeDevices
from vray_blender.plugins.material.MtlDisplacement import checkForUpdatedMtlWithDisplacement
from vray_blender.plugins.settings.SettingsCameraGlobal import reportAutoSettingsWarning
from vray_blender.bin import VRayBlenderLib as vray


#############################
## Module free functions
#############################

def _exportObjects(ctx: ExporterContext):
    ctx.ts.timeThis("export_objects", lambda: obj_export.run(ctx))


def _exportMaterials(ctx: ExporterContext):
    stats: SceneStats = ctx.ts.timeThis("export_materials", lambda: mtl_export.run(ctx))
    ctx.stats.append(f"{'Materials:':<12} exported {stats.mtls} materials, {stats.plugins} plugins, {stats.attrs} attributes")


def _exportLights(ctx: ExporterContext):
    ctx.ts.timeThis("export_lights", lambda: light_export.LightExporter(ctx).export())


def _exportSettings(ctx: ExporterContext):
    # This method has been reworked to only export Settings
    settingsExporter = settings_export.SettingsExporter(ctx)
    stats: SceneStats = ctx.ts.timeThis("export_settings", lambda: settingsExporter.export())
    ctx.stats.append(f"{'Settings:':<12} exported {stats.plugins} plugins, {stats.attrs} attributes")

def exportViewportView(ctx: ExporterContext, prevViewParams: ViewParams, renderSizesOnly = False):
    return ctx.ts.timeThis("export_view", lambda: view_export.ViewExporter(ctx).exportViewportView(prevViewParams, renderSizesOnly))

def _exportWorld(ctx: ExporterContext):
    world_export.WorldExporter(ctx).export()


def _prunePlugins(self, exporterCtx: ExporterContext):
    """ Remove plugins that are no longer referenced from V-Ray """

    def impl(context):
        obj_export.GeometryExporter(context).prunePlugins()
        mtl_export.MtlExporter(context).prunePlugins()
        light_export.LightExporter(context).prunePlugins()
        view_export.ViewExporter(context).prunePlugins()
        world_export.WorldExporter(context).prunePlugins()
        InstancerExporter.pruneInstances(context)

        # Persist the new snaphot
        self.activeInstancers = exporterCtx.activeInstancers

         # Remove plugins that need to be recreated
        for pluginName in ExporterContext.pluginsToRecreate:
            vray.pluginRemove(exporterCtx.renderer, pluginName)

        ExporterContext.pluginsToRecreate = set()

    exporterCtx.ts.timeThis("prune_plugins", lambda: impl(exporterCtx))


def _syncPlugins(self, exporterCtx: ExporterContext):
    """ Perform synchronization of plugin data before starting an export """

    # Has to be called before syncObjVisibility for proper update of the visibility of the fur objects.
    fur_export.syncFurInfo(exporterCtx)

    # NOTE: Sync object visibility first because the rest of the operations
    # depend on it
    obj_export.GeometryExporter(exporterCtx).syncObjVisibility()

    # NOTE: Updates should tagged be BEFORE any export structures are calculated
    # for the current pass. Keep this at the beginnign of the function.
    UpdateTracker.tagCrossObjectUpdates(exporterCtx, bpy.data.materials, UpdateTarget.MATERIAL)
    UpdateTracker.tagCrossObjectUpdates(exporterCtx, bpy.data.lights, UpdateTarget.LIGHT)
    UpdateTracker.tagCrossObjectUpdates(exporterCtx, bpy.data.worlds, UpdateTarget.WORLD)

    light_export.syncLightMeshInfo(exporterCtx)
    mtl_export.syncMtlExportCache(exporterCtx)

    if exporterCtx.rendererMode == RendererMode.Interactive:
        # Only collect light mix info in VFB IPR mode. In viewport mode render channels
        # are not exported.
        light_export.collectLightMixInfo(exporterCtx)

    # NOTE: just to be safe, remove objects that have been processed but not in the current scene
    allObjectIds = { getObjTrackId(o) for o in exporterCtx.sceneObjects }
    for objTrackId in self.persistedState.processedObjects.difference(allObjectIds):
        self.persistedState.processedObjects.discard(objTrackId)

    # Force full scene material re-export to make sure all materials are properly overriden.
    viewLayer = exporterCtx.dg.view_layer
    
    if self.persistedState.materialOverrideMode != viewLayer.vray.material_override_mode or \
        self.persistedState.overrideMaterial != viewLayer.material_override:
        for mtl in bpy.data.materials:
            mtl.update_tag()
            UpdateTracker.tagMtlTopology(exporterCtx.ctx, mtl)
        self.persistedState.exportedMtls.clear()
        exporterCtx.exportedMtls.clear()

    # Check if the currently updated material contains a displacement node
    checkForUpdatedMtlWithDisplacement(exporterCtx)


def _persistState(self, exporterCtx: ExporterContext):
    self.persistedState.activeInstancers = exporterCtx.activeInstancers
    self.persistedState.activeGizmos = exporterCtx.activeGizmos
    self.persistedState.exportedMtls = exporterCtx.exportedMtls
    self.persistedState.activeFurInfo = exporterCtx.activeFurInfo
    self.persistedState.activeMeshLightsInfo = exporterCtx.activeMeshLightsInfo

    viewLayer = exporterCtx.dg.view_layer
    self.persistedState.materialOverrideMode = viewLayer.vray.material_override_mode
    self.persistedState.overrideMaterial = viewLayer.material_override


def _syncView(ctx: ExporterContext):
    commonSettings = ctx.commonSettings

    viewSettings = vray.ViewSettings()
    viewSettings.viewportImageType = commonSettings.viewportImageType
    viewSettings.viewportImageQuality = commonSettings.viewportImageQuality
    viewSettings.renderMode = commonSettings.renderMode
    viewSettings.vfbFlags = commonSettings.vfbFlags

    vray.syncViewSettings(ctx.renderer, viewSettings)


def _syncAnimFrame(self, exporterCtx: ExporterContext):
    """ Set the current animation frame to all plugins that might require it. """

    if self.lastExportedFrame == exporterCtx.currentFrame:
        # Synchronization is only necessary when the frame has changed
        return

    self.lastExportedFrame = exporterCtx.currentFrame

    settingsExporter = SettingsExporter(exporterCtx)
    settingsExporter.exportPlugin("SettingsCurrentFrame")

    for proxy in [o for o in exporterCtx.sceneObjects if isObjectVrayProxy(o)]:
        pluginName = Names.pluginObject("vrayproxy", Names.object(proxy))

        # Make a void change to a property of the plugin in order to force a re-export.
        # Without this, the proxy won't update its internal state to match the animation frame
        # set through SettingsCurrentCamera.
        geomMeshFile = proxy.data.vray.GeomMeshFile
        flipValue = 0 if geomMeshFile.anim_start != 0 else 1
        vray.pluginUpdateFloat(exporterCtx.renderer, pluginName, 'anim_start', flipValue)
        vray.pluginUpdateFloat(exporterCtx.renderer, pluginName, 'anim_start', geomMeshFile.anim_start)


def _getRenderMaskState(exporterCtx: ExporterContext):
    propGroup = exporterCtx.dg.scene.vray.SettingsImageSampler
    renderMaskData = []

    match propGroup.render_mask_mode:
        case '1': # Texture
            renderMaskData = [propGroup.render_mask_texture_file]

        case '2' | '4': # Object / Selected objects
            if propGroup.render_mask_mode == '2':
                renderMaskObjects = propGroup.object_selector.getSelectedItems(exporterCtx.ctx, asIncludes=True)
            else:
                renderMaskObjects = [o for o in exporterCtx.ctx.selected_objects if filterRenderMasks(o)]
            renderMaskData = [objectToAttrPlugin(o).name for o in renderMaskObjects]

        case '3': # Object id
            if (objectIDs := stringToIntList(propGroup.render_mask_object_ids_list, ';')) is not None:
                renderMaskData = objectIDs
            else:
                debug.reportError(f"Invalid object ID list in V-Ray Render Mask settings: {propGroup.render_mask_object_ids_list}")

    return RenderMaskState(propGroup.render_mask_mode, propGroup.render_mask_clear, renderMaskData)


def _syncRenderMask(self, exporterCtx: ExporterContext):
    currentRenderMask = _getRenderMaskState(exporterCtx)
    if self.persistedState.renderMaskState != currentRenderMask:
        # Recreate the SettingsImageSampler, this fixes most of the IPR issues with the render mask.
        pluginName = Names.singletonPlugin("SettingsImageSampler")
        vray.pluginRemove(exporterCtx.renderer, pluginName)
        settingsExporter = SettingsExporter(exporterCtx)
        settingsExporter.exportPlugin("SettingsImageSampler")
        self.persistedState.renderMaskState = currentRenderMask


def _syncNames(exporterCtx: ExporterContext):
    def impl(exporterCtx):
        updatedObjects = [u.id.original for u in exporterCtx.dg.updates if hasattr(u.id, 'vray') or isinstance(u.id, bpy.types.NodeTree)]
        for o in updatedObjects:
            syncObjectUniqueName(o, reset=False)

    if exporterCtx.fullExport:
        exporterCtx.ts.timeThis("syncNames", lambda: syncUniqueNames(reset=False))
    else:
        exporterCtx.ts.timeThis("syncNames", lambda: impl(exporterCtx) )



#############################
## VRayRendererIprBase
#############################

class VRayRendererIprBase:
    """ Viewport (interactive) render engine implementation """


    # Flag that indicates if the scene should be reset on "view_update" execution
    _resetRenderer = False

    # Handle recursion when showing the warning about V-Ray Scene object not being
    # modifiable during interactive rendering
    _enableVraySceneWarning = True

    # When a viewport renderer is activated, it will store here an identifiers of the Blender 3D space
    # and window it was invoked in. When a rendering is requested without a valid context (e.g. from an 
    # external event handler), we can use these identifiers to find the last space for which the
    # V-Ray renderer was used and route the event to the same space.
    # NOTE: the values stored are the native pointers to the structures. They is not unique, 
    # but in the unlikely case the value is reused the only consequence will be VFB manipulating
    # the wrong space. Spaces and windows are not an ID type so currently there is no better way 
    # to identify them.
    _lastView3dPtr = 0
    _lastWindowPtr = 0


    def __init__(self, isViewport: bool, iprContext: IprContext, context: bpy.types.Context):
        # An opaque pointer to the VRay renderer object owned by the C++ library
        self.renderer = None

        self.pocExportOptions = 0
        self.isViewport = isViewport
        
        # Store an identifier of the screen space it was invoked in.
        VRayRendererIprBase._lastView3dPtr = iprContext.view3d.as_pointer()
        VRayRendererIprBase._lastWindowPtr = iprContext.window.as_pointer()

        # Set up plugin tracking
        self.objTrackers        = {}
        self.nodeTrackers       = {}
        self.objTrackers        = dict([(t, ObjTracker(t)) for t in OBJ_TRACKERS])
        self.nodeTrackers       = dict([(t, ScopedNodeTracker(t)) for t in NODE_TRACKERS])
        self.activeInstancers   = set()
        self.meshLightInfo      = set()
        self.furInfo            = set()
        self.activeGizmos       = set()
        self.miscCache          = {}
        self.lastExportedFrame  = None

        # Current view settings. Blender does not provide view change notification, so
        # we have to compare the old state to the new state on each draw operation.
        self.viewParams = ViewParams()

        # Cache & draw the most recent viewport image.
        self.drawData: gl_draw.DrawData = None

        # Used to store ExporterCtx.exportedMtls between update calls.
        self.exportedMtls: dict[bpy.types.Material, AttrPlugin] = {}

        # State to carry on to the next render cycle
        self.persistedState = PersistedState()
        
        self.persistedState.materialOverrideMode = context.view_layer.vray.material_override_mode
        self.persistedState.overrideMaterial = context.view_layer.material_override
    
        # Time when last export has finished. Uset for rate-limiting updates to the veiwport.
        self.tmLastExport = 0.0
        reportAutoSettingsWarning(bpy.context)


    def _clearSceneOnReset(self, isFullExport, renderer):
        """ Performs 'Clear Scene' if the 'reset' flag is set
        Args:
            isFullExport (str): indicates that a full export will be performed
            renderer (vray.Renderer): renderer object
        Returns:
            bool: the new value of the 'isFullExport' argument
        """
        if VRayRendererIprBase._resetRenderer and not isFullExport:
            vray.clearScene(renderer)
            isFullExport = True
            # Reset the stored view matrix to force camera plugins to be re-exported.
            self.persistedState.prevRegion3dViewMatrix = None
        VRayRendererIprBase._resetRenderer = False
        return isFullExport


    @staticmethod
    def _showSceneStatus(exporterCtx: ExporterContext):
        """ Show a warning about V-Ray Scene objects not being modifiable during interactive rednering """
        wmgr = exporterCtx.ctx.window_manager.vray

        if VRayRendererIprBase._enableVraySceneWarning and not wmgr.vrayscene_warning_shown:

            # debug.report() will post an update to the current view which would result in recursion
            # as the current function is invoked as part of the Depsgraph.update_post event handling
            # in IPR mode. Disable recursive invocations.
            VRayRendererIprBase._enableVraySceneWarning = False

            if any([o for o in exporterCtx.sceneObjects if isObjectVrayScene(o)]):
                debug.report('INFO', 'Changes to V-Ray Scene objects made during interactive rendering will be applied after rendering is restarted.')
                wmgr.vrayscene_warning_shown = True
            VRayRendererIprBase._enableVraySceneWarning = True


    @staticmethod
    def getActiveIprContext():
        """ Return the active Ipr context.

            The context is determined when the user explicitly requests an interactive 
            rendering, and pointers to the active SpaceView3D and Window are stored.
            On subsequent calls, both the window and the view are looked up using the 
            stored pointers. If there is no match, the IPR rendering is stopped and 
            the stored pointers are reset.
            The context is considered active as long as the window it points to is open and
            the view can be found on any of the currently created screens. This allows the 
            user to switch to a screen without a 3D view but still see the changes made to 
            the scene in the VFB. When the view cannot be found, the algorithm looks for
            another 3D view on the currently active screen. If such is found, the VFB is 
            automatically bound to the new view and the user can see right away which 
            view is being rendered. If no replacement view is found, the rendering is
            stopped. In this case the user must initiate a new IPR render manually.
            Closing the window with the bound view will stop the rendering.

        """
        from vray_blender.lib.blender_utils import getFirstAvailableView3D

        def getLastActiveSpace():
            if not __class__._lastView3dPtr:
                return None
            
            assert __class__._lastWindowPtr

            # Check whether the window to which the space belongs has been closed. Blender does not immediately
            # remove closed windows' data, so the space might be still alive but in a closed window.
            window = next((w for w in bpy.data.window_managers[0].windows if w.as_pointer() == __class__._lastWindowPtr), None)
            if not window:
                return None
            
            # Search for the space in all screens, not in the active one only, because the user
            # might have switched to another screen. He can still switch back though and will also
            # likely want to see the changes made to the scene in the meantime.
            return next(( space
                        for screen in bpy.data.screens
                        for area in screen.areas if area.type == 'VIEW_3D'
                        for space in area.spaces \
                            if (space.type == 'VIEW_3D') and (space.as_pointer() == VRayRendererIprBase._lastView3dPtr)), None)

        window = bpy.context.window
        
        if view3d := getLastActiveSpace():
            return IprContext(view3d, window)

        if __class__._lastWindowPtr not in (0,  window.as_pointer()):
            # The window containing the space the renderer is attached to was closed.
            # Reset the cached pointers so that the next search could bind to the currently active space
            __class__._lastWindowPtr = 0
            __class__._lastView3dPtr = 0
            return None

        # Search for a View3d space in the current screen. We will get here if either a
        # 3d View was closed in the active screen, or the user has requested a render in a new view.
        if view3d := getFirstAvailableView3D():
            __class__._lastView3dPtr = view3d.as_pointer()
            __class__._lastWindowPtr = window.as_pointer()
            return IprContext(view3d, window)

        return None


    # Export and render scene
    def _export(self, exporterCtx: ExporterContext, engine: bpy.types.RenderEngine = None):

        vray.startExport(self.renderer, bpy.context.scene.vray.Exporter.debug_threads)

        try:
            _syncNames(exporterCtx)

            commonSettings = CommonSettings(exporterCtx.dg.scene, engine, isInteractive = True)
            commonSettings.updateFromScene()
            exporterCtx.commonSettings = commonSettings
            exporterCtx.currentFrame = commonSettings.animation.frameCurrent

            # Always export the properties that do not depend on the depsgraph updates.
            _syncView(exporterCtx)
            _syncAnimFrame(self, exporterCtx)

            region3d = exporterCtx.iprContext.region3d

            # Needed to detect viewport changes in the view draw functions.
            if region3d.view_matrix != self.persistedState.prevRegion3dViewMatrix:
                self.persistedState.prevRegion3dViewMatrix = region3d.view_matrix.copy()

            cameraOnly = all(hasattr(u.id, "type") and (u.id.type == 'CAMERA' or u.id.type == 'PERSP') for u in exporterCtx.dg.updates)
            if not exporterCtx.fullExport and cameraOnly:
                self.viewParams = exportViewportView(exporterCtx, self.viewParams, False)

            elif exporterCtx.fullExport or exporterCtx.dg.updates:
                exporterCtx.calculateObjectVisibility()

                _syncPlugins(self, exporterCtx)
                _prunePlugins(self, exporterCtx)
                _syncRenderMask(self, exporterCtx)
                VRayRendererIprBase._showSceneStatus(exporterCtx)

                _exportObjects(exporterCtx)
                _exportLights(exporterCtx)

                hasCameraUpdates = any(hasattr(u.id, "type") and u.id.type == 'CAMERA' for u in exporterCtx.dg.updates)
                if hasCameraUpdates or exporterCtx.fullExport:
                    self.viewParams = exportViewportView(exporterCtx, self.viewParams, False)

                if exporterCtx.fullExport:
                    _exportSettings(exporterCtx)
                _exportWorld(exporterCtx)
                _exportMaterials(exporterCtx)

                if exporterCtx.rendererMode == RendererMode.Interactive:
                    self._linkRenderChannels(exporterCtx)

                _persistState(self, exporterCtx)

        except Exception as ex:
            if engine:
                engine.error_set(f"Add-on error: {str(ex)}")
            debug.printExceptionInfo(ex, "VRayRendererIprViewport::_export()")
            return False

        UpdateTracker.clear()
        vray.finishExport(self.renderer, interactive = True)

        # Clear any temporary objects and state AFTER the export has finished, as the temp objects
        # might have been scheduled for asynchronous processing.
        __class__._clearTempState(exporterCtx)

        return True


    def _getExporterContext(self, ctx: bpy.types.Context, rendererMode: RendererMode, dg: bpy.types.Depsgraph, isFullExport: bool):
        context = ExporterContext()
        context.renderer         = self.renderer
        context.rendererMode     = rendererMode
        context.objTrackers      = self.objTrackers
        context.nodeTrackers     = self.nodeTrackers
        context.activeGizmos     = self.activeGizmos
        context.ctx              = ctx or bpy.context
        context.dg               = dg or bpy.context.evaluated_depsgraph_get()
        context.fullExport       = isFullExport
        context.ts               = tools.TimeStats("Python export code")
        context.exportedMtls     = self.exportedMtls
        context.persistedState   = self.persistedState
        context.iprContext       = __class__.getActiveIprContext()

        return context

    def _createRenderer(self, exporterType):
        from vray_blender.lib.export_utils import setupDistributedRendering

        exporter = bpy.context.scene.vray.Exporter

        settings = vray.ExporterSettings()
        setupDistributedRendering(settings, exporterType, True)
        settings.exporterType  = exporterType
        settings.renderThreads = exporter.custom_thread_count if exporter.use_custom_thread_count=='FIXED' else -1

        updateEnabledComputeDevices(bpy.context)
        self.renderer = vray.getMainRenderer(settings)

        if self.renderer == 0:
            from vray_blender.engine.render_engine import VRayRenderEngine
            debug.reportError(VRayRenderEngine.ERR_MSG_ZMQ_SERVER_DOWN)

    @staticmethod
    def _clearTempState(exporterCtx: ExporterContext):
        for obj in exporterCtx.objectsWithTempMeshes:
            obj.to_mesh_clear()


    def _linkRenderChannels(self, exporterCtx: ExporterContext):
        """ In order for certain plugins to affect the render channels, those render channels
            must be explicitly listed in a parameter of the plugin. This function updates
            the relevant plugin properties.
        """
        for pluginData, channelsList in exporterCtx.pluginRenderChannels.items():
            updateValue(self.renderer, pluginData[0], pluginData[1], channelsList)