import bpy

from vray_blender.bin import VRayBlenderLib as vray
from vray_blender.lib import camera_utils as ct
from vray_blender.plugins.system.compute_devices import ComputeDevices, ComputeDeviceSelector

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
        rd = scene.render

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


# Custom export animation settings as a separate property group
class AnimationSettingsVrsceneExport(bpy.types.PropertyGroup):
    customFrameStart: bpy.props.IntProperty(
        name = "Start Frame",
        description = "Start frame for export (overrides scene frame start during VRScene export only)",
        default = 1,
        min = 0
    )

    customFrameEnd: bpy.props.IntProperty(
        name = "End Frame",
        description = "End frame for export (overrides scene frame end during VRScene export only)",
        default = 250,
        min = 0
    )

    exportAnimation: bpy.props.BoolProperty(
        name = "Export Animation",
        description = "Export animation frames",
        default = False
    )

    frameRangeMode: bpy.props.EnumProperty(
        name='Animation Mode',
        description='How to handle animation during export',
        items = (
            ('SCENE_RANGE', "Scene Range", "Use scene frame range"),
            ('CUSTOM_RANGE', "Custom Range", "Use custom frame range")
        ),
        default = 'SCENE_RANGE'
    )

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

    animationSettingsVrsceneExport: bpy.props.PointerProperty(
        type=AnimationSettingsVrsceneExport
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
        debug.setLogLevel(int(self.verbose_level), False)


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
        update = _updateLogLevel,
        options = set()
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
        update=_displayVfbOnTopUpdate,
        options = set()
    )

    use_custom_thread_count: bpy.props.EnumProperty(
        name = "Threads Mode",
        description = "Allows you to adjust the number of CPU rendering threads.",
        items = (
            ('AUTO',  "Auto",  "Use all system threads."),
            ('FIXED', "Fixed", "Use a fixed number of threads. In some cases it can be beneficial to leave one or more threads for third-party applications.")
        ),
        default = 'AUTO',
        options = set()
    )

    custom_thread_count: bpy.props.IntProperty(
        name = "Threads",
        description = "The maximum number of CPU threads V-Ray is allowed to use",
        default = 1,
        min = 1,
        max = 256,
        options = set()
    )

    lower_thread_priority: bpy.props.BoolProperty(
        name = "Lower Thread Priority",
        description = "Use lower thread priority for rendering. Helps reduce Windows issues (like freezing) during CPU-intensive tasks.",
        default = True,
        options = set()
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
        default = False,
        options = set()
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
        default = "V-Ray for Blender"
    )

    vray_cloud_job_name: bpy.props.StringProperty(
        name = "Job",
        description = "Chaos Cloud Job Name",
        default = "$F"
    )

    # Unused, left for compatibility reasons and should be removed in a later release.
    computeDevices: bpy.props.PointerProperty(
        name = "Compute Devices",
        description = "Select compute devices for rendering",
        type = ComputeDevices,
        options = set()
    )

    device_type: bpy.props.EnumProperty(
        name = "Device Type",
        description = "Rendering device",
        items = (
            ('CPU', "V-Ray", ""),
            ('GPU', "V-Ray GPU", ""),
        ),
        default = 'CPU',
        options = set()
    )

    use_gpu_rtx: bpy.props.BoolProperty(
        name = "Use RTX (no CPU, Slower start)",
        default = False,
        options = set()
    )

def getRegClasses():
    return (
        ComputeDeviceSelector,
        ComputeDevices,
        AnimationSettingsVrsceneExport,
        VRayExporter,
    )


def register():
    for regClass in getRegClasses():
        bpy.utils.register_class(regClass)


def unregister():
    for regClass in reversed(getRegClasses()):
        bpy.utils.unregister_class(regClass)
