# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later


import bpy
import os
import sys
import time

from vray_blender import debug
from vray_blender.lib import blender_utils, sys_utils
from vray_blender.bin import VRayBlenderLib as vray
from vray_blender import bl_info

# Update the compute devices in the UI in the main thread.
def _updateComputeDevicesCallback(deviceType: int, deviceNames: list[str], defaultDeviceStates: list[bool]):
    def _updateComputeDevices():
        computeDevices = blender_utils.getVRayPreferences().compute_devices
        computeDevices.updateComputeDeviceSelectors(deviceType, deviceNames, defaultDeviceStates)
    bpy.app.timers.register(_updateComputeDevices)

class ZMQProcess:
    """ The ZMQ server process is started on the local machine and uses the Blender
        console for its output.
    """

    _started = False

    def ensureRunning(self):
        """ Start VRayZmqServer process if not already running and establish
            the control connection to it.
        """
        # Reset
        if not ZMQProcess._started:
            self._start()
            ZMQProcess._started = True

    def _getDumpInfoLogFile(self):
        from vray_blender.lib.path_utils import getV4BTempDir

        if zmqLog := sys_utils.StartupConfig.zmqServerLog:
            return zmqLog

        if sys_utils.StartupConfig.debugUI:
            # The {pid} pattern will be replaced by the process id of ZmqServer, so that each instance
            # had its own log file.
            return os.path.join(getV4BTempDir(), "dumpInfoLog_{pid}.txt")

        return ""

    @staticmethod
    def _zmqServerAbortCallback(msg):
        from vray_blender.engine.render_engine import VRayRenderEngine
        VRayRenderEngine.resetAll()

        from vray_blender.utils.cosmos_handler import cosmosHandler
        cosmosHandler.abortDownload()

        # Check if ZMQ server has crashed or exited normally
        if msg in ("General error", "STD exception", "V-Ray exception", "Unknown error"):
            debug.reportAsync('WARNING', "Restart is required! V-Ray internal error occurred.")

    def _start(self):
        from vray_blender.utils.cosmos_handler import cosmosHandler, CosmosDownloadStatus, CosmosRelinkStatus, assetImportCallback
        from vray_blender.engine.vfb_event_handler import VfbEventHandler
        from vray_blender.plugins.BRDF.BRDFScanned import scannedLicenseCallback, scannedParamBlockCallback

        self._startServerProcess()

        # Cosmos Browser download notifications callback
        self.assetImportCallback = lambda assetSettings: assetImportCallback(assetSettings)
        vray.setCosmosImportCallback(self.assetImportCallback)
        self.cosmosDownloadSize = lambda relinkStatus, downloadSize: cosmosHandler._setCosmosRelinkState(CosmosRelinkStatus(relinkStatus), downloadSize)
        vray.setCosmosDownloadSize(self.cosmosDownloadSize)
        self.downloadedCosmosAssets = lambda downloadStatus, assets: cosmosHandler._setCosmosDownloadedAssets(CosmosDownloadStatus(downloadStatus), assets)
        vray.setCosmosDownloadAssets(self.downloadedCosmosAssets)
        self.scannedLicenseCallback = lambda licensed: scannedLicenseCallback(licensed)
        vray.setScannedLicenseCallback(self.scannedLicenseCallback)
        self.scannedParamBlockCallback = lambda materialId, nodeName, paramBlock: scannedParamBlockCallback(materialId, nodeName, paramBlock)
        vray.setScannedParamBlockCallback(self.scannedParamBlockCallback)

         # VFB start button callback
        self.renderStartCallback = lambda isViewport: VfbEventHandler.startInteractiveRender() if isViewport else VfbEventHandler.startProdRender(forceAnimation=False)
        vray.setRenderStartCallback(self.renderStartCallback)

        # Abort-all-renders callback (e.g. if ZmqServer crashes)
        vray.setZmqServerAbortCallback(ZMQProcess._zmqServerAbortCallback)

        self._updateVFBSettings = lambda vfbSettings: VfbEventHandler.updateVfbSettings(vfbSettings)
        vray.setVfbSettingsUpdateCallback( self._updateVFBSettings)

        self._updateVFBLayers = lambda vfbLayers: VfbEventHandler.updateVfbLayers(vfbLayers)
        vray.setVfbLayersUpdateCallback( self._updateVFBLayers )

        self._updateComputeDevices = lambda deviceType, deviceNames, defaultDeviceStates: _updateComputeDevicesCallback(deviceType, deviceNames, defaultDeviceStates)
        vray.setUpdateComputeDevicesCallback(self._updateComputeDevices)


    def stop(self):
        """ Stop VRayZmqServer process """
        if ZMQProcess._started:
            debug.printDebug('Stopping VRayZmqServer control connection. VRayZmqServer may not stop ' +
                                'immediately if there are pending production jobs.' )
            vray.stop()
            ZMQProcess._started = False


    @staticmethod
    def isRunning():
        return vray.isRunning()


    def _startServerProcess(self):
        assert not ZMQProcess._started

        executablePath = sys_utils.getZmqServerPath()

        if not executablePath or not os.path.exists(executablePath):
            debug.printError(f"Can't find V-Ray ZMQ Server at path {executablePath}")
            return

        prefs = blender_utils.getVRayPreferences()
        if hasattr(bpy.context, 'scene'):
            settings = bpy.context.scene.vray.Exporter
        else:
            from types import SimpleNamespace

            # During the add-on's registration, we are running in a restricted context
            # where no scene is available

            settings = SimpleNamespace()
            setattr(settings, 'zmq_port', -1)                     # Efemeral port

        args = vray.ZmqServerArgs()

        # Use default settings if vfbSettings.json isn't created yet
        vfbSettingsPath = sys_utils.getVfbSettingsPath()
        if not os.path.isfile(vfbSettingsPath):
            vfbSettingsPath = sys_utils.getVfbDefaultSettingsPath()

        args.exePath             = executablePath
        args.port                = settings.zmq_port
        args.logLevel            = int(prefs.verbose_level)
        args.enableQtLogs        = prefs.enable_qt_logs
        args.headlessMode        = bpy.app.background  # Do not try to show VFB and agreements dialog in headless mode
        args.noHeartbeat         = True
        args.blenderPID          = os.getpid()
        args.vfbSettingsFile     = vfbSettingsPath
        args.dumpLogFile         = self._getDumpInfoLogFile()
        args.vrayLibPath         = sys_utils.getAppSdkLibPath()
        args.appSDKPath          = sys_utils.getAppSdkPath()
        args.pluginVersion       = "".join(bl_info['version'])
        args.blenderVersion      = f'{bpy.app.version[0]}.{bpy.app.version[1]},{bpy.app.version[2]}'

        debug.printInfo("Starting ZmqServer process ...")

        # When not using an ephemeral port for the ZmqServer listener, we have to make sure that there is
        # no other VRayZmqServer process running which would have the port open.
        useEphemeralPort = (args.port == -1)

        if (not useEphemeralPort):
            if sys.platform != "win32":
                debug.printError("Running ZmqServer on a fixed port is only supported on Windows")
                return

            if (not ZMQProcess._waitForProcessToExit(os.path.basename(executablePath), 5)):
                debug.printError("Cannot run ZmqServer: another instance is running. Rendering in V-Ray will not be available.")
                return

        vray.start(args)

        if bpy.app.background:
            # In headless mode, we need to wait for the ZmqServer to start before attempting any rendering
            if not ZMQProcess._waitForZmqServerToStart():
                debug.printAlways('ZmqServer failed to start. Terminating application.')
                sys.exit(-1)


    @staticmethod
    def _waitForProcessToExit(processName, seconds):
        from vray_blender.external import psutil
        # Wait for existing VRayZmqServer process to exit. This is done here and
        # not in VRayBlenderLib because python has a convenient multiplatform interface
        # for listsing the running processes, which lacks in the C++ libraries that
        # VRayBlenderLib is using.
        sleepInterval = 0.2 # seconds
        waitIntervals = int(float(seconds) / sleepInterval)

        for _ in range(waitIntervals):
            try:
                if processName not in (p.name() for p in psutil.process_iter()):
                    return True
            except psutil.NoSuchProcess:
                return True

            debug.printInfo("Waiting for a previous instance of ZmqServer to stop ...")
            time.sleep(sleepInterval)

        return False


    @staticmethod
    def _waitForZmqServerToStart():
        from datetime import datetime
        zmqServerChecks = 60
        startTime = datetime.now()

        while not ZMQProcess.isRunning():
            if zmqServerChecks == 0:
                elapsedSeconds = (datetime.now() - startTime).total_seconds()
                debug.printError(f"ZMQ Server failed to start in {elapsedSeconds} seconds")
                return False
            zmqServerChecks -= 1
            time.sleep(0.5)

        return True

ZMQ = ZMQProcess()
