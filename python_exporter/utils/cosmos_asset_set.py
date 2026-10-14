# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Chaos Cosmos Asset Set import.
#
# An Asset Set is a Cosmos package that contains no geometry of its own - its only
# payload is a settings.json manifest listing other packages, where each of them goes,
# and how they are parented. The server detects the set by its Cosmos tag and forwards
# the manifest path to us as an "AssetSet" import; everything else happens here.
#
# Flow
# ----
#   1. assetImportTimerFunction gets an "AssetSet" item and calls startAssetSetImport().
#      We parse the manifest, open a session, and ask the Cosmos client to import every
#      member, passing an opaque token per member.
#   2. Each member comes back through the normal Cosmos import path as its own queue item,
#      carrying its token in settings.setInstanceToken. noteSetInstance() places it at the
#      transform the manifest asked for.
#   3. Once every member is accounted for, tickSessions() calls _finalize(), which rebuilds
#      the manifest's parent/child relationships, parents what is left to a set root Empty,
#      and pushes a single undo step for the whole set.
#
# Members are imported separately, not instanced - the same as V-Ray for Maya and 3ds Max.

import json
import math
import os
import time
import uuid

import bpy
from mathutils import Euler, Matrix, Vector

from vray_blender import debug
from vray_blender.lib.blender_utils import getSceneMetersPerUnit, selectObject
from vray_blender.lib.lib_utils import sanitizeDatablockName
from vray_blender.utils import cosmos_notify
from vray_blender.bin import VRayBlenderLib as vray


# Highest settings.json spec we know how to read. A newer manifest is imported anyway -
# the format only ever grows - but we warn, matching Maya and 3ds Max.
SPEC_VERSION = 1

# Manifest positions are in centimeters.
_CM_TO_METERS = 0.01

# Seconds of complete silence after which a session gives up waiting and finalizes with
# whatever arrived. The server reports failed members explicitly, so this only covers a
# member that produced no reply at all. Measured in wall-clock rather than timer ticks
# because the import timer changes its interval while it has a backlog to drain.
_SESSION_TIMEOUT_SECONDS = 300.0


class SetInstance:
    """One placement of one member package, as described by the manifest."""

    __slots__ = ('packageId', 'instanceName', 'parentName', 'position', 'rotation', 'scale')

    def __init__(self, packageId, instanceName, parentName, position, rotation, scale):
        self.packageId    = packageId
        self.instanceName = instanceName
        # Name of the instance this one hangs off, or '' when it sits directly in the set.
        self.parentName   = parentName
        # Centimeters, Z-up, absolute (NOT relative to parentName).
        self.position     = position
        # Euler XYZ in degrees.
        self.rotation     = rotation
        self.scale        = scale


class AssetSetSession:
    """Tracks one in-flight set import until every member has been accounted for."""

    __slots__ = ('setName', 'dropOffset', 'instances', 'indexByName', 'objects', 'counted',
                 'pending', 'failed', 'lastActivity')

    def __init__(self, setName: str, dropOffset: Vector, instances: list[SetInstance]):
        self.setName    = setName
        self.dropOffset = dropOffset
        # Indexed by the integer part of the instance token.
        self.instances  = instances
        # instanceName -> index into 'instances', for resolving the manifest's parent links.
        # First occurrence wins; nothing in the format guarantees names are unique.
        self.indexByName = {}
        for index, instance in enumerate(instances):
            self.indexByName.setdefault(instance.instanceName, index)
        # Instance index -> the object created for it. Keyed by index rather than by name so
        # that two instances sharing a name do not overwrite each other. Missing for members
        # that produced no object (a material) or that failed to import.
        self.objects    = {}
        # Instance indices already accounted for, so a member reported twice is counted once.
        self.counted    = set()
        self.pending    = len(instances)
        # Members that were accounted for but never made it into the scene. 'pending' cannot
        # answer that on its own - a failure is counted like any other reply, or the set
        # would never complete - and 'objects' cannot either, because a material member
        # legitimately produces no object.
        self.failed     = 0
        self.lastActivity = time.monotonic()


# setToken -> AssetSetSession. Only ever touched from the import timer, i.e. the main
# thread, so no locking is needed.
_sessions: dict[str, AssetSetSession] = {}


def clearSessions():
    """Drop every in-flight session.

    Called before a .blend is replaced and on addon unregister: a session holds references
    to objects in the file that is going away, and finalizing it afterwards would touch
    freed data.
    """
    _sessions.clear()


def _parentName(hierarchy: str) -> str:
    """Extract the parent instance name from a manifest hierarchy path.

    The path is rooted at "scene" and its last token is the instance itself, so the parent
    is the second-to-last token: "scene/Table001/Vase001" -> "Table001". An instance
    directly under the set ("scene/Table001") has no parent and yields ''.

    Same rule as the 3ds Max and Maya importers.
    """
    parentPath = hierarchy.rpartition('/')[0]
    if not parentPath or parentPath == 'scene':
        return ''
    return parentPath.rpartition('/')[2]


def _readVector(entry: dict, key: str, default: float) -> list[float]:
    """Read a 3-float manifest field, falling back to (default,)*3 when absent or malformed.

    The default matters: a missing 'scale' must read as 1, not 0, or the instance would
    collapse to zero size.
    """
    value = entry.get(key)
    if not isinstance(value, list) or len(value) < 3:
        return [default] * 3
    try:
        return [float(value[i]) for i in range(3)]
    except (TypeError, ValueError):
        return [default] * 3


def parseManifest(filePath: str) -> tuple[list[SetInstance] | None, int]:
    """Parse an Asset Set settings.json into a flat list of instances.

    The manifest is a JSON array: element 0 is the spec-version header, the rest are member
    packages, each with one or more instances. Returns the instances and the number of
    entries that had to be skipped. The instances are None when the manifest could not be
    read at all, which is already reported here - the caller must not report it again, or
    one broken file produces two notifications. An empty list means it was read and asked
    for nothing. Skipped entries are counted rather than reported one by one: the
    notification queue is capped and de-duplicated, so a notification per bad entry would
    crowd out the errors that matter. Maya drops most such entries without a message at all.
    """
    skipped = 0

    if not filePath or not os.path.isfile(filePath):
        cosmos_notify.error(f"Chaos Cosmos Asset Set: settings.json not found: {filePath!r}")
        return None, skipped

    try:
        with open(filePath, 'r', encoding='utf-8') as f:
            root = json.load(f)
    except Exception as e:
        # The traceback belongs in the console; the browser gets the short version.
        debug.printExceptionInfo(e, f"Chaos Cosmos Asset Set: could not read {filePath!r}")
        cosmos_notify.post(cosmos_notify.CosmosLevel.Error,
                           "Chaos Cosmos Asset Set: its settings.json could not be read")
        return None, skipped

    if not isinstance(root, list) or not root:
        cosmos_notify.error(f"Chaos Cosmos Asset Set: malformed settings.json: {filePath!r}")
        return None, skipped

    # Element 0 is the spec-version header, members start at index 1. Only skip it when it
    # really is the header - a manifest that omits it must not lose its first member.
    hasHeader = isinstance(root[0], dict) and 'spec_version' in root[0]
    if hasHeader:
        specVersion = root[0].get('spec_version')
        if isinstance(specVersion, int) and specVersion > SPEC_VERSION:
            debug.printWarning(
                f"Chaos Cosmos Asset Set: settings.json has spec_version={specVersion}, "
                f"newer than the supported {SPEC_VERSION}. Importing anyway."
            )

    instances = []
    for member in (root[1:] if hasHeader else root):
        if not isinstance(member, dict):
            continue
        packageId = member.get('package_id')
        memberInstances = member.get('instances')
        if not packageId or not isinstance(memberInstances, list):
            # Same as Maya, which also drops such a member. Say so, or the set silently
            # comes up short with nothing to explain the missing pieces.
            debug.printWarning(
                f"Chaos Cosmos Asset Set: member {member.get('package_name', '?')!r} has no "
                f"'package_id' or no instance list, skipping it"
            )
            skipped += 1
            continue

        for entry in memberInstances:
            if not isinstance(entry, dict):
                continue
            instanceName = entry.get('instance_name')
            if not instanceName:
                debug.printWarning(
                    f"Chaos Cosmos Asset Set: instance of package {packageId!r} has no "
                    f"'instance_name', skipping it"
                )
                skipped += 1
                continue

            rotation = _readVector(entry, 'rotation', 0.0)
            scale    = _readVector(entry, 'scale', 1.0)

            # translate * scale * rotate shears when a rotated instance is scaled
            # non-uniformly, and Blender's loc/rot/scale decomposition drops shear on
            # assignment, so such an instance would land subtly wrong. Spec 1 content does
            # not do this; say so rather than fail silently if it ever does.
            if any(rotation) and len(set(scale)) > 1:
                debug.printWarning(
                    f"Chaos Cosmos Asset Set: instance {instanceName!r} combines a rotation "
                    f"with a non-uniform scale, which Blender cannot represent exactly. "
                    f"Its shear will be dropped."
                )

            instances.append(SetInstance(
                packageId    = str(packageId),
                instanceName = str(instanceName),
                parentName   = _parentName(str(entry.get('hierarchy', 'scene'))),
                position     = _readVector(entry, 'position', 0.0),
                rotation     = rotation,
                scale        = scale,
            ))

    return instances, skipped


def instanceMatrix(instance: SetInstance, dropOffset: Vector, unitScale: float) -> Matrix:
    """World matrix for a set instance, in Blender units.

    'unitScale' is the scene's meters per Blender unit (getSceneMetersPerUnit), passed in rather than read from the
    context so the composition can be tested without a scene.

    Cosmos manifests are Z-up right-handed, the same as Blender, so there is no axis
    conversion - only centimeters to scene units. (Maya has to apply a -90 degree X
    rotation here; 3ds Max, also Z-up, applies none either.)

    Composed as translate * scale * rotate to match both reference importers. That differs
    from Blender's own loc/rot/scale order only for a non-uniform scale on a rotated
    instance, which parseManifest warns about.
    """
    cmToScene = _CM_TO_METERS / unitScale
    location  = Vector(instance.position) * cmToScene + dropOffset
    rotation  = Euler([math.radians(r) for r in instance.rotation], 'XYZ')

    return (Matrix.Translation(location)
            @ Matrix.Diagonal(Vector(instance.scale)).to_4x4()
            @ rotation.to_matrix().to_4x4())


def startAssetSetImport(settings):
    """Handle an "AssetSet" import: parse the manifest and request every member.

    'settings' is the CosmosAssetSettings of the set package itself. Its drop coordinates,
    when present, are where the user dropped the set and become the origin every member is
    placed relative to; a browser Import-button import has none and places at the origin.
    """
    if isSetInstance(settings):
        # Neither Maya nor 3ds Max supports this either. Importing it anyway would place the
        # inner set at the world origin instead of at the transform the outer manifest asks
        # for, which is worse than not importing it.
        cosmos_notify.reportError(
            f"Chaos Cosmos Asset Set: member '{settings.assetName}' is itself a set. "
            f"Nested sets are not supported and it was skipped."
        )
        return

    instances, skipped = parseManifest(settings.settingsFile)
    if instances is None:
        # The manifest could not be read, and parseManifest() has already said so.
        return
    if not instances:
        cosmos_notify.reportError(
            f"Chaos Cosmos Asset Set '{settings.assetName}' contains no instances to import")
        return

    if skipped:
        # One message for all of them - see parseManifest().
        cosmos_notify.warning(
            f"Chaos Cosmos Asset Set '{settings.assetName}': {skipped} manifest "
            f"entry(s) were skipped. See the log for the reasons."
        )

    dropOffset = Vector((settings.worldX, settings.worldY, settings.worldZ)) \
        if getattr(settings, 'hasDropCoords', False) else Vector((0.0, 0.0, 0.0))

    setToken = uuid.uuid4().hex
    _sessions[setToken] = AssetSetSession(settings.assetName, dropOffset, instances)

    debug.printInfo(
        f"Chaos Cosmos Asset Set '{settings.assetName}': importing {len(instances)} instance(s)")

    # One request for the whole set. Each member is identified by its position in the
    # manifest, which is what comes back to us as settings.setInstanceToken. Triplanar and
    # real-world scale are off: they are material options and a set references models.
    vray.importCosmosAsset(
        [i.packageId for i in instances],
        False,
        False,
        instanceTokens = [f"{setToken}:{i}" for i in range(len(instances))]
    )


def noteSetInstance(settings, obj: bpy.types.Object | None, imported: bool = True) -> bool:
    """Account for one imported set member, placing it when it produced an object.

    Called for every import that carries a setInstanceToken, whatever its asset type - a
    member that produces no object (a material) still has to be counted, or the set would
    never reach completion and never get parented.

    'imported' is False for a member that never made it into the scene. It is counted all
    the same, but separately, so that the set does not report a success over the failures
    the user has just been shown.

    Returns False when the member's set is already gone, i.e. it arrived after the session
    timed out. The caller must then treat it as a plain import, because nothing will place
    it or fold it into the set's undo step.
    """
    session, instance, index = _resolve(settings.setInstanceToken)
    if session is None:
        return False

    if index >= 0:
        if index in session.counted:
            # One member can be reported more than once: the server reports a failure from
            # both the download and the import stage of the same entry. Counting it twice
            # would drive 'pending' to zero early and finalize the set without the members
            # still in flight.
            session.lastActivity = time.monotonic()
            return True
        session.counted.add(index)

    if instance is not None and obj is not None:
        unitScale = getSceneMetersPerUnit(bpy.context)
        obj.matrix_world = instanceMatrix(instance, session.dropOffset, unitScale)
        session.objects[index] = obj

    if not imported:
        session.failed += 1

    session.pending -= 1
    session.lastActivity = time.monotonic()
    return True


def isSetInstance(settings) -> bool:
    """True when this import is a member of an Asset Set."""
    return bool(getattr(settings, 'setInstanceToken', ''))


def tickSessions():
    """Finalize the sets that are done, and give up on the ones that have gone quiet.

    Called once per import-timer tick, after the tick has finished importing. Finalizing
    here rather than from noteSetInstance() means a set is always closed with its last
    member fully built, and outside the drop-placement context the member was imported in.

    The timeout covers a member that produced no reply at all; a member the server could
    not import reports itself and is counted immediately.
    """
    now = time.monotonic()
    for setToken, session in list(_sessions.items()):
        if session.pending <= 0:
            _finalize(setToken)
        elif now - session.lastActivity > _SESSION_TIMEOUT_SECONDS:
            # Logged here, shown to the user by _finalize - which knows whether anything
            # arrived at all, and would otherwise report a success right on top of this.
            debug.printError(
                f"Chaos Cosmos Asset Set '{session.setName}': {session.pending} instance(s) "
                f"never arrived, finalizing the set without them"
            )
            _finalize(setToken, timedOut=True)


def _resolve(instanceToken: str):
    """Look up the (session, instance, index) a token refers to.

    (None, None, -1) means the set is gone. A live session with an unusable index still
    returns the session, so the member is counted and the set can reach completion.
    """
    setToken, _, indexStr = instanceToken.partition(':')
    session = _sessions.get(setToken)
    if session is None:
        # Already finalized (e.g. by the idle timeout) - the set is done, ignore the latecomer.
        return None, None, -1

    try:
        index = int(indexStr)
    except ValueError:
        index = -1

    if 0 <= index < len(session.instances):
        return session, session.instances[index], index

    debug.printError(f"Chaos Cosmos Asset Set: unrecognized instance token {instanceToken!r}")
    return session, None, -1


def _parentKeepTransform(child: bpy.types.Object, parent: bpy.types.Object):
    """Parent child to parent without moving it.

    Manifest positions are absolute, so every member is already in the right place by the
    time we parent it. matrix_parent_inverse cancels out the parent's transform; a bare
    'child.parent = parent' would shift the child instead. Equivalent to Maya's
    'parent -absolute' and Max's AttachChild(keepTM).
    """
    child.parent = parent
    child.matrix_parent_inverse = parent.matrix_world.inverted()


def _finalize(setToken: str, timedOut: bool = False):
    """Rebuild the manifest hierarchy, add the set root, and close the session.

    'timedOut' says the set gave up waiting, so the result is reported as incomplete
    rather than as a success.
    """
    session = _sessions.pop(setToken, None)
    if session is None:
        return

    # The members were positioned by assigning matrix_world without a depsgraph
    # evaluation in between, so flush those writes before reading matrix_world back
    # to compute the parent inverses.
    bpy.context.view_layer.update()

    # Members whose parent did not import fall back to the top level, same as Maya and Max.
    # What happens to the top level differs: they get parented to a set root Empty below,
    # which neither reference creates.
    topLevel = []
    for index, instance in enumerate(session.instances):
        obj = session.objects.get(index)
        if obj is None:
            continue
        parentIndex = session.indexByName.get(instance.parentName, -1) if instance.parentName else -1
        parentObj = session.objects.get(parentIndex)
        if parentObj is not None and parentObj is not obj:
            _parentKeepTransform(obj, parentObj)
        else:
            topLevel.append(obj)

    if topLevel:
        setRoot = bpy.data.objects.new(sanitizeDatablockName(session.setName) or "Cosmos Set", None)
        setRoot.empty_display_type = 'PLAIN_AXES'
        setRoot.location = session.dropOffset
        bpy.context.collection.objects.link(setRoot)
        bpy.context.view_layer.update()

        for obj in topLevel:
            _parentKeepTransform(obj, setRoot)

        # Leave the set root as the selection so the user can immediately move the whole set.
        # Each member selected itself as it was imported, so without this the last member to
        # arrive - an arbitrary one - would stay selected.
        selectObject(setRoot)

    # One message for the whole set - its members are not announced individually. The
    # count is of the instances the manifest asked for, NOT of session.objects, which
    # holds only those that produced an object and so is short by every material member
    # even when the set imported perfectly.
    # A member that failed was counted like any other reply, so it has left 'pending' - it
    # is missing from the scene all the same and must not be reported as a success.
    total = len(session.instances)
    absent = max(session.pending, 0) + session.failed
    if timedOut or absent:
        cosmos_notify.warning(
            f"Chaos Cosmos Asset Set '{session.setName}' was imported without "
            f"{absent} of its {total} instance(s)")
    else:
        cosmos_notify.success(
            f"Successfully imported asset: {session.setName} ({total} instance(s))")

    debug.printInfo(
        f"Chaos Cosmos Asset Set '{session.setName}': placed {len(session.objects)} object(s)")

    if session.objects:
        # One undo step for the whole set. The per-member pushes are suppressed while a set is
        # importing, and timer-driven imports do not push undo on their own, so this collapses
        # the entire set into a single entry.
        bpy.ops.ed.undo_push(message=f"Import Cosmos Set {session.setName}")
