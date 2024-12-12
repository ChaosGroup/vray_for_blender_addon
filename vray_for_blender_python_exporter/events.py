
import bpy

from vray_blender.lib import blender_utils
from vray_blender.bin import VRayBlenderLib as vray
from vray_blender.engine.render_engine import VRayRenderEngine
from vray_blender.engine.vfb_event_handler import VfbEventHandler
from vray_blender.engine.zmq_process import ZMQ
from vray_blender.lib.names import IdGenerator, syncUniqueNames
from vray_blender.nodes.color_ramp import syncColorRamps, registerColorRamps
from vray_blender.version import getBuildVersionString
from vray_blender.lib.sys_utils import getVfbDefaultLayersPath

@bpy.app.handlers.persistent
def _onSavePost(e):
    bpy.ops.vray.dr_nodes_save()


@bpy.app.handlers.persistent
def _onSavePre(e):
    scene = bpy.context.scene
    scene.vray.Exporter.vrayAddonVersion = getBuildVersionString() 
    scene.vray.SettingsVFB.vfb2_layers = VfbEventHandler.getVfbLayers()
    

@bpy.app.handlers.persistent
def _onLoadPre(e):
    # Reset any active renderers when the blender scene is switched 
    VRayRenderEngine.resetAll()
    VfbEventHandler.reset()


@bpy.app.handlers.persistent
def _onLoadPost(e):
    from vray_blender import engine
    engine.ensureRunning()

    # Reset the global unique ID generator. This will keep the generated IDs to a 
    # decent size and will also ensure that on reload, given that no changes have been
    # made to the scene, the IDs will remain the same
    IdGenerator.reset()
    
    # Set unique ids to all objects with VRay properties
    syncUniqueNames(reset=True)

    # Register all color ramp controls that need to receive update notifications
    registerColorRamps()

    isDefaultScene = not bpy.data.filepath
    if (not isDefaultScene):
        # Run upgrade for the loaded scene, if necessary
        bpy.ops.vray.upgrade_scene('INVOKE_DEFAULT')
    
    # If SettingsVFB.vfb2_layers is empty, it will be reset to the default layer configuration
    # to override any VFB layer settings from previously opened scenes.
    settingsVFB = bpy.context.scene.vray.SettingsVFB
    if not settingsVFB.vfb2_layers: 
        with open(getVfbDefaultLayersPath() , 'r') as vfbDefaultLayers:
            settingsVFB.vfb2_layers = vfbDefaultLayers.read()

    # Setting VFB Layers if new scene is being loaded
    VfbEventHandler.updateVfbLayers(settingsVFB.vfb2_layers)
    vray.setVfbLayers(settingsVFB.vfb2_layers)

@bpy.app.handlers.persistent
def _onUndoPost(e):
    # Color ramp registrations are not stored with the scene and need to be recreated
    registerColorRamps()

@bpy.app.handlers.persistent
def _onRedoPost(e):
    # Color ramp registrations are not stored with the scene and need to be recreated
    registerColorRamps()

@bpy.app.handlers.persistent
def _onUpdatePre(e):
    from vray_blender.exporting.light_export import fixBlenderLights
    from vray_blender.plugins.templates.common import cleanupObjectSelectorLists
    fixBlenderLights()
    cleanupObjectSelectorLists()
    syncColorRamps()


def register():
    blender_utils.addEvent(bpy.app.handlers.save_pre, _onSavePre)
    blender_utils.addEvent(bpy.app.handlers.save_post, _onSavePost)
    blender_utils.addEvent(bpy.app.handlers.load_post, _onLoadPost)
    blender_utils.addEvent(bpy.app.handlers.load_pre, _onLoadPre)
    blender_utils.addEvent(bpy.app.handlers.undo_post, _onUndoPost)
    blender_utils.addEvent(bpy.app.handlers.undo_post, _onRedoPost)
    blender_utils.addEvent(bpy.app.handlers.depsgraph_update_pre, _onUpdatePre)

    # Explicitly run VfbEventHandler as the add-on registration won't trigger
    # a scene reload.
    VfbEventHandler.ensureRunning(reset=True)


def unregister():

    VfbEventHandler.stop()

    blender_utils.delEvent(bpy.app.handlers.save_pre, _onSavePre)
    blender_utils.delEvent(bpy.app.handlers.save_post, _onSavePost)
    blender_utils.delEvent(bpy.app.handlers.load_post, _onLoadPost)
    blender_utils.delEvent(bpy.app.handlers.load_pre, _onLoadPre)
    blender_utils.delEvent(bpy.app.handlers.undo_post, _onUndoPost)
    blender_utils.delEvent(bpy.app.handlers.undo_post, _onRedoPost)
    blender_utils.delEvent(bpy.app.handlers.depsgraph_update_pre, _onUpdatePre)