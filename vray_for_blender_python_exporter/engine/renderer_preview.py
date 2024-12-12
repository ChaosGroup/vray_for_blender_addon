import threading
import bpy
import mathutils

from vray_blender.engine.renderer_prod_base import VRayRendererProdBase

from vray_blender import debug
from vray_blender.lib.common_settings import CommonSettings
from vray_blender.lib.defs import ExporterContext, NodeContext, PluginDesc, ExporterContext, ExporterType
from vray_blender.lib.names import Names, syncUniqueNames, syncUniqueNamesForPreview
from vray_blender.lib.plugin_utils import updateValue
from vray_blender.lib.export_utils import exportPlugin

from vray_blender.bin import VRayBlenderLib as vray


class VRayRendererPreview(VRayRendererProdBase):
    """ Material preview renderer implementation. """

    def __init__(self):
        super().__init__(isPreview=True)
        self.lock = threading.Lock()


    def abort(self):
        """ Abort the rendering job, if it is running. This method can be called from 
            any context.
        """
        with self.lock:
            if self.renderer:
                # Abort the job in vray. The cleanup will be performed when the job
                # has finished.
                vray.renderEnd(self.renderer)


    def render(self, engine: bpy.types.RenderEngine, dg: bpy.types.Depsgraph):

        with self.lock:
            self.renderer = self._createRenderer(ExporterType.PREVIEW)
            
        # In production mode we always perform a full export which only adds data to the scene. 
        # Clearing the scene will ensure that no remnants of a previous scene are left around.
        vray.clearScene(self.renderer)
        
        # Common settings don't change during the animation, collect up front
        commonSettings = CommonSettings(bpy.context.scene, engine, isInteractive = False)
        commonSettings.updateFromScene()

        exporterCtx = self._getExporterContext(self.renderer, dg, commonSettings)
        syncUniqueNamesForPreview(exporterCtx.dg)
        exporterCtx.calculateObjectVisibility()
        
        # Obtain a rendering target from Blender and set it to the C++ renderer
        self._renderStart(exporterCtx)
        success = False
        
        try:
            VRayRendererPreview._syncView(exporterCtx)
            
            self._renderScene(engine, exporterCtx)
            success = True
        except Exception as ex:
            self._reportError(engine, f"{str(ex)} See log for details")
            debug.printExceptionInfo(ex, "VRayRendererPreview::render()")
        
        with self.lock:
            self._renderEnd(engine, success)
        
   
    def _renderScene(self, engine: bpy.types.RenderEngine, exporterCtx: ExporterContext):
        """ Export and render the current frame."""
        scene = exporterCtx.dg.scene
        
        vray.setRenderFrame(self.renderer, scene.frame_current)
        self._export(engine, exporterCtx)
        
        if not vray.renderFrame(self.renderer, waitForCompletion=True):
            self._reportInfo(engine, "Material preview render job aborted")


    def _exportSceneAdjustments(self, exporterCtx: ExporterContext):
        """ An override to some of the plugins's attributes is needed when exporting preview scene. """
            
        # The 'Foor' object in the scene doesn't have a V-Ray material attached to it
        # For that BRDFVRayMtl with TexChecker is attached to the Floor object node
        dg = exporterCtx.dg
        floor = dg.objects['Floor']
        
        nodeCtx = NodeContext(exporterCtx)
        nodeCtx.rootObj = floor.active_material

        # Create TexChecker texture
        texCheckerPluginName = Names.nextVirtualNode(nodeCtx, "TexChecker")
        texCheckerFloor = PluginDesc(texCheckerPluginName, "TexChecker")
        texCheckerFloor.setAttribute("placement_type", "1")
        texCheckerFloor.setAttribute("w", 10)
        texCheckerFloor.setAttribute("h", 10)
        texCheckerFloor.setAttribute("white_color", mathutils.Color((0.6,0.6,0.6)))
        texCheckerFloor.setAttribute("black_color", mathutils.Color((0.2,0.2,0.2)))
        texCheckerPlugin = exportPlugin(exporterCtx, texCheckerFloor)

        brdfPluginName = Names.nextVirtualNode(nodeCtx, "BRDFVRayMtl")
        brdfFloor = PluginDesc(brdfPluginName, "BRDFVRayMtl")
        brdfFloor.setAttribute("diffuse", texCheckerPlugin)
        brdfPlugin = exportPlugin(exporterCtx, brdfFloor)

        floorMtlName = Names.object(floor.active_material) 
        mtlSingleBrdfFloor = PluginDesc(floorMtlName, "MtlSingleBRDF")
        mtlSingleBrdfFloor.setAttribute("brdf", brdfPlugin)
        floorMtlPlugin = exportPlugin(exporterCtx, mtlSingleBrdfFloor)

        # Attaching the newly created material to the Floor Object
        # WARNING: In future blender releases the floor object name could get changed (and respectively its V-Ray node)
        floorNodeName = Names.vrayNode(Names.object(floor))
        updateValue(exporterCtx.renderer, floorNodeName, "material", floorMtlPlugin)

        # The default intensity of the lights in the scene is too small and for that, it is increased
        squaredLight = dg.objects['SquaredLight']
        circularLight = dg.objects['CircularLight']

        updateValue(exporterCtx.renderer, Names.objectData(squaredLight), "intensity", 20)
        updateValue(exporterCtx.renderer, Names.objectData(circularLight), "intensity", 100)
        