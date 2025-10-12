import bpy
import time
from typing import Dict

from vray_blender.engine import NODE_TRACKERS, OBJ_TRACKERS

from vray_blender import debug
from vray_blender.lib import gl_draw, defs
from vray_blender.lib.common_settings import CommonSettings
from vray_blender.lib.camera_utils import ViewParams
from vray_blender.lib.defs import ExporterContext, RendererMode, AttrPlugin, PersistedState, getObjTrackId
from vray_blender.lib.names import syncObjectUniqueName, syncUniqueNames, Names
from vray_blender.lib.plugin_utils import updateValue
from vray_blender.exporting.instancer_export import InstancerExporter
from vray_blender.exporting.plugin_tracker import ObjTracker, ScopedNodeTracker
from vray_blender.exporting.settings_export import SettingsExporter
from vray_blender.exporting.tools import isObjectVrayProxy, isObjectVrayScene
from vray_blender.exporting.update_tracker import UpdateTracker, UpdateTarget
from vray_blender.exporting import tools, obj_export, mtl_export, view_export, settings_export, light_export, world_export, hair_export

from vray_blender.bin import VRayBlenderLib as vray

from vray_blender.engine.zmq_process import ZMQ


#############################
## Module free functions
#############################

def _exportObjects(ctx: ExporterContext):
    ctx.ts.timeThis("export_objects", lambda: obj_export.run(ctx)) 


def _exportMaterials(ctx: ExporterContext):
    stats: defs.SceneStats = ctx.ts.timeThis("export_materials", lambda: mtl_export.run(ctx))
    ctx.stats.append(f"{'Materials:':<12} exported {stats.mtls} materials, {stats.plugins} plugins, {stats.attrs} attributes")


def _exportLights(ctx: ExporterContext):
    ctx.ts.timeThis("export_lights", lambda: light_export.LightExporter(ctx).export()) 


def _exportSettings(ctx: ExporterContext):
    # This method has been reworked to only export Settings
    settingsExporter = settings_export.SettingsExporter(ctx)
    stats: defs.SceneStats = ctx.ts.timeThis("export_settings", lambda: settingsExporter.export())
    ctx.stats.append(f"{'Settings:':<12} exported {stats.plugins} plugins, {stats.attrs} attributes")

def exportViewportView(ctx: ExporterContext, view3D: bpy.types.SpaceView3D, prevViewParams: ViewParams):
    return ctx.ts.timeThis("export_view", lambda: view_export.ViewExporter(ctx).exportViewportView(view3D, prevViewParams))

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
    
    # NOTE: Sync object visibility first because the rest of the operations
    # depend on it
    obj_export.GeometryExporter(exporterCtx).syncObjVisibility()
    
    # NOTE: Updates should tagged be BEFORE any export structures are calculated
    # for the current pass. Keep this at the beginnign of the function. 
    UpdateTracker.tagCrossObjectUpdates(exporterCtx, bpy.data.materials, UpdateTarget.MATERIAL)
    UpdateTracker.tagCrossObjectUpdates(exporterCtx, bpy.data.lights, UpdateTarget.LIGHT)
    UpdateTracker.tagCrossObjectUpdates(exporterCtx, bpy.data.worlds, UpdateTarget.WORLD)

    light_export.syncLightMeshInfo(exporterCtx)
    hair_export.syncFurInfo(exporterCtx)
    mtl_export.calculateMatExportCache(exporterCtx)
    
    if exporterCtx.rendererMode == RendererMode.Interactive:
        # Only collect light mix info in VFB IPR mode. In viewport mode render channels
        # are not exported.
        light_export.collectLightMixInfo(exporterCtx)

    # NOTE: just to be safe, remove objects that have been processed but not in the current scene
    allObjectIds = { getObjTrackId(o) for o in exporterCtx.sceneObjects }
    for objTrackId in self.persistedState.processedObjects.difference(allObjectIds):
        self.persistedState.processedObjects.discard(objTrackId)


def _persistState(self, exporterCtx: ExporterContext):
    self.persistedState.visibleObjects = exporterCtx.visibleObjects
    self.persistedState.activeInstancers = exporterCtx.activeInstancers
    self.persistedState.activeGizmos = exporterCtx.activeGizmos
    self.persistedState.exportedMtls = exporterCtx.exportedMtls
    self.persistedState.activeFurInfo = exporterCtx.activeFurInfo
    self.persistedState.activeMeshLightsInfo = exporterCtx.activeMeshLightsInfo


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


def _syncNames(exporterCtx: ExporterContext):
    
    def impl(exporterCtx):
        updatedObjects = [u.id.original for u in exporterCtx.dg.updates if hasattr(u.id, 'vray') or isinstance(u.id, bpy.types.NodeTree)]
        for o in updatedObjects:
            syncObjectUniqueName(o, reset=False)

    if exporterCtx.fullExport:
        exporterCtx.ts.timeThis("syncNames", lambda: syncUniqueNames(reset=False))

        for id in [ id for id in exporterCtx.dg.ids if hasattr(id, 'vray') and isinstance(id, bpy.types.Object)]:
            for slot in [s for s in id.material_slots if s.material and s.material.use_nodes and hasattr(s.material.node_tree, 'nodes')]:
                for node in slot.material.node_tree.nodes:
                    syncObjectUniqueName(node, reset=False)
    else:
        exporterCtx.ts.timeThis("syncNames", lambda: impl(exporterCtx) )


def _showSceneStatus(exporterCtx: ExporterContext):
    wmgr = exporterCtx.ctx.window_manager.vray
    if not wmgr.vrayscene_warning_shown:
        if any([o for o in exporterCtx.sceneObjects if isObjectVrayScene(o)]):
            debug.report('INFO', 'Changes to V-Ray Scene objects made during interactive rendering will be applied after rendering is restarted.')
            wmgr.vrayscene_warning_shown = True


#############################
## VRayRendererIprBase
#############################

class VRayRendererIprBase:
    """ Viewport (interactive) render engine implementation """

        
    # Flag that indicates if the scene should be reset on "view_update" execution
    _resetRenderer = False

    def __init__(self, isViewport):
        # An opaque pointer to the VRay renderer object owned by the C++ library
        self.renderer = None

        self.pocExportOptions = 0
        self.isViewport = isViewport
      
        # Set up plugin tracking
        self.objTrackers        = {}
        self.nodeTrackers       = {}
        self.objTrackers        = dict([(t, ObjTracker(t)) for t in OBJ_TRACKERS])
        self.nodeTrackers       = dict([(t, ScopedNodeTracker(t)) for t in NODE_TRACKERS])
        self.visibleObjects     = set()
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

        # Time when last export has finished. Uset for rate-limiting updates to the veiwport.
        self.tmLastExport = 0.0


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
        VRayRendererIprBase._resetRenderer = False
        return isFullExport


    # Export and render scene
    def _export(self, exporterCtx: ExporterContext, engine: bpy.types.RenderEngine = None, view3D: bpy.types.SpaceView3D = None):

        vray.startExport(self.renderer, bpy.context.scene.vray.Exporter.debug_threads)
        success = True

        try:
            exporterCtx.calculateObjectVisibility()

            _syncNames(exporterCtx)

            commonSettings = CommonSettings(exporterCtx.dg.scene, engine, isInteractive = True)
            commonSettings.updateFromScene()
            exporterCtx.commonSettings = commonSettings
            exporterCtx.currentFrame = commonSettings.animation.frameCurrent

            # Always export the properties that do not depend on the depsgraph updates.
            _syncView(exporterCtx)
            _syncAnimFrame(self, exporterCtx)

            if exporterCtx.fullExport or exporterCtx.dg.updates:
                _syncPlugins(self, exporterCtx)
                _prunePlugins(self, exporterCtx)
                _showSceneStatus(exporterCtx)

                _exportObjects(exporterCtx)
                _exportLights(exporterCtx)

                # The order of export of view and settings is important, because
                # the settings export needs valid ViewParams
                # view3D can be None in reexport - in that case skip exportViewportView calls
                assert view3D or not exporterCtx.fullExport, "view3D parameter should not be empty"
                if view3D:
                    self.viewParams = exportViewportView(exporterCtx, view3D, self.viewParams)

                _exportSettings(exporterCtx)
                _exportMaterials(exporterCtx)
                _exportWorld(exporterCtx)

                if exporterCtx.rendererMode == RendererMode.Interactive:
                    self._linkRenderChannels(exporterCtx)

                _persistState(self, exporterCtx)

            success = True
        except Exception as ex:
            if engine:
                engine.error_set(f"Add-on error: {str(ex)}")
            debug.printExceptionInfo(ex, "VRayRendererIprViewport::_export()")

        UpdateTracker.clear()
        vray.finishExport(self.renderer, interactive = True)
        
        # Clear any temporary objects and state AFTER the export has finished, as the temp objects
        # might have been scheduled for asynchronous processing.
        __class__._clearTempState(exporterCtx)

        # Rate-limit the updates we process as too many updates will result in unresponsive viewport.
        self._limitFrameRate(20)

        return success


    def _getExporterContext(self, ctx: bpy.types.Context, rendererMode: RendererMode, dg: bpy.types.Depsgraph, isFullExport: bool):
        context = ExporterContext()
        context.renderer         = self.renderer
        context.rendererMode     = rendererMode
        context.objTrackers      = self.objTrackers
        context.nodeTrackers     = self.nodeTrackers
        context.visibleObjects   = self.visibleObjects
        context.activeGizmos     = self.activeGizmos
        context.ctx              = ctx or bpy.context
        context.dg               = dg or bpy.context.evaluated_depsgraph_get()
        context.fullExport       = isFullExport
        context.ts               = tools.TimeStats("Python export code")
        context.exportedMtls     = self.exportedMtls
        context.persistedState   = self.persistedState

        return context

    def _createRenderer(self, exporterType):
        from vray_blender.lib.export_utils import setupDistributedRendering

        exporter = bpy.context.scene.vray.Exporter

        settings = vray.ExporterSettings()
        setupDistributedRendering(settings, exporterType, True)
        settings.exporterType  = exporterType
        settings.renderThreads = exporter.custom_thread_count if exporter.use_custom_thread_count=='FIXED' else -1

        self.renderer = vray.createRenderer(settings)


    def _limitFrameRate(self, fps: int):
        """ If updates come too fast, sleep for some time in order to limit the 
            max number of rendered images to the specified frame rate.
        """
        
        if self.tmLastExport != 0.0:
            frameTime = 1.0 / fps
            elapsed = time.time() - self.tmLastExport
            
            if elapsed < frameTime:
                time.sleep(frameTime - elapsed)

        self.tmLastExport = time.time()


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