import os
import bpy

from vray_blender.bin import VRayBlenderLib as vray
from vray_blender.engine.render_engine import VRayRenderEngine
from vray_blender.engine.vfb_event_handler import VfbEventHandler
from vray_blender.lib import blender_utils, image_utils
from vray_blender.lib.names import IdGenerator, syncUniqueNames
from vray_blender.nodes.color_ramp import syncColorRamps, registerColorRamps, pruneColorRamps
from vray_blender.plugins.BRDF.BRDFScanned import registerScannedNodes


@bpy.app.handlers.persistent
def _onSavePost(e):
    from vray_blender.operators import VRAY_OT_dr_nodes_save

    if VRAY_OT_dr_nodes_save.poll(bpy.context):
        bpy.ops.vray.dr_nodes_save()


@bpy.app.handlers.persistent
def _onSavePre(e):
    from vray_blender.version import getBuildVersionString

    scene = bpy.context.scene

    # Use the dictionary access syntax here in order to avoid updates to the scene
    scene.vray.Exporter['vrayAddonVersion'] = getBuildVersionString()
    scene.vray.SettingsVFB['vfb2_layers'] = VfbEventHandler.getVfbLayers()


@bpy.app.handlers.persistent
def _onLoadPre(e):
    # Reset any active renderers when the blender scene is switched
    VRayRenderEngine.resetAll()
    VfbEventHandler.reset()


@bpy.app.handlers.persistent
def _onLoadPost(e):
    from vray_blender import engine, debug
    from vray_blender.nodes.curves_node import registerColorMapCurves
    from vray_blender.lib.blender_utils import checkAndReportVersionIncompatibility
    
    checkAndReportVersionIncompatibility()
    
    engine.ensureRunning()

    # Reset the global unique ID generator. This will keep the generated IDs to a
    # decent size and will also ensure that on reload, given that no changes have been
    # made to the scene, the IDs will remain the same
    IdGenerator.reset()

    # Set unique ids to all objects with VRay properties
    syncUniqueNames(reset=True)

    # Register all color ramp controls that need to receive update notifications
    registerColorRamps()

    # Register all nodes that have a CurvesMap (Remap) widget.
    curvesMapNodes = ["VRayNodeTexRemap", "VRayNodeBRDFToonMtl"] # .bl_idname
    for node in curvesMapNodes:
        registerColorMapCurves(node)

    registerScannedNodes()

    # If SettingsVFB.vfb2_layers is empty, the server will reset layers to their default configuration
    # to override any VFB layer settings from previously opened scenes.
    settingsVFB = bpy.context.scene.vray.SettingsVFB

    # Set VFB Layers if new scene is being loaded
    VfbEventHandler.updateVfbLayers(settingsVFB.vfb2_layers, settingsAreFromScene=True)
    vray.setVfbLayers(settingsVFB.vfb2_layers)
    vray.resetVfbToolbar()

    # Notify the Cosmos browser that the scene name has changed
    vray.updateCosmosSceneName(os.path.basename(e))

    # Set the log level to the one saved in the scene
    prefs = blender_utils.getVRayPreferences()
    debug.setLogLevel(int(prefs.verbose_level), prefs.enable_qt_logs)

    # Remove any images from the previous scene explicitly saved by the plugin and track the ones from the current scene.
    image_utils.clearSavedImages()
    image_utils.trackImageUpdates()

    # Run upgrade for the loaded scene, if necessary
    if bpy.app.background:
        # In headless mode, the rendering may start before the scene is upgraded.
        # Invoke the operaror synchronously
        bpy.ops.vray.upgrade_scene('INVOKE_DEFAULT')
    else:
        VfbEventHandler.upgradeScene()


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
    from vray_blender.lib.camera_utils import fixOverrideCameraType

    if blender_utils.deleteOperatorHasBeenCalled():
        cleanupObjectSelectorLists()
    
    fixBlenderLights()

    pruneColorRamps()
    syncColorRamps()
    fixOverrideCameraType()

    image_utils.trackImageUpdates()
    image_utils.updateTexturePlaceholderNode()


# bpy.types.BlendImportContext was added in 4.3
if bpy.app.version >= (4, 3, 0):
    @bpy.app.handlers.persistent
    def _onImportPost(ctx: bpy.types.BlendImportContext):
        from vray_blender.nodes.curves_node import createMtlCurvesNodes
        for item in ctx.import_items:
            if item.id_type == 'MATERIAL':
                createMtlCurvesNodes(item.id)


def register():
    blender_utils.addEvent(bpy.app.handlers.save_pre, _onSavePre)
    blender_utils.addEvent(bpy.app.handlers.save_post, _onSavePost)
    blender_utils.addEvent(bpy.app.handlers.load_post, _onLoadPost)
    blender_utils.addEvent(bpy.app.handlers.load_pre, _onLoadPre)
    blender_utils.addEvent(bpy.app.handlers.undo_post, _onUndoPost)
    blender_utils.addEvent(bpy.app.handlers.redo_post, _onRedoPost)
    blender_utils.addEvent(bpy.app.handlers.depsgraph_update_pre, _onUpdatePre)

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
    blender_utils.delEvent(bpy.app.handlers.redo_post, _onRedoPost)
    blender_utils.delEvent(bpy.app.handlers.depsgraph_update_pre, _onUpdatePre)

    # bpy.app.handlers.blend_import_post was added in 4.3
    if bpy.app.version >= (4, 3, 0):
        blender_utils.delEvent(bpy.app.handlers.blend_import_post, _onImportPost)
