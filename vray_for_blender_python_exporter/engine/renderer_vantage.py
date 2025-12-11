import blf, bpy, gpu, gpu_extras, json, os, requests, subprocess, sys, time

import gpu_extras.presets

from enum import Enum

import vray_blender.bin.VRayBlenderLib as vray
from vray_blender import debug
from vray_blender.engine.render_engine import VRayRenderEngine
from vray_blender.engine.renderer_ipr_vfb  import VRayRendererIprVfb
from vray_blender.engine.vfb_event_handler import VfbEventHandler
from vray_blender.lib.defs import ExporterType, RendererMode
from vray_blender.lib.blender_utils import getVRayPreferences
from vray_blender.lib.path_utils import getIconsDir

COMMAND_SERVER_PORT = 20702

_vantageLogo = None

def _getVantageIcon(iconName: str) -> bpy.types.Image:
    """
    Loads a custom icon from the addon's 'icons' folder.
    Caches the image to avoid re-loading.
    """
    iconPath = os.path.join(getIconsDir(), iconName)
    global _vantageLogo
    if _vantageLogo is not None:
        return _vantageLogo

    if not os.path.exists(iconPath):
        debug.printError(f"Error loading vantage icon: Icon not found at {iconPath}")
        return None

    try:
        img = bpy.data.images.load(iconPath, check_existing=True)
        _vantageLogo = gpu.texture.from_image(img)
        return _vantageLogo
    except Exception as e:
        debug.printExceptionInfo(e, "vatage_render._getVantageIcon")
        return None

class VantageStatusData:
    def __init__(self):
        self.state = ""
        self.liveLinkState = ""
        self.offlineTaskState = ""
        self.offlineStartFrame = None
        self.offlineCurrentFrame = None
        self.offlineEndFrame = None
        self.lastProcessedTaskID = None
        self.appVersion = ""

class VantageInitStatus(Enum):
    Success = "Success"
    VantageNotInstalled = "Vantage is not installed on this system"
    VantageFailedToStart = "The Vantage process failed to start"
    VantageServerBusy = "The Vantage server is busy"
    VantageIsNotRenderingInteractive = "Vantage is not rendering in Interactive"
    VantageConnectionFailed = "Vantage connection failed"
    VantageUnsupportedVersion = "Unsupported Vantage version"

def _getFileVersion(path: str):
    import ctypes
    from ctypes import wintypes

    if not path:
        return None

    GetFileVersionInfoSizeW = ctypes.windll.version.GetFileVersionInfoSizeW
    GetFileVersionInfoW = ctypes.windll.version.GetFileVersionInfoW
    VerQueryValueW = ctypes.windll.version.VerQueryValueW
    GetLastError = ctypes.windll.kernel32.GetLastError

    size = GetFileVersionInfoSizeW(path, None)
    if size == 0:
        return None

    buf = ctypes.create_string_buffer(size)

    if not GetFileVersionInfoW(path, 0, size, buf):
        return int(GetLastError()), None, None, None, None

    lplpBuffer = ctypes.c_void_p()
    puLen = wintypes.UINT()
    if not VerQueryValueW(buf, "\\", ctypes.byref(lplpBuffer), ctypes.byref(puLen)):
        return None

    class VS_FIXEDFILEINFO(ctypes.Structure):
        _fields_ = [
            ("dwSignature", wintypes.DWORD),
            ("dwStrucVersion", wintypes.DWORD),
            ("dwFileVersionMS", wintypes.DWORD),
            ("dwFileVersionLS", wintypes.DWORD),
            ("dwProductVersionMS", wintypes.DWORD),
            ("dwProductVersionLS", wintypes.DWORD),
            ("dwFileFlagsMask", wintypes.DWORD),
            ("dwFileFlags", wintypes.DWORD),
            ("dwFileOS", wintypes.DWORD),
            ("dwFileType", wintypes.DWORD),
            ("dwFileSubtype", wintypes.DWORD),
            ("dwFileDateMS", wintypes.DWORD),
            ("dwFileDateLS", wintypes.DWORD),
        ]

    verInfo = ctypes.cast(lplpBuffer, ctypes.POINTER(VS_FIXEDFILEINFO)).contents

    return verInfo.dwProductVersionMS

def _getRequestUrl(vantageAddress: str, urlPath: str) -> str:
    return f"http://{vantageAddress}:" + str(COMMAND_SERVER_PORT) + urlPath

def _sendPing(vantageAddress: str):
    url = _getRequestUrl(vantageAddress, "/ping")
    try:
        response = requests.get(url, timeout=2)
    except requests.RequestException:
        return None
    if response.status_code != 200:
        debug.printError(f"Server return error code: {response.status_code}")
        return None
    return response.text

def _getVantageStatus(vantageAddress: str):
    commandData = {"command": "getStatus"}
    try:
        url = _getRequestUrl(vantageAddress, "/command")
        response = requests.post(url, json=commandData, timeout=5)
        response.raise_for_status()
        vantageStatusString = response.text
    except requests.RequestException:
        return None

    # Debug output if needed
    # if is_debug_enabled():
    #    global last_status_string
    #    if vantage_status_string != last_status_string:
    #        print("DEBUG:", vantage_status_string)
    #        last_status_string = vantage_status_string

    try:
        resultJson = json.loads(vantageStatusString)
    except json.JSONDecodeError:
        return "Failed parsing JSON response from Vantage command result"

    for key in ["state", "liveLinkState", "offlineTaskState"]:
        if key not in resultJson or not isinstance(resultJson[key], str):
            return f"Missing '{key}' string property in getStatus response"

    status = VantageStatusData()
    status.state = resultJson["state"]
    status.liveLinkState = resultJson["liveLinkState"]
    status.offlineTaskState = resultJson["offlineTaskState"]
    status.offlineStartFrame = resultJson.get("offlineStartFrame")
    status.offlineCurrentFrame = resultJson.get("offlineCurrentFrame")
    status.offlineEndFrame = resultJson.get("offlineEndFrame")
    status.lastProcessedTaskID = resultJson.get("lastProcessedTaskID")
    status.appVersion = resultJson.get("appVersion", "")

    return status

_cachedVantagePath = None

def getVantageExecutableFile():
    global _cachedVantagePath
    if _cachedVantagePath:
        return _cachedVantagePath

    import winreg
    try:
        uninstall_key = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\Chaos Vantage"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, uninstall_key) as key:
            # There is no dedicated entry for the Vantage executable file, so the only current option is to
            # construct the root installation path from the stored uninstall icon path entry.
            displayIcon, _ = winreg.QueryValueEx(key, "DisplayIcon")
    except:
        return None

    # Remove surrounding quotes if present
    displayIcon = displayIcon.strip('"')

    # Go two levels up: uninstaller file -> uninstall dir -> installation dir
    vantageUninstallDir = os.path.dirname(displayIcon)
    vantageInstallDir = os.path.dirname(vantageUninstallDir)

    if not vantageInstallDir or not os.path.isdir(vantageInstallDir):
        return None

    resultPath = os.path.join(vantageInstallDir, "vantage.exe")

    _cachedVantagePath = resultPath
    return resultPath

class VRayRendererVantageLiveLink(VRayRendererIprVfb):
    @staticmethod
    def checkAndReportVantageState(context):
        preferences = getVRayPreferences(context)
        vantageHost = preferences.vantage_host
        tryStartVantage = sys.platform == "win32" and (vantageHost.startswith("127.") or vantageHost == "::1" or vantageHost.lower() == "localhost")
        # The Vantage command server isn't accessible from other machines so we skip the checks below if
        # it's running and directly rely on DR2 to connect and report any potenatial errors.
        if not tryStartVantage:
            return VantageInitStatus.Success

        pingResult = _sendPing(vantageHost)
        if not pingResult:
            vantageExecutable = getVantageExecutableFile()
            if not vantageExecutable or not os.path.exists(vantageExecutable):
                return VantageInitStatus.VantageNotInstalled
            try:
                commandLine = [vantageExecutable]
                if version := _getFileVersion(vantageExecutable):
                    majorVersion=version >> 16
                    minorVersion=version & 0xffff
                    if majorVersion > 2 or (majorVersion == 2 and minorVersion >= 6):
                        commandLine.append("-skipHome")

                # The ZMQ server QT env vars seem to somehow be leaking to all child processes... So for
                # now just send the current(blender) environment to the child process.
                subprocess.Popen(commandLine, env=os.environ, creationflags=subprocess.CREATE_NEW_CONSOLE, shell=True)
            except Exception as e:
                debug.printExceptionInfo(e)
                return VantageInitStatus.VantageFailedToStart

            waitInterval = 0.5
            maxWaitTime = 2 * 60
            startTime = time.time()
            vantageState = ""
            failedAttempts = 0
            maxFailedAttempts = 10
            while (time.time() - startTime < maxWaitTime and (vantageState == "Startup" or vantageState == "LoadingScene" or vantageState == "")):
                time.sleep(waitInterval)
                vantageStatus = _getVantageStatus(vantageHost)
                if not vantageStatus:
			        # The Vantage server will not start immediately after starting the executable so wait for up to 5s.
                    if failedAttempts >= maxFailedAttempts:
                        return VantageInitStatus.VantageConnectionFailed

                    failedAttempts+=1
                    continue
                vantageState = vantageStatus.state
            if not vantageState or vantageState == "Startup" or vantageState == "LoadingScene":
                return VantageInitStatus.VantageFailedToStart

            if vantageState == "RunningInteractive":
                return VantageInitStatus.Success

        vantageState = _getVantageStatus(vantageHost)
        if not vantageState:
            return VantageInitStatus.VantageFailedToStart if sys.platform == "win32" else VantageInitStatus.VantageConnectionFailed

        if vantageState.state == "RenderingInteractive" or vantageState.state == "RenderingInteractivePaused":
            if vantageState.liveLinkState == "Connected":
                return VantageInitStatus.VantageServerBusy

            # Due to recent changes in the DR2 protocols we can only support Vantage 3.0 and above.
            if vantageState.appVersion < "3.0.0":
                return VantageInitStatus.VantageUnsupportedVersion

            return VantageInitStatus.Success

        return VantageInitStatus.VantageIsNotRenderingInteractive

    def _getExporterContext(self, ctx: bpy.types.Context, rendererMode: RendererMode, dg: bpy.types.Depsgraph, isFullExport: bool):
        context = super()._getExporterContext(ctx, rendererMode, dg, isFullExport)
        context.rendererMode = RendererMode.Vantage
        return context

    def _initRenderer(self):
        self._createRenderer(ExporterType.VANTAGE_LIVE_LINK)

        def onStopped(isAborted):
            if isAborted:
               bpy.app.timers.register(lambda: debug.reportError("Connection to renderer lost. Restart Vantage Live Link."))
               VfbEventHandler.stopVantageLiveLink() 

        self.cbRenderStopped = lambda isAborted: onStopped(isAborted)
        vray.setRenderStoppedCallback(self.renderer, self.cbRenderStopped)
        
        VRayRendererIprVfb._activeRenderer = self.renderer

def drawCallbackVantage():
    if not VRayRendererVantageLiveLink.isActive() or not isinstance(VRayRenderEngine.iprRenderer, VRayRendererVantageLiveLink):
        return
    # For now use the draw callback to stop the render in case it was aborted.
    if not VRayRendererVantageLiveLink._activeRenderer or not vray.renderJobIsRunning(VRayRendererVantageLiveLink._activeRenderer):
        return

    ICON_PX_SIZE = 64

    region = bpy.context.region
    x, y = region.width - 75, 10
    vantageLogo = _getVantageIcon("Vantage.png")
    if vantageLogo:
        gpu.state.blend_set('ALPHA')
        gpu_extras.presets.draw_texture_2d(vantageLogo, (x, y), ICON_PX_SIZE, ICON_PX_SIZE)
        gpu.state.blend_set('NONE')
    else:
        blf.position(0, x, y, 0)
        blf.size(0, ICON_PX_SIZE)
        blf.draw(0, "⏳")