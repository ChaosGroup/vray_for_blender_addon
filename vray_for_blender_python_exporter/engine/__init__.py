import bpy

from vray_blender.engine.zmq_process import ZMQ

from vray_blender.bin import VRayBlenderLib as vray


NODE_TRACKERS = (
    'MTL',     
    'LIGHT',   
    'WORLD',   
    'CHANNELS',
    'OBJ'
)

OBJ_TRACKERS = (
    'OBJ',
    'OBJ_MTL',      # For material options (e.g. MtlRenderStats) attached to objects
    'LIGHT',
    'CAMERA',
    'INSTANCER',
    'GIZMO'
)



def shutdown():
    ZMQ.stop()


def _engineExit():
    vray.exit()


def ensureRunning():
    from vray_blender.utils.cosmos_handler import registerAssetImportTimerFunction
    from vray_blender.plugins.BRDF.BRDFScanned import registerScannedImportTimerFunction
    from vray_blender.engine.vfb_event_handler import VfbEventHandler

    ZMQ.ensureRunning()
    VfbEventHandler.ensureRunning(reset=True)

    # The Asset Import and Set Viewport Mode timers have to be registered
    # again every time a new scene is loaded
    registerAssetImportTimerFunction()
    registerScannedImportTimerFunction()


def _getRegClasses():
    from vray_blender.engine.render_engine import VRayRenderEngine
    from vray_blender.engine.draw_scheduler import VRay_OT_draw_viewport_timer

    return (
        VRayRenderEngine,
        VRay_OT_draw_viewport_timer,
    )

def resetActiveIprRendering():
    from vray_blender.engine.renderer_ipr_viewport import VRayRendererIprViewport
    from vray_blender.engine.renderer_ipr_vfb import VRayRendererIprVfb

    for iprRenderer in (VRayRendererIprViewport, VRayRendererIprVfb):
        iprRenderer.reset()

def resetViewportIprRendering():
    from vray_blender.engine.renderer_ipr_viewport import VRayRendererIprViewport

    VRayRendererIprViewport.reset()

def register():
    import atexit
    atexit.unregister(_engineExit)
    atexit.register(_engineExit)

    for regClass in _getRegClasses():
        bpy.utils.register_class(regClass)


def unregister():
    for regClass in reversed(_getRegClasses()):
        bpy.utils.unregister_class(regClass)


