# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
import json
import os
import platform
import sys
from vray_blender import version
from vray_blender.lib import blender_utils, sys_utils
from vray_blender.plugins.system.compute_devices import ComputeDevices
from vray_blender.bin import VRayBlenderLib as vray
from vray_blender.ui.community_edition import drawCELimitedFeatureIcon

if sys.platform == "win32":
    # The psutil we ship is currently broken on macos. It takes into account numa nodes
    # and is only used for the total thread count message we show the user, but we should
    # be fine with os.cpu_count on most MacOS machines.
    from vray_blender.external import psutil

def getVRayCloudPath():
    """
    If Chaos Cloud is installed on the system returns the path to it, else - None.
    """

    # check whether vcloud JSON file exists
    vcloudJsonDir = '%APPDATA%/Chaos/Cloud/client/' if bpy.app.build_platform == b'Windows' else '$HOME/.ChaosGroup/vcloud/client/'
    vcloudJsonDir = os.path.expandvars(vcloudJsonDir)
    vcloudJsonFilename = vcloudJsonDir + 'vcloud.json'
    if not os.path.exists(vcloudJsonFilename):
        return None

    # check whether the vcloud executable file exists via the "executable" field in the JSON file
    vcloudFullPath = ''
    with open(vcloudJsonFilename, 'r') as jsonFileId:
        jsonData = json.load(jsonFileId)
        vcloudFullPath = os.path.expandvars(jsonData['executable'])

    return vcloudFullPath if os.path.exists(vcloudFullPath) else None

def _getTelemetry(keyName):
    if keyName in os.environ:
        envVal = os.environ[keyName]
        if envVal in ["0", "1"]:
            return envVal != "0"

    if sys.platform == "win32":
        import winreg
        # Telemetry settings are stored in three places:
        # 1. 'keyName' envvar
        # 2. HKEY_CURRENT_USER\Environment\'keyName' for the current user
        # 3. HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Control\Session Manager\Environment for the system
        # User setting takes precedence and is the one we can set from Blender w/o admin privileges
        userPath = "Environment"
        systemPath = r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"
        userVal = sys_utils.getWinRegistry(userPath, keyName)
        if userVal is not None and userVal != "-1":
            return userVal != "0"
        else:
            systemVal = sys_utils.getWinRegistry(systemPath, keyName, winreg.HKEY_LOCAL_MACHINE)
            return systemVal == "1"
    else:
        telemetryConfigPath = os.path.expandvars('$HOME/.Chaos/telemetry/config.ini')
        telemetryConfig = sys_utils.parseIni(telemetryConfigPath)
        if keyName in telemetryConfig:
            return telemetryConfig[keyName] == "1"

    return False

def _getAnonymizedTelemetry(self):
    return _getTelemetry("VRAY_SEND_ANONYMIZED_FEEDBACK")

def _getPersonalizedTelemetry(self):
    return _getTelemetry("VRAY_SEND_PERSONALIZED_FEEDBACK")

def _setTelemetry(self, keyName, value):
    def _getNewTelemetryValue(value):
        return "1" if value else "0"

    # Update envvar if it was present
    newValue = _getNewTelemetryValue(value)
    if keyName in os.environ:
        os.environ[keyName] = newValue

    if sys.platform == "win32":
        pathToKey = "Environment"
        # Update registry value here too, so it works even if ZMQ server isn't running
        sys_utils.setWinRegistry(pathToKey, keyName, newValue)
    else:
        anonymizedTelemetry = newValue if keyName == "VRAY_SEND_ANONYMIZED_FEEDBACK" else _getNewTelemetryValue(self.anonymized_telemetry)
        personalizedTelemetry = newValue if keyName == "VRAY_SEND_PERSONALIZED_FEEDBACK" else _getNewTelemetryValue(self.personalized_telemetry)
        telemetryConfigPath = os.path.expandvars('$HOME/.Chaos/telemetry/config.ini')
        telemetryConfig = f"""; Configuration variable for controlling the Chaos anonymized telemetry.
VRAY_SEND_ANONYMIZED_FEEDBACK={anonymizedTelemetry}

; Configuration variable for controlling the Chaos personalized telemetry.
VRAY_SEND_PERSONALIZED_FEEDBACK={personalizedTelemetry}
"""
        with open(telemetryConfigPath, "w", encoding="utf-8") as file:
            file.write(telemetryConfig)

def _setAnonymizedTelemetry(self, value):
    _setTelemetry(self, "VRAY_SEND_ANONYMIZED_FEEDBACK", value)
    if not value and self.personalized_telemetry:
        self.personalized_telemetry = value

    # Send a message to ZMQ server to change the registry value, otherwise it won't pick up the change
    vray.setTelemetryState(value, self.personalized_telemetry)

def _setPersonalizedTelemetry(self, value):
    _setTelemetry(self, "VRAY_SEND_PERSONALIZED_FEEDBACK", value)

    # Send a message to ZMQ server to change the registry value, otherwise it won't pick up the change
    vray.setTelemetryState(self.anonymized_telemetry, value)


class VRayRenderNode(bpy.types.PropertyGroup):
    nodeName: bpy.props.StringProperty(
        name = "Node name",
        description = "The name of the render node",
        update = lambda self, context: blender_utils.markPreferencesDirty(context),
        default = "Render Node"
    )

    address: bpy.props.StringProperty(
        name = "IP/Hostname",
        description = "Render node IP or hostname",
        update = lambda self, context: blender_utils.markPreferencesDirty(context),
        default = "IP/Hostname"
    )

    port: bpy.props.IntProperty(
        name = "Port",
        description = "Distributed rendering port",
        min = 0,
        max = 65535,
        default = 20209
    )

    use: bpy.props.BoolProperty(
        name = "Use Node",
        description = "Use render node",
        default = True
    )


class VRayProfiler(bpy.types.PropertyGroup):
    # The values match VRay::VRayProfilerSettings::Mode. 'Off' (0) disables the profiler.
    mode: bpy.props.EnumProperty(
        name        = "Mode",
        description = "Operational mode of the V-Ray Profiler",
        items       = (
            ('0', "Off",     "The profiler is disabled"),
            ('2', "Render",  "Profile the whole rendering process"),
            ('3', "Process", "Only profile the preparing state (plugin initialization, geometry compilation, etc.)"),
        ),
        default     = '0',
        options     = set()
    )

    maxDepth: bpy.props.IntProperty(
        name        = "Max Depth",
        description = "The maximum ray bounces that will be profiled",
        default     = 4,
        min         = 1,
        max         = 8,
        options     = set()
    )

    outputDirectory: bpy.props.StringProperty(
        name        = "Output Directory",
        description = "The directory where the profiler reports will be created. Must be set for the profiler to run",
        default     = "",
        subtype     = 'DIR_PATH',
        options     = set()
    )


class VRAY_OT_switch_license_type(bpy.types.Operator):
    """ Toggle the Community / Commercial license selection and restart the
        VRayZmqServer process so the new license type is applied. """
    bl_idname      = "vray.switch_license_type"
    bl_label       = "Switch License Type"
    bl_description = "Switch between Commercial and Community editions. The V-Ray server will be restarted to apply the new license type"
    bl_options     = {'INTERNAL'}

    @staticmethod
    def _isRenderRunning():
        """ Return True if any V-Ray renderer is currently active.
            Mirrors the set of renderer slots cleared by VRayRenderEngine.resetAll().
        """
        from vray_blender.engine.render_engine import VRayRenderEngine
        return any((
            VRayRenderEngine.prodRenderer,
            VRayRenderEngine.previewRenderer,
            VRayRenderEngine.viewportRenderer,
            VRayRenderEngine.iprRenderer,
        ))

    def invoke(self, context, event):
        # Only prompt when an active render would be aborted by the restart.
        if self._isRenderRunning():
            return context.window_manager.invoke_props_dialog(self, width=400)
        return self.execute(context)

    def draw(self, context):
        layout = self.layout
        layout.label(text="A V-Ray render is currently running.", icon='ERROR')
        layout.label(text="Switching the license type will restart the V-Ray server")
        layout.label(text="and abort the active render. Continue?")

    def execute(self, context):
        from vray_blender.engine.zmq_process import ZMQ
        from vray_blender.engine.render_engine import VRayRenderEngine

        prefs = context.preferences.addons[VRayExporterPreferences.bl_idname].preferences
        prefs.community_edition = not prefs.community_edition

        # Make sure no renderers are using the about-to-die ZmqServer.
        VRayRenderEngine.resetAll()

        # Restart the server so the new -license value is picked up.
        ZMQ.stop()
        ZMQ.ensureRunning()

        return {'FINISHED'}


# Custom export animation settings as a separate property group
class AnimationSettingsVrsceneExport(bpy.types.PropertyGroup):
    customFrameStart: bpy.props.IntProperty(
        name = "Start Frame",
        description = "Start frame for export (overrides scene frame start during VRScene export only)",
        default = 1,
        min = 0,
        update = lambda self, context: blender_utils.markPreferencesDirty(context)
    )

    customFrameEnd: bpy.props.IntProperty(
        name = "End Frame",
        description = "End frame for export (overrides scene frame end during VRScene export only)",
        default = 250,
        min = 0,
        update = lambda self, context: blender_utils.markPreferencesDirty(context)
    )

    customFrameStep: bpy.props.IntProperty(
        name = "Frame Step",
        description = "Frame step for export (overrides scene frame step during VRScene export only)",
        default = 1,
        min = 0,
        update = lambda self, context: blender_utils.markPreferencesDirty(context)
    )

    customFramesList: bpy.props.StringProperty(
        name = "Frames",
        description = "A list of frames to export (e.g. 1,3-10:3)",
        default = "",
        update = lambda self, context: blender_utils.markPreferencesDirty(context)
    )

    exportAnimation: bpy.props.BoolProperty(
        name = "Export Animation",
        description = "Export animation frames",
        default = False,
        update = lambda self, context: blender_utils.markPreferencesDirty(context)
    )

    frameRangeMode: bpy.props.EnumProperty(
        name='Animation Mode',
        description='How to handle animation during export',
        items = (
            ("SCENE_RANGE", "Scene Range", "Use scene frame range"),
            ("CUSTOM_RANGE", "Custom Range", "Use custom frame range"),
            ("CUSTOM_FRAMES", "Custom Frames", "Use custom list of frames")
        ),
        default = "SCENE_RANGE",
        update = lambda self, context: blender_utils.markPreferencesDirty(context)
    )


def _onListerLayoutRedraw(self, context):
    """ Redraw any open lister window and persist the changed setting. """
    from vray_blender.ui.lister import core
    blender_utils.markPreferencesDirty(context)
    core.tagListerRedraw(context)


def _onListerGeometryCombined(self, context):
    # Combining/splitting geometry can drop the active section from the navbar (e.g.
    # 'Proxies' when combined); move to the section that replaced it.
    from vray_blender.ui.lister import core
    view = core.getListerView(context)
    if view is not None:
        # Resolve from the RAW stored enum index, not view.active_category - the latter
        # re-resolves against the just-refiltered item list and yields '' for the hidden section.
        cats = core.getCategories()
        rawIdx = view.get('active_category', 0)
        cat = cats[rawIdx] if isinstance(rawIdx, int) and 0 <= rawIdx < len(cats) else None
        if self.geometry_combined:
            if cat is not None and cat.geometryRole == 'split':
                view.active_category = 'GEOMETRY'
        elif cat is not None and cat.geometryRole == 'combined':
            view.active_category = 'PROXIES'
    blender_utils.markPreferencesDirty(context)
    core.tagListerRedraw(context)


class VRayListerPreferences(bpy.types.PropertyGroup):
    """ Global V-Ray Scene Lister layout settings (per-file view state lives on the Scene).
        The names are mirrored by core.ListerState - keep them in sync. """
    layout_mode: bpy.props.EnumProperty(
        name = "Layout",
        description = "How the V-Ray Scene Lister arranges the types",
        items = (
            ('TABBED',  "Tabbed",  "Show one type at a time, selected from tabs on top", 'LINENUMBERS_ON', 0),
            ('STACKED', "Stacked", "Show every type stacked in collapsible groups", 'LINENUMBERS_OFF', 1),
        ),
        default = 'STACKED',
        update = _onListerLayoutRedraw,
    )

    alignment_mode: bpy.props.EnumProperty(
        name = "Column Alignment",
        description = "How columns line up across the stacked sections (Stacked layout only)",
        items = (
            ('NONE',    "Independent",  "Each section sizes its own columns; sections do not line up"),
            ('UNIFIED', "Unified Grid", "Every section shares one column layout, with blank cells where a type has no value"),
        ),
        default = 'UNIFIED',
        update = _onListerLayoutRedraw,
    )

    geometry_combined: bpy.props.BoolProperty(
        name = "Combine Geometry",
        description = "Show proxies, splats, decals, fur, scenes and clippers together "
                      "in one 'Geometry' section instead of separate sections",
        default = False,
        update = _onListerGeometryCombined,
    )

    max_visible_rows: bpy.props.IntProperty(
        name = "Max Visible Rows",
        description = "Maximum rows drawn per group. Blender's immediate-mode UI has no row "
                      "virtualization, so very large values will make the lister slow to redraw. "
                      "Use the Search filter to work with large scenes instead of raising this limit",
        default = 500,
        min    = 10,
        soft_max = 2000,
        update = _onListerLayoutRedraw,
    )

    unified_hide_exclusive: bpy.props.BoolProperty(
        name = "Hide Type-Exclusive Columns",
        description = "In Unified Grid mode, hide columns that appear in only one group type "
                      "from the shared layout (select, visibility and name columns are always shown). "
                      "Reduces blank cells when groups have very different column sets",
        default = False,
        update = _onListerLayoutRedraw,
    )

    hidden_columns: bpy.props.StringProperty(
        options = {'HIDDEN'},
        default = "",
        update = _onListerLayoutRedraw,
    )

    mat_editor_layout: bpy.props.EnumProperty(
        name = "Editor List Placement",
        description = "Where the material list lives in the Material Editor view",
        items = (
            ('NAVBAR', "Navbar (drag-resize)",
             "Material list alone in the resizable navbar region; switch categories from the "
             "header dropdown. Drag the navbar border to resize - a real mouse-drag divider"),
            ('SPLIT',  "Split panel (slider)",
             "Material list as a separate column beside the parameters; category tabs stay in "
             "the navbar. The editor is sized with the Editor Width slider"),
            ('TABLE',  "Table on top, editor below",
             "The full materials table (with its basic-parameter columns) on top, and the "
             "selected material's preview and parameters below it"),
            ('PLAIN',  "Plain table (no editor)",
             "Just the materials table with its parameter columns, like the other sections - "
             "no preview/editor panel"),
        ),
        default = 'SPLIT',
        update = _onListerLayoutRedraw,
    )
    mat_editor_panel_width: bpy.props.FloatProperty(
        name = "Editor Width",
        description = "Width of the material editor (preview + parameters) panel, in UI units, "
                      "in the split-panel layout. A fixed width, so the editor stays put and the "
                      "material list takes the remaining space",
        default = 24.0, min = 12.0, max = 64.0,
        update = _onListerLayoutRedraw,
    )
    mat_editor_list_display: bpy.props.EnumProperty(
        name = "List Display",
        description = "How the material list shows its entries in the Navbar / Split layouts",
        items = (
            ('LIST',       "List",       "A compact text list, one material per row"),
            ('THUMBNAILS', "Thumbnails", "A grid of material preview thumbnails"),
        ),
        default = 'LIST',
        update = _onListerLayoutRedraw,
    )


class VRayExporterPreferences(bpy.types.AddonPreferences):
    bl_idname = "vray_blender"

    vray_cloud_binary = getVRayCloudPath()
    detect_vray_cloud = vray_cloud_binary is not None

    lister: bpy.props.PointerProperty(
        type = VRayListerPreferences,
        name = "Scene Lister",
        description = "V-Ray Scene Lister layout settings",
    )

    anonymized_telemetry: bpy.props.BoolProperty(
        default = False,
        name="Enable anonymized Telemetry",
        description="Includes the most used Product functionality and/or parameter values. The data is not personally identifiable and is not tied to the user's individual account",
        get=_getAnonymizedTelemetry,
        set=_setAnonymizedTelemetry
    )

    personalized_telemetry: bpy.props.BoolProperty(
        default = False,
        name="Enable personalized Telemetry",
        description="Same as anonymized telemetry, but contains personally identifiable information tied to an individual user's license to help Chaos tailor and optimize the Product for better personal use",
        get=_getPersonalizedTelemetry,
        set=_setPersonalizedTelemetry
    )

    vantage_host: bpy.props.StringProperty(
        name = "Address",
        default = "localhost",
        description = "V-Ray Vantage Live Link host"
    )

    vantage_port: bpy.props.IntProperty(
        name = "Port",
        default = 20703,
        description = "V-Ray Vantage Live Link port",
        min = 1,
        max = 65535
    )

    use_remote_dispatcher: bpy.props.BoolProperty(
        name = "Use Remote Dispatcher",
        description = "Use a remote dispatcher server for distributed rendering.",
        default = False
    )

    dispatcher: bpy.props.PointerProperty(
        name="Dispatcher",
        type = VRayRenderNode,
        description = "V-Ray remote dispatcher node"
    )

    nodes: bpy.props.CollectionProperty(
        name = "Render Nodes",
        type =  VRayRenderNode,
        description = "V-Ray render nodes"
    )

    nodes_selected: bpy.props.IntProperty(
        name = "Render Node Index",
        default = -1,
        min = -1,
        max = 100
    )

    render_only_on_nodes: bpy.props.BoolProperty(
        name        = "Don't Use Local Machine",
        description = "Use distributed rendering excluding the local machine",
        default     = False
    )

    preferences_menu: bpy.props.EnumProperty(
        default = 'PREFERENCES_MENU_GENERAL',
        items = (
            ('PREFERENCES_MENU_GENERAL', "General", "General preferences settings"),
            ('PREFERENCES_MENU_LOGGING', "Logging", "Logging settings"),
            ('PREFERENCES_MENU_GPU_DEVICES', "Device selection", "Select which GPU devices will be used for rendering"),
            ('PREFERENCES_MENU_DR',  "Distributed rendering",  "Change Distributed rendering preferences"),
        ),
        description = "Preference menu selection",
        options = { 'HIDDEN' }
    )

    compute_devices: bpy.props.PointerProperty(
        name = "Compute Devices",
        description = "Select compute devices for rendering",
        type = ComputeDevices
    )

    VRayProfiler: bpy.props.PointerProperty(
        name = "V-Ray Profiler",
        type = VRayProfiler,
        description = "V-Ray Profiler settings"
    )

    def _updateLogLevel(self, context):
        from vray_blender import debug
        sys_utils.StartupConfig.logLevel = None
        debug.setLogLevel(int(self.verbose_level), bool(self.enable_qt_logs))

        blender_utils.markPreferencesDirty(context)


    verbose_level: bpy.props.EnumProperty(
        name = "Log Level",
        description = "Specifies the verbosity level of information printed to the standard output",
        items = (
            ('0', "No information", "No information printed"),
            ('1', "Errors only",    "Only errors"),
            ('2', "Warnings",       "Errors and warnings"),
            ('3', "Info",           "Errors, warnings and informational messages"),
            ('4', "All",            "All output"),
        ),
        default = '2',
        update = _updateLogLevel
    )

    enable_qt_logs: bpy.props.BoolProperty(
        name = "Log Qt output",
        description = "Used to control logging from Qt",
        update = _updateLogLevel,
        default = False
    )

    loaded_from_scene: bpy.props.BoolProperty(
        name="Preferences loaded from the scene",
        default=False
    )

    ask_for_upgrade_confirm: bpy.props.BoolProperty(
        name="Ask for confirmation when upgrading scenes",
        description="When enabled will ask before upgrading scenes, if not upgrades will happen automatically",
        default=True
    )

    mtl_use_roughness: bpy.props.BoolProperty(
        name="Use roughness for new materials by default",
        description="Global switch between roughness and glossiness modes.",
        default=False
    )
    
    def _updateCheckForUpdates(self, context):
        from vray_blender.utils.update_checker import onUpdateSettingsChanged
        onUpdateSettingsChanged(self.auto_check_for_updates)
        
    auto_check_for_updates: bpy.props.BoolProperty(
        name="Automatic Check for Updates",
        description="When enabled, V-Ray will automatically check for updates",
        default=True,
        update = _updateCheckForUpdates
    )

    last_check_for_updates: bpy.props.FloatProperty(
        name = "Last Check for updates",
        description = "The time of the last check for updates",
        default = 0
    )

    community_edition: bpy.props.BoolProperty(
        name = "Community Edition",
        description = "Run V-Ray for Blender as the Community Edition. Features that are not available in CE are disabled while this is enabled",
        default = False
    )

    export_scene_file_path: bpy.props.StringProperty(
        name = "File path",
        default = '',
        description = "Path to the exported .vrscene file"
    )

    export_proxy_file_path: bpy.props.StringProperty(
        name = "File path",
        default = '',
        description = "Path to the exported .vrmesh file"
    )

    export_proxy_scope: bpy.props.EnumProperty(
        name = "Export",
        description = "Choose whether proxy export includes the whole scene or only selected objects",
        items = (
            ('SELECTION', "Selected Objects", "Export only selected objects"),
            ('WHOLE_SCENE', "Whole Scene", "Export all eligible scene objects"),
        ),
        default = 'SELECTION'
    )

    export_proxy_add_to_scene: bpy.props.BoolProperty(
        name = "Add Proxy to Scene",
        description = "After export, import the .vrmesh as a V-Ray Proxy object at the 3D cursor",
        default = False,
    )

    export_proxy_remove_exported_objects: bpy.props.BoolProperty(
        name = "Remove Exported Objects",
        description = "After a successful export, delete the source objects that were written to the proxy",
        default = False,
    )

    export_proxy_elements_per_voxel: bpy.props.IntProperty(
        name = "Elements per Voxel",
        description = "Target number of triangles in each voxel before subdivision (0 uses exporter default)",
        default = 0,
        min = 0,
    )

    export_proxy_preview_faces: bpy.props.IntProperty(
        name = "Preview Faces",
        description = "Approximate number of preview mesh triangles (0 disables preview geometry)",
        default = 10000,
        min = 0,
    )

    export_proxy_preview_type: bpy.props.EnumProperty(
        name = "Preview Type",
        description = "Method used to build the proxy preview mesh",
        items = (
            ('0', "Face Sampling", "Fastest; copies faces; triangles may look disconnected"),
            ('1', "Clustering", "Grid-based vertex reduction; robust on disconnected geometry"),
            ('2', "Edge Collapse", "Best quality where the mesh is connected; slower"),
            ('3', "Combined", "Clustering then edge collapse; recommended default"),
        ),
        default = '3',
    )

    export_proxy_animation_range: bpy.props.EnumProperty(
        name = "Animation Range",
        description = "Whether the proxy stores a single frame or a frame range",
        items = (
            ('CURRENT_FRAME', "Current Frame", "Export geometry for the current frame only"),
            ('FRAME_RANGE', "Frame Range", "Export an animated proxy using start and end frame"),
        ),
        default = 'CURRENT_FRAME',
    )

    export_proxy_start_frame: bpy.props.IntProperty(
        name = "Start Frame",
        description = "First frame when Animation Range is set to Frame Range",
        default = 0,
    )

    export_proxy_end_frame: bpy.props.IntProperty(
        name = "End Frame",
        description = "Last frame when Animation Range is set to Frame Range",
        default = 10,
        min = 0,
    )

    export_material_preview_scene: bpy.props.BoolProperty(
        name = "Export material preview scene",
        description = "Export a .vrscene for the material preview. The scene path is the vrscene path set above but with a '_preview' suffix",
        default = False
    )

    export_scene_compressed: bpy.props.BoolProperty(
        name = "Compressed",
        description = "Compress geometric information so that the resulting .vrscene file is smaller. Only valid if 'Meshes in HEX Format' is enabled",
        default = True
    )

    export_scene_hex_meshes: bpy.props.BoolProperty(
        name = "Meshes in HEX Format",
        description = "Write geometric information as binary data to avoid round-off errors",
        default = True
    )

    export_scene_hex_transforms: bpy.props.BoolProperty(
        name = "Transforms in HEX Format",
        description = "Write object matrices information as binary data to avoid round-off errors",
        default = True
    )

    export_scene_separate_files: bpy.props.BoolProperty(
        name = "Separate Files",
        description = "Write each object category to a separate file",
        default = False
    )

    export_scene_pack: bpy.props.BoolProperty(
        name = "Pack",
        description = "After export, collect all assets referenced by the scene into a folder next to the "
                      ".vrscene and make their paths relative, so the scene can be shared and redistributed "
                      "easily. Requires Chaos Cloud to be installed",
        default = False
    )

    export_scene_zip: bpy.props.BoolProperty(
        name = "Zip",
        description = "Archive the packed scene folder into a single .zip file",
        default = False
    )

    export_scene_plugin_types: bpy.props.StringProperty(
        name = 'Export File Types',
        description = 'Export file types separated by comma'
    )

    animationSettingsVrsceneExport: bpy.props.PointerProperty(
        type=AnimationSettingsVrsceneExport
    )

    def _drawAboutPanel(self, layout):
        from vray_blender.menu import VRAY_OT_show_account_status
        box = layout.box()
        header, subLayout = box.panel(idname="about", default_closed=False)
        header.label(text="About")
        if subLayout:
            split = subLayout.split(factor=0.05, align=True)
            split.column()
            panel = split.column(align=True)
            panel.separator()

            subLayout = panel.column()
            subLayout.use_property_split = True
            subLayout.use_property_decorate = False

            rowVersion = subLayout.row()
            rowVersion.label(text=f"Version: {version.getBuildVersionString()}")

            rowAccount = subLayout.row()
            rowAccount.label(text="Chaos Account settings")
            rowAccount.operator(VRAY_OT_show_account_status.bl_idname, text="Open")

            rowLicense = subLayout.row()
            currentLicense = "Community" if self.community_edition else "Commercial"
            switchTarget   = "Commercial" if self.community_edition else "Community"
            rowLicense.label(text=f"License: {currentLicense} Edition")
            rowLicense.operator(VRAY_OT_switch_license_type.bl_idname, text=f"Use {switchTarget} Edition")

            rowCloudBinary = subLayout.row()
            if self.detect_vray_cloud:
                rowCloudBinary.label(text="Chaos Cloud {0}".format(self.vray_cloud_binary))
            else:
                rowCloudBinary.label(text="Chaos Cloud is not detected on your system!")

    def _drawTelemetryPanel(self, layout):
        box = layout.box()
        header, subLayout = box.panel(idname="Usage statistics sharing", default_closed=True)
        header.label(text="Usage statistics")
        if subLayout:
            split = subLayout.split(factor=0.05, align=True)
            split.column()

            panel = split.column(align=True)
            panel.separator()

            telemetryTextLabelCol = panel.column()
            telemetryTextLabelCol.label(text="The Improvement Program of Chaos Software (Chaos)")
            telemetryTextLabelCol.label(text="helps improve the Product by tracking general usage statistics")
            telemetryTextLabelCol.label(text="and to automatically collect crash information.")

            panel.separator(factor=2)
            panel.label(text="You can choose to enable (and change your choice at any time):")

            telemetryCol = panel.column()
            telemetryCol.separator(type="LINE")
            telemetryCol.prop(self, "anonymized_telemetry", text="Anonymized Telemetry")
            telemetryCol.label(text="Includes the most used Product functionality and/or parameter values.")
            telemetryCol.label(text="The data is not personally identifiable and is not tied")
            telemetryCol.label(text="to the user's individual account")
            telemetryCol.separator(type="LINE")

            personalTelemetryCol = telemetryCol.column()
            personalTelemetryCol.enabled = self.anonymized_telemetry
            personalTelemetryCol.prop(self, "personalized_telemetry", text="Personalized Telemetry")
            personalTelemetryCol.label(text="Same as anonymized telemetry, but contains personally identifiable")
            personalTelemetryCol.label(text="information tied to an individual user's license to help Chaos tailor")
            personalTelemetryCol.label(text="and optimize the Product for better personal use")

            telemetryCol.separator(type="LINE")
            opTelemetry = telemetryCol.operator("vray.url_open", text="Learn more", icon='HELP')
            opTelemetry.url = "https://documentation.chaos.com/space/VBLD/117637472/Chaos+Telemetry"
            opTelemetry.description = "Open Chaos Telemetry documentation page"

    def _drawAdditionalSettingsPanel(self, layout):
        box = layout.box()
        header, subLayout = box.panel(idname="additional_settings", default_closed=True)
        header.label(text="Additional settings")
        if subLayout:
            split = subLayout.split(factor=0.05, align=True)
            split.column()
            panel = split.column(align=True)
            panel.separator()

            subLayout = panel.column()
            subLayout.use_property_split = True
            subLayout.use_property_decorate = False

            subLayout.prop(self, 'ask_for_upgrade_confirm')
            subLayout.prop(self, 'mtl_use_roughness')
            subLayout.prop(self, 'auto_check_for_updates')

    def _drawPerformancePanel(self, layout, context):
        box = layout.box()
        header, subLayout = box.panel(idname="performance", default_closed=True)
        header.label(text="Performance")
        if not subLayout:
            return

        split = subLayout.split(factor=0.05, align=True)
        split.column()
        panel = split.column(align=True)
        panel.separator()

        vrayExporter = context.scene.vray.Exporter

        subLayout = panel.column()
        subLayout.use_property_split = True
        subLayout.use_property_decorate = False

        subLayout.prop(vrayExporter, 'use_custom_thread_count', text='Thread Mode')
        threadCount = psutil.cpu_count() if sys.platform == "win32" else os.cpu_count()
        infoRow = subLayout.row()
        infoRow.alignment = 'RIGHT'
        infoRow.label(text=f"The system has {threadCount} CPU threads")
        threadsRow = subLayout.row()
        threadsRow.enabled = vrayExporter.use_custom_thread_count == 'FIXED'
        threadsRow.prop(vrayExporter, 'custom_thread_count', text='Threads')
        if platform.system() != "Linux":
            subLayout.separator()
            subLayout.prop(vrayExporter, 'lower_thread_priority', text='Lower Thread Priority')

    def _drawGeneralPanel(self, layout, context):
        self._drawAboutPanel(layout)
        self._drawTelemetryPanel(layout)
        self._drawAdditionalSettingsPanel(layout)
        self._drawPerformancePanel(layout, context)

    def _drawLoggingPanel(self, layout, context):
        box = layout.box()
        header, subLayout = box.panel(idname="logging", default_closed=False)
        header.label(text="Logging")
        if subLayout:
            split = subLayout.split(factor=0.05, align=True)
            split.column()
            panel = split.column(align=True)
            panel.separator()

            subLayout = panel.column()
            subLayout.use_property_split = True
            subLayout.use_property_decorate = False

            subLayout.prop(self, "verbose_level")
            subLayout.prop(self, "enable_qt_logs")

    def _drawComputeDevicesPanel(self, layout, context):
        computeDevices = self.compute_devices

        header, subLayout = layout.box().panel(idname="Compute Devices", default_closed=False)
        header.label(text="V-Ray GPU Compute Devices")
        if subLayout:
            subLayout = subLayout.column()
            subLayout.use_property_split = True
            subLayout.use_property_decorate = False
            if sys.platform == "darwin":
                if devicesList := getattr(computeDevices, "devicesMetal"):
                    for device in devicesList:
                        deviceRow = subLayout.row()
                        deviceRow.prop(device, "deviceEnabled", text=device.deviceName)
            else:
                subLayout.prop(computeDevices, "gpuDeviceType")
                subLayout.separator()

                from vray_blender.plugins.system.compute_devices import getDeviceCollectionByType
                deviceType = computeDevices.gpuDeviceType
                devicesAttr = getDeviceCollectionByType(deviceType)
                if devicesList := getattr(computeDevices, devicesAttr):
                    for device in devicesList:
                        deviceRow = subLayout.row()
                        deviceRow.prop(device, "deviceEnabled", text=device.deviceName)
                else:
                    noDevicesRow = subLayout.row()
                    noDevicesRow.label(text="No compute devices available")

    def _drawDistributedRenderingPanel(self, layout, context):
        header, subLayout = layout.box().panel(idname='dr_prefs', default_closed=False)

        headerRow = header.row()
        headerRow.alignment = 'LEFT'
        headerRow.scale_x = 0.75
        labelRow = headerRow.row()
        labelRow.label(text="V-Ray Distributed Rendering")
        labelRow.enabled = not vray.isCommunityEdition()

        if vray.isCommunityEdition():
            drawCELimitedFeatureIcon(headerRow)

        if subLayout:
            subLayout.enabled = not vray.isCommunityEdition()
            split = subLayout.split(factor=0.05, align=True)
            split.column()
            panel = split.column(align=True)
            panel.separator()

            subLayout = panel.column()
            subLayout.use_property_split = True
            subLayout.use_property_decorate = False

            subLayout.prop(self, 'render_only_on_nodes')

            subLayout.prop(self, 'use_remote_dispatcher', text='Use Remote Dispatcher')
            col = subLayout.column()
            col.enabled = self.use_remote_dispatcher
            col.prop(self.dispatcher, "address", text='Remote Dispatcher')
            col.prop(self.dispatcher, "port", text=' ')

            row = panel.row()
            row.template_list('VRAY_UL_DR', '', self, 'nodes', self, 'nodes_selected', rows=3)
            col = row.column(align=True)
            col.operator('vray.render_nodes_add', text="", icon='ADD')
            col.operator('vray.render_nodes_remove', text="", icon='REMOVE')

            col = col.row().column(align=True)
            col.operator('vray.dr_nodes_load', text="", icon='FILE_FOLDER')
            col.operator('vray.dr_nodes_save', text="", icon='DISK_DRIVE')

        if False:
            # Draw the vantage settings on all operating systems, MacOS V-Ray Blender and
            # Vantage running on Windows is a valid use-case.
            header, subLayout = layout.box().panel(idname="live_link", default_closed=False)
            header.label(text='Vantage Live Link')
            if subLayout:
                subLayout = subLayout.column()
                subLayout.use_property_split = True
                subLayout.use_property_decorate = False

                subLayout.prop(self, 'vantage_host', text='Vantage Host')
                subLayout.prop(self, 'vantage_port', text=' ')

    def draw(self, context):
        layout = self.layout

        row = layout.row(align=True)
        row.prop(self, 'preferences_menu', expand=True)

        if self.preferences_menu == 'PREFERENCES_MENU_GENERAL':
            self._drawGeneralPanel(layout, context)
        elif self.preferences_menu == 'PREFERENCES_MENU_LOGGING':
            self._drawLoggingPanel(layout, context)
        elif self.preferences_menu == 'PREFERENCES_MENU_GPU_DEVICES':
            self._drawComputeDevicesPanel(layout, context)
        elif self.preferences_menu == 'PREFERENCES_MENU_DR':
            self._drawDistributedRenderingPanel(layout, context)


def getRegClasses():
    return (
        VRayRenderNode,
        AnimationSettingsVrsceneExport,
        VRayProfiler,
        VRAY_OT_switch_license_type,
        VRayListerPreferences,
        VRayExporterPreferences,
    )


def register():
    for regClass in getRegClasses():
        bpy.utils.register_class(regClass)


def unregister():
    for regClass in reversed(getRegClasses()):
        bpy.utils.unregister_class(regClass)
