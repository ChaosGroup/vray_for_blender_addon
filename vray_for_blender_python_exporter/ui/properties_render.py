# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy, platform, os, sys
from bpy.types import Context

from vray_blender.engine import ZMQ
from vray_blender.lib import draw_utils
from vray_blender.lib.sys_utils import StartupConfig, activeRendererExists
from vray_blender.operators import VRAY_OT_render
from vray_blender.plugins import getPluginModule
from vray_blender.ui import classes
from vray_blender.ui.classes import pollEngine, RenderPanelGroups
from vray_blender.ui.icons import getIcon
from vray_blender.utils.update_checker import UpdateStatus
from vray_blender.version import getAddonVersion, formatNumericVersion
from vray_blender.bin import VRayBlenderLib as vray
from vray_blender.ui.community_edition import drawCELimitedFeatureIcon, addCEPricingURLInfo

if sys.platform == "win32":
    # The psutil we ship is currently broken on macos. It takes into account numa nodes
    # and is only used for the total thread count message we show the user, but we should
    # be fine with os.cpu_count on most MacOS machines.
    from vray_blender.external import psutil

_GI_ENGINE_BRUTE_FORCE = '2'
_GI_ENGINE_LIGHT_CACHE = '3'

def getRenderIcon(vrayExporter):
    renderIcon = 'RENDER_ANIMATION'
    if vrayExporter.animation_mode == 'FRAME':
        renderIcon = 'NONE'
    return renderIcon


               

class VRAY_PT_Context(classes.VRayRenderPanel):
    """ PROPERTIES->Render panel header"""
    bl_label   = ""
    bl_options = {'HIDE_HEADER'}
    bl_panel_groups = RenderPanelGroups

    @classmethod
    def poll_group(cls, context):
        return True

    def draw(self, context: bpy.types.Context):
        layout = self.layout
        scene  = context.scene
        rd     = scene.render

        self._drawRendererSelector(context)
        layout.separator()
        self._drawAboutBox(context)

        vrayScene = scene.vray
        vrayBake  = vrayScene.BakeView

        if not vray.isInitialized():
            message = "V-Ray initializing..." if vray.hasLicense() else "No V-Ray license..."
            layout.label(text=message, icon="INFO")

        if not vrayBake.use:
            # RENDER button
            renderCol = layout.column()
            renderCol.enabled = vray.isInitialized()
            renderCol.operator(VRAY_OT_render.bl_idname, text="Render") 
            renderCol.separator()

        # Render panel group switcher (Globals, GI, Sampler, System) 
        layout.prop(context.window_manager.vray, 'ui_render_context', expand=True)
        layout.separator()


    def _drawRendererSelector(self, context: bpy.types.Context):
        """ Draw V-Ray-specific render engine/device selectors """
        if context.engine == 'VRAY_RENDER_RT':
            layout = self.layout
            vrayExporter = context.scene.vray.Exporter

            # Render engine/device cannot be changed if a rendering job is under way.
            enabled = not activeRendererExists()

            layoutDevice = layout.column()
            layoutDevice.use_property_split = True
            layoutDevice.use_property_decorate = False
            layoutDevice.enabled = enabled
            layoutDevice.active = enabled


            #if context.scene.render.has_multiple_engines:
            layoutDevice.prop(context.scene.render, "engine", text="Render Engine")
            layoutDevice.prop(vrayExporter, "device_type", text="V-Ray Engine", )

            if vrayExporter.device_type == 'GPU':
                if sys.platform != "darwin":
                    layoutDevice.separator()
                    gpuDeviceRow = layoutDevice.row()
                    gpuDeviceRow.alignment = 'CENTER'
                    gpuDeviceRow.label(text="GPU Device")

                    gpuDeviceBox = gpuDeviceRow.box()
                    gpuDeviceBox.alignment = 'CENTER'
                    gpuDeviceBox.scale_y = 0.5  # This scale makes GPU Device aligned vertically
                    gpuDeviceBox.label(text='RTX' if vrayExporter.use_gpu_rtx else 'CUDA')
                    devicesRow = gpuDeviceRow

                    # Adding the use_gpu_rtx prop into new centered row,
                    # so it can be aligned with the "GPU Device" label.
                    rtxRow = layoutDevice.row()
                    rtxRow.alignment = 'CENTER'
                    rtxRow.scale_x = 2
                    rtxRow.prop(vrayExporter, "use_gpu_rtx")
                else:
                    devicesRow = layoutDevice.row()
                    devicesRow.alignment = 'RIGHT'
                devicesRow.operator(
                    "vray.open_preferences",
                    text="Devices", icon="PREFERENCES"
                ).menu_tab = 'PREFERENCES_MENU_GPU_DEVICES'
            

    def _drawAboutBox(self, context: bpy.types.Context):   
        # V-Ray about info
        box = self.layout.box()
        box.label(text="V-Ray for Blender", icon_value = getIcon('VRAY_LOGO'))
        split = box.split(factor=0.5)
        leftCol = split.column()
        rightCol = split.column()

        # Line 2: License info and Upgrade button on the same row
        if vray.isCommunityEdition():
            leftCol.label(text='Community Edition', icon_value =getIcon('INFO_ABOUT'))
            op = rightCol.operator('vray.url_open', text='Go Pro', icon='URL')
            addCEPricingURLInfo(op)
            leftCol.separator()
            rightCol.separator()
        
        # Line 3: Update info
        match UpdateStatus.current:
            case UpdateStatus.NoUpdate | UpdateStatus.Error:      
                leftCol.label(text=f"Version: {getAddonVersion()}", icon='INFO')
            
            case UpdateStatus.Available:
                leftCol.label(text='Update available:', icon='INFO')
                op = rightCol.operator("vray.url_open", text=f"v{formatNumericVersion(UpdateStatus.version)}", icon='URL')
                op.url = UpdateStatus.downloadURL
                op.description = "Go to V-Ray for Blender downloads page"
    
########  ########    ###    ##       ######## #### ##     ## ########
##     ## ##         ## ##   ##          ##     ##  ###   ### ##
##     ## ##        ##   ##  ##          ##     ##  #### #### ##
########  ######   ##     ## ##          ##     ##  ## ### ## ######
##   ##   ##       ######### ##          ##     ##  ##     ## ##
##    ##  ##       ##     ## ##          ##     ##  ##     ## ##
##     ## ######## ##     ## ########    ##    #### ##     ## ########

class VRAY_PT_Device(classes.VRayRenderPanel):
    bl_label = "V-Ray GPU"
    bl_panel_groups = RenderPanelGroups

    vrayPlugins = ["SettingsRTEngine"]

    @classmethod
    def poll_custom(cls, context):
        VRayScene = context.scene.vray
        VRayExporter = VRayScene.Exporter
        return VRayExporter.device_type == 'GPU'



 ######   ##        #######  ########     ###    ##        ######
##    ##  ##       ##     ## ##     ##   ## ##   ##       ##    ##
##        ##       ##     ## ##     ##  ##   ##  ##       ##
##   #### ##       ##     ## ########  ##     ## ##        ######
##    ##  ##       ##     ## ##     ## ######### ##             ##
##    ##  ##       ##     ## ##     ## ##     ## ##       ##    ##
 ######   ########  #######  ########  ##     ## ########  ######

class VRAY_PT_Globals(classes.VRayRenderPanel):
    bl_label   = "Globals"
    bl_panel_groups = RenderPanelGroups

    def draw(self, context):
        cameraLayout = self.layout.column()

        cameraLayoutContainer  = self.drawSection(context, cameraLayout, "SettingsCameraGlobal", "camera")
        if cameraLayoutContainer:
            cameraLayoutContainer.use_property_split = False
            self.drawPlugin(context, cameraLayoutContainer, "SettingsMotionBlur")
            self.drawPlugin(context, cameraLayoutContainer, "SettingsCameraDof")

        self.drawPlugin(context, self.layout, "SettingsDefaultDisplacement")
        self.drawPlugin(context, self.layout, "SettingsOptions")


######## ##     ## ########   #######  ########  ######## ######## ########
##        ##   ##  ##     ## ##     ## ##     ##    ##    ##       ##     ##
##         ## ##   ##     ## ##     ## ##     ##    ##    ##       ##     ##
######      ###    ########  ##     ## ########     ##    ######   ########
##         ## ##   ##        ##     ## ##   ##      ##    ##       ##   ##
##        ##   ##  ##        ##     ## ##    ##     ##    ##       ##    ##
######## ##     ## ##         #######  ##     ##    ##    ######## ##     ##

class VRAY_PT_Exporter(classes.VRayRenderPanel):
    bl_label   = "V-Ray Frame Buffer"
    bl_panel_groups = RenderPanelGroups

    def draw(self, context):
        layout = draw_utils.subPanel(self.layout)
        layout.use_property_decorate = False
        vrayExporter = context.scene.vray.Exporter

        if context.scene.render.engine == 'VRAY_RENDER_RT':
            if StartupConfig.debugUI:
                boxDebug = layout.box()
                boxDebug.label(text="Debug options")
                zmqRunning = ZMQ.isRunning()

                stat = 'RUNNING' if zmqRunning else 'STOPPED'
                boxDebug.label(text=f"ZMQ server status: {stat}")
                boxDebug.prop(vrayExporter, 'zmq_port')
                boxDebug.prop(vrayExporter, "debug_threads")
                boxDebug.prop(vrayExporter, "debug_log_times")
                boxDebug.separator()
                row = boxDebug.row(align=True)
                row.prop(vrayExporter, 'export_scene_file_path', text='Vrscene path')
                boxDebug.prop(vrayExporter, 'export_material_preview_scene', text='Export material preview vrscene')
                row.operator('vray.select_vrscene_export_file', text='', icon='FILE_FOLDER')
                exportSceneLayout = boxDebug.row(align=True)
                exportSceneLayout.enabled = not vray.isCommunityEdition()
                exportSceneLayout.operator("vray.export_scene", text="Export vrscene")

            layout.separator()

        # TODO: Uncomment when implemented
        # col.prop(vrayExporter, 'calculate_instancer_velocity')
        # layout.prop(vrayExporter, 'subsurf_to_osd')

        layout.prop(vrayExporter, 'display_vfb_on_top', text="VFB Always On Top")

        layout.separator()

class VRAY_PT_Performance(classes.VRayRenderPanel):
    bl_label   = "Performance"
    bl_options = {'DEFAULT_CLOSED'}
    bl_panel_groups = RenderPanelGroups

    def draw(self, context):
        layout = draw_utils.subPanel(self.layout)
        layout.use_property_decorate = False
        vrayExporter = context.scene.vray.Exporter
        layout.prop(vrayExporter, 'use_custom_thread_count', text='Thread Mode')
        row = layout.row()
        row.alignment = 'RIGHT'
        if sys.platform == "win32":
            row.label(text=f"The system has {psutil.cpu_count()} CPU threads")
        else:
            row.label(text=f"The system has {os.cpu_count()} CPU threads")
        row = layout.row()
        row.enabled = vrayExporter.use_custom_thread_count == 'FIXED'
        row.prop(vrayExporter, 'custom_thread_count', text='Threads')
        if platform.system() != "Linux":
            layout.separator()
            layout.prop(vrayExporter, 'lower_thread_priority', text='Lower Thread Priority')
        layout.separator()


 ######   #######  ##        #######  ########     ##     ##    ###    ##    ##    ###     ######   ######## ##     ## ######## ##    ## ########
##    ## ##     ## ##       ##     ## ##     ##    ###   ###   ## ##   ###   ##   ## ##   ##    ##  ##       ###   ### ##       ###   ##    ##   
##       ##     ## ##       ##     ## ##     ##    #### ####  ##   ##  ####  ##  ##   ##  ##        ##       #### #### ##       ####  ##    ##   
##       ##     ## ##       ##     ## ########     ## ### ## ##     ## ## ## ## ##     ## ##   #### ######   ## ### ## ######   ## ## ##    ##   
##       ##     ## ##       ##     ## ##   ##      ##     ## ######### ##  #### ######### ##    ##  ##       ##     ## ##       ##  ####    ##   
##    ## ##     ## ##       ##     ## ##    ##     ##     ## ##     ## ##   ### ##     ## ##    ##  ##       ##     ## ##       ##   ###    ##   
 ######   #######  ########  #######  ##     ##    ##     ## ##     ## ##    ## ##     ##  ######   ######## ##     ## ######## ##    ##    ##   
class VRAY_PT_ColorManagement(classes.VRayRenderPanel):
    bl_label = "Color Management"
    bl_options = {'DEFAULT_CLOSED'}
    bl_panel_groups = RenderPanelGroups

    vrayPlugins = ["SettingsUnitsInfo"]


class VRAY_PT_GpuTextureOptions(classes.VRayRenderPanel):
    bl_label = "GPU Texture Options"
    bl_panel_groups = RenderPanelGroups

    @classmethod
    def poll_custom(cls, context):
        vrayExporter = context.scene.vray.Exporter
        return vrayExporter.device_type == 'GPU'

    def draw(self, context):
        layout = draw_utils.subPanel(self.layout)
        settingsRTEngine = context.scene.vray.SettingsRTEngine

        layout.use_property_decorate = False
        resizeTexRow = layout.row()
        resizeTexRow.prop(settingsRTEngine, "opencl_resizeTextures")
        resizeTexRow.enabled = not settingsRTEngine.out_of_core_textures
        texSizeRow = layout.row()
        texSizeRow.prop(settingsRTEngine, "opencl_texsize")
        texSizeRow.enabled = settingsRTEngine.opencl_resizeTextures == "1" and not settingsRTEngine.out_of_core_textures
        textureFormatRow = layout.row()
        textureFormatRow.enabled = not settingsRTEngine.out_of_core_textures
        textureFormatRow.prop(settingsRTEngine, "opencl_textureFormat")
        layout.prop(settingsRTEngine, "out_of_core_textures")


#### ##     ##    ###     ######   ########     ######     ###    ##     ## ########  ##       ######## ########
 ##  ###   ###   ## ##   ##    ##  ##          ##    ##   ## ##   ###   ### ##     ## ##       ##       ##     ##
 ##  #### ####  ##   ##  ##        ##          ##        ##   ##  #### #### ##     ## ##       ##       ##     ##
 ##  ## ### ## ##     ## ##   #### ######       ######  ##     ## ## ### ## ########  ##       ######   ########
 ##  ##     ## ######### ##    ##  ##                ## ######### ##     ## ##        ##       ##       ##   ##
 ##  ##     ## ##     ## ##    ##  ##          ##    ## ##     ## ##     ## ##        ##       ##       ##    ##
#### ##     ## ##     ##  ######   ########     ######  ##     ## ##     ## ##        ######## ######## ##     ##

class VRAY_PT_ImageSampler(classes.VRayRenderPanel):
    bl_label = "Image Sampler"
    bl_panel_groups = RenderPanelGroups

    def draw(self, context):
        vrayScene = context.scene.vray
        settingsImageSampler = vrayScene.SettingsImageSampler

        layout = self.layout
        layout.use_property_decorate = False

        classes.drawPluginUI(context, layout, settingsImageSampler, getPluginModule('SettingsImageSampler'))

        # Antialiasing rollout
        panelAntialiasingUniqueId = 'SettingsImageSampler_Antialiasing'
        if panelAntialiasing := draw_utils.rollout(layout, panelAntialiasingUniqueId, "Anti-Aliasing Filter"):
            panelAntialiasing.prop(settingsImageSampler, "filter_type")

            if settingsImageSampler.filter_type != 'NONE':
                filterPluginType = settingsImageSampler.filter_type
                filterPropGroup  = getattr(vrayScene, filterPluginType)

                classes.drawPluginUI(context, panelAntialiasing, filterPropGroup, getPluginModule(filterPluginType))

        # Render Mask rollout
        panelRenderMaskUniqueId = 'SettingsImageSampler_RenderMask'
        if panelRenderMask := draw_utils.rollout(layout, panelRenderMaskUniqueId, "Render Mask"):
            uiPainter = draw_utils.UIPainter(context, getPluginModule('SettingsImageSampler'), settingsImageSampler)
            uiPainter.renderWidgetsSection(panelRenderMask, 'render_mask')

class VRAY_PT_BucketSize(classes.VRayRenderPanel):
    bl_label = "Bucket Options"
    bl_panel_groups = RenderPanelGroups

    vrayPlugins = ["SettingsRegionsGenerator"]

    @classmethod
    def poll_custom(cls, context):
        settingsImageSampler = context.scene.vray.SettingsImageSampler
        return settingsImageSampler.type != "3"

    def draw(self, context):
        layout = self.layout
        vrayScene = context.scene.vray

        if vrayScene.Exporter.device_type == 'CPU':
            col = layout.column()
            col.use_property_split = True
            col.prop(vrayScene.SettingsRegionsGenerator, 'xc', text='Bucket Size')

        classes.drawPluginUI(context, layout, vrayScene.SettingsRegionsGenerator, getPluginModule('SettingsRegionsGenerator'))
     
 ######   ####
##    ##   ##
##         ##
##   ####  ##
##    ##   ##
##    ##   ##
 ######   ####

class VRAY_PT_GI(classes.VRayRenderPanel):
    bl_label = "Global Illumination"
    bl_panel_groups = RenderPanelGroups

    def drawPanelCheckBox(self, context: Context):
        settingsGI = context.scene.vray.SettingsGI
        self.layout.prop(settingsGI, 'on', text="")

    def draw(self, context):
        layout = draw_utils.subPanel(self.layout)
        layout.use_property_decorate = False

        settingsGI = context.scene.vray.SettingsGI
        assert settingsGI.primary_engine == _GI_ENGINE_BRUTE_FORCE, "The Primary GI engine should always be Brute Force"

        layout.active = settingsGI.on
        layout.prop(settingsGI, "secondary_engine", text="Engine")

        layout.separator()
        layout.prop(settingsGI, "use_light_cache_for_interactive", text="Use Light Cache for Interactive Rendering")



########  ########  ##     ## ######## ########    ########  #######  ########   ######  ########
##     ## ##     ## ##     ##    ##    ##          ##       ##     ## ##     ## ##    ## ##
##     ## ##     ## ##     ##    ##    ##          ##       ##     ## ##     ## ##       ##
########  ########  ##     ##    ##    ######      ######   ##     ## ########  ##       ######
##     ## ##   ##   ##     ##    ##    ##          ##       ##     ## ##   ##   ##       ##
##     ## ##    ##  ##     ##    ##    ##          ##       ##     ## ##    ##  ##    ## ##
########  ##     ##  #######     ##    ########    ##        #######  ##     ##  ######  ########

class VRAY_PT_GI_BruteForce(classes.VRayRenderPanel):
    bl_label = "Brute Force"
    bl_panel_groups = RenderPanelGroups

    @classmethod
    def poll_custom(cls, context):
        settingsGI = context.scene.vray.SettingsGI
        return settingsGI.on and (settingsGI.secondary_engine == '2')

    def draw(self, context):
        layout = draw_utils.subPanel(self.layout)

        vsce= context.scene.vray
        module= vsce.SettingsDMCGI

        split= layout.split()
        col= split.column()
        col.prop(module, "depth")


##       ####  ######   ##     ## ########     ######     ###     ######  ##     ## ########
##        ##  ##    ##  ##     ##    ##       ##    ##   ## ##   ##    ## ##     ## ##
##        ##  ##        ##     ##    ##       ##        ##   ##  ##       ##     ## ##
##        ##  ##   #### #########    ##       ##       ##     ## ##       ######### ######
##        ##  ##    ##  ##     ##    ##       ##       ######### ##       ##     ## ##
##        ##  ##    ##  ##     ##    ##       ##    ## ##     ## ##    ## ##     ## ##
######## ####  ######   ##     ##    ##        ######  ##     ##  ######  ##     ## ########

class VRAY_PT_GI_LightCache(classes.VRayRenderPanel):
    bl_label = "Light Cache"
    bl_panel_groups = RenderPanelGroups

    @classmethod
    def poll_custom(cls, context):
        settingsGI = context.scene.vray.SettingsGI
        return settingsGI.on and (settingsGI.secondary_engine == '3')

    def draw(self, context):
        layout = draw_utils.subPanel(self.layout)
        layout.use_property_decorate = False

        vs = context.scene.vray
        pluginModule = getPluginModule('SettingsLightCache')
        painter = draw_utils.UIPainter(context, pluginModule, vs.SettingsLightCache)
        painter.renderPluginUI(layout)


########  ########
##     ## ##     ##
##     ## ##     ##
##     ## ########
##     ## ##   ##
##     ## ##    ##
########  ##     ##

class VRAY_PT_DR(classes.VRayRenderPanel):
    bl_label = "Distributed Rendering"
    bl_panel_groups = RenderPanelGroups

    @classmethod
    def poll_custom(cls, context):
        return vray.withDR2 and pollEngine(context)

    def drawPanelCheckBox(self, context: Context):
        vrayDR = context.scene.vray.VRayDR

        if vray.isCommunityEdition():
            self.bl_label = ""
            ceTooltipRow = self.layout.row()
            ceTooltipRow.label(text=VRAY_PT_DR.bl_label)
            ceTooltipRow.enabled = False
            drawCELimitedFeatureIcon(self.layout)
        else:
            self.bl_label = VRAY_PT_DR.bl_label
            self.layout.prop(vrayDR, "on" , text="")

    def draw(self, context):
        layout = self.layout

        vrayScene       = context.scene.vray
        vrayDR          = vrayScene.VRayDR
        settingsOptions = vrayScene.SettingsOptions
        isLimited       = vray.isCommunityEdition()

        layout.enabled = vrayDR.on and (not isLimited)
        layout.use_property_decorate = False
        layout.use_property_split = True

        layout.prop(vrayDR, 'ignoreInInteractive')
        layout.prop(vrayDR, 'transferAssets')

        sub = layout.row()
        sub.enabled = vrayDR.transferAssets
        sub.prop(settingsOptions, 'misc_useCachedAssets')
        sub = layout.row()
        sub.enabled = vrayDR.transferAssets and settingsOptions.misc_useCachedAssets
        sub.prop(settingsOptions, 'dr_assetsCacheLimitType', text="Limit Cache")
        sub = layout.row()
        sub.enabled = vrayDR.transferAssets and settingsOptions.misc_useCachedAssets and settingsOptions.dr_assetsCacheLimitType != '0'
        sub.prop(settingsOptions, 'dr_assetsCacheLimitValue', text="Limit")

        layout.operator(
            "vray.open_preferences",
            text="Render Hosts", icon="PREFERENCES"
        ).menu_tab = 'PREFERENCES_MENU_DR'



########     ###    ##    ## ########
##     ##   ## ##   ##   ##  ##
##     ##  ##   ##  ##  ##   ##
########  ##     ## #####    ######
##     ## ######### ##  ##   ##
##     ## ##     ## ##   ##  ##
########  ##     ## ##    ## ########

# NOTE:
# Bake-related nodes are registered in utils_bake.py
#


 ######     ###    ##     ##  ######  ######## ####  ######   ######
##    ##   ## ##   ##     ## ##    ##    ##     ##  ##    ## ##    ##
##        ##   ##  ##     ## ##          ##     ##  ##       ##
##       ##     ## ##     ##  ######     ##     ##  ##        ######
##       ######### ##     ##       ##    ##     ##  ##             ##
##    ## ##     ## ##     ## ##    ##    ##     ##  ##    ## ##    ##
 ######  ##     ##  #######   ######     ##    ####  ######   ######

class VRAY_PT_SettingsCaustics(classes.VRayRenderPanel):
    bl_label = "Caustics"
    bl_options = {'DEFAULT_CLOSED'}
    bl_panel_groups = RenderPanelGroups

    def drawPanelCheckBox(self, context):
        self.layout.prop(context.scene.vray.SettingsCaustics, 'on', text="")

    def draw(self, context):
        settingsCaustics = context.scene.vray.SettingsCaustics

        self.layout.active = settingsCaustics.on
        classes.drawPluginUI(context, self.layout, settingsCaustics, getPluginModule('SettingsCaustics'))


########  ########  ######   ####  ######  ######## ########     ###    ######## ####  #######  ##    ##
##     ## ##       ##    ##   ##  ##    ##    ##    ##     ##   ## ##      ##     ##  ##     ## ###   ##
##     ## ##       ##         ##  ##          ##    ##     ##  ##   ##     ##     ##  ##     ## ####  ##
########  ######   ##   ####  ##   ######     ##    ########  ##     ##    ##     ##  ##     ## ## ## ##
##   ##   ##       ##    ##   ##        ##    ##    ##   ##   #########    ##     ##  ##     ## ##  ####
##    ##  ##       ##    ##   ##  ##    ##    ##    ##    ##  ##     ##    ##     ##  ##     ## ##   ###
##     ## ########  ######   ####  ######     ##    ##     ## ##     ##    ##    ####  #######  ##    ##

def getRegClasses():
    return (
        VRAY_PT_Context,

        # Render
        VRAY_PT_Device,
        # Sampler
        VRAY_PT_GpuTextureOptions,
        VRAY_PT_ImageSampler,
        VRAY_PT_BucketSize,

        # GI
        VRAY_PT_GI,
        VRAY_PT_GI_BruteForce,
        VRAY_PT_GI_LightCache,
        VRAY_PT_SettingsCaustics,

        # Globals
        VRAY_PT_Globals,
        VRAY_PT_ColorManagement,
        # System
        VRAY_PT_Exporter,
        VRAY_PT_Performance,
        VRAY_PT_DR,
    )

from bl_ui import properties_render as BlenderRender

_originalPoll = None


@classmethod
def myPoll(cls, context):
    # Return False when V-Ray is the active engine
    return not pollEngine(context)

def register():
    from vray_blender.lib.class_utils import registerClass

    global _originalPoll
    _originalPoll = bpy.types.RENDER_PT_context.poll
    bpy.types.RENDER_PT_context.poll = myPoll

    for regClass in getRegClasses():
        registerClass(regClass)



def unregister():
    for regClass in reversed(getRegClasses()):
        bpy.utils.unregister_class(regClass)

    bpy.types.RENDER_PT_context.poll = _originalPoll
