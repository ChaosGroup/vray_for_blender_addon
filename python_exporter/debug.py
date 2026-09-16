# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later


import functools
import sys
import threading
import traceback
import time
import datetime

import bpy

from vray_blender.bin import VRayBlenderLib as vray
from vray_blender.lib.mixin import VRayOperatorBase


class LogLevel:
    Always  = 0
    Error   = 1
    Warning = 2
    Info    = 3
    Debug   = 4

class VfbMessageLevel:
    MessageError = 0
    MessageWarning = 1
    MessageInfo = 2
    MessageDebug = 3

# Blender to V-Ray log level map
_LOG_LEVEL_MAP = {
    'ERROR'   : LogLevel.Error,
    'WARNING' : LogLevel.Warning,
    'INFO'    : LogLevel.Info,
    'DEBUG'   : LogLevel.Debug,
}

# Log message
#
def printMsg(message, level=LogLevel.Always, raw=False):
    """ Log message through VRay logging channel. 

    Args:
        message (str): The message 
        level(LogLevel): Verbosity level
        raw (bool, optional): Do not prepend attributes (e.g. time/level) to the message. Defaults to False
    """
    vray.log(message, level, raw)


# Shortcuts for the different verbosity levels
def printAlways(message, raw=False):
    """ Unconditionally log the message. 

    Args:
        message (str)
        raw (bool, optional): Do not prepend attributes (e.g. time/level) to the message. Defaults to False.
    """
    msg = message if raw else f"[V-Ray] {message}"
    printMsg(msg, level=LogLevel.Always, raw=raw)

def printError(message, raw=False):
    printMsg(message, level=LogLevel.Error, raw=raw)

def printWarning(message, raw=False):
    printMsg(message, level=LogLevel.Warning, raw=raw)

def printInfo(message, raw=False):
    printMsg(message, level=LogLevel.Info, raw=raw)

def printDebug(message, raw=False):
    printMsg(message, level=LogLevel.Debug, raw=raw)


def reportError(message, engine: bpy.types.RenderEngine = None, exc: Exception = None):
    """ Show the error message in Blender UI. In addition, print the message
        and the exception info to the console.
    """
    from vray_blender.lib.lib_utils import isRestrictedContext

    # The function may be called in a restricted context (e.g. during Blender startup).
    # UI-related services are not available in this context.
    if not isRestrictedContext(bpy.context):
        if engine:
            engine.report({'ERROR'}, message)
        else:
            report('ERROR', message)
    else:
        errMsg = f"{message} Error: {exc}" if exc else message
        printError(f"{errMsg}")

    if exc:
        printExceptionInfo(exc)

def setLogLevel(level: int, qtLog: bool):
    """ Change the log verbosity level of VRayBlenderLib and ZmqServer """
    vray.setLogLevel(level, qtLog)


def printExceptionInfo(e, source: str = ""):
    """ Print exception info and stack trace.

        @param e - an exception
        @param source - arbitrary string by which the source of the exception will be identified
    """

    msg = f"Exception of type '{type(e).__name__}': {e}"
    if source:
        msg = f"{msg}   [ {source} ]"

    printError(msg)
    
    _, _, exc_traceback = sys.exc_info()
    printAlways("".join(traceback.extract_tb(exc_traceback).format()), raw=True)


def timeIt(method):
    def timed(*args, **kw):
        
        sys.stdout.write("V-Ray For Blender")
        sys.stdout.write(": %s()...\n" % method.__name__)
        sys.stdout.flush()
        
        ts = time.time()
        result = method(*args, **kw)
        te = time.time() - ts
        td = datetime.timedelta(seconds=te)
        d  = datetime.datetime(1,1,1) + td
        
        sys.stdout.write("V-Ray For Blender")
        sys.stdout.write(": %s() done [%.2i:%.2i:%.2i]\n" % (method.__name__, d.hour, d.minute, d.second))
        sys.stdout.flush()
        
        return result
    return timed


def attachDebugger():
    """ pydev debugger attaches automatically only to the main thread. For 
        other threads, call this function from code executed by the thread
        you want to debug. The debugger will only break on breakpoints hit
        AFTER the call to this function has been made.
    """ 
    try:
        import pydevd
        pydevd.settrace(suspend=False)
    except ImportError:
        pass


class ExceptionLogger:
    """ Context manager for running blocks of code for which any thrown exceptions 
        will be logged through the VRayBlenderLib logging system.
        
        Usage:

        with debug.ExceptionLogger():
            your code goes here
    """

    def __init__(self, source: str = ""):
        self._source = source

    def __enter__(self):
        return None

    def __exit__(self, excType, exc, traceback):
        if exc:
           printExceptionInfo(exc, source=self._source) 


############  Reports in Blender's UI  ############

class VRAY_OT_report(VRayOperatorBase):
    """ Implements reporting in Blender's status area regardless of the context.

        The report is driven from the operator's own TIMER event. modal() must never
        finish on a user input event: returning {'FINISHED'} consumes the triggering
        event, and if that event is the mouse-button release, the window manager keeps
        the mouse stuck in the 'down' state (hovering then activates other widgets
        without a click).
    """
    bl_idname = "vray.report"
    bl_label = "Report"

    message: bpy.props.StringProperty()
    reportType: bpy.props.StringProperty(default="ERROR")

    _timer = None

    def execute(self, context):
        if context.window:
            wm = context.window_manager
            self._timer = wm.event_timer_add(0.001, window=context.window)
            wm.modal_handler_add(self)
            return {'RUNNING_MODAL'}

        printMsg(self.message, level=_LOG_LEVEL_MAP[self.reportType])
        return {"CANCELLED"}

    def modal(self, context, event):
        if event.type != 'TIMER':
            # Let real input events flow to the UI untouched.
            return {'PASS_THROUGH'}

        if self._timer is not None:
            context.window_manager.event_timer_remove(self._timer)
            self._timer = None

        try:
            self.report({self.reportType}, self.message)
        except Exception:
            pass
        return {'FINISHED'}

    @classmethod
    def poll(cls, context):
        # The operator should be callable even if the default engine is not V-Ray.
        return True



# State for coalescing identical, rapidly-repeated reports (see report()).
_reportLock          = threading.Lock()
_lastReport          = None    # (severity, msg) of the last shown/queued report
_lastReportTime      = 0.0     # time.monotonic() of that report
_REPORT_COALESCE_SEC = 0.5     # suppress identical repeats within this window


def report(severity: str, msg: str):
    """ Show a message in Blender's status area and log it to the V-Ray console.

        This is the single public entry point for status reporting. It is safe to call
        from ANY context - the main thread, worker/native threads, property update/set
        callbacks, depsgraph/draw handlers and the restricted startup context. The UI
        display is deferred to the next main-loop tick via a one-shot timer, so bpy.ops
        is never invoked from an unsafe context. The message is also always printed to
        the console with the corresponding severity level.

    Args:
        severity (str): One of the enum values in https://docs.blender.org/api/current/bpy_types_enum_items/wm_report_items.html#rna-enum-wm-report-items
        msg (str): The message to show.
    """
    now = time.monotonic()
    with _reportLock:
        global _lastReport, _lastReportTime
        # Coalesce identical messages fired in rapid succession (e.g. from draw or
        # depsgraph handlers) so they neither flood the console nor spawn a report
        # operator per frame. Distinct messages are never dropped.
        if (severity, msg) == _lastReport and (now - _lastReportTime) < _REPORT_COALESCE_SEC:
            return
        _lastReport, _lastReportTime = (severity, msg), now

    printMsg(msg, level=_LOG_LEVEL_MAP[severity])
    bpy.app.timers.register(functools.partial(_showReport, severity, msg))


def _showReport(severity: str, msg: str):
    """ Invoke the report operator on the main thread from a one-shot timer - a context
        in which bpy.ops is valid. Returns None to unregister after a single run.
    """
    if bpy.context.window:
        bpy.ops.vray.report(reportType=severity, message=f"V-Ray: {msg}")
    return None



############  Registration  ############

def getRegClasses():
    return (
        VRAY_OT_report,
    )


def register():
    for regClass in getRegClasses():
        bpy.utils.register_class(regClass)


def unregister():
    for regClass in getRegClasses():
        bpy.utils.unregister_class(regClass)
