import os
import bpy

from vray_blender.bin import VRayBlenderLib as vray
from vray_blender.engine.render_engine import VRayRenderEngine
from vray_blender.engine.vfb_event_handler import VfbEventHandler
from vray_blender.engine.zmq_process import ZMQ
from vray_blender.lib import blender_utils, image_utils
from vray_blender.lib.names import IdGenerator, syncUniqueNames
from vray_blender.nodes.color_ramp import syncColorRamps, registerColorRamps
from vray_blender.vray_tools.vray_proxy import fixPosOfProxyLightsOnPreviewUpdate
from vray_blender.plugins.BRDF.BRDFScanned import registerScannedNodes

IS_DEFAULT_SCENE = False # Indicates that current loaded scene is newly created

def isDefaultScene() :
    global IS_DEFAULT_SCENE
    return IS_DEFAULT_SCENE


@bpy.app.handlers.persistent
def _onSavePost(e):
    global IS_DEFAULT_SCENE

    bpy.ops.vray.dr_nodes_save()

    IS_DEFAULT_SCENE = False


@bpy.app.handlers.persistent
def _onSavePre(e):
    from vray_blender.version import getBuildVersionString, getAddonUpgradeNumber
    from vray_blender import UPGRADE_NUMBER, debug
    
    scene = bpy.context.scene
    
    if scene.vray.Exporter.get('vrayAddonUpgradeNumber', None) != UPGRADE_NUMBER:
        debug.printAlways(f"[V-Ray] Scene version updated to {getAddonUpgradeNumber()}")
    
    # Use the dictionary access syntax here in order to avoid updates to the scene
    scene.vray.Exporter['vrayAddonVersion'] = getBuildVersionString() 
    scene.vray.Exporter['vrayAddonUpgradeNumber'] = UPGRADE_NUMBER
    scene.vray.SettingsVFB['vfb2_layers'] = VfbEventHandler.getVfbLayers()
    

@bpy.app.handlers.persistent
def _onLoadPre(e):
    # Reset any active renderers when the blender scene is switched 
    VRayRenderEngine.resetAll()
    VfbEventHandler.reset()


@bpy.app.handlers.persistent
def _onLoadPost(e):
    global IS_DEFAULT_SCENE
    IS_DEFAULT_SCENE = not bpy.data.filepath

    from vray_blender import engine, debug
    from vray_blender.plugins.texture.TexRemap import registerCurvesNodes as registerTexRemapCurves
    engine.ensureRunning()

    # Reset the global unique ID generator. This will keep the generated IDs to a 
    # decent size and will also ensure that on reload, given that no changes have been
    # made to the scene, the IDs will remain the same
    IdGenerator.reset()

    # Set unique ids to all objects with VRay properties
    syncUniqueNames(reset=True)

    # Register all color ramp controls that need to receive update notifications
    registerColorRamps()

    registerTexRemapCurves()

    registerScannedNodes()

    # Run upgrade for the loaded scene, if necessary
    bpy.ops.vray.upgrade_scene('INVOKE_DEFAULT')

    # If SettingsVFB.vfb2_layers is empty, the server will reset layers to their default configuration
    # to override any VFB layer settings from previously opened scenes.
    settingsVFB = bpy.context.scene.vray.SettingsVFB

    # Set VFB Layers if new scene is being loaded
    VfbEventHandler.updateVfbLayers(settingsVFB.vfb2_layers)
    vray.setVfbLayers(settingsVFB.vfb2_layers)


    vray.resetVfbToolbar()

    # Notify the Cosmos browser that the scene name has changed
    vray.updateCosmosSceneName(os.path.basename(e))

    # Set the log level to the one saved in the scene
    debug.setLogLevel(int(bpy.context.scene.vray.Exporter.verbose_level))

    # Remove any images from the previous scene explicitly saved by the plugin.
    image_utils.clearSavedImages()


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
    from vray_blender.nodes.nodes import updateNodeLinks
    from vray_blender.lib.camera_utils import fixOverrideCameraType
    
    fixBlenderLights()
    cleanupObjectSelectorLists()
    syncColorRamps()
    updateNodeLinks()
    fixOverrideCameraType()

    image_utils.checkPackedImageForUpdates()


@bpy.app.handlers.persistent
def _onUpdatePost(scene, depsgraph):
    fixPosOfProxyLightsOnPreviewUpdate(depsgraph)

# bpy.types.BlendImportContext was added in 4.3
if bpy.app.version >= (4, 3, 0):
    @bpy.app.handlers.persistent
    def _onImportPost(ctx: bpy.types.BlendImportContext):
        from vray_blender.plugins.texture.TexRemap import createMtlCurvesNodes
        for item in ctx.import_items:
            if item.id_type == 'MATERIAL':
                createMtlCurvesNodes(item.id)

    
def register():
    blender_utils.addEvent(bpy.app.handlers.save_pre, _onSavePre)
    blender_utils.addEvent(bpy.app.handlers.save_post, _onSavePost)
    blender_utils.addEvent(bpy.app.handlers.load_post, _onLoadPost)
    blender_utils.addEvent(bpy.app.handlers.load_pre, _onLoadPre)
    blender_utils.addEvent(bpy.app.handlers.undo_post, _onUndoPost)
    blender_utils.addEvent(bpy.app.handlers.undo_post, _onRedoPost)
    blender_utils.addEvent(bpy.app.handlers.depsgraph_update_pre, _onUpdatePre)
    blender_utils.addEvent(bpy.app.handlers.depsgraph_update_post, _onUpdatePost)

    # bpy.app.handlers.blend_import_post was added in 4.3
    if bpy.app.version >= (4, 3, 0):
        blender_utils.addEvent(bpy.app.handlers.blend_import_post, _onImportPost)

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
    blender_utils.delEvent(bpy.app.handlers.depsgraph_update_post, _onUpdatePost)

    # bpy.app.handlers.blend_import_post was added in 4.3
    if bpy.app.version >= (4, 3, 0):
        blender_utils.delEvent(bpy.app.handlers.blend_import_post, _onImportPost)