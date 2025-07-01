
import time
import os
from pathlib import PurePath
import sys
import bpy

from vray_blender.engine.renderer_prod import VRayRendererProd
from vray_blender.engine.renderer_preview import VRayRendererPreview
from vray_blender.engine.renderer_ipr_viewport import VRayRendererIprViewport
from vray_blender.engine.renderer_ipr_vfb import VRayRendererIprVfb
from vray_blender.engine.vfb_event_handler import VfbEventHandler
from vray_blender.engine.zmq_process import ZMQProcess
from vray_blender.plugins import PLUGIN_MODULES
from vray_blender import debug
from vray_blender.bin import VRayBlenderLib as vray

class VRayRenderEngine(bpy.types.RenderEngine):
    bl_idname = 'VRAY_RENDER_RT'
    bl_label = "V-Ray"

    # Render engine supports being used for rendering previews of materials, lights and worlds
    bl_use_preview = True

    # Don’t expose Cycles and Eevee shading nodes in the node editor 
    # user interface, so own nodes can be used instead
    bl_use_shading_nodes_custom = True
    
    # Let blender show texture previews
    bl_use_texture_preview = False

    # There can be only one of each type of renderer active at any one time.
    prodRenderer = None
    previewRenderer = None
    viewportRenderer = None
    iprRenderer = None

    ERR_MSG_ZMQ_SERVER_DOWN = "No connection to V-Ray renderer, retry in a few seconds."
    ERR_MSG_MULTIPLE_VIEWPORT_RENDERERS = "Cannot render with V-Ray to more than one viewport."
    ERR_MSG_PROD_RENDER_RUNNING = "Production rendering is already running. Cannot start viewport rendering." 
    ERR_MSG_QUAD_VIEW = "Cannot render QuadView with V-Ray." 

        
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # During initialization, there is no information as to which type the renderer should be.
        # These members will be assigned when a VRay renderer is created ( during the 
        # first invocation of the appropriate 'update' callback )    
        
        # The viewport renderer is a special case because Blender may create more than one at the
        # same time, and may also create instances that are never used, i.e. their view_update/view_draw 
        # methods are never called. 
        
        # We only want to allow one instance of the viewport renderer active at any one time. 
        # If _viewportRendererOwner is True, this is the only instance of VRayRenderer that has actually
        # created a viewport renderer, i.e. that it owns the VRayRenderer.viewportRenderer. 
        self._viewportRendererOwner = False
        
        # A rendering engine will be marked as 'inactive' if a renderer cannot be created for it, 
        # e.g. because another renderer of the same type is already running. Once marked, it won't be 
        # used for rendering. Because each renderer is attached to a specific area, this will allow
        # us to know to never render in the area unless the render engine is recreated.
        self._inactiveViewport = False
        
        
    def __del__(self):
        # For the viewport, Blender creates (and destroys) multiple rendering engine instances. Some of them
        # are never used or at least their view_update/view_draw methods are not called. Because 'viewportRenderer' is
        # initialized in the view_update() callback, it may not be valid when __del__ is called. 
        try:
            # Delete viewportRenderer only through the owning instance
            if hasattr(self, "_viewportRendererOwner") and self._viewportRendererOwner and VRayRenderEngine.viewportRenderer:    
                VRayRenderEngine.viewportRenderer.stop()
                VRayRenderEngine.viewportRenderer = None
                self._viewportRendererOwner = False
        except ReferenceError as ex:
            # Parts of the python object might have been destroyed before __del__-eting, so this exception
            # is kind of OK-ish. Don't report as error.
            debug.printInfo(f"Exception in VRayRenderEngine.__del__(): {ex}")


    @staticmethod
    def resetAll():
        """ Reset all renderers. This method should be called when ZMQ server is restarted for
            whatever reason. In this case, the matching renderers on the server are no longer available 
            so we need to recreate them in V4B as well.
        """ 
        VRayRenderEngine.stopInteractiveRenderer()

        if VRayRenderEngine.previewRenderer is not None:
            VRayRenderEngine.previewRenderer.abort()
            VRayRenderEngine.previewRenderer = None
        
        if VRayRenderEngine.prodRenderer is not None:
            VRayRenderEngine.prodRenderer.abort()
            VRayRenderEngine.prodRenderer = None

        if VRayRenderEngine.viewportRenderer is not None:
            VRayRenderEngine.viewportRenderer.stop()
            VRayRenderEngine.viewportRenderer = None

    @staticmethod
    def startInteractiveRenderer():
        VRayRenderEngine.iprRenderer = VRayRendererIprVfb()
        VRayRenderEngine.iprRenderer.start()

    @staticmethod
    def stopInteractiveRenderer():
        if VRayRenderEngine.iprRenderer:
            VRayRenderEngine.iprRenderer.stop()
            VRayRenderEngine.iprRenderer = None

    def _stopViewportRenderer(self):
        # Enabling "Solid" viewport mode triggers blender to stop the viewport rendering instance
        VfbEventHandler.stopViewportRender()

        # VRayRenderer.viewportRenderer value will be set to None when the 
        # viewport bpy.types.RenderEngine instance is deleted
        viewPortRunningChecks = 100 # If More than viewPortRunningChecks are made the viewport renderer is probably stalled
        viewPortCheckSleepTime = 0.2
        while VRayRendererIprViewport.isActive() and viewPortRunningChecks > 0:
            time.sleep(viewPortCheckSleepTime)
            viewPortRunningChecks -= 1
         
        if viewPortRunningChecks == 0:
            debug.printError(f'Waiting more than {float(viewPortRunningChecks) * viewPortCheckSleepTime} seconds'
                             ' for the viewport bpy.types.RenderEngine instance to be destroyed')
            return False

        return True

    # Final render callback
    # Called on a non-main thread.
    def update(self, blendData: bpy.types.BlendData, dg: bpy.types.Depsgraph):
        
        if bpy.app.background:
            # In headless mode, parse command line.
            self._parseHeadlessArguments()

        try:
            if self.is_preview:
                VRayRenderEngine.previewRenderer = VRayRendererPreview()
            else:
                # The production rendering job must not be running in parallel with viewport rendering
                # because they are using same instance of VRay::Renderer
                if not VRayRendererProd.isActive():
                    VRayRenderEngine.stopInteractiveRenderer()
                    if self._stopViewportRenderer():
                        VRayRenderEngine.prodRenderer = VRayRendererProd()
                    else:
                        debug.printError("Production rendering can't be started")
        except Exception as ex:
            debug.printExceptionInfo(ex, "VRayRenderEngine::update()")

        # All processing is done in the render() callback


    # Final render callback
    # This method is called for both final renders small preview for materials, world and lights.
    # Called on a non-main thread.
    def render(self, depsgraph: bpy.types.Depsgraph):
        if self.is_preview and depsgraph.scene.render.resolution_x < 64: # Don't render icons
            return

        try:
            if self.is_preview:
                VRayRenderEngine.previewRenderer.render(self, depsgraph)
            elif VRayRenderEngine.prodRenderer:
                VRayRenderEngine.prodRenderer.render(self, depsgraph)
        except Exception as ex:
            debug.printExceptionInfo(ex, "VRayRenderEngine::render()")


    # Viewport render
    # This method gets called once at the start and whenever the scene or 3D viewport changes.
    def view_update(self, context: bpy.types.Context, depsgraph: bpy.types.Depsgraph):
        if not vray.isInitialized():
            self.update_stats("WARNING", 'Can\'t start render job. V-Ray is not initialized')
            self._stopViewportRenderer()
            return

        if self._inactiveViewport:
            return
        
        if not ZMQProcess.isRunning():
            self._showCannotRenderErrorMsg(bpy.context, connLost=True)
            return
        
        try:
            # Blender will try to create multiple viewport renderers if multiple views are switched 
            # to Viewport mode. We however don't want to allow more than one viewport renderer to run simultaneously 
            # because they are using same instance of VRay::Renderer. Always called on the main thread.
            # Create and use renderer for this class instance only if there is none yet.                
            if not VRayRenderEngine.viewportRenderer:
                if (not VRayRendererProd.isActive()) and not VRayRenderEngine._isQuadView(context):
                    VRayRenderEngine.stopInteractiveRenderer()
                    VRayRenderEngine.viewportRenderer = VRayRendererIprViewport(context)
                    self._viewportRendererOwner = True
            elif not self._viewportRendererOwner:
                # Only mark as inactive if another viewport renderer is active. If the renderer was not 
                # created because a PROD renderer was running, it is OK to try to initialize it again. 
                self._inactiveViewport = True

            if self._viewportRendererOwner:
                VRayRenderEngine.viewportRenderer.view_update(self, context, depsgraph)
            else:
                self._showCannotRenderErrorMsg(context)
        except Exception as ex:
            debug.printExceptionInfo(ex, "VRayRenderEngine::view_update()")


    # # Viewport render
    # This method is called whenever Blender redraws the 3D viewport.
    # The presence of this method also determines whether the 'RENDERED' viewport 
    # mode will be available. Always called on the main thread.
    def view_draw(self, context, depsgraph):
        if self._inactiveViewport:
            return
        
        if not ZMQProcess.isRunning():
            self._showCannotRenderErrorMsg(bpy.context, connLost=True)
            return
        
        try:
            if self._viewportRendererOwner \
                    and VRayRenderEngine.viewportRenderer \
                    and not VRayRenderEngine._isQuadView(context):
                
                VRayRenderEngine.viewportRenderer.view_draw(self, context, depsgraph)
            else:
                self._showCannotRenderErrorMsg(context)

        except Exception as ex:
            debug.printExceptionInfo(ex, "VRayRenderEngine::view_draw()")


    # We are in background mode, so override UI settings with supported arugmnets
    def _parseHeadlessArguments(self):

        assert bpy.app.background

        frameStart    = None
        frameEnd      = None
        output        = ''
        renderAnim    = False
        imgFormat     = None
        lastIdx = len(sys.argv) - 1
        
        for (idx, arg) in enumerate(sys.argv):
            hasNext = idx < lastIdx
            if arg in {'-f', '--render-frame'} and hasNext:
                frameStart = frameEnd = sys.argv[idx + 1]
            elif arg in {'-s', '--frame-start'} and hasNext:
                frameStart = sys.argv[idx + 1]
            elif arg in {'-e', '--frame-end'} and hasNext:
                frameEnd = sys.argv[idx + 1]
            elif arg in {'-o', '--render-output'} and hasNext:
                output = sys.argv[idx + 1]
            elif arg in {'-F', '--render-format'} and hasNext:
                imgFormat = sys.argv[idx + 1]
            elif arg in {'-a', '--render-anim'}:
                renderAnim = True

        vrayScene = bpy.context.scene.vray
        vrayExporter = vrayScene.Exporter
        debug.printInfo('Command line overrides:')

        if imgFormat:
            formats = self._getImageFormats()
            newFormatName = None
            newFormatIdx = 0
            savedFormatName = None
            for img in formats:
                if img[1].lower() == imgFormat.lower():
                    newFormatName = img[1]
                    newFormatIdx = img[0]
                if img[0].lower() == vrayScene.SettingsOutput.img_format:
                    # get this so we can log it
                    savedFormatName = img[1]
                if newFormatName and savedFormatName:
                    break

            if newFormatName:
                if newFormatName != savedFormatName:
                    debug.printInfo('Changing image output format from "%s" to "%s"' % (savedFormatName, newFormatName))
                    vrayScene.SettingsOutput.img_format = newFormatIdx
            else:
                debug.printError('Format "%s" not found, using "%s"' % (imgFormat, savedFormatName))

        if output != '':
            outputDir  = output
            outputFile = ''
            
            outPath = PurePath(output)
            
            if outPath.suffix != '':
                outputFile = outPath.stem
                outputDir  = str(outPath.parent)

            if outputFile:
                vrayScene.SettingsOutput.img_file = outputFile
            
            vrayScene.SettingsOutput.img_dir  = outputDir
            
            debug.printInfo(f'Changing image output directory to "{output}"')
            
            vrayExporter.auto_save_render = True

            debug.printInfo(f'Changing .vrscene output directory to "{output}"')
    
        if renderAnim and vrayExporter.animation_mode == 'FRAME':
            # if we dont have anim mode set, use Full Range
            debug.printInfo('Changing Animation Mode from "%s" to "ANIMATION"' % vrayExporter.animation_mode)
            vrayExporter.animation_mode = 'ANIMATION'

        if frameStart == frameEnd and frameStart != None:
            # force single frame
            debug.printInfo('Changing Animation Mode from "%s" to "FRAME"' % vrayExporter.animation_mode)
            vrayExporter.animation_mode = 'FRAME'

        # Deliberately set an invalid name to the output file. Currently, there is no way to
        # tell Blender to not save the render result in headless mode. Tracking the exact filenames
        # in order to later delete the files however is not straightforward, so just make sure
        # that nothing is being written.
        bpy.context.scene.render.filepath = "*"


    def _getImageFormats(self):
        try:
            items = PLUGIN_MODULES['SettingsOutput'].Parameters
            for param in items:
                if param['attr'] == 'img_format':
                    return param['items']
        except Exception:
            return []
        return []
   

    def _showCannotRenderErrorMsg(self, context: bpy.types.Context, connLost=False):
        if not vray.isInitialized():
            return

        prodRenderIsActive = VRayRendererProd.isActive()
        isQuadView = VRayRenderEngine._isQuadView(context)

        if connLost or (self._viewportRendererOwner and not VRayRenderEngine.viewportRenderer):
            errMsg = VRayRenderEngine.ERR_MSG_ZMQ_SERVER_DOWN
        elif isQuadView:
            errMsg = VRayRenderEngine.ERR_MSG_QUAD_VIEW
        elif prodRenderIsActive: 
            errMsg = VRayRenderEngine.ERR_MSG_PROD_RENDER_RUNNING
        else:
            errMsg = VRayRenderEngine.ERR_MSG_MULTIPLE_VIEWPORT_RENDERERS

        self.update_stats("ERROR", errMsg)


    @staticmethod
    def _isQuadView(context: bpy.types.Context):
        return (r := context.region) and (r.alignment == 'QUAD_SPLIT')