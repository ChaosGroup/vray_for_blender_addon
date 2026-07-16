# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import glob
import os
import re
import pathlib
import platform
import time

import bpy
from bpy_extras.io_utils import ImportHelper

from vray_blender.engine   import vfb_event_handler
from vray_blender.lib      import blender_utils, color_utils, sys_utils, common_settings, path_utils
from vray_blender.lib.defs import ProdRenderMode
from vray_blender          import debug

from vray_blender.bin import VRayBlenderLib as vray
from vray_blender.ui.community_edition import drawCELimitedFeatureWarning

from vray_blender.engine.render_engine import VRayRenderEngine
from vray_blender.engine.renderer_ipr_viewport import VRayRendererIprViewport
from vray_blender.engine.renderer_ipr_vfb import VRayRendererIprVfb
from vray_blender.version import getSceneVersionString, getSceneUpgradeNumber, getAddonUpgradeNumber, checkIfSceneNeedsUpgrade
from vray_blender.ui.community_edition import getLimitedFeatureDescription, getCELimitedFeatureMsg

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
    bl_options        = {'INTERNAL', 'UNDO'}

    def execute(self, context):
        preferences = blender_utils.getVRayPreferences(context)

        preferences.nodes.add()
        preferences.nodes[-1].name = "Render Node"
        preferences.nodes_selected = len(preferences.nodes) - 1

        return {'FINISHED'}


class VRAY_OT_node_del(VRayOperatorBase):
    bl_idname         = 'vray.render_nodes_remove'
    bl_label          = "Remove Render Node"
    bl_description    = "Remove render node"
    bl_options        = {'INTERNAL', 'UNDO'}

    def execute(self, context):
        preferences = blender_utils.getVRayPreferences(context)

        if preferences.nodes_selected >= 0:
            preferences.nodes.remove(preferences.nodes_selected)
            preferences.nodes_selected = max(0, preferences.nodes_selected - 1)

        return {'FINISHED'}


class VRAY_OT_dr_nodes_load(VRayOperatorBase):
    bl_idname      = "vray.dr_nodes_load"
    bl_label       = "Load DR Nodes"
    bl_description = "Load distributed rendering nodes list"
    bl_options     = {'INTERNAL', 'UNDO'}

    def execute(self, context):
        preferences = blender_utils.getVRayPreferences(context)

        nodesFilepath = os.path.join(sys_utils.getUserConfigDir(), "render_nodes.txt")

        if not os.path.exists(nodesFilepath):
            return {'CANCELLED'}

        with open(nodesFilepath, 'r') as nodesFile:
            preferences.nodes.clear()

            for line in nodesFile.readlines():
                l = line.strip()
                if not l:
                    continue

                item = preferences.nodes.add()

                nodeSetup = l.split(":")
                if len(nodeSetup) == 4:
                    item.name     = nodeSetup[0]
                    item.nodeName = nodeSetup[0]
                    item.address  = nodeSetup[1]
                    item.use      = int(nodeSetup[2])
                    item.port     = int(nodeSetup[3])

        preferences.nodes_selected = 0

        return {'FINISHED'}


class VRAY_OT_dr_nodes_save(VRayOperatorBase):
    bl_idname      = "vray.dr_nodes_save"
    bl_label       = "Save DR Nodes"
    bl_description = "Save distributed rendering nodes list"
    bl_options     = {'INTERNAL'}

    def execute(self, context):
        preferences = blender_utils.getVRayPreferences(context)

        nodesFilepath = os.path.join(sys_utils.getUserConfigDir(), "render_nodes.txt")

        with open(nodesFilepath, 'w') as nodesFile:
            for item in preferences.nodes:
                item_data = "{item.nodeName}:{item.address}:{item.use:d}:{item.port:d}".format(item=item)
                nodesFile.write("%s\n" % item_data)

        return {'FINISHED'}


class VRAY_OT_open_preferences(VRayOperatorBase):
    bl_idname      = "vray.open_preferences"
    bl_label       = "Open V-Ray Preferences"
    bl_description = ("Open the V-Ray preferences menu")

    menu_tab: bpy.props.StringProperty(default="NONE")

    def invoke(self, context, event):
        valid_view_modes = [
            'PREFERENCES_MENU_GENERAL',
            'PREFERENCES_MENU_GPU_DEVICES',
            'PREFERENCES_MENU_DR'
        ]
        if self.menu_tab in valid_view_modes:
            prefs = blender_utils.getVRayPreferences(context)
            prefs.preferences_menu = self.menu_tab
            if self.menu_tab == 'PREFERENCES_MENU_GPU_DEVICES':
                from vray_blender.plugins.system.compute_devices import getDeviceTypeByName
                sceneType = bpy.context.scene.vray.Exporter.gpu_device_type
                prefs.compute_devices.gpuDeviceType = getDeviceTypeByName(sceneType)

        blender_utils.showVRayPreferences()
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

    data_path: bpy.props.StringProperty(
        name= "Data",
        description= "Data path",
        maxlen= 1024,
        default= ""
    )

    d_color: bpy.props.EnumProperty(
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

    use_temperature: bpy.props.BoolProperty(
        name= "Use temperature",
        description= "Use temperature",
        default= False
    )

    temperature: bpy.props.IntProperty(
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
        wm = context.window_manager
        return wm.invoke_props_dialog(self, width=self.dialog_width)

    def execute(self, context):
        D_COLOR = {
            'D75': 7500,
            'D65': 6500,
            'D55': 5500,
            'D50': 5000,
        }

        def recursive_attr(data, attrs):
            if not attrs:
                return data
            attr = attrs.pop()
            return recursive_attr(getattr(data, attr), attrs)

        if self.data_path:
            attrs = self.data_path.split('.')
            attr = attrs.pop() # Attribute to set
            attrs.reverse()

            data_pointer = recursive_attr(context, attrs)

            temperature = D_COLOR[self.d_color]

            if self.use_temperature:
                temperature = self.temperature

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
        # Move the cursor to the center of the screen, as Blender will show the dialog somewhere around the
        # cursor position. First however save the current cursor position so that we could move it back
        # once the dialog has been shown, or the user may be confused by the jumping cursor.
        self.originalMouseX = event.mouse_x
        self.originalMouseY = event.mouse_y
        context.window.cursor_warp(int(context.window.width / 2), int(context.window.height / 2))


    def _cursorWrap(self, context: bpy.types.Context):
        if not self.mouseMoved:
            # Move the cursor back to where the user expects it to be
            self.mouseMoved = True
            context.window.cursor_warp(self.originalMouseX, self.originalMouseY)



class VRAY_OT_message_box(VRAY_OT_message_box_base):
    """Operator that displays a simple message dialog"""
    bl_idname = "vray.message_box"
    bl_label = "V-Ray for Blender"

    message: bpy.props.StringProperty(
        description = "The message to display in the dialog",
        default     = "",
        options     = {'HIDDEN'}
    )

    title: bpy.props.StringProperty(
        description = "The dialog's title",
        default     = "V-Ray for Blender",
        options     = {'HIDDEN'}
    )

    width: bpy.props.IntProperty(
        description = "The width of the dialof in pixels",
        default     = 300,
        options     = {'HIDDEN'}
    )

    icon: bpy.props.StringProperty(
        description = "The icon to display in the dialog",
        default     = "",
        options     = {'HIDDEN'}
    )

    def draw(self, context):
        layout = self.layout
        
        split = layout.split(factor=0.1)
        col1 = split.column()
        col2 = split.column()
        
        col1.label(text="", icon = self.icon)
        
        # Split the message into lines.
        lines = self.message.split('\n')
        for line in lines:
            col2.label(text=line)

    def execute(self, context):
        return {'FINISHED'}

    def invoke(self, context, event):
        """Invokes the dialog popup"""
        self._centerDialog(context, event)
        return context.window_manager.invoke_props_dialog(self, width=self.width, title=self.title)


class VRAY_OT_add_new_material(VRayOperatorBase):
    bl_idname      = "vray.new_material"
    bl_label       = "Add New Material"
    bl_description = "Add new material"
    bl_options     = {'INTERNAL', 'UNDO'}

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

                while vray.exportJobIsRunning(renderer):
                    time.sleep(0.1)

                msgInfo = f"Exported scene: {exportSettings.filePath}."
                self.report({'INFO'}, msgInfo)
                debug.printInfo(msgInfo)
            elif msgErr:
                self.report({'ERROR'}, msgErr)
                debug.printError(msgErr)
        else:
            msgErr = "Export scene: no active V-Ray renderer. Start an interactive render and try again."
            self.report({'ERROR'}, msgErr)
            debug.printError(msgErr)
        return {'FINISHED'}

    @classmethod
    def description(cls, context, properties):
        return getLimitedFeatureDescription(cls.bl_description)

class VRAY_OT_get_ui_mouse_position(VRayOperatorBase):
    """ Gets the mouse position relative to the current view """
    bl_idname   = "vray.get_ui_mouse_position"
    bl_label    = "Mouse location"
    bl_options  = {'INTERNAL'}

    pos_x: bpy.props.IntProperty()
    pos_y: bpy.props.IntProperty()

    def invoke(self, context, event):
        if not context.region:
            return {'CANCELLED'}
        region = context.region.view2d
        ui_scale = context.preferences.system.ui_scale
        x, y = region.region_to_view(event.mouse_region_x, event.mouse_region_y)

        VRAY_OT_get_ui_mouse_position.pos_x = x / ui_scale
        VRAY_OT_get_ui_mouse_position.pos_y = y / ui_scale

        return {'FINISHED'}

class VRAY_OT_select_exporter_output_file_base(VRayOperatorBase):
    """File select dialog for an Exporter output path. Subclasses set PATH_ATTR and SUFFIX."""
    bl_options = {'INTERNAL'}

    filepath: bpy.props.StringProperty(subtype="FILE_PATH")

    # Name of the attribute in the Exporter settings to store the file path (to be set by subclasses)
    PATH_ATTR: str = ""

    # File suffix/extension for the output file, e.g., ".vrscene" or ".vrmesh" (to be set by subclasses)
    SUFFIX: str = ""

    def invoke(self, context, event):
        from pathlib import Path
        preferences = blender_utils.getVRayPreferences(context)

        if filePath := getattr(preferences, self.PATH_ATTR):
            self.filepath = filePath
        elif blendPath := context.blend_data.filepath:
            self.filepath = str(Path(blendPath).with_suffix(self.SUFFIX))
        else:
            self.filepath = f"untitled{self.SUFFIX}"

        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        setattr(blender_utils.getVRayPreferences(context), self.PATH_ATTR, self.filepath)
        if context.area:
            context.area.tag_redraw()
        return {'FINISHED'}


class VRAY_OT_select_vrscene_export_file(VRAY_OT_select_exporter_output_file_base):
    """ Shows a File Select dialog for selecting an output file
        for the vrscene export operation.
    """
    bl_idname       = "vray.select_vrscene_export_file"
    bl_label        = "V-Ray Select vrscene file for output"
    bl_description  = "Select an output .vrscene file"

    filter_glob: bpy.props.StringProperty(
        default="*.vrscene",
        options={'HIDDEN'}
    )

    PATH_ATTR = "export_scene_file_path"
    SUFFIX = ".vrscene"


class VRAY_OT_select_proxy_export_file(VRAY_OT_select_exporter_output_file_base):
    """ Shows a File Select dialog for selecting an output file
        for the proxy export operation.
    """
    bl_idname       = "vray.select_proxy_export_file"
    bl_label        = "V-Ray Select proxy file for output"
    bl_description  = "Select an output .vrmesh file"

    filter_glob: bpy.props.StringProperty(
        default="*.vrmesh",
        options={'HIDDEN'}
    )

    PATH_ATTR = "export_proxy_file_path"
    SUFFIX = ".vrmesh"

class VRAY_OT_render(VRAY_OT_message_box_base):
    bl_idname       = "vray.render"
    bl_label        = "Start Production Render"
    bl_description  = "Render scene using the V-Ray renderer"
    bl_options      = {'INTERNAL'}

    class _ErrorType:
        NoError = 0
        FileExists = 1
        InvalidFileName = 2
        InvalidFolderName = 3

    errorType: bpy.props.IntProperty(default=_ErrorType.NoError, options={'HIDDEN'})
    errorMsg:  bpy.props.StringProperty() # Additional error info in case of a failed check

    # Per-invocation override of the scene's animation mode. 'AUTO' uses the scene's
    # `Exporter.animation_mode`; the other values force the corresponding mode for this job
    # without mutating the persistent property.
    forceMode: bpy.props.EnumProperty(
        items=(
            ('AUTO',      '', ''),
            ('ANIMATION', '', ''),
            ('FRAME',     '', ''),
        ),
        default='AUTO',
        options={'HIDDEN'},
    )

    @classmethod
    def description(cls, context, properties):
        if vray.isInitialized():
            return cls.bl_description
        return f"{cls.bl_description}. Unavailable until V-Ray is initialized"

    def _getAnimationMode(self, scene) -> str:
        """Return the effective 'FRAME'/'ANIMATION' choice for this invocation."""
        if self.forceMode == 'AUTO':
            return scene.vray.Exporter.animation_mode
        return self.forceMode

    def execute(self, context: bpy.types.Context):
        if vray.isInitialized():
            # This status message should ideally be printed right before the rendering starts but here is the last
            # chance for it to be shown BEFORE the render job is complete. Once the operator starts executing, no
            # updates to the UI will be made until it's finished.
            debug.report('INFO', 'Started render job. Blender UI will be unresponsive until the rendering is complete')
            
            from vray_blender.engine.renderer_prod_base import VRayRendererProdBase
            
            uiRegionContext = VRayRendererProdBase.getActiveUIRegionContext()
            vfb_event_handler.VfbEventHandler.startProdRender(self.forceMode, uiRegionContext)
        else:
            debug.report('WARNING', "Can't start render job. V-Ray is not initialized")
        return {'FINISHED'}

    def _checkOutputInfo(self, context):
        """ Checks if there is rendered result with the same name """

        from vray_blender.external.pathvalidate import is_valid_filename, is_valid_filepath
        from vray_blender.lib.path_utils import (PathExpander, checkOutputFileExists,
                                                  setSessionExpander, clearSessionExpander,
                                                  withLayerSuffix, hasFrameToken)

        # Reset any previously set values
        self.errorType = __class__._ErrorType.NoError
        self.errorMsg = ''
        clearSessionExpander()

        if not context.scene.vray.Exporter.auto_save_render:
            # The Output rollout is disabled, no images will be written to disk
            return True

        settingsOutput = context.scene.vray.SettingsOutput

        expander = PathExpander(context)

        expandedFolderName = expander.expand(settingsOutput.img_dir)
        if not settingsOutput.img_dir or not is_valid_filepath(expandedFolderName, platform=platform.system()):
            self.errorType = __class__._ErrorType.InvalidFolderName
            return False

        # V-Ray tokens such as <frame04> and the $frame placeholder are expanded at
        # render time, not by us, so strip them before validating the filename.
        expandedFileName = re.sub(r"<[^>]+>|\$frame", "0000", expander.expand(settingsOutput.img_file))
        
        if not settingsOutput.img_file or not is_valid_filename(expandedFileName):
            self.errorType = __class__._ErrorType.InvalidFileName
            return False

        try:
            os.makedirs(expandedFolderName, exist_ok=True)
        except Exception as exc:
            self.errorType = __class__._ErrorType.InvalidFolderName
            self.errorMsg = str(exc)
            return False
        
        if not settingsOutput.output_overwrite_warn:
            return True

        imgFmt = int(settingsOutput.img_format)
        viewLayers = [layer for layer in context.scene.view_layers if layer.use]
        multipleLayers = len(viewLayers) > 1
        isAnimation = self._getAnimationMode(context.scene) == 'ANIMATION'

        # Reuse the expander built above for the whole job. $viewlayer is resolved
        # per-call so a single instance covers all view layers without recomputing static values.
        setSessionExpander(expander)

        for layer in viewLayers:
            # Pass None for single-layer renders so $viewlayer still expands to the real
            # layer name via the context fallback, matching what the exporter writes.
            # Pass the explicit name for multi-layer renders to get the _LayerName suffix.
            viewLayerName = layer.name if multipleLayers else None

            imgFileBase = withLayerSuffix(settingsOutput.img_file, viewLayerName)
            # Mirrors SettingsOutput.img_file_needFrameNumber: True when V-Ray appends
            # '.NNNN' to the filename because no frame placeholder is present.
            needFrameNumber = isAnimation and not hasFrameToken(settingsOutput.img_file)

            if isAnimation:
                # The active camera can change per-frame via camera markers, which affects
                # paths that contain $camera. Expand paths per frame and cache directory
                # listings so long animation ranges don't hammer the file system.
                dirCache: dict[str, set[str]] = {}
                frameRange = common_settings.getAnimationFrames(context.scene, layer.name)

                for frame in frameRange:
                    layerImgDir = expander.expand(settingsOutput.img_dir, frame,
                                                  viewLayerName=viewLayerName)
                    imgFileName = os.path.basename(expander.expandFilename(
                        imgFileBase, imgFmt, frame=frame, viewLayerName=viewLayerName))

                    if layerImgDir not in dirCache:
                        dirCache[layerImgDir] = (
                            set(os.listdir(layerImgDir)) if os.path.isdir(layerImgDir) else set()
                        )

                    if checkOutputFileExists(dirCache[layerImgDir], imgFileName, frame, needFrameNumber):
                        self.errorType = __class__._ErrorType.FileExists
                        break
            else:
                frame = context.scene.frame_current
                layerImgDir = expander.expand(settingsOutput.img_dir, frame,
                                              viewLayerName=viewLayerName)
                imgFileName = os.path.basename(expander.expandFilename(
                    imgFileBase, imgFmt, frame=frame, viewLayerName=viewLayerName))
                existingFiles = set(os.listdir(layerImgDir)) if os.path.isdir(layerImgDir) else set()
                if checkOutputFileExists(existingFiles, imgFileName, frame, needFrameNumber=False):
                    self.errorType = __class__._ErrorType.FileExists

            if self.errorType:
                break

        return not self.errorType


    def invoke(self, context, event):
        if self.forceMode in {'FRAME', 'ANIMATION'}:
            context.window_manager.vray.render_button_mode = self.forceMode

        if not _validateFramesList(context):
            self.report({'WARNING'}, f"Invalid frames list, render aborted. See console log for details.")
            return {'CANCELLED'}

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
                layout.label(text="One or more existing output images will be overwritten.")
                layout.label(text="Proceed?")
                layout.prop(context.scene.vray.SettingsOutput, "output_overwrite_warn", text="Always ask me.")

            case _:
                assert not f'Invalid message selector: {self.errorType}'


class VRAY_OT_set_render_mode(VRayOperatorBase):
    """Sets the render button mode (single frame or animation) without starting a render."""
    bl_idname      = "vray.set_render_mode"
    bl_label       = "Set Render Mode"
    bl_description = "Change the render button between single frame and animation mode"
    bl_options     = {'INTERNAL'}

    mode: bpy.props.EnumProperty(
        items=(
            ('ANIMATION', '', ''),
            ('FRAME',     '', ''),
        ),
        default='FRAME',
        options={'HIDDEN'},
    )

    def execute(self, context):
        context.window_manager.vray.render_button_mode = self.mode
        return {'FINISHED'}


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
        vray.updateScenePath(path_utils.getScenePath())
        vray.openVFB()
        return {'FINISHED'}


class VRAY_OT_render_interactive(VRayOperatorBase):
    bl_idname       = "vray.render_interactive"
    bl_label        = "Start Interactive Render"
    bl_description  = "Render scene using the interactive V-Ray renderer"
    bl_options      = {'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return VRayOperatorBase.poll(context) and \
                (   not VRayRenderEngine.iprRenderer or  \
                    type(VRayRenderEngine.iprRenderer) is not VRayRendererIprVfb)


    def execute(self, context):
        from vray_blender.lib.defs import UIRegionContext

        # The order of the following commands to ZmqServer is important. Opening VFB
        # first might lead to an irrecoverable V-Ray Core error.
        vfb_event_handler.VfbEventHandler.stopViewportRender()
        vfb_event_handler.VfbEventHandler.stopVantageLiveLink()

        uiRegionContext = UIRegionContext(context.space_data, context.window) if context.space_data.type == 'VIEW_3D' else None
        vfb_event_handler.VfbEventHandler.startInteractiveRender(uiRegionContext)

        # This operator is only invoked from the main menu and this is the only case
        # in which we want to open the VFB for an interactive rendering session.
        vray.updateScenePath(path_utils.getScenePath())
        vray.openVFB()
        return {'FINISHED'}


class VRAY_OT_render_interactive_stop(VRayOperatorBase):
    bl_idname = "vray.render_interactive_stop"
    bl_label = "Stop Interactive Render"
    bl_description = "Stop render of scene using the interactive V-Ray renderer"

    @classmethod
    def poll(cls, context):
        return VRayOperatorBase.poll(context) and \
                VRayRenderEngine.iprRenderer and  \
                type(VRayRenderEngine.iprRenderer) is VRayRendererIprVfb

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

        if vray.isCommunityEdition():
            self.report({'WARNING'}, getCELimitedFeatureMsg())
            return {'CANCELLED'}

        if not _validateFramesList(context):
            self.report({'WARNING'}, f"Invalid frames list, export aborted. See console log for details.")
            return {'CANCELLED'}
        
        debug.report('INFO', 'Started .vrscene export. Blender UI will be unresponsive until the operation is complete.')
        fileTypes = []
        preferences = blender_utils.getVRayPreferences(context)

        if preferences.export_scene_separate_files:
            if self.exportView:             fileTypes.append('view')
            if self.exportLights:           fileTypes.append('lights')
            if self.exportGeometry:         fileTypes.append('geometry')
            if self.exportNodes:            fileTypes.append('nodes')
            if self.exportMaterials:        fileTypes.append('materials')
            if self.exportTextures:         fileTypes.append('textures')
            if self.exportBitmaps:          fileTypes.append('bitmaps')
            if self.exportRenderChannels:   fileTypes.append('render_elements')

        preferences.export_scene_plugin_types = ','.join(fileTypes)

        from vray_blender.engine.renderer_prod_base import VRayRendererProdBase
        uiRegionContext = VRayRendererProdBase.getActiveUIRegionContext()
    
        if self.synchronous:
            # Synchronous mode is used by the testing frameworks.
            vfb_event_handler.VfbEventHandler.startProdRenderSync(renderMode=ProdRenderMode.EXPORT_VRSCENE, uiRegionContext=uiRegionContext)
        else:
            vfb_event_handler.VfbEventHandler.exportVrscene(uiRegionContext)

        return {'FINISHED'}

    def invoke(self, context, event):

        preferences = blender_utils.getVRayPreferences(context)
        fileCategories = preferences.export_scene_plugin_types.split(',')

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

        windowTitle = "Export V-Ray .vrscene file"
        if vray.isCommunityEdition():
            return context.window_manager.invoke_popup(self, width=400)
        return context.window_manager.invoke_props_dialog(self, width=400, title=windowTitle, confirm_text="Export")


    def draw(self, context: bpy.types.Context):
        if vray.isCommunityEdition():
            drawCELimitedFeatureWarning(self.layout)
            return

        layout = self.layout.box()
        layout.use_property_decorate = False
        layout.active = not vray.isCommunityEdition()

        preferences = blender_utils.getVRayPreferences(context)

        row = layout.row(align=True)
        row.prop(preferences, 'export_scene_file_path')
        row.operator('vray.select_vrscene_export_file', text='', icon='FILE_FOLDER')

        hexBox = layout.box()
        hexBox.use_property_split = False
        # 'Compressed' only applies to HEX meshes, so disable it when those are off.
        compressedRow = hexBox.row()
        compressedRow.enabled = preferences.export_scene_hex_meshes
        compressedRow.prop(preferences, 'export_scene_compressed')

        hexFormatRow = hexBox.row(align=True)
        hexFormatRow.prop(preferences, 'export_scene_hex_meshes')
        hexFormatRow.prop(preferences, 'export_scene_hex_transforms')

        sepBox = layout.box()
        sepBox.prop(preferences, 'export_scene_separate_files')

        splitOuter = sepBox.split(factor=0.1)
        splitOuter.column()
        splitOuter.enabled = splitOuter.active = preferences.export_scene_separate_files

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

        # Archive packer: collect all assets referenced by the scene next to the .vrscene via
        # Chaos Cloud (and optionally zip them), so it is unavailable when Chaos Cloud is missing.
        archiveBox = layout.box()
        archiveBox.use_property_split = False
        archiveBox.label(text="Archive")

        archiveRow = archiveBox.row(align=True)
        packCell = archiveRow.row(align=True)
        packToggle = packCell.row()
        packToggle.enabled = preferences.detect_vray_cloud
        packToggle.prop(preferences, 'export_scene_pack')

        if not preferences.detect_vray_cloud:
            # Blender can't attach a custom tooltip to the disabled checkbox, so explain the
            # missing Chaos Cloud requirement through an info icon next to the Pack option.
            packCell.operator('vray.pack_requires_ccloud_tooltip', text='', icon='INFO', emboss=False)

        zipCell = archiveRow.row()
        zipCell.enabled = preferences.detect_vray_cloud and preferences.export_scene_pack
        zipCell.prop(preferences, 'export_scene_zip')

        # Animation export options
        animationRow = layout.box().row(align=True)

        animSettings = preferences.animationSettingsVrsceneExport
        animationRow.prop(animSettings, 'exportAnimation')

        animationRangeCol = animationRow.column()
        animationRangeCol.use_property_split = False
        animationRangeCol.enabled = animSettings.exportAnimation
        animationRangeCol.prop(animSettings, 'frameRangeMode', text="")

        customRangeCol = animationRangeCol.column()
        customRangeCol.use_property_split = False
        customRangeCol.enabled = animSettings.exportAnimation and animSettings.frameRangeMode in ('CUSTOM_RANGE', 'CUSTOM_FRAMES')
        if animSettings.frameRangeMode == 'CUSTOM_RANGE':
            customRangeCol.prop(animSettings, 'customFrameStart')
            customRangeCol.prop(animSettings, 'customFrameEnd')
            customRangeCol.prop(animSettings, 'customFrameStep')
        elif animSettings.frameRangeMode == 'CUSTOM_FRAMES':
            customRangeCol.prop(animSettings, 'customFramesList')
        else:
            customRangeCol.prop(context.scene, 'frame_start')
            customRangeCol.prop(context.scene, 'frame_end')
            customRangeCol.prop(context.scene, 'frame_step')

        self._cursorWrap(context)

    @classmethod
    def description(cls, context, properties):
        return getLimitedFeatureDescription(cls.bl_description)


def _validateFramesList(context: bpy.types.Context):
    from vray_blender.lib.lib_utils import parseFramesToFlatList

    preferences = blender_utils.getVRayPreferences(context)
    animSettings = preferences.animationSettingsVrsceneExport

    if animSettings.exportAnimation and animSettings.frameRangeMode == 'CUSTOM_FRAMES':
        frames = animSettings.customFramesList

        if not frames:
            return False
        
        return bool(parseFramesToFlatList(frames))
    
    return True
    

class VRAY_OT_cloud_submit(VRAY_OT_message_box_base):
    bl_idname       = "vray.cloud_submit"
    bl_label        = "Submit to Cloud"
    bl_description  = "Submit scene for Cloud rendering using V-Ray production renderer"
    bl_options      = {'INTERNAL'}

    originalMouseX : bpy.props.IntProperty(default = 0)
    originalMouseY: bpy.props.IntProperty(default = 0)
    mouseMoved: bpy.props.BoolProperty(default=False)

    def execute(self, context):
        debug.report('INFO', 'Started Chaos Cloud submission. Blender UI will be unresponsive until the operation is complete.')
        
        from vray_blender.engine.renderer_ipr_base import VRayRendererIprBase
        uiRegionContext = VRayRendererIprBase.getActiveUIRegionContext()

        vfb_event_handler.VfbEventHandler.cloudSubmit(uiRegionContext)
        return {'FINISHED'}

    def invoke(self, context, event):
        self._centerDialog(context, event)
        return context.window_manager.invoke_props_dialog(self, width=400, title="Submit to Cloud", confirm_text="Submit")

    def draw(self, context: bpy.types.Context):
        from vray_blender.ui.properties_output import _drawPathPropWithPlaceholders
        VRayExporter = context.scene.vray.Exporter

        self.layout.prop(VRayExporter, 'vray_cloud_project_name')
        _drawPathPropWithPlaceholders(self.layout, VRayExporter, 'vray_cloud_job_name', "Job Name",
                                      target_prop_group="EXPORTER")
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

    # We don't want to show multiple upgrade dialogs at the same time. This will
    # happen because the upgrade may be started on several conditions some of which
    # may happen simultaneously. While this flag is True, all other attempts to
    # show the dialog will be cancelled.

    _executing = False

    dont_ask_again: bpy.props.BoolProperty(
        name="Don't ask again",
        default=False,
        update=lambda self, context: setattr(blender_utils.getVRayPreferences(), 'ask_for_upgrade_confirm', not self.dont_ask_again)
    )

    @staticmethod
    def cancel(self, context):
        __class__._executing = False


    def execute(self, context):
        try:
            from vray_blender.version import getSceneUpgradeNumber, getAddonUpgradeNumber, upgradeScene

            sceneUpgradeNum = getSceneUpgradeNumber()
            addonUpgradeNum = getAddonUpgradeNumber()

            # invoke() won't be called in headless mode so perform the same version number checks here
            if sceneUpgradeNum != addonUpgradeNum:
                if upgradeScene(sceneUpgradeNum, addonUpgradeNum):
                    self.report({ 'INFO' }, "Your scene has been upgraded")
                    return {'FINISHED'}

            return {'CANCELLED'}
        finally:
            __class__._executing = False


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
            from vray_blender import UPGRADE_NUMBER
            context.scene.vray.Exporter.vrayAddonUpgradeNumber = UPGRADE_NUMBER
            return {'CANCELLED'}

        if sceneUpgradeNum != addonUpgradeNum:
            if not __class__._executing:
                __class__._executing = True
            else:
                return {'CANCELLED'}
            if blender_utils.getVRayPreferences(context).ask_for_upgrade_confirm:
                self._centerDialog(context, event)
                return context.window_manager.invoke_props_dialog(self, width=400, title="V-Ray Scene Version Update", confirm_text="OK")
            else:
                return self.execute(context)

        return {'CANCELLED'}


    def draw(self, context: bpy.types.Context):
        from vray_blender.lib.blender_utils import isDefaultScene

        sceneAlias = "default scene" if isDefaultScene() else "scene"

        self.layout.label(text = f"The {sceneAlias} was created with an older version of V-Ray for Blender.")

        self.layout.separator()
        self.layout.label(text="Click OK to run the update procedure.")
        self.layout.label(text="If everything goes well, save the .blend file.")
        self.layout.label(text="If you encounter any problems, look in the console for error messages.")
        self._cursorWrap(context)

        self.layout.prop(self, 'dont_ask_again')

    @classmethod
    def poll(cls, context):
        # The operator vray.upgrade_scene should be callable even if the default engine is not V-Ray.
        return True

class VRAY_OT_FileSelect(VRayOperatorBase, ImportHelper):
    """Generic File Select dialog box"""
    bl_idname = "vray.file_select"
    bl_label = "Select file"
    bl_description = "Show a Select File dialog"

    # The filter_glob is used by the file browser to hide files 
    # that don't match the extension. This is defined in the child.
    filter_glob: bpy.props.StringProperty(
        default = "*",
        options = {'HIDDEN'}
    )

    relative_path: bpy.props.BoolProperty(
        name = "Relative Path",
        description = "File path is relative to the blend file",
        default = True,
    )

    # The type of the property holder object
    # ['material' | 'light' | 'world' | 'object'] for shader nodes
    # 'camera', 'settings'
    object_type: bpy.props.StringProperty(
        default = "",
        description = "Type of property holder object",
        options = {'HIDDEN'}
    )

    selector_name: bpy.props.StringProperty(
        default = "", 
        description = "Arbitrary identifier string",
        options = {'HIDDEN'}
    )

    object_name: bpy.props.StringProperty(
        default = "", 
        description = "Arbitrary identifier string",
        options = {'HIDDEN'}
    )


    callback: bpy.props.StringProperty(
        default = "", 
        description = "Full path to the callback function",
        options = {'HIDDEN'}
    )


    plugin_type: bpy.props.StringProperty(
        default = "",
        description = "The ID of the plugin module",
        options = {'HIDDEN'}
    )

    bound_property: bpy.props.StringProperty(
        default = "",
        description = "The name of the bound property",
        options = {'HIDDEN'}
    )

    
    @staticmethod
    def setFilter(self, extensions: list[str]):
        """ Convert a list of extensions in common formats to a glob string.

            Args:
            extensions: A list of strings. All of "ext", ".ext" and "*.ext" 
                        formats are supported 
        """
        self.filter_glob = ';'.join(f"*.{e.lstrip('.*')}" for e in extensions)


    def execute(self, context: bpy.types.Context):
        # This is where the file processing logic goes.
        # We call a custom method so children don't have to rewrite 'execute'.
        if not self.filepath:
            return {'CANCELLED'}
        
        from vray_blender.lib.sys_utils import importFunction

        if fn := importFunction(self.callback):
            fn(self, self.filepath)
            return {'FINISHED'}
        
        assert False, "No callback function set"


class VRAY_OT_testing_log_marker(bpy.types.Operator):
    bl_idname = "vray.testing_log_marker"
    bl_label = "Log marker in UI test output"
    bl_description = "Helper for adding test separator markers to the log output."

    def execute(self, context):
        debug.printAlways("## TEST START ##", raw=True)
        return {'FINISHED'}


class VRAY_OT_url_open(VRayOperatorBase):
    """A specialization of the wm.url_open Blender operator which allows a custom description"""
    bl_idname = "vray.url_open"
    bl_label = "Open URL"
    bl_options = {'INTERNAL'}
    
    url: bpy.props.StringProperty()

    description: bpy.props.StringProperty(
        name="Description",
        description="Custom description of the URL"
    )

    def execute(self, context):
        bpy.ops.wm.url_open(url=self.url)
        return {'FINISHED'}
    
    @classmethod
    def description(cls, context, properties):
        if properties.description:
            return properties.description
        return cls.bl_description

class VRAY_OT_open_last_profiler_report(VRayOperatorBase):
    """Open the most recently written V-Ray profiler HTML report in a web browser"""
    bl_idname = "vray.open_last_profiler_report"
    bl_label = "Show Last Profile"
    bl_description = "Open the last V-Ray profiler report in a web browser"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        outputDir = blender_utils.getVRayPreferences(context).VRayProfiler.outputDirectory
        if not outputDir:
            self.report({'WARNING'}, "No profiler output directory set")
            return {'CANCELLED'}

        outputDir = bpy.path.abspath(outputDir)
        files = glob.glob(os.path.join(outputDir, "vray_profiler_*.html"))

        if not files:
            self.report({'WARNING'}, f"No profiler reports found in: {outputDir}")
            return {'CANCELLED'}

        files.sort(key=os.path.getmtime, reverse=True)
        bpy.ops.wm.url_open(url=pathlib.Path(files[0]).as_uri())
        return {'FINISHED'}


class VRAY_OT_CE_limited_feature_tooltip(VRayOperatorBase):
    """A dummy operator to display a tooltip for a limited feature in the Community Edition"""
    bl_idname = "vray.ce_limited_feature_tooltip"
    bl_label = ""
    bl_description = getCELimitedFeatureMsg()
    bl_options = {'INTERNAL'}

    def execute(self, context):
        return {'FINISHED'}


class VRAY_OT_pack_requires_ccloud_tooltip(VRayOperatorBase):
    """A dummy operator that uses its tooltip to explain that Chaos Cloud is required for packing.
       Blender cannot attach a custom tooltip to a disabled checkbox, so this is shown as an info
       icon next to the Pack option when the Chaos Cloud executable is not found."""
    bl_idname = "vray.pack_requires_ccloud_tooltip"
    bl_label = ""
    bl_description = "Chaos Cloud was not found on this system. It is required to pack scene assets"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        return {'FINISHED'}


class VRAY_OT_jump_to_setting(VRayOperatorBase):
    """Switch to the relevant render-settings tab, expand any rollouts that contain
       the target parameter, and highlight that parameter for a few seconds."""
    bl_idname      = "vray.jump_to_setting"
    bl_label       = "Show in Render Settings"
    bl_description = "Show this parameter in Render Settings"
    bl_options     = {'INTERNAL'}

    target: bpy.props.EnumProperty(
        name = "Target",
        items = (
            ('AUTO_EXPOSURE',      '', ''),
            ('AUTO_WHITE_BALANCE', '', ''),
            ('MOTION_BLUR',        '', ''),
            ('CAUSTICS',           '', ''),
        ),
        options = {'HIDDEN'},
    )

    # Mapping from a `VRAY_OT_jump_to_setting.target` value to:
    #   - the active render context (`window_manager.vray.ui_render_context`),
    #   - the panel-state BoolProperties on `wm.vray.common_tab` to flip to True so
    #     the rollouts containing the parameter are forced open via `panel_prop`,
    #   - the highlight key consumed by `draw_utils.isHighlighted` to apply the
    #     alert wrapper around the right parameter,
    #   - the name of the top-level `bpy.types.Panel` class (in
    #     `vray_blender.ui.properties_render`) that needs to be force-opened so
    #     the user can actually see the highlighted parameter; empty string to
    #     skip the force-open workaround.
    _JUMP_TARGETS = {
        'AUTO_EXPOSURE':      ('2', ('panel_globals_camera_open',),                                  'SettingsCameraGlobal.auto_exposure',     'VRAY_PT_Globals'),
        'AUTO_WHITE_BALANCE': ('2', ('panel_globals_camera_open',),                                  'SettingsCameraGlobal.auto_white_balance','VRAY_PT_Globals'),
        'MOTION_BLUR':        ('2', ('panel_globals_camera_open', 'panel_globals_motion_blur_open'), 'SettingsMotionBlur.on',                  'VRAY_PT_Globals'),
        'CAUSTICS':           ('1', (),                                                              'SettingsCaustics.on',                    'VRAY_PT_SettingsCaustics'),
    }

    # How long the alert highlight stays on the target parameter.
    _HIGHLIGHT_DURATION = 3.0

    _forceOpenPending: set = set()

    @staticmethod
    def _forcePanelOpenTick():
        """ A hack to force-open the panel on the next draw cycle.
            Currently Blender's Python API does not expose a way to programmatically expand
            a bpy.types.Panel whose state is collapsed.
        """
        from vray_blender.ui import properties_render as _properties_render

        pending = list(VRAY_OT_jump_to_setting._forceOpenPending)
        VRAY_OT_jump_to_setting._forceOpenPending.clear()

        if not pending:
            return None

        # The Hack is to basically unregister and re-register the panel with a new bl_idname
        # and no DEFAULT_CLOSED option.
        for className in pending:
            panelClass = getattr(_properties_render, className, None)
            if panelClass is None:
                continue

            bpy.utils.unregister_class(panelClass)

            panelClass.bl_options = set(panelClass.bl_options) - {'DEFAULT_CLOSED'}
            panelClass.bl_idname = className + str(time.time_ns())
            bpy.utils.register_class(panelClass)

        return None


    @staticmethod
    def _clearHighlight():
        """ Runs once after `_HIGHLIGHT_DURATION` seconds, clears the highlight state
            and triggers a final redraw so the alert visuals disappear.
        """
        wmVray = getattr(bpy.context.window_manager, 'vray', None)
        if wmVray is not None:
            wmVray.common_tab.highlight_target = ""

        return None


    def _queueForcePanelOpen(self, panelClassName: str):
        """ Queue `panelClassName` to be force-opened on the next timer tick. """
        if not panelClassName:
            return
        self._forceOpenPending.add(panelClassName)
        if not bpy.app.timers.is_registered(self._forcePanelOpenTick):
            bpy.app.timers.register(self._forcePanelOpenTick, first_interval=0)


    def execute(self, context):
        targetCfg = self._JUMP_TARGETS.get(self.target)
        if targetCfg is None:
            return {'CANCELLED'}

        renderContext, panelFlags, highlightKey, panelClassName = targetCfg
        wmVray = context.window_manager.vray
        commonUI = wmVray.common_tab

        wmVray.ui_render_context = renderContext
        for flagName in panelFlags:
            setattr(commonUI, flagName, True)

        commonUI.highlight_target = highlightKey

        # Open the panels containing the highlighted parameter on the next draw cycle.
        self._queueForcePanelOpen(panelClassName)

        # Clear the highlight after _HIGHLIGHT_DURATION seconds.
        if bpy.app.timers.is_registered(self._clearHighlight):
            bpy.app.timers.unregister(self._clearHighlight)
        bpy.app.timers.register(self._clearHighlight, first_interval=self._HIGHLIGHT_DURATION)

        return {'FINISHED'}


def getRegClasses():
    return (
        VRAY_OT_node_add,
        VRAY_OT_node_del,
        VRAY_OT_dr_nodes_load,
        VRAY_OT_dr_nodes_save,
        VRAY_OT_open_preferences,
        VRAY_OT_flip_resolution,
        VRAY_OT_set_kelvin_color,
        VRAY_OT_add_sky,

        VRAY_OT_add_new_material,
        VRAY_OT_export_scene,
        VRAY_OT_get_ui_mouse_position,
        VRAY_OT_select_vrscene_export_file,
        VRAY_OT_select_proxy_export_file,
        VRAY_OT_render,
        VRAY_OT_set_render_mode,
        VRAY_OT_render_interactive,
        VRAY_OT_render_interactive_stop,
        VRAY_OT_render_viewport,
        VRAY_OT_export_vrscene,
        VRAY_OT_cloud_submit,

        VRAY_OT_copy_plugin_version,
        VRAY_OT_upgrade_scene,

        VRAY_OT_testing_log_marker,
        VRAY_OT_FileSelect,
        VRAY_OT_url_open,
        VRAY_OT_open_last_profiler_report,
        VRAY_OT_CE_limited_feature_tooltip,
        VRAY_OT_pack_requires_ccloud_tooltip,
        VRAY_OT_message_box,
        VRAY_OT_jump_to_setting,
    )


def register():
    for regClass in getRegClasses():
        bpy.utils.register_class(regClass)


def unregister():
    for regClass in getRegClasses():
        bpy.utils.unregister_class(regClass)
