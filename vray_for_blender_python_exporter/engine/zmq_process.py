
import bpy
import os
import sys
import time

from vray_blender import debug
from vray_blender.external import psutil
from vray_blender.lib import sys_utils
from vray_blender.bin import VRayBlenderLib as vray
from vray_blender import bl_info


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

    def _start(self):
        from vray_blender.nodes.operators. import_file import assetImportCallback
        from vray_blender.engine.render_engine import VRayRenderEngine
        from vray_blender.engine.vfb_event_handler import VfbEventHandler

        self._startServerProcess()

        # Cosmos Browser download notifications callback
        self.assetImportCallback = lambda type, matPath, objPath, lightPath, locationMap: assetImportCallback(type, matPath, objPath, lightPath, locationMap)
        vray.setCosmosImportCallback(self.assetImportCallback)

         # VFB start button callback
        self.renderStartCallback = lambda isViewport: VfbEventHandler.startInteractiveRender() if isViewport else VfbEventHandler.startProdRender()
        vray.setRenderStartCallback(self.renderStartCallback)
        
        # Abort-all-renders callback (e.g. if ZmqServer crashes)
        self.renderAbortCallback = lambda: VRayRenderEngine.resetAll()
        vray.setRenderAbortCallback(self.renderAbortCallback)

        self._updateVFBSettings = lambda vfbSettings: VfbEventHandler.updateVfbSettings(vfbSettings)
        vray.setVfbSettingsUpdateCallback( self._updateVFBSettings)
        
        self._updateVFBLayers = lambda vfbLayers: VfbEventHandler.updateVfbLayers(vfbLayers)
        vray.setVfbLayersUpdateCallback( self._updateVFBLayers )


    def stop(self):
        """ Stop VRayZmqServer process """
        if ZMQProcess._started:
            debug.printDebug('Stopping VRayZmqServer control connection. VRayZmqServer may not stop ' +
                                'immediately if there are pending production jobs.' )
            vray.ZmqControlConn.stop()
            # Do not allow restart after stop() has been called
    

    @staticmethod
    def isRunning():
        return vray.ZmqControlConn.check()
    

    def _startServerProcess(self):
        assert not ZMQProcess._started

        executablePath = sys_utils.getZmqServerPath()

        if not executablePath or not os.path.exists(executablePath):
            debug.printError(f"Can't find V-Ray ZMQ Server at path {executablePath}")
            return

        def _joinPath(*args):
            return os.path.normpath(os.path.join(*args))

        appSdkPath = sys_utils.getAppSdkPath()

        if sys.platform == "win32":
            vrayLibPath = _joinPath(appSdkPath, "VRaySDKLibrary.dll")
        else:
            raise NotImplementedError()
        
        if hasattr(bpy.context, 'scene'):
            settings = bpy.context.scene.vray.Exporter
        else:
            from types import SimpleNamespace

            # During the add-on's registration, we are running in a restricted context
            # where no scene is available
            
            settings = SimpleNamespace()
            setattr(settings, 'zmq_port', -1)               # Efemeral port   
            setattr(settings, 'verbose_level', sys_utils.StartupConfig.logLevel)    
            setattr(settings, 'zmq_renderer_threads', -1 )  # Hardware concurrency - 1

        args = vray.ZmqServerArgs()

        # Use default settings if vfbSettings.json isn't created yet 
        vfbSettingsPath = sys_utils.getVfbSettingsPath()
        if not os.path.isfile(vfbSettingsPath):
            vfbSettingsPath = sys_utils.getVfbDefaultSettingsPath()

        args.exePath        = executablePath
        args.port           = settings.zmq_port
        args.logLevel       = int(settings.verbose_level)
        args.headlessMode   = bpy.app.background  # Do not try to show VFB and agreements dialog in headless mode
        args.noHeartbeat    = True
        args.blenderPID     = os.getpid()
        args.vfbSettingsFile= vfbSettingsPath
        args.dumpLogFile    = self._getDumpInfoLogFile()
        args.renderThreads  = settings.zmq_renderer_threads
        args.vrayLibPath    = vrayLibPath
        args.appSDKPath     = appSdkPath
        args.pluginVersion  = "".join(bl_info['version'])

        debug.printInfo("Starting ZmqServer process ...")
        
        # When not using an ephemeral port for the ZmqServer listener, we have to make sure that there is 
        # no other VRayZmqServer process running which would have the port open.
        useEphemeralPort = (args.port == -1)

        if (not useEphemeralPort) and (not ZMQProcess._waitForProcessToExit(os.path.basename(executablePath), 5)):
            debug.printError("Cannot run ZmqServer: another instance is running. Rendering in V-Ray will not be available.")
            return
        
        vray.ZmqControlConn.start(args)


    @staticmethod
    def _waitForProcessToExit(processName, seconds):
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


ZMQ = ZMQProcess()
