import os
import re

import bpy
from bpy.props import *
from vray_blender.engine  import vfb_event_handler
from vray_blender.lib     import blender_utils, color_utils, sys_utils, common_settings
from vray_blender.lib.defs import ProdRenderMode
from vray_blender         import debug

from vray_blender.bin import VRayBlenderLib as vray

from vray_blender.engine.renderer_ipr_viewport import VRayRendererIprViewport
from vray_blender.engine.renderer_ipr_vfb import VRayRendererIprVfb
from vray_blender.version import getSceneVersionString, getSceneUpgradeNumber, getAddonUpgradeNumber, checkIfSceneNeedsUpgrade

from vray_blender.lib.mixin import VRayOperatorBase


########  ########
##     ## ##     ##
##     ## ##     ##
##     ## ########
##     ## ##   ##
##     ## ##    ##
########  ##     ##

class VRAY_OT_node_add(VRayOperatorBase):
    bl_idname         = 'vray.render_nodes_add'
    bl_label          = "Add Render Node"
    bl_description    = "Add render node"
    bl_options        = {'INTERNAL'}

    def execute(self, context):
        vs= context.scene.vray
        module= vs.VRayDR

        module.nodes.add()
        module.nodes[-1].name= "Render Node"

        return {'FINISHED'}


class VRAY_OT_node_del(VRayOperatorBase):
    bl_idname         = 'vray.render_nodes_remove'
    bl_label          = "Remove Render Node"
    bl_description    = "Remove render node"
    bl_options        = {'INTERNAL'}
    
    def execute(self, context):
        vs= context.scene.vray
        module= vs.VRayDR

        if module.nodes_selected >= 0:
           module.nodes.remove(module.nodes_selected)
           module.nodes_selected-= 1

        return {'FINISHED'}


class VRAY_OT_dr_nodes_load(VRayOperatorBase):
    bl_idname      = "vray.dr_nodes_load"
    bl_label       = "Load DR Nodes"
    bl_description = "Load distributed rendering nodes list"
    bl_options     = {'INTERNAL'}

    def execute(self, context):
        VRayScene = context.scene.vray
        VRayDR = VRayScene.VRayDR

        nodesFilepath = os.path.join(sys_utils.getUserConfigDir(), "render_nodes.txt")

        if not os.path.exists(nodesFilepath):
            return {'CANCELLED'}

        with open(nodesFilepath, 'r') as nodesFile:
            VRayDR.nodes.clear()

            for line in nodesFile.readlines():
                l = line.strip()
                if not l:
                    continue

                item = VRayDR.nodes.add()

                nodeSetup = l.split(":")

                # Initial format
                if len(nodeSetup) == 2:
                    item.name, item.address = nodeSetup
                # "Use" added
                elif len(nodeSetup) == 3:
                    item.name    = nodeSetup[0]
                    item.address = nodeSetup[1]
                    item.use     = int(nodeSetup[2])
                # Port override added
                elif len(nodeSetup) == 5:
                    item.name = nodeSetup[0]
                    item.address = nodeSetup[1]
                    item.use = int(nodeSetup[2])
                    item.port_override = int(nodeSetup[3])
                    item.port = int(nodeSetup[4])

        VRayDR.nodes_selected = 0

        return {'FINISHED'}


class VRAY_OT_dr_nodes_save(VRayOperatorBase):
    bl_idname      = "vray.dr_nodes_save"
    bl_label       = "Save DR Nodes"
    bl_description = "Save distributed rendering nodes list"
    bl_options     = {'INTERNAL'}

    def execute(self, context):
        VRayScene = context.scene.vray
        VRayDR = VRayScene.VRayDR

        nodesFilepath = os.path.join(sys_utils.getUserConfigDir(), "render_nodes.txt")

        with open(nodesFilepath, 'w') as nodesFile:
            for item in VRayDR.nodes:
                item_data = "{item.name}:{item.address}:{item.use:d}:{item.port_override:d}:{item.port:d}".format(item=item)
                nodesFile.write("%s\n" % item_data)

        return {'FINISHED'}


########  ########  ######   #######  ##       ##     ## ######## ####  #######  ##    ##
##     ## ##       ##    ## ##     ## ##       ##     ##    ##     ##  ##     ## ###   ##
##     ## ##       ##       ##     ## ##       ##     ##    ##     ##  ##     ## ####  ##
########  ######    ######  ##     ## ##       ##     ##    ##     ##  ##     ## ## ## ##
##   ##   ##             ## ##     ## ##       ##     ##    ##     ##  ##     ## ##  ####
##    ##  ##       ##    ## ##     ## ##       ##     ##    ##     ##  ##     ## ##   ###
##     ## ########  ######   #######  ########  #######     ##    ####  #######  ##    ##

class VRAY_OT_flip_resolution(VRayOperatorBase):
    bl_idname      = "vray.flip_resolution"
    bl_label       = "Flip resolution"
    bl_description = "Flip render resolution"
    bl_options     = {'INTERNAL'}
    
    def execute(self, context):
        scene = context.scene
        rd    = scene.render

        VRayScene = scene.vray

        rd.resolution_x, rd.resolution_y = rd.resolution_y, rd.resolution_x
        rd.pixel_aspect_x, rd.pixel_aspect_y = rd.pixel_aspect_y, rd.pixel_aspect_x

        return {'FINISHED'}
    


 ######   #######  ##        #######  ########
##    ## ##     ## ##       ##     ## ##     ##
##       ##     ## ##       ##     ## ##     ##
##       ##     ## ##       ##     ## ########
##       ##     ## ##       ##     ## ##   ##
##    ## ##     ## ##       ##     ## ##    ##
 ######   #######  ########  #######  ##     ##

class VRAY_OT_set_kelvin_color(VRayOperatorBase):
    bl_idname      = "vray.set_kelvin_color"
    bl_label       = "Kelvin color"
    bl_description = "Set color temperature"
    bl_options     = {'INTERNAL'}

    data_path: StringProperty(
        name= "Data",
        description= "Data path",
        maxlen= 1024,
        default= ""
    )

    d_color: EnumProperty(
        name= "Illuminant series D",
        description= "Illuminant series D",
        items= (
            ('D75',  "D75",  "North sky Daylight"),
            ('D65',  "D65",  "Noon Daylight"),
            ('D55',  "D55",  "Mid-morning / Mid-afternoon Daylight"),
            ('D50',  "D50",  "Horizon Light"),
        ),
        default= 'D50'
    )

    use_temperature: BoolProperty(
        name= "Use temperature",
        description= "Use temperature",
        default= False
    )

    temperature: IntProperty(
        name= "Temperature",
        description= "Kelvin temperature",
        min= 800,
        max= 12000,
        default= 5000
    )

    dialog_width = 150

    def draw(self, context):
        layout = self.layout

        split = layout.split()
        col = split.column()
        col.prop(self, 'd_color', text="Type")
        sub = col.row(align=True)
        sub.prop(self, 'use_temperature', text="")
        sub.prop(self, 'temperature', text="K")

    def invoke(self, context, event):
        wm= context.window_manager
        return wm.invoke_props_dialog(self, width = self.dialog_width)

    def execute(self, context):
        D_COLOR= {
            'D75': 7500,
            'D65': 6500,
            'D55': 5500,
            'D50': 5000,
        }

        def recursive_attr(data, attrs):
            if not attrs:
                return data
            attr= attrs.pop()
            return recursive_attr(getattr(data, attr), attrs)

        if self.data_path:
            attrs= self.data_path.split('.')
            attr= attrs.pop() # Attribute to set
            attrs.reverse()

            data_pointer= recursive_attr(context, attrs)

            temperature= D_COLOR[self.d_color]

            if self.use_temperature:
                temperature= self.temperature

            setattr(data_pointer, attr, tuple(color_utils.kelvinToRGB(temperature)))

        return {'FINISHED'}


######## ######## ##     ## ######## ##     ## ########  ########  ######
   ##    ##        ##   ##     ##    ##     ## ##     ## ##       ##    ##
   ##    ##         ## ##      ##    ##     ## ##     ## ##       ##
   ##    ######      ###       ##    ##     ## ########  ######    ######
   ##    ##         ## ##      ##    ##     ## ##   ##   ##             ##
   ##    ##        ##   ##     ##    ##     ## ##    ##  ##       ##    ##
   ##    ######## ##     ##    ##     #######  ##     ## ########  ######

class VRAY_OT_add_sky(VRayOperatorBase):
    bl_idname      = "vray.add_sky"
    bl_label       = "Add Sky texture"
    bl_description = "Add Sky texture to the background"
    bl_options     = {'INTERNAL'}

    def execute(self, context):
        # TODO: Create noded version
        #
        return {'FINISHED'}


##     ## ####  ######   ######
###   ###  ##  ##    ## ##    ##
#### ####  ##  ##       ##
## ### ##  ##   ######  ##
##     ##  ##        ## ##
##     ##  ##  ##    ## ##    ##
##     ## ####  ######   ######

class VRAY_OT_message_box_base(VRayOperatorBase):
    """ Base class for an OK/Cancel message box. It will position the dialog
        in the center of the screen.

        Usage:
        ------
        class MyMsgBox(VRAY_OT_message_box_base):
            ...
            def invoke(self, context, event):
                self._centerDialog(context, event)
                return context.window_manager.invoke_props_dialog(self)
    """
    originalMouseX : bpy.props.IntProperty(default = 0)
    originalMouseY: bpy.props.IntProperty(default = 0)
    mouseMoved: bpy.props.BoolProperty(default=False)

    def _centerDialog(self, context, event):
        # Move the cursor to the center of the screen, as Blender will show the dialog somwhere around the 
        # cursor position. First however save the current cursor position so that we could move it back 
        # once the dialog has been shown, or the user may be confused by the jumping cursor.
        self.originalMouseX = event.mouse_x
        self.originalMouseY = event.mouse_y
        context.window.cursor_warp(int(context.window.width / 2), int(context.window.height / 2))

    
    def modal(self, context, event):
        match event.type:
            case 'RET':
                # User has confirmed the operation. The execute() will be invoked by Blender
                # when this method returns.
                return {'FINISHED'}
            case 'ESC':
                return {'CANCELLED'}
            case _:
                return {"PASS_THROUGH"}
    

    def _cursorWrap(self, context: bpy.types.Context):
        if not self.mouseMoved:
            # Move the cursor back to where the user expects it to be
            self.mouseMoved = True
            context.window.cursor_warp(self.originalMouseX, self.originalMouseY)



class VRAY_OT_add_new_material(VRayOperatorBase):
    bl_idname      = "vray.new_material"
    bl_label       = "Add New Material"
    bl_description = "Add new material"
    bl_options     = {'INTERNAL'}

    def execute(self, context):
        from vray_blender.nodes import tree_defaults

        ma = bpy.data.materials.new(name="Material")
        ma.use_fake_user = True

        tree_defaults.addMaterialNodeTree(ma)

        maIndex = 0
        for i in range(len(bpy.data.materials)):
            if bpy.data.materials[i] == ma:
                break
            maIndex += 1

        VRayExporter = context.scene.vray.Exporter
        VRayExporter.materialListIndex = maIndex

        return {'FINISHED'}


class VRAY_OT_export_scene(VRayOperatorBase):
    bl_idname       = "vray.export_scene"
    bl_label        = "Export V-Ray scene"
    bl_description  = "Export V-Ray scene to a file using the interactive renderer"
    bl_options      = {'INTERNAL'}

    def execute(self, context):
        if renderer := VRayRendererIprVfb.getActiveRenderer() or VRayRendererIprViewport.getActiveRenderer():
            exportSettings, msgErr = common_settings.collectExportSceneSettings(context.scene)

            if exportSettings:
                vray.setRenderFrame(renderer, context.scene.frame_current)
                if not vray.writeVrscene(renderer, exportSettings):
                    self.report({'ERROR'}, "Scene export failed")
                    return { 'FINISHED' }
                msgInfo = f"Exported scene: {exportSettings.filePath}."
                self.report({'INFO'}, msgInfo)
                debug.printInfo(msgInfo)
            elif msgErr:
                self.report({'ERROR'}, msgErr)
                debug.printError(msgErr)
        else:
            msgErr = "Export scene: no active renderer."
            self.report({'ERROR'}, msgErr)
            debug.printError(msgErr)
        return {'FINISHED'}


class VRAY_OT_get_ui_mouse_position(VRayOperatorBase):
    """ Gets the mouse position relative to the current view """
    bl_idname   = "vray.get_ui_mouse_position"
    bl_label    = "Mouse location"
    bl_options  = {'INTERNAL'}
    
    pos_x: bpy.props.IntProperty()
    pos_y: bpy.props.IntProperty()

    def invoke(self, context, event):
        
        region = context.region.view2d  
        ui_scale = context.preferences.system.ui_scale     
        x, y = region.region_to_view(event.mouse_region_x, event.mouse_region_y)
        
        VRAY_OT_get_ui_mouse_position.pos_x = x / ui_scale
        VRAY_OT_get_ui_mouse_position.pos_y = y / ui_scale

        return {'FINISHED'}
    
class VRAY_OT_select_vrscene_export_file(VRayOperatorBase):
    """ Shows a File Select dialog for selecting a an output file
        for the vrscene export operation.
    """
    bl_idname       = "vray.select_vrscene_export_file"
    bl_label        = "V-Ray Select vrscene file for output"
    bl_description  = "Select an output .vrscene file"
    bl_options      = {'INTERNAL'}
    
    filepath: bpy.props.StringProperty(subtype="FILE_PATH")
    
    def invoke(self, context, event):
        from pathlib import Path
        exporter = context.scene.vray.Exporter

        if filePath := exporter.export_scene_file_path:
            self.filepath = filePath
        elif blendPath := context.blend_data.filepath:
            self.filepath = str(Path(blendPath).with_suffix(".vrscene"))
        else:
            self.filepath = 'untitled.vrscene'

        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        from pathlib import Path
        context.scene.vray.Exporter.export_scene_file_path = self.filepath
        context.area.tag_redraw()
        return {'FINISHED'}


class VRAY_OT_render(VRAY_OT_message_box_base):
    bl_idname = "vray.render"
    bl_label = "Start Production Render"
    bl_description = "Render scene using the V-Ray renderer"

    class _ErrorType:
        NoError = 0
        FileExists = 1
        InvalidFileName = 2
        InvalidFolderName = 3

    errorType: bpy.props.IntProperty(default=_ErrorType.NoError)
    errorMsg:  bpy.props.StringProperty() # Additional error info in case of a failed check

    @classmethod
    def description(cls, context, properties):
        if vray.isInitialized():
            return cls.bl_description
        return f"{cls.bl_description}. Unavailable until V-Ray is initialized"
    
    def execute(self, context):
        if vray.isInitialized():
            # This status message should ideally be printed right before the rendering starts but here is the last
            # chance for it to be shown BEFORE the render job is complete. Once the operator starts executing, no 
            # updates to the UI will be made until it's finished.
            debug.report('INFO', 'Started render job. Blender UI will be unresponsive until the rendering is complete')
            vfb_event_handler.VfbEventHandler.startProdRender()
        else:
            debug.report('WARNING', "Can't start render job. V-Ray is not initialized")
        return {'FINISHED'}

    def _checkOutputInfo(self, context):
        """ Checks if there is rendered result with the same name """

        from vray_blender.external.pathvalidate import is_valid_filename
        from vray_blender.lib.path_utils import expandPathVariables, getOutputFileName

        # Reset any previously set values
        self.errorType = __class__._ErrorType.NoError
        self.errorMsg = ''

        if not context.scene.vray.Exporter.auto_save_render:
            # The Output rollout is disabled, no images will be written to disk
            return True
        
        settingsOutput = context.scene.vray.SettingsOutput

        # Validate the output folder and try to create it
        if not (imgDir := expandPathVariables(context, settingsOutput.img_dir)):
            self.errorType = __class__._ErrorType.InvalidFolderName
            return False
                
        if not settingsOutput.img_file or \
            not is_valid_filename(expandPathVariables(context, settingsOutput.img_file)):
            self.errorType = __class__._ErrorType.InvalidFileName
            return False


        try:
            os.makedirs(imgDir, exist_ok=True)
        except Exception as exc:
            self.errorType = __class__._ErrorType.InvalidFolderName
            self.errorMsg = str(exc)
            return False   

        if not settingsOutput.output_overwrite_warn:
            return True
        
        imgFiles = [] # When rendering multiple view layers and animations the output images will be more than one
        imgFmt = int(settingsOutput.img_format)
        viewLayers = [layer for layer in context.scene.view_layers if layer.use]
        for layer in viewLayers:
            layerName = layer.name if len(viewLayers) > 1 else ""
            imgFile = getOutputFileName(context,  settingsOutput.img_file, imgFmt, layerName)

            if context.scene.vray.Exporter.animation_mode == 'ANIMATION':
                # Rendered results of the animation have digits representing their frame numbers.
                # Instead of comparing the image to the files in the directory,
                # it is matched to a regex pattern representing a file with a frame number and zeros in front of it.
                name, extension = os.path.splitext(imgFile)
                for frame in range(context.scene.frame_start, context.scene.frame_end + 1):
                    # Using regex matching instead of adding zeros in front of the frame number and comparing file names
                    # is a more reliable way to check for file existence, because the zeros in the name are generated by V-Ray
                    # and we can't be sure how many there will be.
                    imgFiles.append(f"^{name}.0*{frame}\\{extension}$")
            else:
                imgFiles.append(imgFile)


        if any( any(re.match(imgFile, file) for imgFile in imgFiles) for file in os.listdir(imgDir)):
            self.errorType = __class__._ErrorType.FileExists
            
        return not self.errorType

        
    def invoke(self, context, event):
        if not self._checkOutputInfo(context):
            # Invoking props dialog that warns the user that the new render job will 
            # overwrite the render result
            self._centerDialog(context, event)
            return context.window_manager.invoke_props_dialog(self, width=400)
        
        return self.execute(context)


    def draw(self, context):
        layout = self.layout

        match self.errorType:
            case __class__._ErrorType.InvalidFolderName | __class__._ErrorType.InvalidFileName:
                alias = 'folder' if self.errorType == __class__._ErrorType.InvalidFolderName else 'file'
                layout.label(text=f"The output {alias} name for the rendered image(s) is empty or invalid.")
                
                if self.errorMsg:
                    layout.label(text=f"    {self.errorMsg}.")
                
                layout.label(text="Render output will not be saved.")
                layout.label(text="Do you still want to render?")
            
            case __class__._ErrorType.FileExists:
                layout.label(text="An output image with the same name already exists.")
                layout.label(text="Overwrite it?")
                layout.prop(context.scene.vray.SettingsOutput, "output_overwrite_warn", text="Always ask me.")
            
            case _:
                assert not f'Invalid message selector: {self.errorType}'


class VRAY_OT_render_viewport(VRayOperatorBase):
    bl_idname = "vray.render_viewport"
    bl_label = "Start Viewport Render"
    bl_description = "Render scene using the viewport V-Ray renderer"

    def execute(self, context):
        # The order of the following commands to ZmqServer is important. Opening VFB
        # first might lead to an irrecoverable V-Ray Core error.
        vfb_event_handler.VfbEventHandler.startViewportRender()
        # This operator is only invoked from the main menu and this is the only case
        # in which we want to open the VFB for an interactive rendering session.
        vray.openVFB()
        return {'FINISHED'}


class VRAY_OT_render_interactive(VRayOperatorBase):
    bl_idname = "vray.render_interactive"
    bl_label = "Start Interactive Render"
    bl_description = "Render scene using the interactive V-Ray renderer"

    def execute(self, context):
        # The order of the following commands to ZmqServer is important. Opening VFB
        # first might lead to an irrecoverable V-Ray Core error.
        vfb_event_handler.VfbEventHandler.stopViewportRender()
        vfb_event_handler.VfbEventHandler.startInteractiveRender()

        # This operator is only invoked from the main menu and this is the only case
        # in which we want to open the VFB for an interactive rendering session.
        vray.openVFB()
        return {'FINISHED'}
    

class VRAY_OT_render_interactive_stop(VRayOperatorBase):
    bl_idname = "vray.render_interactive_stop"
    bl_label = "Stop Interactive Render"
    bl_description = "Stop render of scene using the interactive V-Ray renderer"

    def execute(self, context):
        vfb_event_handler.VfbEventHandler.stopInteractiveRender()
        return {'FINISHED'}




class VRAY_OT_export_vrscene(VRAY_OT_message_box_base):
    bl_idname = "vray.export_vrscene"
    bl_label = "Save as V-Ray"
    bl_description = "Export scene to .vrscene file using V-Ray production renderer.\nV-Ray must be the active renderer for this command to be enabled."

    exportView: bpy.props.BoolProperty(
        name='View',
        description='Export view'
    )

    exportLights: bpy.props.BoolProperty(
        name='Light',
        description='Export lights'
    )

    exportGeometry: bpy.props.BoolProperty(
        name='Geometry',
        description='Export geometry'
    )

    exportNodes: bpy.props.BoolProperty(
        name='Nodes',
        description='Export nodes'
    )

    exportMaterials: bpy.props.BoolProperty(
        name='Materials',
        description='Export materials'
    )

    exportTextures: bpy.props.BoolProperty(
        name='Textures',
        description='Export textures'
    )

    exportBitmaps: bpy.props.BoolProperty(
        name='Bitmaps',
        description='Export bitmaps'
    )

    exportRenderChannels: bpy.props.BoolProperty(
        name='Render Channels',
        description='Export render channels'
    )

    synchronous: bpy.props.BoolProperty(
        default=False,
        description="If True, run the export operator directly, not through VfbEventHandler" 
    )

    def execute(self, context):
        debug.report('INFO', 'Started .vrscene export. Blender UI will be unresponsive until the operation is complete.')
        fileTypes = []
        vrayExporter = context.scene.vray.Exporter

        if vrayExporter.export_scene_separate_files:
            if self.exportView:             fileTypes.append('view')
            if self.exportLights:           fileTypes.append('lights')
            if self.exportGeometry:         fileTypes.append('geometry')
            if self.exportNodes:            fileTypes.append('nodes')
            if self.exportMaterials:        fileTypes.append('materials')
            if self.exportTextures:         fileTypes.append('textures')
            if self.exportBitmaps:          fileTypes.append('bitmaps')
            if self.exportRenderChannels:   fileTypes.append('render_elements')

        vrayExporter.export_scene_plugin_types = ','.join(fileTypes)
        
        if self.synchronous:
            vfb_event_handler.VfbEventHandler.startProdRenderSync(renderMode=ProdRenderMode.EXPORT_VRSCENE)
        else:
            vfb_event_handler.VfbEventHandler.exportVrscene()

        return {'FINISHED'}

    def invoke(self, context, event):
        
        vrayExporter = context.scene.vray.Exporter
        fileCategories = vrayExporter.export_scene_plugin_types.split(',')

        for category in fileCategories:
            if category == 'view':              self.exportView = True
            if category == 'lights':            self.exportLights = True
            if category == 'geometry':          self.exportGeometry = True
            if category == 'nodes':             self.exportNodes = True
            if category == 'materials':         self.exportMaterials = True
            if category == 'textures':          self.exportTextures = True
            if category == 'bitmaps':           self.exportBitmaps = True
            if category == 'render_elements':   self.exportRenderChannels = True

        self._centerDialog(context, event)
        
        return context.window_manager.invoke_props_dialog(self, width=400, title="Export V-Ray .vrscene file", confirm_text="Export")
    
    def draw(self, context: bpy.types.Context):
        layout = self.layout.box()
        layout.use_property_decorate = False

        vrayExporter = context.scene.vray.Exporter
        
        row = layout.row(align=True)
        row.prop(vrayExporter, 'export_scene_file_path')
        row.operator('vray.select_vrscene_export_file', text='', icon='FILE_FOLDER')
       
        layout.separator()
        
        layoutCompressed = layout.column(align=True)
        layoutCompressed.enabled = vrayExporter.export_scene_hex_meshes
        layoutCompressed.prop(vrayExporter, 'export_scene_compressed')
        
        layout.prop(vrayExporter, 'export_scene_hex_meshes')
        layout.prop(vrayExporter, 'export_scene_hex_transforms')
        layout.prop(vrayExporter, 'export_scene_separate_files')
        
        splitOuter = layout.split(factor=0.1)
        splitOuter.column()
        splitOuter.enabled = splitOuter.active = vrayExporter.export_scene_separate_files

        boxOuter = splitOuter.column()
        splitInner = boxOuter.split()
      
        col1 = splitInner.column()
        col1.prop(self, 'exportView')
        col1.prop(self, 'exportLights')
        col1.prop(self, 'exportGeometry')
        col1.prop(self, 'exportNodes')
        
        col2 = splitInner.column()
        col2.prop(self, 'exportMaterials')
        col2.prop(self, 'exportTextures')
        col2.prop(self, 'exportBitmaps')
        col2.prop(self, 'exportRenderChannels')

        self._cursorWrap(context)



class VRAY_OT_cloud_submit(VRAY_OT_message_box_base):
    bl_idname = "vray.cloud_submit"
    bl_label = "Submit to Cloud"
    bl_description = "Submit scene for Cloud rendering using V-Ray production renderer"
 
    originalMouseX : bpy.props.IntProperty(default = 0)
    originalMouseY: bpy.props.IntProperty(default = 0)
    mouseMoved: bpy.props.BoolProperty(default=False)

    def execute(self, context):
        debug.report('INFO', 'Started Chaos Cloud submission. Blender UI will be unresponsive until the operation is complete.')
        vfb_event_handler.VfbEventHandler.cloudSubmit()
        return {'FINISHED'}

    def invoke(self, context, event):
        self._centerDialog(context, event)
        return context.window_manager.invoke_props_dialog(self, width=400, title="Submit to Cloud", confirm_text="Submit")
    
    def draw(self, context: bpy.types.Context):
        VRayExporter = context.scene.vray.Exporter

        self.layout.prop(VRayExporter, 'vray_cloud_project_name')
        self.layout.prop(VRayExporter, 'vray_cloud_job_name')     
        
        self._cursorWrap(context)


class VRAY_OT_copy_plugin_version(VRayOperatorBase):
    bl_idname       = "vray.copy_plugin_version"
    bl_label        = "Copy V-Ray plugin version to clipboard"
    bl_description  = "Copy to clipboard the version of the V-Ray plugin with which the scene was last saved."
    
    def execute(self, context):
        version = getSceneVersionString()
        debug.printAlways(f">>> Scene was created with VRay for Blender version [{version}]")
        sys_utils.copyToClipboard(version)
        
        debug.report('INFO', 'Plugin version copied to clipboard.')
        return {'FINISHED'}


class VRAY_OT_upgrade_scene(VRAY_OT_message_box_base):
    bl_idname       = "vray.upgrade_scene"
    bl_label        = "Update Blender scene to the current version of the V-Ray plugin"
    bl_description  = "If the opened scene has been created with a previous version of the V-Ray plugin, update it to the current version."
    
    def execute(self, context):
        from vray_blender.version import getSceneUpgradeNumber, getAddonUpgradeNumber, upgradeScene

        sceneUpgradeNum = getSceneUpgradeNumber()
        addonUpgradeNum = getAddonUpgradeNumber()

        # invoke() won't be called in headless mode so perform the same version number checks here
        if sceneUpgradeNum != addonUpgradeNum:
            if upgradeScene(sceneUpgradeNum, addonUpgradeNum):
                return {'FINISHED'}
        
        return {'CANCELLED'}


    def invoke(self, context, event):
        sceneUpgradeNum = getSceneUpgradeNumber()
        addonUpgradeNum = getAddonUpgradeNumber()

        if sceneUpgradeNum == '0000':
            # Scene was produced by Blender with no V-Ray installed, no need to upgrade.
            # The latest version will be set to the scene when it is saved.
            return {'CANCELLED'}
        
        # Often the scene will not need to be upgarded because it does not contain data that needs
        # to be upgraded. Do a precheck and spare the user the upgrade dialog.
        if not checkIfSceneNeedsUpgrade(sceneUpgradeNum, addonUpgradeNum):
            return {'CANCELLED'}
        
        if sceneUpgradeNum != addonUpgradeNum:
            self._centerDialog(context, event)
            return context.window_manager.invoke_props_dialog(self, width=400, title="V-Ray Scene Version Update", confirm_text="OK")
        
        return {'CANCELLED'}
    

    def draw(self, context: bpy.types.Context):
        from vray_blender.events import isDefaultScene
        
        sceneAlias = "default scene" if isDefaultScene() else "scene"

        self.layout.label(text = f"The {sceneAlias} was created with an older version of V-Ray for Blender.")
        
        self.layout.separator()
        self.layout.label(text="Click OK to run the update procedure.")
        self.layout.label(text="If everything goes well, save the .blend file.")
        self.layout.label(text="If you encounter any problems, look in the console for error messages.")
        self._cursorWrap(context)

    @classmethod
    def poll(cls, context):
        # The operator vray.upgrade_scene should be callable even if the default engine is not V-Ray.
        return True


def getRegClasses():
    return (
        VRAY_OT_node_add,
        VRAY_OT_node_del,
        VRAY_OT_dr_nodes_load,
        VRAY_OT_dr_nodes_save,
        VRAY_OT_flip_resolution,
        VRAY_OT_set_kelvin_color,
        VRAY_OT_add_sky,

        VRAY_OT_add_new_material,
        VRAY_OT_export_scene,
        VRAY_OT_get_ui_mouse_position,
        VRAY_OT_select_vrscene_export_file,
        VRAY_OT_render,
        VRAY_OT_render_interactive,
        VRAY_OT_render_interactive_stop,
        VRAY_OT_render_viewport,
        VRAY_OT_export_vrscene,
        VRAY_OT_cloud_submit,

        VRAY_OT_copy_plugin_version,
        VRAY_OT_upgrade_scene,
    )


def register():
    for regClass in getRegClasses():
        bpy.utils.register_class(regClass)


def unregister():
    for regClass in getRegClasses():
        bpy.utils.unregister_class(regClass)
