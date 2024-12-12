
import sys
import traceback
import time
import datetime

import bpy

from vray_blender.bin import VRayBlenderLib as vray


class LogLevel:
    Always  = 0
    Error   = 1
    Warning = 2
    Info    = 3
    Debug   = 4


# Log message
#
def printAlways(message, raw=False):
    """ Unconditionally log the message. 

    Args:
        message (str)
        raw (bool, optional): Do not prepend attributes (e.g. time/level) to the message. Defaults to False.
    """
    vray.log(message, level=LogLevel.Always, raw=raw)

def printError(message, raw=False):
    vray.log(message, level=LogLevel.Error, raw=raw)

def printWarning(message, raw=False):
    vray.log(message, level=LogLevel.Warning, raw=raw)

def printInfo(message, raw=False):
    vray.log(message, level=LogLevel.Info, raw=raw)

def printDebug(message, raw=False):
    vray.log(message, level=LogLevel.Debug, raw=raw)


def reportError(message, engine: bpy.types.RenderEngine = None, exc: Exception = None):
    """ Show the error message in Blender UI. In addition, print the message
        and the exception info to the console.
    """
    if engine:
        engine.report({'ERROR'}, message)
    else:
        report('ERROR', message)

    errMsg = f"{message} Error: {exc}" if exc else message
    printError(f"{errMsg}")
    
    if exc:
        printExceptionInfo(exc)


def setLogLevel(level: int):
    vray.setLogLevel(level)
    vray.ZmqControlConn.setLogLevel(level)


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
class VRAY_OT_report(bpy.types.Operator):
    """ Implements reporting in Blender status area regardless of the context """
    bl_idname = "vray.report"
    bl_label = "Report"

    message: bpy.props.StringProperty()
    reportType: bpy.props.StringProperty(default="ERROR")

    def execute(self, context):
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        try:
            self.report({self.reportType}, self.message)
            return {'FINISHED'}
        except:
            return {'PASS_THROUGH'}
        

def report(severity: str, msg: str):
    """ Report in Blender's status area. This function is a replacement
        for the report() method of blender classes (e.g. operators)
        that can be used in any context.

    Args:
        severity (str): One of the enum values in https://docs.blender.org/api/current/bpy_types_enum_items/wm_report_items.html#rna-enum-wm-report-items
        msg (_type_): The message to show.
    """
    bpy.ops.vray.report(reportType=severity, message=f"V-Ray: {msg}")



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
