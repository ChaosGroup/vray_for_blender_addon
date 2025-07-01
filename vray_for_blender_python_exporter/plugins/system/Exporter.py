
import bpy
import winreg

from vray_blender.bin import VRayBlenderLib as vray

import os
import json

from vray_blender import version
from vray_blender.lib import sys_utils
from vray_blender.lib import camera_utils as ct



TYPE = 'SYSTEM'
ID   = 'VRayExporter'
NAME = 'Exporter'
DESC = "Exporter configuration"


# TODO fix or remove this function
def _mtlEditorUpdatePreview(self, context):
    if self.materialListIndexPrev != self.materialListIndex:
        self.materialListIndexPrev = self.materialListIndex

def _stereoToggleUpdate(self, context):
        scene = context.scene
        vrayScene = scene.vray
        stereoSettings = vrayScene.VRayStereoscopicSettings
        rd    = scene.render
        
        if stereoSettings.use:
            # The order of changing stereoSettings.use and getting the size is important here,
            # do not change
            mult = ct.getStereoscopicSizeMult(vrayScene)
            rd.resolution_x = int(rd.resolution_x / mult.w)
            rd.resolution_y = int(rd.resolution_y / mult.h)
            stereoSettings.use = False
        else:
            stereoSettings.use = True
            mult = ct.getStereoscopicSizeMult(vrayScene)
            rd.resolution_x = int(rd.resolution_x * mult.w)
            rd.resolution_y = int(rd.resolution_y * mult.h)

def _displayVfbOnTopUpdate(self, context):
    vray.setVfbOnTop(self.display_vfb_on_top)


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

def _getTelemetry(self, keyName):
    # Telemetry settings are stored in three places:
    # 1. 'keyName' envvar
    # 2. HKEY_CURRENT_USER\Environment\'keyName' for the current user
    # 3. HKEY_LOCAL_MACHINE\SYSTEM\CurrentControlSet\Control\Session Manager\Environment for the system
    # User setting takes precedence and is the one we can set from Blender w/o admin privileges
    userPath = "Environment"
    systemPath = r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"
    if keyName in os.environ:
        envVal = os.environ[keyName]
        if envVal in ["0", "1"]:
            return envVal != "0"
    userVal = sys_utils.getWinRegistry(userPath, keyName)
    if userVal != None and userVal != "-1":
        return userVal != "0"
    else:
        systemVal = sys_utils.getWinRegistry(systemPath, keyName, winreg.HKEY_LOCAL_MACHINE)
        return systemVal == "1"

def _getAnonymizedTelemetry(self):
    return _getTelemetry(self, "VRAY_SEND_ANONYMIZED_FEEDBACK")

def _getPersonalizedTelemetry(self):
    return _getTelemetry(self, "VRAY_SEND_PERSONALIZED_FEEDBACK")

def _setTelemetry(self, keyName, value):
    pathToKey = "Environment"
    newValue = "1" if value==True else "0"
    # Update envvar if it was present
    if keyName in os.environ:
        os.environ[keyName]=newValue
    # Update registry value here too, so it works even if ZMQ server isn't running
    sys_utils.setWinRegistry(pathToKey, keyName, newValue)

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

class VRayExporterPreferences(bpy.types.AddonPreferences):
    bl_idname = "vray_blender"

    vray_cloud_binary = getVRayCloudPath()
    detect_vray_cloud = vray_cloud_binary is not None

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

    def draw(self, context):
        from vray_blender.menu import VRAY_OT_show_account_status

        layout = self.layout
        
        rowVersion = layout.row()
        rowVersion.label(text=f"Version: {version.getBuildVersionString()}")

        rowAccount = layout.row()
        rowAccount.label(text="Chaos Account settings")
        rowAccount.operator(VRAY_OT_show_account_status.bl_idname, text="Open")

        rowCloudBinary = layout.row()
        if self.detect_vray_cloud:
            rowCloudBinary.label(text="Chaos Cloud binary: {0}".format(self.vray_cloud_binary))    
        else:
            rowCloudBinary.label(text="Chaos Cloud binary is not detected on your system!")

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
            telemetryCol.operator("wm.url_open", text="Learn more", icon='HELP',).url = "https://docs.chaos.com/display/VBLD/Chaos+Telemetry"





class VRayExporter(bpy.types.PropertyGroup):
    experimental: bpy.props.BoolProperty(
        name        = "Experimental",
        description = "Enable experimental options",
        default     = False
    )

    spherical_harmonics: bpy.props.EnumProperty(
        name = "Spherical Harmonics Mode",
        description = "Bake or render spherical harmonics",
        items = (
            ('BAKE',   "Bake",   ""),
            ('RENDER', "Render", ""),
        ),
        default = 'BAKE'
    )

    vrayAddonVersion: bpy.props.StringProperty(
        name = "V-Ray addon version",
        description = "The version of the V-Ray addon used to create the scene",
        default = ""
    )

    vrayAddonUpgradeNumber: bpy.props.IntProperty(
        name = "V-Ray addon upgrade version",
        description = "The upgrade version of the V-Ray addon used to create the scene",
        default = 0
    )

    ######## ##     ## ########   #######  ########  ########
    ##        ##   ##  ##     ## ##     ## ##     ##    ##
    ##         ## ##   ##     ## ##     ## ##     ##    ##
    ######      ###    ########  ##     ## ########     ##
    ##         ## ##   ##        ##     ## ##   ##      ##
    ##        ##   ##  ##        ##     ## ##    ##     ##
    ######## ##     ## ##         #######  ##     ##    ##

    currentBakeObject: bpy.props.PointerProperty(
        name = "Current Bake object",
        type = bpy.types.Object,
        options= {'HIDDEN'},
    )

    isBakeMode: bpy.props.BoolProperty(
        name = "Bake mode",
        description = "Bake texture mode",
        default = False
    )

    use_stereo: bpy.props.BoolProperty(
        name = "Stereo",
        description = "Render stereoscopic",
        default = False,
        update = _stereoToggleUpdate
    )

    use_still_motion_blur: bpy.props.BoolProperty(
        name        = "Still Motion Blur",
        description = "Generate data for motion blur",
        default     = False
    )

    use_hair: bpy.props.BoolProperty(
        name = "Export Hair",
        description = "Render hair",
        default = True
    )

    calculate_instancer_velocity: bpy.props.BoolProperty(
        name = "Calculate instancer velocity",
        description = "Used to calculate particles velocty when using motion blur",
        default = False
    )

    use_smoke: bpy.props.BoolProperty(
        name = "Export Smoke",
        description = "Render smoke",
        default = True
    )

    camera_loop: bpy.props.BoolProperty(
        name = "Camera Loop",
        description = "Render views from all cameras",
        default = False
    )

    export_scene_file_path: bpy.props.StringProperty(
        name = "File path",
        default = '',
        description = "Path to the exported .vrscene file"
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

    export_scene_plugin_types: bpy.props.StringProperty(
        name = 'Export File Types',
        description = 'Export file types separated by comma'
    )

    materialListIndex: bpy.props.IntProperty(
        name        = "Material List Index",
        description = "Material list index",
        min         = -1,
        default     = -1,
        update      = _mtlEditorUpdatePreview,
    )

    materialListIndexPrev: bpy.props.IntProperty(
        name        = "Material List Index Previous State",
        description = "Material list index previous state",
        min         = -1,
        default     = -1,
    )

    materialListShowPreview: bpy.props.BoolProperty(
        name        = "Show preview",
        description = "Show preview",
        default     = True
    )

    useSeparateFiles: bpy.props.BoolProperty(
        name        = "Separate Files",
        description = "Export plugins to separate files",
        default     = True
    )


    ########  ######## ##    ## ########  ######## ########      ######  ######## ######## ######## #### ##    ##  ######    ######
    ##     ## ##       ###   ## ##     ## ##       ##     ##    ##    ## ##          ##       ##     ##  ###   ## ##    ##  ##    ##
    ##     ## ##       ####  ## ##     ## ##       ##     ##    ##       ##          ##       ##     ##  ####  ## ##        ##
    ########  ######   ## ## ## ##     ## ######   ########      ######  ######      ##       ##     ##  ## ## ## ##   ####  ######
    ##   ##   ##       ##  #### ##     ## ##       ##   ##            ## ##          ##       ##     ##  ##  #### ##    ##        ##
    ##    ##  ##       ##   ### ##     ## ##       ##    ##     ##    ## ##          ##       ##     ##  ##   ### ##    ##  ##    ##
    ##     ## ######## ##    ## ########  ######## ##     ##     ######  ########    ##       ##    #### ##    ##  ######    ######

    animation_mode: bpy.props.EnumProperty(
        name = "Animation Mode",
        description = "Animation Type",
        items = (
            ('FRAME',         "Single Frame",                "Render only current frame"),
            ('ANIMATION',     "Animation",                   "Export full animation range then render"),
        ),
        default = 'FRAME'
    )

    draft: bpy.props.BoolProperty(
        name = "Draft Render",
        description = "Render with low settings",
        default = False
    )

    image_to_blender: bpy.props.BoolProperty(
        name = "Image To Blender",
        description = "Pass image to Blender on render end (EXR file format is used)",
        default = False
    )

    ########  ########   #######   ######  ########  ######   ######
    ##     ## ##     ## ##     ## ##    ## ##       ##    ## ##    ##
    ##     ## ##     ## ##     ## ##       ##       ##       ##
    ########  ########  ##     ## ##       ######    ######   ######
    ##        ##   ##   ##     ## ##       ##             ##       ##
    ##        ##    ##  ##     ## ##    ## ##       ##    ## ##    ##
    ##        ##     ##  #######   ######  ########  ######   ######

    def _updateLogLevel(self, context):
        from vray_blender import debug
        debug.setLogLevel(int(self.verbose_level))


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

    
    log_window: bpy.props.BoolProperty(
        name = "Show Log Window",
        description = "Show log window (Linux)",
        default = False
    )

    log_window_type: bpy.props.EnumProperty(
        name = "Log Window Type",
        description = "Log window type",
        items = (
            ('DEFAULT', "Default",        ""),
            ('XTERM',   "XTerm",          ""),
            ('GNOME',   "Gnome Terminal", ""),
            ('KDE',     "Konsole",        ""),
            ('CUSTOM',  "Custom",         "")
        ),
        default = 'DEFAULT'
    )

    log_window_term: bpy.props.StringProperty(
        name = "Custom Terminal",
        description = "Custom log window terminal command",
        default = "x-terminal-emulator"
    )

    display_vfb_on_top: bpy.props.BoolProperty(
        name = "Display Always On Top",
        description = "Display VFB on top of the other windows",
        default = True,
        update=_displayVfbOnTopUpdate
    )

    use_custom_thread_count: bpy.props.EnumProperty(
        name = "Threads Mode",
        description = "Allows you to adjust the number of CPU rendering threads.",
        items = (
            ('AUTO',  "Auto",  "Use all system threads."),
            ('FIXED', "Fixed", "Use a fixed number of threads. In some cases it can be beneficial to leave one or more threads for third-party applications.")
        ),
        default = 'AUTO'
    )

    custom_thread_count: bpy.props.IntProperty(
        name = "Threads",
        description = "The maximum number of CPU threads V-Ray is allowed to use",
        default = 1,
        min = 1,
        max = 256
    )

    lower_thread_priority: bpy.props.BoolProperty(
        name = "Lower Thread Priority",
        description = "Use lower thread priority for rendering. Helps reduce Windows issues (like freezing) during CPU-intensive tasks.",
        default = True
    )

    detect_vray: bpy.props.BoolProperty(
        name = "Detect V-Ray",
        description = "Detect V-Ray binary location",
        default = True
    )

    vray_binary: bpy.props.StringProperty(
        name = "Path",
        subtype = 'FILE_PATH',
        description = "Path to V-Ray binary. Don\'t use relative path here - use absolute!"
    )

    auto_save_render: bpy.props.BoolProperty(
        name = "Save Render",
        description = "Save render automatically",
        default = False
    )

    subsurf_to_osd: bpy.props.BoolProperty(
        name        = "Subsurf To OpenSubdiv",
        description = "Automatically convert Subsurf modifier (if last in stack) to V-Ray's OpenSubdiv subdivision",
        default     = False
    )

    data_format: bpy.props.EnumProperty(
        name = "Export Format",
        description = "Export data format",
        items = (
            ('ZIP',   "ZIP",        "Compress list data"),
            ('HEX',   "HEX",        "Export list data in hex format"),
            ('ASCII', "Plain Text", "Export as plain text"),
        ),
        default = 'ZIP'
    )

    debug_log_times: bpy.props.BoolProperty(
        name = "Log times",
        description = "Log time info to console",
        default = False
    )

    debug_threads: bpy.props.IntProperty(
        name = "Exporter threads",
        description = "Exporter threads count",
        default = 2,
        min = 1,
        max = 64
    )

    zmq_address: bpy.props.StringProperty(
        name = "Server Address",
        description = "Server address",
        default = ""
    )

    zmq_port: bpy.props.IntProperty(
        name = "Server Port",
        description = "Server port. Pass -1 for a system-chosen port number.",
        min = 1000,
        max = 65535,
        default = -1
    )

    vray_cloud_project_name: bpy.props.StringProperty(
        name = "Project",
        description = "Chaos Cloud Project Name",
        default = "Blender for V-Ray"
    )

    vray_cloud_job_name: bpy.props.StringProperty(
        name = "Job",
        description = "Chaos Cloud Job Name",
        default = "$F"
    )

    device_type: bpy.props.EnumProperty(
        name = "Device Type",
        description = "Rendering device",
        items = (
            ('CPU', "V-Ray", ""),
            ('GPU', "V-Ray GPU", ""),
        ),
        default = 'CPU'
    )

    use_gpu_rtx: bpy.props.BoolProperty(
        name = "Use RTX (no CPU, Slower start)",
        default = False
    )
  

def getRegClasses():
    return (
        VRayExporter,
        VRayExporterPreferences
    )


def register():
    for regClass in getRegClasses():
        bpy.utils.register_class(regClass)


def unregister():
    for regClass in getRegClasses():
        bpy.utils.unregister_class(regClass)
