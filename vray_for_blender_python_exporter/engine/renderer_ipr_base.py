import bpy 
import time

from numpy import ndarray

from vray_blender.engine import NODE_TRACKERS, OBJ_TRACKERS

from vray_blender import debug
from vray_blender.lib import gl_draw, defs
from vray_blender.lib.common_settings import CommonSettings
from vray_blender.lib.camera_utils import ViewParams
from vray_blender.lib.defs import ExporterContext, RendererMode
from vray_blender.lib.names import syncObjectUniqueName, syncUniqueNames
from vray_blender.exporting.instancer_export import InstancerExporter
from vray_blender.exporting.plugin_tracker import ObjTracker, ScopedNodeTracker, getObjTrackId
from vray_blender.exporting.update_tracker import UpdateTracker
from vray_blender.exporting import tools, obj_export, mtl_export, view_export, settings_export, light_export, world_export
from vray_blender.nodes.utils import getNodeByType, getObjectsFromSelector
from vray_blender.engine.vfb_event_handler import VfbEventHandler

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


def _syncPlugins(self, exporterCtx: ExporterContext):
    """ Perform synchronization of plugin data before starting an export """
    def impl(context):
        # Obj exporter
        geom = obj_export.GeometryExporter(context)
        geom.prunePlugins()
        geom.syncObjVisibility()
        
        light_export.syncMeshLightInfo(exporterCtx)

        mtl_export.MtlExporter(context).prunePlugins()
        light_export.LightExporter(context).prunePlugins()
        view_export.ViewExporter(context).prunePlugins()
        world_export.WorldExporter(context).prunePlugins()
        InstancerExporter.pruneInstances(self.activeInstancers, exporterCtx)
        
        # Persist the new snaphot
        self.activeInstancers = exporterCtx.activeInstancers

    exporterCtx.ts.timeThis("sync_plugins", lambda: impl(exporterCtx))


def _syncPluginsPostExport(self, exporterCtx: ExporterContext):
    """ Perform synchronization of plugin data after an export has completed """
    light_export.syncMeshLightInfoPostExport(exporterCtx)
    

def _syncView(ctx: ExporterContext):
    commonSettings = ctx.commonSettings

    viewSettings = vray.ViewSettings()
    viewSettings.viewportImageType = commonSettings.viewportImageType
    viewSettings.viewportImageQuality = commonSettings.viewportImageQuality
    viewSettings.renderMode = commonSettings.renderMode
    viewSettings.vfbFlags = commonSettings.vfbFlags

    vray.syncViewSettings(ctx.renderer, viewSettings)


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
        self.activeGizmos       = set()
        self.miscCache          = {}
        
        # Current view settings. Blender does not provide view change notification, so 
        # we have to compare the old state to the new state on each draw operation.
        self.viewParams = ViewParams()

        # Cache & draw the most recent viewport image.
        self.drawData: gl_draw.DrawData = None


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
            _syncNames(exporterCtx)
            
            commonSettings = CommonSettings(exporterCtx.dg.scene, engine, isInteractive = True)
            commonSettings.updateFromScene()
            exporterCtx.commonSettings = commonSettings

            _syncView(exporterCtx)
            
            if exporterCtx.fullExport or exporterCtx.dg.updates:
                _syncPlugins(self, exporterCtx)
                
                _exportObjects(exporterCtx)
                _exportLights(exporterCtx)
                
                # The order of export of view and settings is important, because
                # the settings export needs valid ViewParams
                self.viewParams = exportViewportView(exporterCtx, view3D, self.viewParams)

                _exportSettings(exporterCtx)
                _exportMaterials(exporterCtx)
                _exportWorld(exporterCtx)

                _syncPluginsPostExport(self, exporterCtx)

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


        # VRay experiences frequent crashes when successive updates are made to 
        # the scene in rapid succession, e.g. when the user drags a slider that 
        # continuously fires updates. This is especially pronounced when the lights
        # are being updated. To mitigate the issue, rate-limit the updates.
        # NOTE: The value of 50 ms used here is arbitrary and may need tweaking
        # on differet machines / scenes. It has been chosen because it gives a good
        # ratio of user experience and stability, but does not guarantee crash-free
        # operation.
        # TODO: investigate further and replace with a reliable solution  
        time.sleep(0.05)

        return success
    

    def _getExporterContext(self, ctx: bpy.types.Context, rendererMode: RendererMode, dg: bpy.types.Depsgraph, isFullExport: bool):
        context = ExporterContext()
        context.renderer         = self.renderer
        context.rendererMode     = rendererMode
        context.objTrackers      = self.objTrackers
        context.nodeTrackers     = self.nodeTrackers
        context.visibleObjects   = self.visibleObjects
        context.cachedMeshLightsInfo = self.meshLightInfo
        context.activeGizmos     = self.activeGizmos
        context.ctx              = ctx or bpy.context
        context.dg               = dg or bpy.context.evaluated_depsgraph_get()
        context.fullExport       = isFullExport
        context.ts               = tools.TimeStats("Python export code")

        context.calculateObjectVisibility()

        return context

    def _createRenderer(self, exporterType):
        settings = vray.ExporterSettings()
        settings.exporterType = exporterType
        self.renderer = vray.createRenderer(settings)


    @staticmethod
    def _clearTempState(exporterCtx: ExporterContext):
        for tеmpObj in exporterCtx.tempObjects:
            bpy.data.objects.remove(tеmpObj)