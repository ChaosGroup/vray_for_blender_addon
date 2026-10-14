# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

""" Turns the keyframes of imported .vrscene parameters into Blender fcurves.

    The import records where each animated attribute landed (AnimTargets) and this module keys those
    targets afterwards. Curves are LINEAR with CONSTANT extrapolation, which is how V-Ray interpolates.
"""

import bisect
import math

import bpy
import mathutils

from dataclasses import dataclass, field
from typing import Any

from vray_blender import debug
from vray_blender.lib import blender_utils


@dataclass(slots=True)
class AnimTarget:
    """ Where one animated attribute landed. """
    owner: Any                      # the bpy_struct written: prop group, socket or datablock
    prop: str                       # property name on `owner`
    pluginName: str                 # source plugin, for the AnimatedKeys lookup
    attrName: str                   # source attribute, before any remap
    written: Any = None             # the static value written, compared against key 0
    interp: str = 'LINEAR'
    convert: Any = None             # raw key value -> written value
    dataPath: str = None            # explicit RNA path, for owners that cannot build their own
    bake: bool = False              # also key every whole frame between the keys


@dataclass(slots=True)
class AnimTargets:
    """ Every animated attribute the import wrote, in write order. Holds only attributes with keys. """
    targets: list = field(default_factory=list)

    def record(self, owner, prop: str, pluginName: str, attrName: str,
               written=None, interp: str = 'LINEAR', convert=None, dataPath: str = None,
               bake: bool = False):
        self.targets.append(
            AnimTarget(owner, prop, pluginName, attrName, written, interp, convert, dataPath, bake))


####################################################################################################
# fcurve authoring
####################################################################################################

_INTERP_CODES: dict[str, int] = {}


def _interpCode(name: str) -> int:
    """ The integer foreach_set('interpolation', ...) takes for an interpolation name. """
    if not _INTERP_CODES:
        items = bpy.types.Keyframe.bl_rna.properties['interpolation'].enum_items
        _INTERP_CODES.update({item.identifier: item.value for item in items})
    return _INTERP_CODES[name]


def _ensureFCurves(idBlock, ledger):
    """ The fcurve collection of idBlock, creating the action (and on 5.0+ the slot and channelbag)
        if needed. None when the datablock cannot hold animation.
    """
    animData = idBlock.animation_data or idBlock.animation_data_create()
    if animData is None:
        return None
    if animData.action is None:
        action = bpy.data.actions.new(name=f"{idBlock.name}Action")
        if ledger is not None:
            ledger.track(action)
        animData.action = action

    fcurves = blender_utils.getFCurves(idBlock, ensure=True)
    # An empty channelbag's fcurves is falsy but valid. Test for the API, not for truthiness.
    return fcurves if hasattr(fcurves, 'new') else None


def _writeCurve(fcurves, dataPath: str, index: int, samples: list, interp: str) -> bool:
    """ Write one fcurve from [(frame, value), ...]. False when the channel is already animated. """
    existing = fcurves.find(dataPath, index=max(index, 0))
    if existing is not None:
        return False

    curve = fcurves.new(dataPath, index=max(index, 0))

    flat = []
    for frame, value in samples:
        flat.append(float(frame))
        flat.append(float(value))

    curve.keyframe_points.add(len(samples))
    curve.keyframe_points.foreach_set('co', flat)
    curve.keyframe_points.foreach_set('interpolation', [_interpCode(interp)] * len(samples))
    curve.extrapolation = 'CONSTANT'
    # Sorts the keys and recomputes handles. Keep after the last foreach_set.
    curve.update()
    return True


def _componentCount(value) -> int:
    """ 0 for a scalar property, else the number of fcurves it needs. """
    if hasattr(value, '__len__') and not isinstance(value, str):
        return len(value)
    return 0


def _coerce(value, count: int):
    """ A raw key value shaped like the static write: alpha dropped, scalars broadcast, RGB padded with
        an opaque alpha. """
    if count == 0:
        return float(value[0]) if hasattr(value, '__len__') and not isinstance(value, str) else float(value)

    if not hasattr(value, '__len__') or isinstance(value, str):
        return [float(value)] * count
    if len(value) >= count:
        return [float(component) for component in value[:count]]
    padded = [float(component) for component in value]
    return padded + [1.0] * (count - len(padded))


def writeAnimation(animTargets: AnimTargets, keysByAttr: dict, framesPerSecond: float,
                   ledger=None, stats=None, yieldEvery: int = 8):
    """ Key every recorded target. Yields the fraction done.

        `keysByAttr` maps (pluginName, attrName) to [(vrayTime, value), ...].
    """
    total = max(len(animTargets.targets), 1)

    for index, target in enumerate(animTargets.targets):
        samples = keysByAttr.get((target.pluginName, target.attrName))
        if samples:
            try:
                _keyTarget(target, samples, framesPerSecond, ledger, stats)
            except Exception as exc:
                debug.printWarning(f"animation: {target.pluginName}.{target.attrName}: {exc}")
                if stats is not None:
                    stats.skipped['animated attr (failed)'] += 1

        if (index % yieldEvery) == 0:
            yield index / total

    yield 1.0


def _keyTarget(target: AnimTarget, samples: list, framesPerSecond: float, ledger, stats):
    owner = target.owner
    idBlock = getattr(owner, 'id_data', None)
    if idBlock is None:
        return

    dataPath = target.dataPath
    if dataPath is None:
        try:
            dataPath = owner.path_from_id(target.prop)
        except (ValueError, TypeError):
            if stats is not None:
                stats.skipped['animated attr (no data path)'] += 1
            return

    count = _componentCount(getattr(owner, target.prop))
    if target.bake:
        samples = _wholeFrameSamples(samples, framesPerSecond)
    converted = [(time * framesPerSecond,
                  _coerce(target.convert(value) if target.convert else value, count))
                 for time, value in samples]

    # Key 0 must match the static value. A mismatch means the import rewrote the value on the way in.
    if target.written is not None and not _matches(target.written, converted[0][1], count):
        if stats is not None:
            stats.skipped['animated attr (value rewritten on import)'] += 1
        return

    fcurves = _ensureFCurves(idBlock, ledger)
    if fcurves is None:
        if stats is not None:
            stats.skipped['animated attr (datablock takes no animation)'] += 1
        return

    if count == 0:
        _writeCurve(fcurves, dataPath, 0, [(f, v) for f, v in converted], target.interp)
    else:
        for component in range(count):
            _writeCurve(fcurves, dataPath, component,
                        [(f, v[component]) for f, v in converted], target.interp)


def _wholeFrameTimes(samples: list, framesPerSecond: float) -> set:
    """ The V-Ray time of every whole frame from the first key to the last. """
    first = math.ceil(samples[0][0] * framesPerSecond)
    last = math.floor(samples[-1][0] * framesPerSecond)
    return {frame / framesPerSecond for frame in range(first, last + 1)}


def _wholeFrameSamples(samples: list, framesPerSecond: float) -> list:
    """ The scalar keys plus a linearly interpolated sample at every whole frame between them. """
    times = sorted({time for time, _ in samples} | _wholeFrameTimes(samples, framesPerSecond))
    keyTimes = [time for time, _ in samples]

    baked = []
    for time in times:
        upper = min(bisect.bisect_left(keyTimes, time), len(samples) - 1)
        upperTime, upperValue = samples[upper]
        if upper == 0 or upperTime == time:
            baked.append((time, upperValue))
            continue
        lowerTime, lowerValue = samples[upper - 1]
        ratio = (time - lowerTime) / (upperTime - lowerTime)
        baked.append((time, lowerValue + (upperValue - lowerValue) * ratio))
    return baked


def _matches(written, keyValue, count: int, tol: float = 1e-4) -> bool:
    """ Whether the first key agrees with the static value. True for values that are not numbers. """
    try:
        if count == 0:
            return abs(float(written) - float(keyValue)) <= tol * max(1.0, abs(float(written)))
        return all(abs(float(a) - float(b)) <= tol * max(1.0, abs(float(a)))
                   for a, b in zip(written, keyValue))
    except (TypeError, ValueError):
        return True


def collectKeys(vrsceneDict: list) -> dict:
    """ The dict's AnimatedKeys as {(pluginName, attrName): [(time, value), ...]}. """
    keys = {}
    for pluginDesc in vrsceneDict:
        animated = pluginDesc.get('AnimatedKeys')
        if not animated:
            continue
        name = pluginDesc.get('Name')
        for attrName, samples in animated.items():
            keys[(name, attrName)] = samples
    return keys


####################################################################################################
# Transforms
#
# A .vrscene Node transform is a world matrix. A parented Blender object keys its local, so each key
# is inverse(parent world at t) @ own world at t, on the union of its own and its ancestors' key times.
####################################################################################################

@dataclass(slots=True)
class TransformSource:
    """ The plugin and parameter an object's world matrix came from. """
    pluginName: str
    paramName: str = 'transform'
    post: mathutils.Matrix = None   # right-multiplied onto each sampled world, e.g. T(pivot)


def _interpolateTransform(lower, upper, ratio):
    """ The matrix between two world matrices: slerped rotation, lerped location and scale. """
    lowerLoc, lowerQuat, lowerScale = lower.decompose()
    upperLoc, upperQuat, upperScale = upper.decompose()

    loc = lowerLoc.lerp(upperLoc, ratio)
    quat = lowerQuat.slerp(upperQuat, ratio)
    scale = lowerScale.lerp(upperScale, ratio)

    return (mathutils.Matrix.Translation(loc)
            @ quat.to_matrix().to_4x4()
            @ mathutils.Matrix.Diagonal(scale).to_4x4())


def _sampleWorld(samples, time, toWorld):
    """ The source world matrix at `time`, held at the first and last key outside the range. """
    if time <= samples[0][0]:
        return toWorld(samples[0][1])
    if time >= samples[-1][0]:
        return toWorld(samples[-1][1])

    for index in range(1, len(samples)):
        upperTime = samples[index][0]
        if time <= upperTime:
            lowerTime = samples[index - 1][0]
            span = upperTime - lowerTime
            ratio = 0.0 if span <= 0.0 else (time - lowerTime) / span
            return _interpolateTransform(toWorld(samples[index - 1][1]),
                                         toWorld(samples[index][1]), ratio)
    return toWorld(samples[-1][1])


class _TransformContext:
    """ Shared state of the hierarchy walk. """

    def __init__(self, sources: dict, keysByAttr: dict, toWorld, framesPerSecond: float,
                 staticWorlds: dict):
        self.sources = sources
        self.keysByAttr = keysByAttr
        self.toWorld = toWorld
        self.framesPerSecond = framesPerSecond
        self.staticWorlds = staticWorlds

    def samplesFor(self, obj):
        source = self.sources.get(obj)
        return None if source is None else self.keysByAttr.get((source.pluginName, source.paramName))

    def worldAt(self, obj, time):
        """ obj's world matrix at a V-Ray time. """
        if (samples := self.samplesFor(obj)) is not None:
            world = _sampleWorld(samples, time, self.toWorld)
            post = self.sources[obj].post
            return world @ post if post is not None else world
        # A static object keeps its world matrix while its parent moves. Do not compose it through the parent.
        if (static := self.staticWorlds.get(obj)) is not None:
            return static.copy()
        if obj.parent is None:
            return obj.matrix_basis.copy()
        # A grouping Empty invented for a path level, which follows its parent.
        return self.worldAt(obj.parent, time) @ obj.matrix_parent_inverse @ obj.matrix_basis


def _decomposeTrack(samples):
    """ [(frame, Matrix)] -> [(frame, loc, quat, scale)], with each quaternion flipped to the short way
        round and a mirror kept on the same axis on every key. """
    mirrored = samples[0][1].to_3x3().determinant() < 0.0
    flip = mathutils.Matrix.Diagonal((1.0, 1.0, -1.0, 1.0)) if mirrored else None

    track = []
    previousQuat = None
    for frame, matrix in samples:
        loc, quat, scale = (matrix @ flip if flip else matrix).decompose()
        if mirrored:
            scale.z = -scale.z
        if previousQuat is not None and quat.dot(previousQuat) < 0.0:
            quat.negate()
        previousQuat = quat
        track.append((frame, loc, quat, scale))
    return track


def _writeTransformTrack(obj, track, ledger) -> bool:
    """ Write the location, rotation_quaternion and scale curves. """
    fcurves = _ensureFCurves(obj, ledger)
    if fcurves is None:
        return False

    obj.rotation_mode = 'QUATERNION'

    channels = (
        ('location', 3, 1),
        ('rotation_quaternion', 4, 2),
        ('scale', 3, 3),
    )
    for dataPath, count, entryIndex in channels:
        for component in range(count):
            _writeCurve(fcurves, dataPath, component,
                        [(entry[0], entry[entryIndex][component]) for entry in track], 'LINEAR')
    return True


def animateTransforms(roots, sources: dict, keysByAttr: dict, toWorld, framesPerSecond: float,
                      staticWorlds: dict, ledger=None, stats=None, hasShear=None) -> int:
    """ Key the transform of every object under `roots` that moves or has a moving ancestor.
        Returns the number of objects keyed.

        `sources` maps object -> TransformSource. `toWorld` turns a raw .vrscene transform into a Blender
        world matrix. `staticWorlds` holds the world matrix each parented object had before parenting.
    """
    context = _TransformContext(sources, keysByAttr, toWorld, framesPerSecond, staticWorlds)
    keyed = 0

    def walk(obj, inheritedTimes):
        nonlocal keyed
        own = context.samplesFor(obj)
        times = set(inheritedTimes)
        if own is not None:
            times.update(time for time, _ in own)
            if sources[obj].post is not None:
                times.update(_wholeFrameTimes(own, framesPerSecond))

        # A grouping Empty has neither keys nor a world of its own, so its local never changes.
        hasOwnWorld = own is not None or obj in staticWorlds
        if len(times) > 1 and hasOwnWorld:
            keyed += _keyOneTransform(obj, sorted(times), context, ledger, stats, hasShear)

        for child in obj.children:
            walk(child, times)

    for root in roots:
        walk(root, frozenset())

    return keyed


def _keyOneTransform(obj, times, context, ledger, stats, hasShear) -> int:
    parent = obj.parent
    matrices = []
    for time in times:
        world = context.worldAt(obj, time)
        if parent is not None:
            world = context.worldAt(parent, time).inverted_safe() @ world
        matrices.append((time * context.framesPerSecond, world))

    if hasShear is not None and any(hasShear(matrix.to_3x3()) for _, matrix in matrices):
        debug.printWarning(f"animation: object '{obj.name}' has a sheared animated transform; "
                           "keeping its static pose")
        if stats is not None:
            stats.skipped['animated sheared transform'] += 1
        return 0

    return 1 if _writeTransformTrack(obj, _decomposeTrack(matrices), ledger) else 0
