
import getpass
import json
import os
import socket
import sys
import shutil
import platform
import winreg
from contextlib import suppress

import bpy
from vray_blender import debug


def getUsername():
    if sys.platform == 'win32':
        return "standalone"
    else:
        return getpass.getuser()


def getHostname():
    return socket.gethostname()


def getArch():
    bitness = platform.architecture()[0]
    if bitness == '32':
        return 'x86'
    return 'x86_64'


def getVRayStandalones():
    VRayPreferences = bpy.context.preferences.addons['vray_blender'].preferences

    vrayExe   = "vray.exe" if sys.platform == 'win32' else "vray"
    splitChar = ';'        if sys.platform == 'win32' else ':'

    vrayPaths = {}

    def getPaths(pathStr):
        if pathStr:
            return pathStr.strip().replace('\"','').split(splitChar)
        return []

    for var in reversed(sorted(os.environ.keys())):
        envVar = os.getenv(var)
        if not envVar:
            continue

        if var.startswith('VRAY_PATH') or var == 'PATH':
            for path in getPaths(envVar):
                vrayExePath = os.path.join(path, vrayExe)
                if os.path.exists(vrayExePath):
                    vrayPaths[var] = vrayExePath

        elif '_MAIN_' in var:
            if var.startswith('VRAY_FOR_MAYA'):
                for path in getPaths(envVar):
                    vrayExePath = os.path.join(path, "bin", vrayExe)
                    if os.path.exists(vrayExePath):
                        vrayPaths[var] = vrayExePath

            elif var.startswith('VRAY30_RT_FOR_3DSMAX'):
                for path in getPaths(envVar):
                    vrayExePath = os.path.join(path, vrayExe)
                    if os.path.exists(vrayExePath):
                        vrayPaths[var] = vrayExePath

    if sys.platform in {'darwin'}:
        import glob

        instLogFilepath = "/var/log/chaos_installs"
        if os.path.exists(instLogFilepath):
            instLog = open(instLogFilepath, 'r').readlines()
            for l in instLog:
                if 'V-Ray Standalone' in l and '[UN]' not in l:
                    installName, path = l.strip().split('=')

                    path = os.path.normpath(os.path.join(path.strip(), '..', '..', '..', "bin"))

                    possiblePaths = glob.glob('%s/*/*/vray' % path)
                    if len(possiblePaths):
                        vrayPaths[installName] = possiblePaths[0]

    return vrayPaths


def getVRayStandalonePath():
    VRayPreferences = bpy.context.preferences.addons['vray_blender'].preferences

    vray_bin = "vray"
    if sys.platform == 'win32':
        vray_bin += ".exe"

    def get_env_paths(var):
        split_char= ';' if sys.platform == 'win32' else ":"
        env_var= os.getenv(var)
        if env_var:
            return env_var.replace('\"','').split(split_char)
        return []

    def find_vray_std_osx_official():
        vrayPath = "/Applications/ChaosGroup/V-Ray/Standalone_for_snow_leopard_x86/bin/snow_leopard_x86/gcc-4.2/vray"
        if os.path.exists(vrayPath):
            return vrayPath
        return None

    def find_vray_std_osx():
        import glob
        instLogFilepath = "/var/log/chaos_installs"
        if not os.path.exists(instLogFilepath):
            return None
        instLog = open(instLogFilepath, 'r').readlines()
        for l in instLog:
            # Example path:
            #  /Applications/ChaosGroup/V-Ray/Standalone_for_snow_leopard_x86/uninstall/linuxinstaller.app/Contents
            #
            if 'V-Ray Standalone' in l and '[UN]' not in l:
                _tmp_, path = l.strip().split('=')

                # Going up to /Applications/ChaosGroup/V-Ray/Standalone_for_snow_leopard_x86/bin
                path = os.path.normpath(os.path.join(path.strip(), '..', '..', '..', "bin"))

                possiblePaths = glob.glob('%s/*/*/vray' % path)
                if len(possiblePaths):
                    return possiblePaths[0]
                return None
        return None

    def find_vray_binary(paths):
        if paths:
            for p in paths:
                if p:
                    vray_path= os.path.join(p,vray_bin)
                    if os.path.exists(vray_path):
                        return vray_path
        return None

    if not VRayPreferences.detect_vray and VRayPreferences.vray_binary:
        manualVRayPath = bpy.path.abspath(VRayPreferences.vray_binary)
        if os.path.exists(manualVRayPath):
            return manualVRayPath

    # Check 'VRAY_PATH' environment variable
    #
    vray_standalone_paths= get_env_paths('VRAY_PATH')
    if vray_standalone_paths:
        vray_standalone= find_vray_binary(vray_standalone_paths)
        if vray_standalone:
            return vray_standalone

    # On OS X check default path and install log
    #
    if sys.platform in {'darwin'}:
        path = find_vray_std_osx_official()
        if path is not None:
            return path
        path = find_vray_std_osx()
        if path is not None:
            return path

    # Try to find Standalone in V-Ray For Maya
    #
    for var in reversed(sorted(os.environ.keys())):
        if var.startswith('VRAY_FOR_MAYA'):
            if var.find('MAIN') != -1:
                debug.printInfo("Searching in: %s" % var)
                vray_maya = find_vray_binary([os.path.join(path, 'bin') for path in get_env_paths(var)])
                if vray_maya:
                    debug.printInfo("V-Ray found in: %s" % vray_maya)
                    return vray_maya

    # Try to find vray binary in %PATH%
    debug.printError("V-Ray not found! Trying to start \"%s\" command from $PATH..." % vray_bin)

    return shutil.which(vray_bin)


def getCyclesShaderPath():
    for path in bpy.utils.script_paths(subdir=os.path.join('addons_core','cycles','shader')):
        if path:
            return path
    return None


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


def getWinRegistry(path: str, key_name: str, registry_root=winreg.HKEY_CURRENT_USER) -> str:
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

def setWinRegistry(path: str, key_name: str, value: str, registry_root=winreg.HKEY_CURRENT_USER) -> bool:
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


def isGPUEngine(scene):
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


def _getResourcesPath():
    """ Returns the full path to the resources folder """
    return os.path.dirname(os.path.realpath(__file__)).replace("lib", "resources")

def getVfbSettingsPath():
    return os.path.join(_getResourcesPath(), "vfbSettings.json")

def getVfbDefaultLayersPath():
    return os.path.join(_getResourcesPath(), "VfbDefaultLayers.json")

def getVfbDefaultSettingsPath():
    return os.path.join(_getResourcesPath(), "vfbDefaultSettings.json")

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
