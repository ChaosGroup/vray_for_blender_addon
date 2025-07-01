import bpy, platform
from bpy.types import Context


from vray_blender.engine.renderer_ipr_viewport import VRayRendererIprViewport
from vray_blender.engine import ZMQ
from vray_blender.external import psutil
from vray_blender.lib import draw_utils
from vray_blender.lib.sys_utils import StartupConfig, isGPUEngine
from vray_blender.operators import VRAY_OT_render
from vray_blender.plugins import getPluginModule
from vray_blender.ui import classes
from vray_blender.ui.classes import RenderPanelGroups
from vray_blender.bin import VRayBlenderLib as vray

_GI_ENGINE_BRUTE_FORCE = '2'
_GI_ENGINE_LIGHT_CACHE = '3'

def getRenderIcon(vrayExporter):
    renderIcon = 'RENDER_ANIMATION'
    if vrayExporter.animation_mode == 'FRAME':
        renderIcon = 'NONE'
    return renderIcon


def _drawRendererSelector(self, context):
    """ Draw V-Ray-specific render engine/device selectors """
    if context.engine == 'VRAY_RENDER_RT':
        layout = self.layout
        vrayExporter = context.scene.vray.Exporter

        # Render engine/device cannot be changed if a rendering job is under way.
        enabled = not VRayRendererIprViewport.isActive()

        layoutDevice = layout.column()
        layoutDevice.use_property_split = True
        layoutDevice.use_property_decorate = False
        layoutDevice.enabled = enabled
        layoutDevice.active = enabled

        layoutDevice.prop(vrayExporter, "device_type", text="V-Ray Engine", )

        if vrayExporter.device_type == 'GPU':
            layoutDevice.separator()
            gpuDeviceRow = layoutDevice.row()
            gpuDeviceRow.alignment = 'CENTER'
            gpuDeviceRow.label(text=f"GPU Device:")

            gpuDeviceBox = gpuDeviceRow.box()
            gpuDeviceBox.alignment = 'CENTER'
            gpuDeviceBox.scale_y = 0.5  # This scale makes GPU Device aligned vertically
            gpuDeviceBox.label(text='RTX' if vrayExporter.use_gpu_rtx else 'CUDA')

            # Adding the use_gpu_rtx prop into new centered row,
            # so it can be aligned with the "GPU Device" label.
            rtxRow = layoutDevice.row()
            rtxRow.alignment = 'CENTER'
            rtxRow.scale_x = 2
            rtxRow.prop(vrayExporter, "use_gpu_rtx")

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

        vrayScene = scene.vray
        vrayBake     = vrayScene.BakeView

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
        return VRayExporter.device_type in {'GPU'}



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
        # The active state of camera settings depends on the GI settings
        settingsGI = context.scene.vray.SettingsGI
        cameraLayout = self.layout.column()
        cameraLayout.active = settingsGI.on and (settingsGI.secondary_engine == _GI_ENGINE_LIGHT_CACHE)

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
                row.prop(vrayExporter, 'export_scene_file_path', text='Scene path')
                row.operator('vray.select_vrscene_export_file', text='', icon='FILE_FOLDER')
                boxDebug.operator("vray.export_scene", text="Export scene")


            layout.separator()

        # TOOD: Uncomment when imelemented
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
        row.label(text=f"The system has {psutil.cpu_count()} CPU threads")
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

        resizeTexRow = layout.row()
        resizeTexRow.prop(settingsRTEngine, "opencl_resizeTextures")
        resizeTexRow.enabled = not settingsRTEngine.out_of_core_textures
        texSizeRow = layout.row()
        texSizeRow.prop(settingsRTEngine, "opencl_texsize")
        texSizeRow.enabled = settingsRTEngine.opencl_resizeTextures == "1" and not settingsRTEngine.out_of_core_textures
        layout.prop(settingsRTEngine, "opencl_textureFormat")
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

        classes.drawPluginUI(context, layout, settingsImageSampler, getPluginModule('SettingsImageSampler'))
        
        # Antialiasing rollout
        panelAntialiasingUniqueId = f'SettingsImageSampler_Antialiasing'
        if panelAntialiasing := draw_utils.rollout(layout, panelAntialiasingUniqueId, "Anti-Aliasing Filter"):
            panelAntialiasing.use_property_decorate = False
            panelAntialiasing.prop(settingsImageSampler, "filter_type")

            if settingsImageSampler.filter_type not in {'NONE'}:
                filterPluginType = settingsImageSampler.filter_type
                filterPropGroup  = getattr(vrayScene, filterPluginType)

                classes.drawPluginUI(context, panelAntialiasing, filterPropGroup, getPluginModule(filterPluginType))
            
        # Render Mask rollout
        panelRenderMaskUniqueId = f'SettingsImageSampler_RenderMask'
        if panelRenderMask := draw_utils.rollout(layout, panelRenderMaskUniqueId, "RenderMask"):
            panelRenderMask.use_property_decorate = False
            panelRenderMask.prop(settingsImageSampler, 'render_mask_mode', text="Type")
        
            match settingsImageSampler.render_mask_mode:
                case '1': # Texture
                    # Disable the Texture field when the IPR is active, as changing the texture
                    # in this case does not work correctly in V-Ray.
                    col = panelRenderMask.column()
                    col.active = not VRayRendererIprViewport.isActive()
                    col.enabled = col.active
                    col.prop(settingsImageSampler, 'render_mask_texture_file')
                case '2': # Objects
                    panelRenderMask.prop_search(settingsImageSampler, 'render_mask_object_selector',  context.scene, 'objects', text="Object")
                    panelRenderMask.prop_search(settingsImageSampler, 'render_mask_collection_selector',  bpy.data, 'collections', text="Collection")
                case '3': # Object IDs
                    panelRenderMask.prop(settingsImageSampler, 'render_mask_object_ids_list')


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

        settingsGI = context.scene.vray.SettingsGI
        assert settingsGI.primary_engine == _GI_ENGINE_BRUTE_FORCE, "The Primary GI engine should always be Brute Force"

        layout.active = settingsGI.on
        layout.prop(settingsGI, "secondary_engine", text="Engine:")

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
        module = vs.SettingsLightCache

        layout.prop(module, "mode", text="Mode")

        layout.separator()

        if not module.mode == '2':
            layout.prop(module, "subdivs")
            layout.prop(module, "sample_size")
        
        layout.separator()
        layout.prop(module, "retrace_enabled")
        sub = layout.column()
        sub.active = module.retrace_enabled
        sub.prop(module, "retrace_threshold", text="Retrace Threshold")

        layout.prop(module, "path_guiding", text="Path Guiding (Experimental)")

        layout.separator()
        if module.mode == '2':
            layout.prop(module,"file", text="File Name")
        else:
            layout.prop(module,"auto_save", text="Auto Save")
            sub = layout.column()
            sub.active = module.auto_save
            sub.prop(module,"auto_save_file", text="File")



########  ########
##     ## ##     ##
##     ## ##     ##
##     ## ########
##     ## ##   ##
##     ## ##    ##
########  ##     ##

class VRAY_PT_DR(classes.VRayRenderPanel):
    bl_label = "Distributed Rendering"
    bl_options = {'DEFAULT_CLOSED'}
    bl_panel_groups = RenderPanelGroups

    def drawPanelCheckBox(self, context: Context):
        vrayDR = context.scene.vray.VRayDR
        self.layout.prop(vrayDR, "on", text="")
    
    def draw(self, context):
        layout = self.layout

        vrayScene       = context.scene.vray
        vrayDR          = vrayScene.VRayDR
        settingsOptions = vrayScene.SettingsOptions

        layout.active = vrayDR.on
        layout.prop(vrayDR, 'assetSharing')
        layout.separator()

        if vrayDR.assetSharing == 'SHARE':
            layout.prop(vrayDR, 'networkType')
            layout.prop(vrayDR, 'shared_dir')
            if vrayDR.networkType == 'WW':
                layout.prop(vrayDR, 'share_name')
            layout.separator()

        elif vrayDR.assetSharing == 'TRANSFER':
            split = layout.split()
            col = split.column()
            col.prop(vrayDR, 'checkAssets')
            col.prop(vrayDR, 'renderOnlyOnNodes')
            col = split.column()
            col.prop(settingsOptions, 'misc_useCachedAssets')
            col.prop(settingsOptions, 'misc_abortOnMissingAsset')
            col.prop(settingsOptions, 'dr_overwriteLocalCacheSettings')

            split= layout.split()
            split.active = settingsOptions.dr_overwriteLocalCacheSettings
            col= split.column()
            col.prop(settingsOptions, 'dr_assetsCacheLimitType', text="Cache Limit")
            sub = col.row()
            sub.active = settingsOptions.dr_assetsCacheLimitType != '0'
            sub.prop(settingsOptions, 'dr_assetsCacheLimitValue', text="Limit")
            layout.separator()

        layout.prop(vrayDR, 'port', text="Port")
        layout.prop(vrayDR, 'limitHosts')
        layout.separator()

        split= layout.split()
        row= split.row()
        row.template_list("VRAY_UL_DR", "", vrayDR, 'nodes', vrayDR, 'nodes_selected', rows= 3)
        col= row.column(align=True)
        col.operator('vray.render_nodes_add',    text="", icon="ADD")
        col.operator('vray.render_nodes_remove', text="", icon="REMOVE")

        col = col.row().column(align=True)
        col.operator('vray.dr_nodes_load',       text="", icon="FILE_FOLDER")
        col.operator('vray.dr_nodes_save',       text="", icon='DISK_DRIVE')

        if vrayDR.nodes_selected >= 0 and len(vrayDR.nodes) > 0:
            render_node= vrayDR.nodes[vrayDR.nodes_selected]

            layout.separator()

            layout.prop(render_node, 'name')
            layout.prop(render_node, 'address')

            split = layout.split()
            col = split.column()
            col.prop(render_node, 'port_override', text="Override Port")
            col = split.column()
            col.active = render_node.port_override
            col.prop(render_node, 'port')


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


 ######  ##    ##  ######  ######## ######## ##     ##
##    ##  ##  ##  ##    ##    ##    ##       ###   ###
##         ####   ##          ##    ##       #### ####
 ######     ##     ######     ##    ######   ## ### ##
      ##    ##          ##    ##    ##       ##     ##
##    ##    ##    ##    ##    ##    ##       ##     ##
 ######     ##     ######     ##    ######## ##     ##

class VRAY_PT_SettingsSystem(classes.VRayRenderPanel):
    bl_label   = "Logging"
    bl_options = {'DEFAULT_CLOSED'}
    bl_panel_groups = RenderPanelGroups

    def draw(self, context):
        layout = draw_utils.subPanel(self.layout)
        layout.use_property_decorate = False
        layout.prop(context.scene.vray.Exporter, 'verbose_level')



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
        VRAY_PT_SettingsSystem,
        # VRAY_PT_DR,
    )


def register():
    from vray_blender.lib.class_utils import registerClass

    # Add V-Ray renderer selectors to the stock Render context panel, 
    # just below the Render engine selector
    bpy.types.RENDER_PT_context.append(_drawRendererSelector)

    for regClass in getRegClasses():
        registerClass(regClass)



def unregister():
    for regClass in reversed(getRegClasses()):
        bpy.utils.unregister_class(regClass)

    bpy.types.RENDER_PT_context.remove(_drawRendererSelector)
