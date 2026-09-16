# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later


bl_info = {
    "name"        : "V-Ray For Blender",
    "author"      : "Chaos Software",
    "blender"     : (4, 5, 0), # this should be the earliest version currently supported by the plugin
    "location"    : "Info header, render engine menu",
    "description" : "V-Ray render engine integration",
    "doc_url"     : "https://documentation.chaos.com/space/VBLD",
    "tracker_url" : "https://support.chaos.com/hc/en-us/requests/new",
    "category"    : "Render",
    "version"     : ("7", "40", "01")
}

# A monotonically increasing number used to identify points at which an upgrade to the scene data
# should be made. Every time a new upgrade script is added, this number should be increased by 1
# and included in the upgrade script's name. The number is saved to the scene, so we can compare
# the current value with the value in a loaded scene and determine which upgrade scripts should
# be run.
# Numbers 0 and 1 are reserved for the scene versions before the upgrade number feature was introduced
UPGRADE_NUMBER = 51

try:
    import numpy as np
except:
    import platform, sys
    if platform.machine() == "x86_64" and sys.platform == "darwin":
        import ensurepip
        ensurepip.bootstrap()

        from importlib.metadata import version
        try:
            oldNumpyVersion = version("numpy")
        except:
            oldNumpyVersion = "1.26.4"
        import subprocess
        subprocess.check_call([sys.executable, "-m", "pip", "uninstall", "-y", "numpy"])
        subprocess.check_call([sys.executable, "-m", "pip", "install", f"numpy=={oldNumpyVersion}"])

# Install a Python implementation of isCommunityEdition on the native
# VRayBlenderLib module so that `vray.isCommunityEdition()` call sites
# resolve to a runtime check against the addon preference. This must run
# before any submodule that calls isCommunityEdition at import time
# (e.g. lib.plugin_utils, utils.update_checker).
from vray_blender.bin import VRayBlenderLib as vray

def _isCommunityEdition():
    import bpy
    try:
        return bool(bpy.context.preferences.addons["vray_blender"].preferences.community_edition)
    except (KeyError, AttributeError):
        # Preferences are not yet registered (e.g. during early addon load).
        return False

vray.isCommunityEdition = _isCommunityEdition

from vray_blender import debug
from vray_blender import features
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
from vray_blender import cosmos_drag_drop
from vray_blender.lib import image_utils

_isRegistered = False
_isVRayInitialized = False

def initVRay():
    """ Initialize VRayBlenderLib. Guarded once-per-process: also called by the Chaos Scatter
        addon, in any order relative to vray_blender's own register(), and the native init is
        not idempotent (it would stack duplicate log writers).
    """
    from vray_blender.lib.path_utils import getV4BTempDir
    from vray_blender.lib.sys_utils import StartupConfig
    import os

    global _isVRayInitialized
    if _isVRayInitialized:
        return
    _isVRayInitialized = True

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
    logLevel = debug.LogLevel.Debug if StartupConfig.debugUI else debug.LogLevel.Warning
    debug.setLogLevel(logLevel , StartupConfig.debugUI)

    from vray_blender.version import getBuildVersionString
    print(f"V-Ray for Blender addon, version {getBuildVersionString()}")
    print(f"V-Ray for Blender logging to {logFile}")
    vray.init(logFile)


def exitVRay():
    """ Tear down VRayBlenderLib and re-arm initVRay(). vray.exit() stops the logging subsystem
        (which clears its writers), so the guard MUST be reset here - otherwise a re-enable of this
        addon (or a start triggered by the Chaos Scatter addon) would skip vray.init() and run with
        no logging at all. Symmetric with the once-per-process init guard.
    """
    global _isVRayInitialized
    vray.exit()
    _isVRayInitialized = False


def _getModules():
    """ Modules requiring registration/unregistration """
    return (
        plugins,
        operators,
        ui,
        menu,
        nodes,
        proxy,
        keymap,
        utils,
        image_utils,
        cosmos_drag_drop,
    )


def register():
    import bpy
    from vray_blender.lib.sys_utils import StartupConfig

    # Init VRayBlenderLib first as it sets up the logging subsystem
    initVRay()

    # Resolve feature flags before anything is registered so that disabled
    # features are hidden from the UI and Blender's F3 operator search.
    features.init()

    debug.register()

    # Parse command line
    StartupConfig.init()

    for mod in _getModules():
        mod.register()

    # Make the bundled presets (e.g. Image Sampler quality levels under
    # <addon>/presets/) discoverable by Blender's preset menus.
    import os
    bpy.utils.register_preset_path(os.path.dirname(__file__))

    events.register()

    # NOTE: Register engine at the end,
    # to be sure all used data is registered.
    engine.register()
    engine.ensureRunning()

    if not bpy.app.background:
        # In headless mode, the upgrade will be triggered when the scene is loaded
        from vray_blender.engine.vfb_event_handler import VfbEventHandler
        VfbEventHandler.upgradeScene()

    global _isRegistered
    _isRegistered = True


def _switchViewportsToSolid():
    """ Switching all 'VIEW_3D' spaces to 'SOLID' shading mode """
    import bpy

    for screen in bpy.data.screens:
        if hasattr(screen, "areas"):
            for area in screen.areas:
                if area.type == 'VIEW_3D':
                    for space in area.spaces:
                        if space.type == 'VIEW_3D':
                            space.shading.type = 'SOLID'


def unregister():

    if not _isRegistered:
        return

    # Switching all 3D viewports to SOLID mode
    # to ensure no updates are triggered during unregistration.
    _switchViewportsToSolid()

    from vray_blender.engine.vfb_event_handler import VfbEventHandler
    from vray_blender.engine.render_engine import VRayRenderEngine

    VRayRenderEngine.resetAll()
    VfbEventHandler.stop()

    import bpy, os
    bpy.utils.unregister_preset_path(os.path.dirname(__file__))

    debug.unregister()
    events.unregister()

    for mod in reversed(_getModules()):
        mod.unregister()

    # The order is important, the engine must be shuted down before unregistration!!!
    engine.shutdown()
    engine.unregister()
    exitVRay()
