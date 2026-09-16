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
from vray_blender.utils.update_checker import onUpdateSettingsChanged, checkForUpdates
from vray_blender import bl_info

# Update the compute devices in the UI in the main thread.
def _updateComputeDevicesCallback(deviceType: int, deviceNames: list[str], defaultDeviceStates: list[bool]):
    def _updateComputeDevices():
        computeDevices = blender_utils.getVRayPreferences().compute_devices
        computeDevices.updateComputeDeviceSelectors(deviceType, deviceNames, defaultDeviceStates)
    bpy.app.timers.register(_updateComputeDevices)


def _onSwitchLicenseToCommunity():
    """ Called from the ZmqServer (on a worker thread) when the user clicks
        "Switch to Community Edition" in the no-license dialog. Defer the
        actual switch onto the main thread because Blender operators and
        preference writes must run there.
    """
    def _runOperator():
        # 'EXEC_DEFAULT' bypasses the operator's invoke() (and its in-render
        # confirmation popup). The operator flips the community_edition
        # preference and restarts the ZmqServer with the new -license arg.
        bpy.ops.vray.switch_license_type('EXEC_DEFAULT')
        return None  # one-shot timer
    bpy.app.timers.register(_runOperator)


class ZMQProcess:
    """ The ZMQ server process is started on the local machine and uses the Blender
        console for its output.
    """

    _started = False
    _engineCallbacksAttached = False

    def ensureRunning(self):
        """ Start VRayZmqServer process if not already running and establish
            the control connection to it.

            NOTE: This is the single entry point for starting the server. It is also used
            by the Chaos Scatter addon, which may run while the vray_blender addon itself
            is disabled, so nothing on this path may require vray_blender to be registered
            (preferences, scene propgroups). Engine-specific callbacks are attached
            separately via attachEngineCallbacks().
        """
        # Reset
        if not ZMQProcess._started:
            self._start()


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

        # Check if ZMQ server has crashed or exited normally. "Client communication error"
        # means the failure was on our side of the connection, not the server process.
        if msg in ("General error", "STD exception", "V-Ray exception", "Unknown error",
                   "Client communication error"):
            debug.report('WARNING', "Restart is required! V-Ray internal error occurred.")

    def _start(self):
        if not self._startServerProcess():
            return

        ZMQProcess._started = True


    def attachEngineCallbacks(self):
        """ Register the vray_blender engine callbacks (Cosmos, VFB, import, licensing) on the
            ZmqServer connection. Called only when the vray_blender addon itself is active; the
            Chaos Scatter addon starts the server without them. ZmqServer.stop() clears the
            callback registry, so this must be re-run after every server (re)start.
        """
        from vray_blender.utils.cosmos_handler import cosmosHandler, CosmosDownloadStatus, CosmosRelinkStatus, assetImportCallback
        from vray_blender.engine.vfb_event_handler import VfbEventHandler
        from vray_blender.plugins.BRDF.BRDFScanned import scannedLicenseCallback, scannedParamBlockCallback

        if ZMQProcess._engineCallbacksAttached or not ZMQProcess._started:
            return

        ZMQProcess._engineCallbacksAttached = True

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

        # .vrscene import notifications. The callbacks only enqueue events for the
        # import operator's modal loop; bpy data is never touched on this thread.
        from vray_blender.nodes.operators.import_vrscene import vrsceneImportProgressCallback, vrsceneImportFinishedCallback
        self._vrsceneImportProgress = lambda importId, pluginsDone, pluginsTotal, stage: \
            vrsceneImportProgressCallback(importId, pluginsDone, pluginsTotal, stage)
        vray.setVrsceneImportProgressCallback(self._vrsceneImportProgress)
        self._vrsceneImportFinished = lambda importId, result: vrsceneImportFinishedCallback(importId, result)
        vray.setVrsceneImportFinishedCallback(self._vrsceneImportFinished)

         # VFB start button callback
        self.renderStartCallback = lambda isViewport: VfbEventHandler.startInteractiveRender() if isViewport \
            else VfbEventHandler.requestProdRender()

        vray.setRenderStartCallback(self.renderStartCallback)

        # Abort-all-renders callback (e.g. if ZmqServer crashes)
        vray.setZmqServerAbortCallback(ZMQProcess._zmqServerAbortCallback)

        self._updateVFBSettings = lambda vfbSettings: VfbEventHandler.updateVfbSettings(vfbSettings)
        vray.setVfbSettingsUpdateCallback(self._updateVFBSettings)

        self._updateVFBLayers = lambda vfbLayers: VfbEventHandler.updateVfbLayers(vfbLayers)
        vray.setVfbLayersUpdateCallback(self._updateVFBLayers)

        self._lightMixTransferToScene = lambda changes: VfbEventHandler.onLightMixTransferToScene(changes)
        vray.setLightMixTransferToSceneCallback(self._lightMixTransferToScene)

        self._vfbMenu = lambda mode, targetName, objectName, distance: VfbEventHandler.onVfbMenu(mode, targetName, objectName, distance)
        vray.setVfbMenuCallback(self._vfbMenu)

        self._addRenderElementToScene = lambda renderElementType: VfbEventHandler.addRenderElementToScene(renderElementType)
        vray.setAddRenderElementToSceneCallback(self._addRenderElementToScene)

        self._vfbShowMessagesWindow = lambda: VfbEventHandler.showMessagesWindow()
        vray.setVfbShowMessagesWindowCallback(self._vfbShowMessagesWindow)

        self._vfbRenderRegionChanged = lambda x, y, w, h, enabled: \
            VfbEventHandler.onVfbRenderRegionChanged(x, y, w, h, enabled)
        vray.setVfbRenderRegionChangedCallback(self._vfbRenderRegionChanged)

        self._updateComputeDevices = lambda deviceType, deviceNames, defaultDeviceStates: _updateComputeDevicesCallback(deviceType, deviceNames, defaultDeviceStates)
        vray.setUpdateComputeDevicesCallback(self._updateComputeDevices)

        self._autoUpdateChanged = lambda autoCheck: onUpdateSettingsChanged(autoCheck)
        vray.setAutoUpdateChangedCallback(self._autoUpdateChanged)

        self._appUpdateRequested = lambda: checkForUpdates(force=True, showDialog=True)
        vray.setAppUpdateRequestedCallback(self._appUpdateRequested)

        self._switchLicenseToCommunity = _onSwitchLicenseToCommunity
        vray.setSwitchLicenseToCommunityCallback(self._switchLicenseToCommunity)


    def stop(self):
        """ Stop VRayZmqServer process """
        if ZMQProcess._started:
            debug.printDebug('Stopping VRayZmqServer control connection. VRayZmqServer may not stop ' +
                                'immediately if there are pending production jobs.' )
            vray.stop()
            ZMQProcess._started = False
            ZMQProcess._engineCallbacksAttached = False


    @staticmethod
    def isRunning():
        return vray.isRunning()


    def _startServerProcess(self):
        assert not ZMQProcess._started

        executablePath = sys_utils.getZmqServerPath()

        if not executablePath or not os.path.exists(executablePath):
            debug.printError(f"Can't find V-Ray ZMQ Server at path {executablePath}")
            return False

        from types import SimpleNamespace

        try:
            prefs = blender_utils.getVRayPreferences()
        except Exception:
            # The vray_blender addon is disabled (server started by the Chaos Scatter addon)
            prefs = SimpleNamespace(verbose_level='2', enable_qt_logs=False)

        if hasattr(bpy.context, 'scene') and hasattr(bpy.context.scene, 'vray'):
            settings = bpy.context.scene.vray.Exporter
        else:
            # During the add-on's registration, we are running in a restricted context
            # where no scene is available. 'scene.vray' is missing when the vray_blender
            # addon is disabled.

            settings = SimpleNamespace()
            setattr(settings, 'zmq_port', -1)                     # Ephemeral port

        args = vray.ZmqServerArgs()

        # Use default settings if vfbSettings.json isn't created yet
        vfbSettingsPath = sys_utils.getVfbSettingsPath()
        if not os.path.isfile(vfbSettingsPath):
            vfbSettingsPath = sys_utils.getVfbDefaultSettingsPath()

        logLevelOverride = sys_utils.StartupConfig.logLevel

        args.exePath             = executablePath
        args.port                = settings.zmq_port
        args.logLevel            = int(logLevelOverride) if logLevelOverride else int(prefs.verbose_level)
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
        args.licenseType         = "community" if vray.isCommunityEdition() else "commercial"

        debug.printInfo("Starting ZmqServer process ...")

        # When not using an ephemeral port for the ZmqServer listener, we have to make sure that there is
        # no other VRayZmqServer process running which would have the port open.
        useEphemeralPort = (args.port == -1)

        if (not useEphemeralPort):
            if sys.platform != "win32":
                debug.printError("Running ZmqServer on a fixed port is only supported on Windows")
                return False

            if (not ZMQProcess._waitForProcessToExit(os.path.basename(executablePath), 5)):
                debug.printError("Cannot run ZmqServer: another instance is running. Rendering in V-Ray will not be available.")
                return False

        success, err = vray.start(args)

        if not success:
            # report() not printError(), so the user sees why V-Ray is missing instead
            # of only finding it in the log.
            debug.report('ERROR', err)
            return False

        if bpy.app.background:
            # In headless mode, we need to wait for the ZmqServer to start before attempting any rendering
            if not ZMQProcess._waitForZmqServerToStart():
                debug.printAlways('ZmqServer failed to start. Terminating application.')
                sys.exit(-1)

        return True

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
