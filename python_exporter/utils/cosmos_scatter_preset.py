# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Chaos Cosmos Scatter preset import.
#
# A Scatter preset is a Cosmos package with no geometry of its own - its payload is a .mbc config
# describing a Chaos Scatter setup plus the ids of the models it scatters. The server detects it by
# its Cosmos tag and forwards the .mbc path to us as a "ScatterPreset" import.
#
# Flow
# ----
#   1. assetImportTimerFunction gets a "ScatterPreset" item and calls startScatterPresetImport().
#      The .mbc can only be read by the AppSDK, so the server does it (backend.readPreset) and
#      returns the parameters of the GeomScatter it filled, plus one placeholder Node per
#      referenced model carrying the position the preset wants it imported at.
#   2. We create the Chaos Scatter object, apply those parameters to it, and ask the Cosmos client
#      to import every referenced model, passing an opaque token per model.
#   3. Each model comes back through the normal Cosmos import path as its own queue item, carrying
#      its token in settings.setInstanceToken. notePresetModel() adds it to the scatter's model
#      list with the frequency the preset authored.
#   4. Once every model is accounted for, tickSessions() calls _finalize(), which recomputes the
#      preview and pushes a single undo step for the whole import.
#
# This mirrors utils/cosmos_asset_set.py, which does the same dance for an Asset Set manifest.

import time
import uuid

import bpy
from mathutils import Vector

from vray_blender import debug
from vray_blender.lib.blender_utils import selectObject
from vray_blender.lib.lib_utils import sanitizeDatablockName
from vray_blender.bin import VRayBlenderLib as vray


# Instance tokens are namespaced so the import dispatch can tell a preset's models from the
# members of an Asset Set, which travel through the same field.
TOKEN_PREFIX = 'scatterpreset'

# Seconds of silence after which a session gives up and finalizes with the models that did arrive.
# Same reasoning as cosmos_asset_set: the server reports failed members explicitly, so this only
# covers a model that produced no reply at all.
_SESSION_TIMEOUT_SECONDS = 300.0


class PresetSession:
    """Tracks one in-flight preset import until every referenced model is accounted for."""

    __slots__ = ('presetName', 'scatterUid', 'assetIds', 'frequencies', 'offsets', 'collection',
                 'counted', 'placed', 'pending', 'lastActivity')

    def __init__(self, presetName, scatterUid, assetIds, frequencies, offsets, collection):
        self.presetName  = presetName
        # The scatter is held by session_uid, not by reference: the models take seconds to arrive
        # and an undo in between frees the object without invalidating our Python reference.
        self.scatterUid  = scatterUid
        self.assetIds    = assetIds
        # Parallel to assetIds: the authored relative frequency of each model, and where to park
        # the source object so the models do not all land on top of each other.
        self.frequencies = frequencies
        self.offsets     = offsets
        self.collection  = collection
        self.counted     = set()
        self.placed      = 0
        self.pending     = len(assetIds)
        self.lastActivity = time.monotonic()


# presetToken -> PresetSession. Only ever touched from the import timer, i.e. the main thread.
_sessions: dict[str, PresetSession] = {}


def clearSessions():
    """Drop every in-flight session (a .blend is being replaced, or the addon is unregistering).

    A session holds references to objects in the file that is going away.
    """
    _sessions.clear()


def isPresetModel(settings) -> bool:
    """True when this import is one of the models a Scatter preset references."""
    return str(getattr(settings, 'setInstanceToken', '')).startswith(TOKEN_PREFIX + ':')


def startScatterPresetImport(settings):
    """Handle a "ScatterPreset" import: read the .mbc, build the scatter, request its models."""
    if getattr(settings, 'setInstanceToken', ''):
        # A preset nested in an Asset Set would have to be placed at the transform the outer
        # manifest asks for, which a scatter carrier cannot represent - its transform moves the
        # whole scattered result. Neither Maya nor 3ds Max supports this either.
        debug.reportError(
            f"Chaos Scatter preset: '{settings.assetName}' arrived as a member of another asset. "
            f"Presets can only be imported on their own, and it was skipped."
        )
        return

    from chaos_scatter import backend, lifecycle, preset

    try:
        # The preset's distances are authored in metres; the propgroup holds scene units.
        unitScale = bpy.context.scene.unit_settings.scale_length or 1.0
        plugins, assetIds = backend.getBackend().readPreset(settings.settingsFile, 1.0 / unitScale)
    except Exception as e:
        debug.printExceptionInfo(e, "cosmos_scatter_preset: could not read the preset config")
        debug.reportError(f"Chaos Scatter preset '{settings.assetName}' could not be read")
        return

    scatterAttrs, offsets = _splitRecords(plugins)
    if scatterAttrs is None:
        debug.reportError(f"Chaos Scatter preset '{settings.assetName}' contains no scatter settings")
        return

    scatterObj = lifecycle.createScatterObject(bpy.context)
    scatterObj.name = sanitizeDatablockName(settings.assetName) or "ChaosScatter"
    # NOT moved to the drop point: the carrier's transform is applied to the whole scattered
    # result, so an offset carrier would lift every instance off its target surface.

    cs = scatterObj.chaos_scatter
    applied, notes = preset.applyPresetParams(cs, scatterAttrs)
    for note in notes:
        debug.printWarning(f"Chaos Scatter preset '{settings.assetName}': {note}")

    _assignTarget(cs, settings, presetName=settings.assetName)

    if not assetIds:
        debug.printInfo(f"Chaos Scatter preset '{settings.assetName}': {applied} setting(s), no models")
        _selectAndFinish(scatterObj, settings.assetName)
        return

    frequencies = _modelFrequencies(scatterAttrs, len(assetIds))
    dropOffset = Vector((settings.worldX, settings.worldY, settings.worldZ)) \
        if getattr(settings, 'hasDropCoords', False) else Vector((0.0, 0.0, 0.0))
    # The offsets are already in scene units - readScatterPreset applied unitRescale to the whole
    # config, which is also why the parameters above are written through unconverted.
    offsets = [dropOffset + offset for offset in offsets]

    presetToken = uuid.uuid4().hex
    collection = _makeCollection(settings.assetName)
    _sessions[presetToken] = PresetSession(settings.assetName, scatterObj.session_uid, assetIds,
                                           frequencies, offsets, collection)

    debug.printInfo(f"Chaos Scatter preset '{settings.assetName}': {applied} setting(s), "
                    f"importing {len(assetIds)} model(s)")

    # Triplanar mapping and real-world scale are material options; a preset references models.
    vray.importCosmosAsset(
        assetIds,
        False,
        False,
        instanceTokens = [f"{TOKEN_PREFIX}:{presetToken}:{i}" for i in range(len(assetIds))]
    )


def notePresetModel(settings, obj: bpy.types.Object | None) -> bool:
    """Account for one imported model, adding it to its scatter's model list.

    Returns False when the session is already gone, i.e. the model arrived after the timeout. The
    caller must then treat it as a plain import, because nothing will place it.
    """
    session, index = _resolve(settings.setInstanceToken)
    if session is None:
        return False

    session.lastActivity = time.monotonic()

    if index in session.counted:
        # The server reports a failure from both the download and the import stage of the same
        # entry, so the same model can be reported twice.
        return True
    session.counted.add(index)
    session.pending -= 1

    if obj is None:
        return True

    scatterObj = _liveObject(session.scatterUid)
    if scatterObj is None:
        # Undone or deleted while the models were still arriving. Nothing will place this one, so
        # it is a plain import from here on - and gets its own undo step from the caller.
        return False

    # Park the source object where the preset asks, so several models do not overlap. Its transform
    # deliberately does NOT affect the scatter - the models are pure geometry sources.
    obj.location = session.offsets[index]
    _moveToCollection(obj, session.collection)

    cs = scatterObj.chaos_scatter
    item = cs.models.add()
    item.object = obj
    item.frequency = session.frequencies[index]
    session.placed += 1

    return True


def tickSessions():
    """Finalize the presets whose models have all arrived, and give up on the quiet ones."""
    now = time.monotonic()
    for presetToken, session in list(_sessions.items()):
        if session.pending <= 0:
            _finalize(presetToken)
        elif now - session.lastActivity > _SESSION_TIMEOUT_SECONDS:
            debug.reportError(
                f"Chaos Scatter preset '{session.presetName}': {session.pending} model(s) never "
                f"arrived, finalizing without them"
            )
            _finalize(presetToken)


def _splitRecords(plugins):
    """Split the server's plugin records into (GeomScatter attributes, model position offsets).

    The records arrive as the filled GeomScatter followed by one placeholder Node per referenced
    model, in the order the preset lists them.
    """
    scatterAttrs = None
    offsets = []
    for _name, pluginType, attrs in plugins:
        if pluginType == 'GeomScatter':
            scatterAttrs = attrs
        elif pluginType == 'Node':
            # A Transform arrives as (matrix, offset).
            offsets.append(Vector(attrs['transform'][1]))
    return scatterAttrs, offsets


def _modelFrequencies(scatterAttrs: dict, count: int) -> list[float]:
    """The authored per-model frequencies, one per referenced model.

    The scatter core sizes model_frequencies by the number of links in the preset but only fills
    the entries whose model was accepted, so the two only line up when every link produced one.
    """
    frequencies = scatterAttrs.get('model_frequencies') or []
    if len(frequencies) != count:
        debug.printDebug(f"Chaos Scatter preset: {len(frequencies)} frequency value(s) for "
                         f"{count} model(s), falling back to equal frequencies")
        return [1.0] * count
    return [float(f) for f in frequencies]


def _assignTarget(cs, settings, presetName: str):
    """Point the scatter at what the user dropped it on.

    The drop target wins over the selection: createScatterObject has already added the selected
    geometry objects, but a drag-and-drop onto a specific surface is the more explicit intent.
    """
    from chaos_scatter import resolve, utils

    targetName = getattr(settings, 'dropTargetObject', '')
    dropTarget = bpy.data.objects.get(targetName) if targetName else None

    if dropTarget is not None and dropTarget.type in resolve.TARGET_TYPES \
            and not utils.isScatterObject(dropTarget):
        cs.targets.clear()
        item = cs.targets.add()
        item.object = dropTarget
        return

    if len(cs.targets) == 0:
        debug.printWarning(
            f"Chaos Scatter preset '{presetName}' was imported without a distribution target. "
            f"Pick one in the Chaos Scatter panel to see the instances."
        )


def _makeCollection(presetName: str):
    """A collection to park the preset's source models in, so they stay out of the way."""
    name = sanitizeDatablockName(presetName) or "Chaos Scatter Preset"
    collection = bpy.data.collections.new(f"{name} Models")
    bpy.context.scene.collection.children.link(collection)
    return collection


def _liveObject(sessionUid: int):
    """The scatter object with this session_uid, or None once it is gone.

    Blender does not remap the object references a Python session holds, so one taken before an
    undo must never be dereferenced - the same reason chaos_scatter.recompute keys on uids.
    """
    return next((obj for obj in bpy.data.objects if obj.session_uid == sessionUid), None)


def _moveToCollection(obj, collection):
    for linked in list(obj.users_collection):
        linked.objects.unlink(obj)
    collection.objects.link(obj)


def _resolve(instanceToken: str):
    """Look up the (session, model index) a token refers to. (None, -1) means the session is gone.

    The token is the one startScatterPresetImport minted, echoed back by the server unchanged.
    """
    _prefix, presetToken, index = str(instanceToken).split(':')
    session = _sessions.get(presetToken)
    if session is None:
        return None, -1   # Already finalized - ignore the latecomer.
    return session, int(index)


def _finalize(presetToken: str):
    """Recompute the preview and close the session."""
    session = _sessions.pop(presetToken, None)
    if session is None:
        return

    debug.printInfo(f"Chaos Scatter preset '{session.presetName}': "
                    f"{session.placed} of {len(session.assetIds)} model(s) placed")

    scatterObj = _liveObject(session.scatterUid)
    if scatterObj is None:
        # Undone or deleted mid-import. The models that did arrive are loose objects in the scene,
        # and their own undo steps were suppressed - push the one the scatter would have pushed.
        if session.placed:
            bpy.ops.ed.undo_push(message=f"Import Cosmos Scatter Preset {session.presetName}")
        return

    _selectAndFinish(scatterObj, session.presetName)


def _selectAndFinish(scatterObj, presetName: str):
    """Kick off the preview and collapse the whole import into one undo step."""
    from chaos_scatter import recompute

    recompute.refresh(scatterObj)
    # Each model selected itself as it was imported; leave the scatter selected instead.
    selectObject(scatterObj)
    bpy.ops.ed.undo_push(message=f"Import Cosmos Scatter Preset {presetName}")
