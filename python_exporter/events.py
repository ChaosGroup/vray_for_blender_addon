# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import functools
import bpy

from vray_blender.bin import VRayBlenderLib as vray
from vray_blender.engine.render_engine import VRayRenderEngine
from vray_blender.engine.vfb_event_handler import VfbEventHandler
from vray_blender.lib import blender_utils, image_utils, path_utils
from vray_blender.lib.names import IdGenerator, syncUniqueNames
from vray_blender.nodes.color_ramp import syncColorRamps, registerColorRamps, pruneColorRamps
from vray_blender.nodes.curves_node import registerCurveNodeSubscriptions, storeAllCurvesData
from vray_blender.nodes.links import clearScheduledLinkFixes
from vray_blender.nodes.utils import unshareNodeTextures
from vray_blender.lib.image_utils import registerBitmapImageNodes
from vray_blender.nodes.tree import upgradeTrees
from vray_blender.plugins.BRDF.BRDFScanned import registerScannedNodes
from vray_blender.utils import cosmos_asset_set, cosmos_scatter_preset, cosmos_drop_batch, cosmos_variants
from vray_blender.utils.update_checker import autoCheckForUpdatesFeatureEnabled


# Global flag to ensure we only initialize the update check subsystem once
_UpdateCheckSystemInitialized = False

_MSGBUS_OWNER = object()

# True between load_pre and load_post - Blender 5.1 can call engine_update_render_passes
# from after_liblink_id_process before bNodeTree typeinfo is bound (compositor's Render
# Layers node_declare path), and our update_render_passes touches world.node_tree which
# crashes in rna_NodeTree_refine on the unbound typeinfo. Skipping while this flag is set
# defers the call until load_post, when the next compositor refresh will retry safely.
_blendFileLoadInProgress = False


def isBlendFileLoading() -> bool:
    return _blendFileLoadInProgress


def _onRenderEngineChange():
    from vray_blender.ui.classes import VRayEngines

    scene = getattr(bpy.context, 'scene', None)
    if not scene:
        return

    for screen in bpy.data.screens:
        for area in screen.areas:
            if area.type == 'NODE_EDITOR':
                for space in area.spaces:
                    if space.type != 'NODE_EDITOR':
                        continue
                    if scene.render.engine in VRayEngines and space.tree_type == 'ShaderNodeTree':
                        space.tree_type = 'VRayNodeTreeEditor'
                    elif scene.render.engine not in VRayEngines and space.tree_type == 'VRayNodeTreeEditor':
                        space.tree_type = 'ShaderNodeTree'

    # Carry Cycles' Depth pass checkbox over to the V-Ray Z Depth channel. Only into a world that
    # is already V-Ray's, the channel setter would otherwise convert it.
    world = scene.world
    if (scene.render.engine in VRayEngines and world and world.node_tree and world.vray.is_vray_class
            and any(vl.use_pass_z for vl in scene.view_layers)):
        world.vray.VRayRenderChannels.VRayNodeRenderChannelZDepth.enabled = True


@bpy.app.handlers.persistent
def _onSavePost(e):
    from vray_blender.operators import VRAY_OT_dr_nodes_save

    if VRAY_OT_dr_nodes_save.poll(bpy.context):
        bpy.ops.vray.dr_nodes_save()

    # After Save As the filepath changes; notify the server so VFB project path
    # and scene name reflect the new location.
    vray.updateScenePath(path_utils.getScenePath())


@bpy.app.handlers.persistent
def _onSavePre(e):
    from vray_blender import UPGRADE_NUMBER
    from vray_blender.version import getBuildVersionString

    scene = bpy.context.scene

    # Use the dictionary access syntax here in order to avoid updates to the scene
    scene.vray.Exporter['vrayAddonVersion'] = getBuildVersionString()
    scene.vray.SettingsVFB['vfb2_layers'] = VfbEventHandler.getVfbLayers()

    # Stamp every V-Ray datablock with the current upgrade number so data appended from
    # this file into another scene carries its version and can be upgraded precisely.
    # Dictionary access avoids tagging the datablocks (and the file) as modified.
    for collection in (bpy.data.materials, bpy.data.objects, bpy.data.lights,
                       bpy.data.worlds, bpy.data.cameras, bpy.data.node_groups):
        for db in collection:
            vrayProps = getattr(db, 'vray', None)
            if vrayProps is not None and hasattr(vrayProps, 'upgradeNumber'):
                vrayProps['upgradeNumber'] = UPGRADE_NUMBER

    # Append and link rebuild the curve widgets from this stored copy.
    storeAllCurvesData()


@bpy.app.handlers.persistent
def _onLoadPre(e):
    global _blendFileLoadInProgress
    _blendFileLoadInProgress = True

    # Wrap in try/except so a failure in resetAll/reset doesn't leave the flag
    # permanently set if load_post then doesn't fire.
    try:
        VRayRenderEngine.resetAll()
        VfbEventHandler.reset()
        vray.clearMainBitmapCache()
        # An in-flight Cosmos Asset Set or Scatter preset holds references to objects in the file
        # being replaced. Finalizing it after the load would touch freed data.
        cosmos_asset_set.clearSessions()
        cosmos_scatter_preset.clearSessions()
        # A drop batch holds no object references, so nothing breaks if one survives the
        # load - but it would go on claiming imports at coordinates that mean nothing in
        # the new scene, and withhold their materials for doing so.
        cosmos_drop_batch.clearBatches()
        # A pending variant swap names an object in the file being replaced, and its reply would
        # otherwise be applied to whatever the new file happens to call by that name.
        cosmos_variants.clearSwaps()
        # Imports queued for the file being replaced.
        from vray_blender.utils.cosmos_handler import clearImportQueue
        clearImportQueue()
        # Drop stale scheduled-fix keys before load.
        clearScheduledLinkFixes()
    except Exception:
        _blendFileLoadInProgress = False
        raise


@bpy.app.handlers.persistent
def _onLoadPost(scenePath):
    from vray_blender import engine, debug
    from vray_blender.lib.blender_utils import checkAndReportVersionIncompatibility
    from vray_blender.lib.sys_utils import StartupConfig

    checkAndReportVersionIncompatibility()

    engine.ensureRunning()

    # Reset the render-element warning dedup so the newly loaded scene re-warns about
    # any of its wired channels that have no compositor mapping.
    from vray_blender.engine.render_elements import resetUnmappedChannelWarnings
    resetUnmappedChannelWarnings()

    # The property pages' back/forward history holds node names from the previous file; a
    # same-named node in the newly loaded one would make Back jump somewhere unrelated.
    from vray_blender.nodes.navigation import clearNavigationHistory
    clearNavigationHistory()

    # A file saved during a viewport render holds the neutral color management. Restore the user's.
    from vray_blender.engine.draw_scheduler import restoreViewTransform
    restoreViewTransform()

    # Reset the global unique ID generator. This will keep the generated IDs to a
    # decent size and will also ensure that on reload, given that no changes have been
    # made to the scene, the IDs will remain the same
    IdGenerator.reset()

    # Upgrade trees coming from Blender 4.5, not completely clear why but they seem to have an empty tree_type.
    upgradeTrees()

    # Before anything below reads them.
    from vray_blender.utils.upgrade_scene import upgradeLinkedData
    upgradeLinkedData()

    # Set unique ids to all objects with VRay properties
    syncUniqueNames(reset=True)

    # Register all color ramp controls that need to receive update notifications
    registerColorRamps()

    # Re-subscribe bitmap image update notifications (cleared on scene reload)
    registerBitmapImageNodes()

    # Register all nodes that use a CurvesMap (Remap) widget.
    registerCurveNodeSubscriptions()

    registerScannedNodes()

    # If SettingsVFB.vfb2_layers is empty, the server will reset layers to their default configuration
    # to override any VFB layer settings from previously opened scenes.
    settingsVFB = bpy.context.scene.vray.SettingsVFB

    # Set VFB Layers if new scene is being loaded
    VfbEventHandler.updateVfbLayers(settingsVFB.vfb2_layers, settingsAreFromScene=True)
    vray.setVfbLayers(settingsVFB.vfb2_layers)
    vray.resetVfbToolbar()

    # Wipe any image left over in the VFB from the previous scene.
    vray.clearVfbImage()

    # Notify the server that the scene path has changed (used for VFB project path and scene name).
    vray.updateScenePath(path_utils.getScenePath())

    # Set the log level to the one saved in the scene
    prefs = blender_utils.getVRayPreferences()
    logLevel = int(StartupConfig.logLevel) if StartupConfig.logLevel is not None else int(prefs.verbose_level)
    debug.setLogLevel(logLevel, prefs.enable_qt_logs)

    # Remove any images from the previous scene explicitly saved by the plugin and track the ones from the current scene.
    image_utils.clearSavedImages()
    image_utils.trackImageUpdates()

    # Node thumbnails hold GPU textures and content keys for the previous file's trees
    try:
        from vray_blender.preview import thumbs
        thumbs.onFileLoad()
    except Exception as ex:
        debug.printError(f"Node thumbnails reset failed: {ex}")

    # Run upgrade for the loaded scene, if necessary
    if bpy.app.background:
        # In headless mode, the rendering may start before the scene is upgraded.
        # Invoke the operator synchronously
        bpy.ops.vray.upgrade_scene('INVOKE_DEFAULT')
    else:
        VfbEventHandler.upgradeScene()

    if autoCheckForUpdatesFeatureEnabled():
        if _UpdateCheckSystemInitialized:
            from vray_blender.utils.update_checker import onCheckTimer
            if not bpy.app.timers.is_registered(onCheckTimer):
                bpy.app.timers.register(onCheckTimer)

    global _blendFileLoadInProgress
    _blendFileLoadInProgress = False

    # update_render_passes() skipped every call made while the flag was set, so the
    # compositor's Render Layers node declared no passes and its sockets are disabled.
    # Re-query the engine now that touching world.node_tree is safe again.
    for viewLayer in bpy.context.scene.view_layers:
        viewLayer.update_render_passes()


@bpy.app.handlers.persistent
def _onRenderJobEnded(e):
    # Starting the render killed any pending material preview jobs (Blender's
    # WM_jobs_kill_all_except), leaving them as spinners forever. Re-kick them.
    from vray_blender.engine.renderer_preview import scheduleStuckPreviewCheck
    scheduleStuckPreviewCheck()


@bpy.app.handlers.persistent
def _onUndoPost(e):
    unshareNodeTextures()
    # Color ramp registrations are not stored with the scene and need to be recreated
    registerColorRamps()
    registerBitmapImageNodes()
    registerCurveNodeSubscriptions()
    _invalidateNodeThumbnails()


@bpy.app.handlers.persistent
def _onRedoPost(e):
    unshareNodeTextures()
    # Color ramp registrations are not stored with the scene and need to be recreated
    registerColorRamps()
    registerBitmapImageNodes()
    registerCurveNodeSubscriptions()
    _invalidateNodeThumbnails()


def _invalidateNodeThumbnails():
    """ Undo and redo restore node properties without running their update callbacks, the only
        signal node thumbnails have that a node changed.
    """
    try:
        from vray_blender.preview import thumbs
        thumbs.onUndoRedo()
    except Exception as ex:
        debug.printError(f"Node thumbnails invalidation failed: {ex}")


@bpy.app.handlers.persistent
def _onUpdatePost(scene, depsgraph):
    from vray_blender.exporting.light_export import fixSceneLights
    from vray_blender.plugins.templates.common import cleanupObjectSelectorLists
    from vray_blender.lib.camera_utils import fixOverrideCameraType

    if blender_utils.deleteOperatorHasBeenCalled():
        cleanupObjectSelectorLists()

    fixSceneLights()

    pruneColorRamps()
    syncColorRamps()
    fixOverrideCameraType()

    image_utils.trackImageUpdates()
    image_utils.updateTexturePlaceholderNode()
    image_utils.tagChangedSequenceBitmaps()

    # Invalidate the render channels panel cache on any World/node-tree change (catches
    # link swaps, muting and group sub-tree edits). Route via PLUGIN_MODULES to hit the
    # bare-name module instance that owns the getter - a package-path import would no-op.
    if depsgraph.id_type_updated('WORLD') or depsgraph.id_type_updated('NODETREE'):
        from vray_blender import plugins
        if rcPanel := plugins.PLUGIN_MODULES.get('VRayRenderChannels'):
            rcPanel.invalidateConnectedChannelsCache()

    # Node thumbnails hold a content key per node. A graph change drops them so the next redraw
    # rehashes what is visible.
    try:
        from vray_blender.preview.thumbs import service as thumbService
        thumbService.onDepsgraphUpdate(scene, depsgraph)
    except Exception as ex:
        from vray_blender import debug
        debug.printError(f"Node thumbnails sync failed: {ex}")

    # Push Blender's camera-view render region to VFB whenever the scene changes
    # and no render is running. syncVfbRenderRegionFromScene() dedupes against
    # its last sent payload, so calls with no relevant change are a cheap no-op.
    VfbEventHandler.syncVfbRenderRegionFromScene()


def _applyFCurveValue(idBlock, fc, frame):
    """ Evaluate `fc` at `frame` and write the result via fc.data_path. """
    dataPath = fc.data_path
    lastDot = dataPath.rfind('.')
    if lastDot < 0:
        return
    parentPath = dataPath[:lastDot]
    propName = dataPath[lastDot + 1:]
    try:
        parent = idBlock.path_resolve(parentPath)
        current = getattr(parent, propName, None)
        if current is None:
            return
        # fc.evaluate() is always a float. Convert it the way Blender's animsys does.
        value = fc.evaluate(frame)
        prop = parent.bl_rna.properties[propName]
        if prop.type == 'BOOLEAN':
            value = value >= 0.5
        elif prop.type == 'INT':
            value = int(value)
        elif prop.type == 'ENUM':
            value = next((e.identifier for e in prop.enum_items if e.value == int(value)), None)
            if value is None:
                return
        try:
            current[fc.array_index] = value
        except (TypeError, AttributeError):
            setattr(parent, propName, value)
    except (ValueError, AttributeError):
        pass


@bpy.app.handlers.persistent
def _onFrameChangePre(scene, depsgraph=None):
    """ Manually evaluate animation on V-Ray group trees.

        Blender's depsgraph builder has hardcoded recursion through
        ShaderNodeGroup but doesn't follow arbitrary NodeCustomGroup
        subclasses (like VRayNodeGroup) - so fcurves on V-Ray group trees
        are never evaluated when the frame changes. Apply them here.
    """
    frame = scene.frame_current + scene.frame_subframe
    for ng in bpy.data.node_groups:
        if not hasattr(ng, 'vray') or ng.vray.tree_type != 'GROUP':
            continue
        for fc in blender_utils.getFCurves(ng):
            _applyFCurveValue(ng, fc, frame)




# bpy.types.BlendImportContext was added in 4.3
if bpy.app.version >= (4, 3, 0):
    @bpy.app.handlers.persistent
    def _onImportPre(ctx: bpy.types.BlendImportContext):
        from vray_blender.utils.upgrade_scene import isReadingSourceFile
        if isReadingSourceFile():
            return

        from vray_blender.nodes.curves_node import recordCurvesNodesBeforeImport
        recordCurvesNodesBeforeImport()

    @bpy.app.handlers.persistent
    def _onImportPost(ctx: bpy.types.BlendImportContext):
        from vray_blender.utils.upgrade_scene import isReadingSourceFile, upgradeFromSource, readSourceCurvesData

        # Guard reentry: reading a source file links data and re-fires this handler.
        if isReadingSourceFile():
            return

        from vray_blender.nodes.curves_node import initImportedCurveNodes
        from vray_blender.nodes import color_ramp
        from vray_blender.nodes.specials.gradient_ramp import VRayNodeColorRamp
        from vray_blender.plugins.texture.TexSoftbox import registerNodeColorRamps

        # Appended/linked data may come from an older V-Ray version. Blender fires no
        # load_post/version_update on append, so run the upgrade here, scoped to the
        # imported datablocks (the active scene is untouched). Group by source file so
        # each group is upgraded from the version it was created with - re-running
        # already-applied scripts on imported data corrupts it. Runs before the fixups
        # below so they operate on upgraded node trees. Silent - console log only.
        bySource = {}
        for item in ctx.import_items:
            if not item.id:
                continue
            path = bpy.path.abspath(item.source_library.filepath) if item.source_library else ''
            bySource.setdefault(path, set()).add(item.id.session_uid)

        for path, uids in bySource.items():
            upgradeFromSource(path, uids)

        # Read once per source file, and only if some curve is not stored on its node.
        getSourceCurvesData = functools.cache(readSourceCurvesData)

        for item in ctx.import_items:
            if item.id_type in ('MATERIAL', 'WORLD', 'LIGHT'):
                ntree = getattr(item.id, 'node_tree', None)
            elif item.id_type == 'OBJECT':
                ntree = getattr(getattr(item.id, 'vray', None), 'ntree', None)
            elif item.id_type == 'NODETREE':
                # Node groups arrive as datablocks of their own, and cannot be reached from the
                # tree of the material that uses them.
                ntree = item.id
            else:
                continue

            if not ntree:
                continue

            path = bpy.path.abspath(item.source_library.filepath) if item.source_library else ''
            initImportedCurveNodes(ntree, lambda: getSourceCurvesData(path))

            for node in ntree.nodes:
                if node.bl_idname == VRayNodeColorRamp.bl_idname:
                    color_ramp.registerColorRamp(node, 'texture', node.texture)
                elif hasattr(node, 'TexSoftbox'):
                    registerNodeColorRamps(node)

        # For some reason imported assetts don't get unique names from the IPR
        # sync calls... So we call syncUniqueNames here as well.
        syncUniqueNames(reset=False)

def register():
    bpy.msgbus.subscribe_rna(
        key=(bpy.types.RenderSettings, 'engine'),
        owner=_MSGBUS_OWNER,
        args=(),
        notify=_onRenderEngineChange,
        options={'PERSISTENT'},
    )

    blender_utils.addEvent(bpy.app.handlers.save_pre, _onSavePre)
    blender_utils.addEvent(bpy.app.handlers.save_post, _onSavePost)
    blender_utils.addEvent(bpy.app.handlers.load_post, _onLoadPost)
    blender_utils.addEvent(bpy.app.handlers.load_pre, _onLoadPre)
    blender_utils.addEvent(bpy.app.handlers.undo_post, _onUndoPost)
    blender_utils.addEvent(bpy.app.handlers.redo_post, _onRedoPost)
    blender_utils.addEvent(bpy.app.handlers.depsgraph_update_post, _onUpdatePost)
    blender_utils.addEvent(bpy.app.handlers.frame_change_pre, _onFrameChangePre)
    blender_utils.addEvent(bpy.app.handlers.render_complete, _onRenderJobEnded)
    blender_utils.addEvent(bpy.app.handlers.render_cancel, _onRenderJobEnded)

    # bpy.app.handlers.blend_import_pre/post were added in 4.3
    if bpy.app.version >= (4, 3, 0):
        blender_utils.addEvent(bpy.app.handlers.blend_import_pre, _onImportPre)
        blender_utils.addEvent(bpy.app.handlers.blend_import_post, _onImportPost)

    # Explicitly run VfbEventHandler as the add-on registration won't trigger
    # a scene reload.
    VfbEventHandler.ensureRunning(reset=True)

    global _UpdateCheckSystemInitialized

    if autoCheckForUpdatesFeatureEnabled():
        if not _UpdateCheckSystemInitialized:
            # This is the first scene loaded after Blender has started
            from vray_blender.utils.update_checker import onInitialCheck
            bpy.app.timers.register(onInitialCheck)
            _UpdateCheckSystemInitialized = True
        else:
            # Scene has been reloaded
            from vray_blender.utils.update_checker import onCheckTimer
            bpy.app.timers.register(onCheckTimer)


def unregister():
    bpy.msgbus.clear_by_owner(_MSGBUS_OWNER)

    VfbEventHandler.stop()

    blender_utils.delEvent(bpy.app.handlers.save_pre, _onSavePre)
    blender_utils.delEvent(bpy.app.handlers.save_post, _onSavePost)
    blender_utils.delEvent(bpy.app.handlers.load_post, _onLoadPost)
    blender_utils.delEvent(bpy.app.handlers.load_pre, _onLoadPre)
    blender_utils.delEvent(bpy.app.handlers.undo_post, _onUndoPost)
    blender_utils.delEvent(bpy.app.handlers.redo_post, _onRedoPost)
    blender_utils.delEvent(bpy.app.handlers.depsgraph_update_post, _onUpdatePost)
    blender_utils.delEvent(bpy.app.handlers.frame_change_pre, _onFrameChangePre)
    blender_utils.delEvent(bpy.app.handlers.render_complete, _onRenderJobEnded)
    blender_utils.delEvent(bpy.app.handlers.render_cancel, _onRenderJobEnded)

    # bpy.app.handlers.blend_import_pre/post were added in 4.3
    if bpy.app.version >= (4, 3, 0):
        blender_utils.delEvent(bpy.app.handlers.blend_import_pre, _onImportPre)
        blender_utils.delEvent(bpy.app.handlers.blend_import_post, _onImportPost)

    from vray_blender.utils.update_checker import onCheckTimer
    if bpy.app.timers.is_registered(onCheckTimer):
        bpy.app.timers.unregister(onCheckTimer)