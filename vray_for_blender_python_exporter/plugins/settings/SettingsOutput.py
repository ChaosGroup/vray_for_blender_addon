
import os
import bpy

from vray_blender.bin import VRayBlenderLib as vray

import vray_blender.debug as debug
from vray_blender.lib.defs import ExporterContext, ExporterBase, PluginDesc, AttrListValue
from vray_blender.lib.settings_defs import AnimationMode
from vray_blender.lib import camera_utils as ct
from vray_blender.lib import export_utils
from vray_blender.lib import plugin_utils
from vray_blender.lib import path_utils
from vray_blender.lib.common_settings import CommonSettings
from vray_blender.lib.camera_utils import ViewParams
from vray_blender.lib.names import Names



plugin_utils.loadPluginOnModule(globals(), __name__)

class SettingsOutputExporter(ExporterBase):
    def __init__(self, ctx: ExporterContext, activeCameraViewParams: ViewParams, prevViewParams: ViewParams):
        super().__init__(ctx)

        # Material previews use their own scene accessible through the 
        # context for the material preview area (which happens to be the 
        # active one when RenderEngine methods are called for the preview)
        self.scene: bpy.types.Scene = bpy.context.scene if ctx.preview else ctx.dg.scene
        self.settings: CommonSettings = ctx.commonSettings
        self.viewParams = activeCameraViewParams
        self.prevViewParams = prevViewParams
        

    def export(self):
        if self.production and self.isAnimation:
            animation = self.commonSettings.animation
            if animation.frameCurrent != animation.frameStart:
                # Output settings should not change during an animation
                return
        
        pluginDesc = PluginDesc(Names.singletonPlugin("SettingsOutput"), "SettingsOutput")
        pluginDesc.vrayPropGroup = self.scene.vray.SettingsOutput

        # Order is important
        self._fillRenderSizes(pluginDesc)
        self._fillAnimation(pluginDesc)

        self._fillOutputPaths(pluginDesc.vrayPropGroup, pluginDesc, noOutput=self.interactive)
        
        if not self._isDrawCall():
            # NOTE: When loading preview image for World image alpha will be replaced with black color.
            # We don't want this, so simply use JPEG which doesn't have alpha channel
            if self.preview:
                pluginDesc.setAttribute('img_noAlpha', True)

        result = export_utils.exportPlugin(self, pluginDesc)
        
        # Setting render and region sizes is not always possible through the plugin system alone, 
        # so call VRayRenderer::setRenderSizes() too.

        # NOTE: Failure to set correct render sizes may result in a hard-to-debug access violation due 
        # to the fact that the image buffer allocated by Blender will not be the same size as what 
        # VRayBlendeLib thinks it is when writing image data to it. See VRayRendererProd._renderStart()
        
        # We don't cache the values for VRayRenderer methods other than setting plugin attributes, so make sure
        # to only call this when the values change, otherwise the scene will be re-rendered
        if (self.prevViewParams is None) or (not self.viewParams.renderSizes.isEqualTo(self.prevViewParams.renderSizes)):
            vray.setRenderSizes(self.renderer, self.viewParams.renderSizes)

        return result
    

    
    def _fillRenderSizes(self, pluginDesc):
        # NOTE: It may seem odd that the render sizes are set both here and in ViewExporter. Unfortunately,
        # VRay AppSDK does not define a clear path for setting render sizes in all circumstances. Setting
        # them through both VRayRenderer interface and through the SettingsOutput plugin (here) is an
        # attempt to cover all cases. 
        
        renderSizes = self.viewParams.renderSizes
        
        pluginDesc.setAttributes({
            "img_width"   : renderSizes.imgWidth,
            "img_height"  : renderSizes.imgHeight,
            "bmp_width"   : renderSizes.bmpWidth,
            "bmp_height"  : renderSizes.bmpHeight,
    
            "rgn_left"    : renderSizes.cropRgnLeft,
            "rgn_top"     : renderSizes.cropRgnTop,
            "rgn_width"   : renderSizes.cropRgnWidth,
            "rgn_height"  : renderSizes.cropRgnHeight,
    
            "r_left"    : renderSizes.rgnLeft,
            "r_top"     : renderSizes.rgnTop,
            "r_width"   : renderSizes.rgnWidth,
            "r_height"  : renderSizes.rgnHeight
        })


    def _fillOutputPaths(self, propGroup, pluginDesc, noOutput: bool):
        # In case of an error or early return, make sure the plugin has valid valued
        pluginDesc.setAttribute('img_file', "")
        pluginDesc.setAttribute('img_dir', "")
        
        if noOutput or \
            ((not self.preview) and (not self.bake) and (not self.settings.autoSaveRender)):
            return 

        if self.preview:
            pluginDesc.setAttribute('img_file', "preview.exr")
            pluginDesc.setAttribute('img_dir', path_utils.getPreviewDir())
            pluginDesc.setAttribute("img_file_needFrameNumber", False)
            return
        
        imgFile = ""
        imgDir = ""
        imgFmt = 0

        if self.bake:
            bakeItem = self.scene.vray.BatchBake.active_item
            imgFmt = int(bakeItem.img_format)
            imgFile = path_utils.getOutputFileName(self.ctx, bakeItem.img_file, imgFmt)
            imgDir = path_utils.expandPathVariables(self.ctx, bakeItem.img_dir)
            pluginDesc.setAttribute("img_file_needFrameNumber", False)
        else:
            imgDir = propGroup.img_dir
            multipleLayers = len([layer for layer in self.scene.view_layers if layer.use]) > 1
            viewLayerName = self.dg.view_layer_eval.name if multipleLayers else ""

            imgFmt = int(propGroup.img_format)
            imgFile = path_utils.getOutputFileName(self.ctx, propGroup.img_file, imgFmt, viewLayerName)
            imgDir = path_utils.expandPathVariables(self.ctx, propGroup.img_dir)
        
        if self.settings.animation.mode == AnimationMode.Animation:
            pluginDesc.setAttribute("img_file_needFrameNumber", True)

        IMAGE_FORMAT_EXR = 5
        if imgFmt == IMAGE_FORMAT_EXR and not propGroup.relements_separateFiles:
            pluginDesc.setAttribute("img_rawFile", True)

        # Try to create the missing folders on the output path
        try:
            os.makedirs(imgDir, exist_ok=True)
        except Exception as exc:
            debug.reportError(f"Failed to create output folder ['{imgDir}']. No output image will be saved.", self.commonSettings.renderEngine, exc)
            return                

        lastChar = imgDir[-1:]
        if lastChar not in ('/', '\\'):
            imgDir = f"{imgDir}/"

        pluginDesc.setAttribute("img_dir", imgDir)
        pluginDesc.setAttribute("img_file", imgFile)


    def _fillAnimation(self, pluginDesc):
        # TODO replace values from scene from the ones gotten from FrameExporter
        # when it has been ported to Python
        
        # The 'frames' parameter needs to be list of lists in animation mode
        # When this mode is not active its unused
        framesList = None
        if self.settings and self.settings.animation.use:
            framesList = AttrListValue()
            framesList.append([int(self.scene.frame_start), int(self.scene.frame_end)])

        pluginDesc.setAttributes({
            'anim_start'       : self.scene.frame_start,
            'anim_end'         : self.scene.frame_end,
            'frame_start'      : self.scene.frame_start,
            'frame_per_second' : 1,
            'frames'           : framesList
        })


    def _isDrawCall(self):
        """ When exporting from a view_draw() callback, as opposed to view_update(),
            we are only concerned with exporting view parameters and cameras. Some export
            data ( e.g. common_settings ) is not available in this mode
        """ 
        return self.settings is None


