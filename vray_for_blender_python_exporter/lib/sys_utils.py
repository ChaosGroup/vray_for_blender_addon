# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later


import getpass
import os
import socket
import sys
from contextlib import suppress
from pathlib import PurePath

import bpy
from vray_blender import debug


def activeRendererExists():
    """ Check if there is an active renderer """
    from vray_blender.engine.renderer_ipr_vfb import VRayRendererIprVfb
    from vray_blender.engine.renderer_ipr_viewport import VRayRendererIprViewport
    from vray_blender.engine.renderer_prod import VRayRendererProd

    return any(r.isActive() for r in (VRayRendererProd, VRayRendererIprViewport, VRayRendererIprVfb))


def getUsername():
    if sys.platform == 'win32':
        return "standalone"
    else:
        return getpass.getuser()


def getHostname():
    return socket.gethostname()


def getPlatformName(executableBaseName: str):
    """ Return the executable name with the correct platform suffix. """
    match sys.platform:
        case 'win32': suffix = ".exe"
        case 'linux': suffix = ""
        case 'darwin': suffix = ""
        case _:
            raise Exception(f"Unsupported platform: {sys.platform}")

    return str(PurePath(executableBaseName).with_suffix(suffix))


def getExporterPath():
    for path in bpy.utils.script_paths(subdir=os.path.join('addons','vray_blender')):
        if path:
            return path
    return None


def getUserConfigDir():
    userConfigDirpath = os.path.join(bpy.utils.user_resource('CONFIG'), "vrayblender")
    if not os.path.exists(userConfigDirpath):
        os.makedirs(userConfigDirpath)
    return userConfigDirpath


def getWinRegistry(path: str, key_name: str, registry_root=None) -> str:
    import winreg
    if not registry_root:
        registry_root = winreg.HKEY_CURRENT_USER

    """
    Reads a string value from the Windows registry.

    Args:
        path (str): The path of the registry key.
        key_name (str): The name of the registry key.
        registry_root (int, optional): The root registry key (default is HKEY_CURRENT_USER).

    Returns:
        int: The string value stored in the specified registry key or None if missing.
    """
    with suppress(FileNotFoundError), winreg.OpenKey(registry_root, path) as key:
        value, _ = winreg.QueryValueEx(key, key_name)
        return value
    return None

def parseIni(filename):
    """ Parse an .ini file. Note that this funciton is only written to 
    be able to parse the telemetry config.ini file and not all files.
    """
    config = {}

    with open(filename, "r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()

            if not line or line.startswith(";") or line.startswith("#"):
                continue

            if "=" in line:
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip()
                config[key] = value

    return config

def setWinRegistry(path: str, key_name: str, value: str, registry_root=None) -> bool:
    import winreg
    if not registry_root:
        registry_root = winreg.HKEY_CURRENT_USER
    """
    Sets a string value in the Windows registry.

    Args:
        path (str): The path of the registry key.
        key_name (str): The name of the registry key.
        value (str): The value to set.
        registry_root (int, optional): The root registry key (default is HKEY_CURRENT_USER).

    Returns:
        bool: True if the value was successfully set, False otherwise.
    """
    with suppress(FileNotFoundError), winreg.OpenKey(registry_root, path, 0, winreg.KEY_WRITE) as key:
        winreg.SetValueEx(key, key_name, 0, winreg.REG_SZ, value)
        return True
    return False


# Return the contents of a file in one of the add-on's folders which 
# can be overridden per user 
def readUserFile(filename, dirName):
    absDirPath = os.path.join(getExporterPath(), dirName)
    filepath = os.path.join(absDirPath, filename)

    filepathUser = os.path.join(getUserConfigDir(), dirName, "%s.user" % filename)

    if os.path.exists(filepathUser):
        filepath = filepathUser

    if not os.path.exists(filepath):
        return ""

    return open(filepath, 'r').read()


def getVRsceneTemplate(filename,):
    return readUserFile(filename, "templates")


def readOverrides(filename):
    return readUserFile(filename, "overrides")


def getPreviewBlend():
    userPreview = os.path.join(getUserConfigDir(), "preview.blend")
    if os.path.exists(userPreview):
        return userPreview
    return os.path.join(getExporterPath(), "preview", "preview.blend")


def isGPUEngine(scene: bpy.types.Scene):
    return scene.vray.Exporter.device_type in {'GPU'}


def getZmqServerFolder():
    """ Return the folder that contains the VRayZmqServer executable """
    # By default, ZmqServer is installed in the bin/ folder of the add-on. This location
    # can be changed from the command line.
    if not (zmqServerFolder := StartupConfig.zmqServerFolder):
        zmqServerFolder =  os.path.dirname(os.path.realpath(__file__)).replace("lib","bin/VRayZmqServer/")

    return zmqServerFolder


def getZmqServerPath():
    """ Return the full path to the VRayZmqServer executable """
    executableName = "VRayZmqServer.exe" if sys.platform == 'win32' else "VRayZmqServer"
    return os.path.join(getZmqServerFolder(), executableName)


def getAppSdkPath():
    """ Return the full path to the appsdk/bin folder """
    return os.path.join(getZmqServerFolder(), "appsdk", "bin")

def getAppSdkLibPath():
    if sys.platform == 'win32':
        libFileNameName = "VRaySDKLibrary.dll"
    elif sys.platform == 'darwin':
        libFileNameName = "libVRaySDKLibrary.dylib"
    else:
        libFileNameName = "libVRaySDKLibrary.so"
    return os.path.join(getAppSdkPath(), libFileNameName)


def _getResourcesPath():
    """ Returns the full path to the resources folder """
    return os.path.dirname(os.path.realpath(__file__)).replace("lib", "resources")

def getVfbSettingsPath():
    return os.path.join(_getResourcesPath(), "vfbSettings.json")

def getVfbDefaultSettingsPath():
    """ Returns the full path (no filename) to the VFB settings default JSON """
    return _getResourcesPath()

def getDefaultTexturePath():
    return os.path.join(_getResourcesPath(), "defaultTexture.png")

def copyToClipboard(text):
    if 'WINDIR' in os.environ:
        # Blender clears the system environments variables
        # which loses the path to clip.exe (needed for copying to clipboard)
        # TODO: handle all supported operating systems
        sys32Path = f"{os.environ['WINDIR']}\\System32\\"
        os.system(f"echo {text}|{sys32Path}\\clip.exe")
    else:
        debug.printWarning("Unable to locate the Windows directory needed for clip.exe," \
                           " which is necessary to copy text to the clipboard.")

def importFunction(functionPath: str, pluginModule = ''):
    """ Import function from a module.

        @param functionPath:    path to the function in format [folder1.folder2...folderN.]functionName
                                If only the name is given, the function is loaded from the pluginModule
        @param pluginModule:    the plugin module to load the function from. Leave empty if full path
                                to the function is specified. 
    """
    assert ('.' in functionPath) or (pluginModule != ''), f"Plugin module not specified for function {functionPath}"

    funcModule = pluginModule
    funcName = functionPath

    if '.' in functionPath:
        # Path relative to vray_blender. Format is folder1....folderN.funcName
        funcModule = importModule('.'.join(functionPath.split('.')[:-1]))
        funcName   = functionPath.split('.')[-1]

    return getattr(funcModule, funcName, None)


def importModule(modulePath: str):
    """ Import a Python module given the path relative to vray_blender """
    import importlib
    try:
        return importlib.import_module(f"vray_blender.{modulePath}")
    except ImportError as e:
        debug.reportError(f"Failed to import module {modulePath}.", exc=e)
        return None


class StartupConfig:
    """ General configuration settings passed on the command line.

        Currently, all command-line args are  intended for usage during development.
        This is why no help is provided to the user and no rigorous checks are made.
    """
    debugUI = False         # If True, append (*) to the names of all V-Ray UI panels
    zmqServerFolder = None  # Override the default ZmqServer location
    zmqServerLog = None     # Override the location of ZmqServer's dumpInfo log
    logLevel = None         # Override the startup log level. It will be in effect
                            #   until a scene is loaded, when the log level will be
                            #   set to the value saved in the scene.

    @staticmethod
    def init():
        if '--vray-debug-ui' in sys.argv:
            # Enables V-Ray specific UI with misc debug info and dev-time properties
            StartupConfig.debugUI = True

        StartupConfig.zmqServerFolder = StartupConfig._getParamValue('--vray-server-folder')
        StartupConfig.zmqServerLog = StartupConfig._getParamValue('--dumpInfoLog')
        StartupConfig.logLevel = StartupConfig._getParamValue('--vray-log-level') or '3' # Info

    @staticmethod
    def _getParamValue(paramName):

        if paramName in sys.argv:
            # Override for the VRayZmqServer.exe installation folder. Used for dev purposes.
            vrayLibArgPos = sys.argv.index(paramName)
            if (vrayLibArgPos + 1) < len(sys.argv):
                return sys.argv[vrayLibArgPos + 1]
            else:
                debug.printError(f"No value specified for the {paramName} command-line argument.")
                return None
