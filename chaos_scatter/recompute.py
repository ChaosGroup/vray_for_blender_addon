# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

""" Event-driven preview recompute pipeline.

    Triggers (no periodic timers - the only timers are one-shot: the 0.15 s debounce that
    coalesces slider drags, and the main-thread marshal of async results):
      - property update callbacks -> markDirty()
      - depsgraph_update_post: intersects depsgraph updates with each scatter's dependencies
      - frame_change_post: fingerprint compare - a frame change emits no depsgraph_update_post
      - render_init/complete/cancel + frame_change_post: synchronous recompute, timers are frozen
      - undo/redo: fingerprint compare
      - load_post: fingerprint compare, PARKED until the server is up; rebuild runtime state

    In-flight policy is latest-wins: a dirty object with a running request cancels it and
    resubmits when the result (or cancellation) lands.
"""

import queue
import time

import bpy
from bpy.app.handlers import persistent

from chaos_scatter import apply as apply_result
from chaos_scatter import collect, curves, lifecycle, params, resolve, utils
from chaos_scatter.backend import getBackend, ScatterResult, SessionNotReady


_DEBOUNCE_SECONDS = 0.15

_dirty = set()                  # session_uids awaiting submission
_inFlight = {}                  # session_uid -> requestId
_requestOwner = {}              # requestId -> session_uid
_resultQueue = queue.Queue()    # (requestId, ScatterResult) from any thread
_applyingResult = False         # guards against handler self-retrigger
_scatterCensus = 0              # scatter carriers in the FILE at the last duplicate/orphan sweep
_objectCount = -1               # len(bpy.data.objects) at the last sweep
_startupRetries = {}            # session_uid -> submits refused while the server was coming up
_lastFingerprint = {}           # session_uid -> fingerprint at the last frame change
_pendingLoadRefresh = set()     # session_uids stale at load, waiting for the server to come up
_rendering = False              # a production render holds the main loop; bpy.app.timers are frozen
_renderBaseline = {}            # session_uid -> fingerprint when the current render started

# The debounce timer is ~0.2s, so this is a couple of seconds of grace for a cold server start.
_MAX_STARTUP_RETRIES = 10

# Ceiling on one frame's synchronous recompute. Generous - it covers a cold server start.
_RENDER_RECOMPUTE_TIMEOUT = 30.0
_RENDER_RECOMPUTE_POLL = 0.005


def markDirty(obj):
    if _applyingResult or not obj.chaos_scatter.is_scatter:
        return
    _dirty.add(obj.session_uid)
    if not bpy.app.timers.is_registered(_flushDirty):
        bpy.app.timers.register(_flushDirty, first_interval=_DEBOUNCE_SECONDS)


def refresh(obj):
    """ Manual, immediate recompute (Refresh button). """
    _dirty.add(obj.session_uid)
    if (retry := _flushDirty()) is not None and not bpy.app.timers.is_registered(_flushDirty):
        # _flushDirty asked to be run again (e.g. the server was still starting) - honor it,
        # otherwise the work sits in _dirty until some unrelated event happens to flush it.
        bpy.app.timers.register(_flushDirty, first_interval=retry)


def _findObject(sessionUid):
    for obj in bpy.data.objects:
        if obj.session_uid == sessionUid:
            return obj
    return None


def _flushPendingLoadRefresh():
    """ Promote the refreshes _onLoadPost parked, but only once a preview session is actually live.

        Event-driven on purpose - no timer polls for the server. There is no "server connected"
        callback to subscribe to, so this hangs off the handlers that already run. Measured 0.2 s
        from a cold file open to promotion (bake corrected 3.7 s in). The cost of having no push
        notification is that a session which comes up while the scene is idle is not noticed until
        the user next touches something - harmless, and never worse than the old never-refresh.
    """
    if not _pendingLoadRefresh or not getBackend().isSessionReady():
        return
    for sessionUid in list(_pendingLoadRefresh):
        if (obj := _findObject(sessionUid)) is not None and utils.isScatterObject(obj):
            markDirty(obj)
    _pendingLoadRefresh.clear()


def _flushDirty(depsgraph=None):
    backend = getBackend()
    for sessionUid in list(_dirty):
        _dirty.discard(sessionUid)

        obj = _findObject(sessionUid)
        if obj is None or not utils.isScatterObject(obj):
            continue

        # Supersede any in-flight request with the latest state (latest-wins). Cancel the old one
        # and resubmit now; its result, if it still arrives, is dropped by the requestId mismatch
        # in _applyPending. (Waiting for the cancelled request's result to resubmit would deadlock:
        # backend.cancel drops that request's callback, so it never comes back.)
        if sessionUid in _inFlight:
            oldRequestId = _inFlight.pop(sessionUid)
            backend.cancel(oldRequestId)
            _requestOwner.pop(oldRequestId, None)

        _submit(obj, backend, depsgraph)

    # Work re-marked DURING this pass could not re-arm the timer - is_registered() stays True
    # for the whole callback - so ask for another tick rather than dropping it.
    return _DEBOUNCE_SECONDS if _dirty else None


def _recomputeForRender(scene, depsgraph):
    """ Recompute every carrier in the scene and BLOCK until the results are applied.

        Non-V-Ray engines only. V-Ray exports GeomScatter itself and never reads the bake, so
        for it this would be a ZMQ round trip per frame that changes nothing on the image.

        bpy.app.timers do not tick while a render holds the main loop, so the debounce and the
        result marshal that the interactive path relies on never run. Pump all three stages here
        instead. The sleep is what lets the backend's message thread take the GIL and answer.

        Only a carrier whose fingerprint differs from the one recorded at render_init is resubmitted,
        so a static scatter costs one compare per frame and a hand-edited bake is left alone.
    """
    if utils.isVRayEngine():
        return

    backend = getBackend()
    for obj in utils.sceneScatterObjects(scene):
        if collect.computeFingerprint(obj, depsgraph) != _renderBaseline.get(obj.session_uid):
            _dirty.add(obj.session_uid)
    if not _dirty:
        return

    deadline = time.monotonic() + _RENDER_RECOMPUTE_TIMEOUT
    while _dirty or _inFlight:
        _flushDirty(depsgraph)
        backend.drainCompleted()
        _applyPending()
        if time.monotonic() > deadline:
            utils.printError("Chaos Scatter preview did not finish recomputing for this frame")
            return
        time.sleep(_RENDER_RECOMPUTE_POLL)


def _dependencyObjects(cs, scene, childMap: dict | None = None):
    """ Without a childMap the model hierarchies are left out - expanding them is O(len(
        bpy.data.objects)) per model item, so the depsgraph path omits them and tests the
        hierarchy from the cheap side instead (see _onDepsgraphUpdate). Callers that need the
        real descendants pass one from resolve.buildChildMap().
    """
    for item in cs.targets:
        if item.object is not None:
            yield item.object
    for item in cs.models:
        if item.object is not None:
            if childMap is None:
                yield item.object
            else:
                yield from resolve.hierarchyObjects(item.object.original, childMap)
    for item in cs.area_modifiers:
        if item.object is not None:
            yield item.object
    if cs.look_at.look_at_target is not None:
        yield cs.look_at.look_at_target
    # The camera the frustum is actually built from - scene.camera in Render Camera mode. Watching
    # camera_clipping_selected_cam alone left a moved render camera invisible to the preview.
    if (camObj := params.clippingCameraObject(cs, scene)) is not None:
        yield camObj


def _submit(obj, backend, depsgraph=None):
    global _applyingResult

    # A render passes ITS depsgraph. The viewport one is not advanced for render frames, and
    # the animated propgroup values live only on that depsgraph's evaluated carrier.
    if depsgraph is None:
        depsgraph = bpy.context.evaluated_depsgraph_get()
        source = obj
    else:
        source = obj.evaluated_get(depsgraph)
    cs = source.chaos_scatter

    # ONE parent->child map for the whole submit. Every hierarchy expansion below needs one and
    # building it is O(len(bpy.data.objects)) each time. Safe to share: nothing reparents during
    # a submit, and the proxy objects created along the way are parentless and are never models.
    childMap = resolve.buildChildMap()

    # The CARRIER is in the check too: applyScatterResult swaps obj.data, which Blender ignores
    # while the object is in edit mode - the result would be dropped and the fresh datablock
    # orphaned on every attempt.
    if obj.mode != 'OBJECT' or any(dep.mode != 'OBJECT' for dep in _dependencyObjects(cs, bpy.context.scene, childMap)):
        # Deliberately not re-marked dirty. Leaving it in _dirty makes _flushDirty re-arm every
        # 0.15s for as long as edit mode lasts, and leaving edit mode already fires the
        # depsgraph handler, which marks it again.
        return

    # Degenerate: bake an empty preview instead of asking the core, which reads target meshes
    # unconditionally. Tested on the RESOLVED lists - a curve whose points were all deleted
    # fills a slot but resolves to nothing. scatter_export applies the same test.
    #
    # Resolved ONCE here and threaded through the rest of the submit: resolveTargets tessellates
    # every curve target (to_mesh for the triangle count, and a second one for the polylines),
    # which dwarfs everything else on this path. Both lists are safe to keep - resolveTargets
    # copies its coordinates out before the temp mesh is freed.
    resolvedTargets = [] if cs.scatter_type == '0' else resolve.resolveTargets(cs, depsgraph)
    resolvedModels = resolve.resolveModels(cs, expandHierarchy=False, childMap=childMap)
    hasTargets = bool(resolvedTargets) or cs.scatter_type == '0'
    if not hasTargets or not resolvedModels:
        _applyingResult = True
        try:
            apply_result.applyScatterResult(obj, None, None)
            # The empty bake is current for this state, so record it. _applyPending is the only
            # other writer and this branch never reaches it, so without this a scatter with no
            # models, or whose target was deleted, stays stale and every undo/redo/load re-dirties
            # it, re-flushes the depsgraph and rebuilds the carrier datablock.
            obj['cscatter_fp'] = collect.computeFingerprint(obj, depsgraph)
        finally:
            _applyingResult = False
        return

    if not backend.isAvailable():
        apply_result.setError(obj, backend.unavailableReason())
        return

    # Refresh the shared instancing group and bind the modifier to the flavour matching this
    # carrier - a file authored on a build with a different PointCloud capability has the other.
    lifecycle.syncModifierNodeGroup(obj)

    # Ensure the prototype/proxy collections are current (idempotent). Also migrates scenes
    # built with an older proto structure to the identity-proxy prototypes.
    lifecycle.rebuildProtoCollections(obj, resolvedModels, depsgraph, childMap)

    try:
        request = collect.buildScatterRequest(source, depsgraph, resolvedTargets, resolvedModels, childMap)
    except Exception as exc:
        utils.printError(f"Failed to build scatter request for '{obj.name}': {exc}")
        apply_result.setError(obj, str(exc))
        return

    sessionUid = obj.session_uid

    def onFinished(requestId, result):
        # May run on any thread: only queue + wake the main thread
        _resultQueue.put((requestId, result))
        if not bpy.app.timers.is_registered(_applyPending):
            bpy.app.timers.register(_applyPending)

    try:
        requestId = backend.submit(request, onFinished)
    except SessionNotReady:
        # ZMQ.ensureRunning() only STARTS the server; the first submit after a cold start
        # regularly lands before it is up. Retry on the debounce timer instead of reporting a
        # permanent error the user has to clear by nudging the scene.
        attempts = _startupRetries.get(sessionUid, 0) + 1
        if attempts > _MAX_STARTUP_RETRIES:
            _startupRetries.pop(sessionUid, None)
            apply_result.setError(obj, "V-Ray server did not start")
            return
        _startupRetries[sessionUid] = attempts
        markDirty(obj)
        return
    except Exception as exc:
        utils.printError(f"Scatter preview submit failed for '{obj.name}': {exc}")
        apply_result.setError(obj, str(exc))
        return

    _startupRetries.pop(sessionUid, None)
    _inFlight[sessionUid] = requestId
    _requestOwner[requestId] = sessionUid


def _applyPending():
    global _applyingResult

    while True:
        try:
            requestId, result = _resultQueue.get_nowait()
        except queue.Empty:
            break

        sessionUid = _requestOwner.pop(requestId, None)
        if sessionUid is None or _inFlight.get(sessionUid) != requestId:
            continue  # stale result (superseded request)
        del _inFlight[sessionUid]

        obj = _findObject(sessionUid)
        if obj is not None and utils.isScatterObject(obj):
            if result.status == ScatterResult.STATUS_OK and result.transforms is not None:
                _applyingResult = True
                try:
                    apply_result.applyScatterResult(obj, result.transforms, result.topo,
                                                    limitHit=result.countLimitHit)
                    obj['cscatter_fp'] = collect.computeFingerprint(
                        obj, bpy.context.evaluated_depsgraph_get())
                finally:
                    _applyingResult = False
            elif result.status == ScatterResult.STATUS_ERROR:
                apply_result.setError(obj, result.errorText)

    # is_registered() reports True for the whole callback, so a result queued while we were
    # draining could not re-arm us - re-check instead of standing down and stranding it.
    return None if _resultQueue.empty() else 0.0


# --------------------------------------------------------------------------
# Handlers
# --------------------------------------------------------------------------

def _sweepDuplicatesAndOrphans():
    lifecycle.handleDuplicates()
    lifecycle.cleanupOrphans()
    return None


@persistent
def _onDepsgraphUpdate(scene, depsgraph):
    global _scatterCensus, _objectCount

    if _applyingResult:
        return

    _flushPendingLoadRefresh()

    # Duplicates and orphans only appear when an ID is added or removed, and len() is O(1) -
    # it keeps the file-wide walk off the interactive path. Both are file-global: scoping them
    # to one scene made a scene switch look like a mass delete.
    objectCount = len(bpy.data.objects)
    if objectCount != _objectCount:
        _objectCount = objectCount
        census = sum(1 for _ in utils.allScatterObjects())
        if census != _scatterCensus:
            _scatterCensus = census
            # Both free datablocks (proxy objects, their meshes, the proto collections) and this
            # handler runs inside scene_graph_update_tagged. Defer a tick, like _refreshAfterUndo
            # (VBLD-2803); it still lands before the 0.15s debounce, so a submit sees the sweep.
            if not bpy.app.timers.is_registered(_sweepDuplicatesAndOrphans):
                bpy.app.timers.register(_sweepDuplicatesAndOrphans)

    scatterObjects = list(utils.sceneScatterObjects(scene))
    if not scatterObjects:
        return

    updatedUids = set()
    ancestorUids = set()    # every parent above an updated object, for the model-hierarchy test below
    shadedUids = set()      # shading-only updates; see the Color Map test below
    for update in depsgraph.updates:
        idBlock = update.id.original if hasattr(update.id, 'original') else update.id
        if not isinstance(idBlock, bpy.types.Object):
            continue
        if update.is_updated_geometry or update.is_updated_transform:
            if update.is_updated_geometry:
                # Invalidates the backend's cached upload of this mesh - see
                # utils.bumpGeometryGeneration.
                utils.bumpGeometryGeneration(idBlock.session_uid)
            updatedUids.add(idBlock.session_uid)
            parent = idBlock.parent
            while parent is not None:
                ancestorUids.add(parent.session_uid)
                parent = parent.parent
        elif update.is_updated_shading:
            shadedUids.add(idBlock.session_uid)

    if not updatedUids and not shadedUids:
        return

    for obj in scatterObjects:
        # Recompute only when a DEPENDENCY changed (distribution surface, model, spline, ...). A
        # change to the carrier itself needs none - its transform is applied to the GN output and
        # our own baked-points data swap is already guarded by _applyingResult. Do NOT skip the
        # object just because its own uid is in updatedUids: when the carrier and a dependency are
        # moved together in one edit, both uids are present and the dependency change must still
        # fire. The dependency set never contains the carrier, so the intersection test alone is
        # correct in every case (carrier-only move -> empty intersection -> no recompute).
        cs = obj.chaos_scatter
        deps = {dep.session_uid for dep in _dependencyObjects(cs, scene)}

        # A model's children are dependencies too, but expanding the hierarchy HERE would cost
        # O(len(bpy.data.objects)) per model item on every tick of every drag. "A descendant of
        # the root was updated" is the same predicate as "the root is an ancestor of something
        # updated", and the parent walk above is O(depth). Only models get this test - the other
        # dependencies are not hierarchy-expanded, so matching them against ancestorUids would
        # fire spuriously whenever an unrelated child of a target or camera moved.
        modelUids = {item.object.session_uid for item in cs.models if item.object is not None}
        # A model's viewport display color is the Color Map clustering MATCH KEY, so in that mode
        # it decides WHERE each model lands. Blender reports a color edit as is_updated_shading
        # only, which the test above ignores by design - a blanket shading trigger would resubmit
        # every scatter in the file on any material tweak. Narrowed to models, in that mode only.
        colorKeyChanged = bool(modelUids & shadedUids) and params.colorMapClusteringActive(cs)
        if (deps & updatedUids) or (modelUids & ancestorUids) or colorKeyChanged:
            markDirty(obj)


@persistent
def _onFrameChange(scene, depsgraph=None):
    """ The only recompute trigger a frame change has. Blender emits NO depsgraph_update_post for a
        frame change, so _onDepsgraphUpdate never sees an animated dependency transform, and the
        animation system writes RNA directly without calling a property's update= callback, so
        onScatterParamUpdate is silent for every keyed / driven / NLA-driven param.

        Same fingerprint compare as _onUndoRedo - it covers the animated collection-item params
        (area modifier operation, target factors, model frequencies) that the scalar param dict
        leaves out, and the dependency matrices the depsgraph never reports here.
    """
    if _applyingResult:
        return

    if _rendering:
        _recomputeForRender(scene, depsgraph)
        return

    _flushPendingLoadRefresh()

    dg = depsgraph if depsgraph is not None else bpy.context.evaluated_depsgraph_get()
    for obj in utils.sceneScatterObjects(scene):
        current = collect.computeFingerprint(obj, dg)
        # First sight of a carrier establishes the baseline without recomputing - the points baked
        # into the file are trusted on load, exactly as _onLoadPost leaves them.
        if _lastFingerprint.setdefault(obj.session_uid, current) != current:
            _lastFingerprint[obj.session_uid] = current
            markDirty(obj)


# msgbus owner token for the render-engine subscription (wiped on load/undo like the curves')
_engineOwner = object()


def _onEngineChange():
    # Re-push the preview mode: non-V-Ray engines force full geometry (see syncModifierInputs)
    for obj in utils.allScatterObjects():
        lifecycle.syncModifierInputs(obj)
        # The clipping frustum is derived from the camera of whichever engine will render, so a
        # scatter that clips has to be recomputed when the engine changes or it keeps culling
        # against the previous engine's camera. Only for clipping users: an engine toggle should
        # not resubmit every scatter in the file.
        if obj.chaos_scatter.camera_clipping.camera_clipping_enabled:
            markDirty(obj)


def _onSceneCameraChange():
    """ The ACTIVE camera changed. In Render Camera mode that is the whole frustum, and the
        depsgraph does not say so: switching between two cameras that have both already been
        evaluated reports every object with shading flags only, so _onDepsgraphUpdate sees no
        transform or geometry update and stands down. (The very first switch to a camera does fire,
        because Blender has to build its evaluated copy - which is why this looks like it works.)
    """
    scene = bpy.context.scene
    for obj in utils.allScatterObjects():
        cs = obj.chaos_scatter
        # Only the scatters that actually take their frustum from the scene camera; one picked
        # through Selected Camera mode is unaffected by which camera is active.
        if params.clippingCameraObject(cs, scene) is scene.camera is not None:
            markDirty(obj)


def _subscribeEngine():
    bpy.msgbus.clear_by_owner(_engineOwner)
    bpy.msgbus.subscribe_rna(key=(bpy.types.RenderSettings, "engine"), owner=_engineOwner,
                             args=(), notify=_onEngineChange)
    bpy.msgbus.subscribe_rna(key=(bpy.types.Scene, "camera"), owner=_engineOwner,
                             args=(), notify=_onSceneCameraChange)


@persistent
def _onRenderInit(_scene):
    global _rendering
    _rendering = True
    _renderBaseline.clear()
    dg = bpy.context.evaluated_depsgraph_get()
    for obj in utils.allScatterObjects():
        _renderBaseline[obj.session_uid] = collect.computeFingerprint(obj, dg)


@persistent
def _onRenderEnd(_scene):
    global _rendering
    _rendering = False
    _renderBaseline.clear()


@persistent
def _onLoadPost(_scene):
    global _scatterCensus, _objectCount
    _dirty.clear()
    _inFlight.clear()
    _requestOwner.clear()
    _scatterCensus = 0
    _objectCount = -1
    _startupRetries.clear()
    # session_uids are per-session and get reused across files - a stale baseline here would
    # suppress the first real frame-change recompute in the newly loaded scene. Re-seeded per
    # carrier below, once the migrations have run.
    _lastFingerprint.clear()
    _pendingLoadRefresh.clear()
    curves.resubscribeAll()
    _subscribeEngine()
    # Rebuild the prototype collections so the baked preview points render against the current
    # (identity-proxy) prototype structure - migrates scenes saved with an older layout. Every
    # scatter in the FILE: the ones outside the active scene need it just as much.
    # The migrations run over every carrier BEFORE any recompute is scheduled below - a submit
    # resolves models against the proto structure rebuildProtoCollections has just fixed up.
    for obj in utils.allScatterObjects():
        lifecycle.syncModifierNodeGroup(obj)
        lifecycle.rebuildProtoCollections(obj)
        lifecycle.syncModifierInputs(obj)

    # Baked points that disagree with the scene are recomputed rather than trusted, same test and
    # same reasoning as _onUndoRedo. Load used to trust the bake unconditionally, which left an
    # ANIMATED parameter showing the wrong preview until the frame changed (_onFrameChange only
    # sees a param CHANGE, and opening a file is not one). Measured at 0.3-0.6 ms per carrier.
    # A carrier with no stored fingerprint predates it - trust its bake, there is nothing to test.
    #
    # PARKED, not submitted: at load the preview session is usually NOT up yet - vray_blender
    # restarts ZmqServer in its own load_post, and isSessionReady() measured False on 2 of 3
    # consecutive opens. Going straight to markDirty would burn the whole SessionNotReady retry
    # budget (10 x 0.15 s) and then report "V-Ray server did not start" on a scene the user has
    # just opened. _flushPendingLoadRefresh promotes the work as soon as a session exists.
    dg = bpy.context.evaluated_depsgraph_get()
    for obj in utils.allScatterObjects():
        # Seed the frame-change baseline with the state AS LOADED. Leaving it empty made the first
        # frame change after a load a no-op: _onFrameChange would merely establish the baseline and
        # therefore miss the very change that triggered it (measured - open at frame 1, scrub to 2,
        # nothing happened).
        fingerprint = collect.computeFingerprint(obj, dg)
        _lastFingerprint[obj.session_uid] = fingerprint
        if (storedFp := obj.get('cscatter_fp')) and fingerprint != storedFp:
            _pendingLoadRefresh.add(obj.session_uid)
    _flushPendingLoadRefresh()


@persistent
def _onUndoRedo(_scene):
    curves.resubscribeAll()
    _subscribeEngine()
    # evaluated_depsgraph_get() flushes the depsgraph; from undo_post that evaluates mid-ID-remap
    # and crashes (VBLD-2803). Defer to the next tick, once the undo is committed.
    if not bpy.app.timers.is_registered(_refreshAfterUndo):
        bpy.app.timers.register(_refreshAfterUndo)


def _refreshAfterUndo():
    # Re-walk the carriers: undo replaced every ID, so a reference captured a tick ago would dangle.
    dg = bpy.context.evaluated_depsgraph_get()
    for obj in utils.allScatterObjects():
        storedFp = obj.get('cscatter_fp')
        if not storedFp:
            continue
        if collect.computeFingerprint(obj, dg) != storedFp:
            markDirty(obj)
    return None


def register():
    bpy.app.handlers.depsgraph_update_post.append(_onDepsgraphUpdate)
    bpy.app.handlers.frame_change_post.append(_onFrameChange)
    bpy.app.handlers.render_init.append(_onRenderInit)
    bpy.app.handlers.render_complete.append(_onRenderEnd)
    bpy.app.handlers.render_cancel.append(_onRenderEnd)
    bpy.app.handlers.load_post.append(_onLoadPost)
    bpy.app.handlers.undo_post.append(_onUndoRedo)
    bpy.app.handlers.redo_post.append(_onUndoRedo)
    _subscribeEngine()


def unregister():
    global _rendering

    for handlerList, handler in (
            (bpy.app.handlers.depsgraph_update_post, _onDepsgraphUpdate),
            (bpy.app.handlers.frame_change_post, _onFrameChange),
            (bpy.app.handlers.render_init, _onRenderInit),
            (bpy.app.handlers.render_complete, _onRenderEnd),
            (bpy.app.handlers.render_cancel, _onRenderEnd),
            (bpy.app.handlers.load_post, _onLoadPost),
            (bpy.app.handlers.undo_post, _onUndoRedo),
            (bpy.app.handlers.redo_post, _onUndoRedo)):
        if handler in handlerList:
            handlerList.remove(handler)

    bpy.msgbus.clear_by_owner(_engineOwner)

    # Drop pending debounce/marshal timers and in-flight bookkeeping. Blender does not reload the
    # addon module on a plain re-enable (globals persist), so stale timers or _inFlight entries
    # would otherwise carry across a disable->enable cycle.
    for timer in (_flushDirty, _applyPending, _refreshAfterUndo, _sweepDuplicatesAndOrphans):
        if bpy.app.timers.is_registered(timer):
            bpy.app.timers.unregister(timer)
    _rendering = False
    _renderBaseline.clear()
    _dirty.clear()
    _inFlight.clear()
    _requestOwner.clear()
    _lastFingerprint.clear()
    _pendingLoadRefresh.clear()
    while not _resultQueue.empty():
        _resultQueue.get_nowait()
