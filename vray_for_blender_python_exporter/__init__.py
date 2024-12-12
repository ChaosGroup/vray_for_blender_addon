
bl_info = {
    "name"        : "V-Ray For Blender",
    "author"      : "Chaos Software GmBH",
    "blender"     : (4, 2, 0), # this should be the version currently supported by the plugin
    "location"    : "Info header, render engine menu",
    "description" : "V-Ray render engine integration",
    "doc_url"     : "https://docs.chaos.com/display/VBLD/",
    "tracker_url" : "https://support.chaos.com/hc/en-us/requests/new",
    "category"    : "Render",
    "version"     : ("7", "00", "10") # Versions are strings since we can have versions like "6.00.00"
}

from vray_blender import debug
from vray_blender import plugins
from vray_blender import operators
from vray_blender import proxy
from vray_blender import menu
from vray_blender import nodes
from vray_blender import engine
from vray_blender import keymap
from vray_blender import ui
from vray_blender import events
from vray_blender import utils

from vray_blender.bin import VRayBlenderLib as vray

def initVRay():
    """ Initialize VRayBlenderLib """
    from vray_blender.lib.path_utils import getV4BTempDir
    from vray_blender.lib.sys_utils import StartupConfig
    import os
    
    # Keep things tidy in a dedicated temp folder 
    logDir = getV4BTempDir()
    os.makedirs(logDir, exist_ok=True)

    # Therе may be multiple copies of Blender running. On the other hand, we want to keep 
    # the number of existing log files under control, so a unique name per session is not
    # an option.
    # The policy implemented below will try to find the first available name from a set of
    # well-known names.
    success = False
    fileName = "vray_blender.log"
    attempt = 1

    while not success:
        logFile = os.path.join(logDir, fileName)
        try:
            if os.path.exists(logFile):
                with open(logFile, 'w'):
                    # Just open the file to make sure it is not locked for writing. 
                    # If it is, this means that we need to try the next name.
                    pass

            success  = True
        except OSError:
            # File locked, try next name
            fileName = f"vray_blender_{attempt}.log"
            attempt += 1

    from vray_blender import debug

    # Set the log level to use during add-on initialization. When a scene is loaded, the
    # level will be reset to the one saved in the scene.
    debug.setLogLevel(debug.LogLevel.Debug if StartupConfig.debugUI else debug.LogLevel.Warning)

    print(f"V-Ray for Blender logging to {logFile}")
    vray.init(logFile)


def register():
    from vray_blender.lib.sys_utils import StartupConfig

    # Init VRayBlenderLib first as it sets up the logging subsystem
    initVRay()

    debug.register()

    # Parse command line
    StartupConfig.init()

    plugins.register()
    operators.register()
    ui.register()
    menu.register()
    nodes.register()
    proxy.register()
    keymap.register()
    events.register()
    utils.register()

    # NOTE: Register engine at the end,
    # to be sure all used data is registered.
    engine.register()
    engine.ensureRunning()

def unregister():
    from vray_blender.engine.vfb_event_handler import VfbEventHandler
    VfbEventHandler.stop()

    # Remove events first
    events.unregister()
    engine.unregister()

    plugins.unregister()
    operators.unregister()
    nodes.unregister()
    menu.unregister()
    proxy.unregister()
    ui.unregister()
    keymap.unregister()
    utils.unregister()

    engine.shutdown()
    debug.unregister()
