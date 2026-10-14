# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

""" Texture sample tier: texture subgraphs evaluated by V-Ray itself.

    The node exporter exports the subgraph into a dedicated renderer, the AppSDK
    UVTextureSampler samples it over [0,1)^2, and the pixels are content-hash cached.
    No render job, no camera.

    Nothing blocks and nothing polls. Transport is vray.sampleTexture; on an older server
    isAvailable() is False.
"""

import hashlib
import threading
import time
from typing import NamedTuple

import numpy as np

import bpy

from vray_blender import debug
from vray_blender.exporting.tools import getFarNodeLink

# PropGroup properties that never affect a sampled result
_SKIPPED_PROPS = {'rna_type', 'name', 'parent_node_id'}

# node pointer -> (bl_idname, name, digest) of the node's own PROPERTY content. The volatile
# parts (mute, image, ramp, placement matrix) are walked every time, never memoised.
_ownHashes = {}

# Helper nodes that produce a matrix rather than a V-Ray plugin
_PLACEMENT_NODES = ('VRayNodeTransform', 'VRayNodeMatrix')

# Keyed by storeKey = f"{bakeKey}@{resolution}"; the external API takes the plain bakeKey

# storeKey -> _Request
_pending = {}
# storeKey -> float32 ndarray (h, w, 4)
_results = {}
# storeKeys whose sampling failed; never retried within the session
_failed = set()
# storeKey -> GPUTexture, created lazily on bind (GPU context required)
_textures = {}
# storeKey -> _Request.slot, and slot -> its last good storeKey
_keyToSlot = {}
_slotLastGood = {}
_inFlight = None      # _InFlight of the running job
_renderer = 0
_cbSampled = None     # current job's callback (the native call holds its own strong ref)
_pumping = False      # reentrancy guard: _finishJob calls pump which may call _finishJob
_strandedResult = None  # (storeKey, image) the callback thread could not hand to the main thread
_strandedLock = threading.Lock()
_warnedUnavailable = False

_rendererLastUsed = 0.0  # monotonic; when the sampler renderer was last talked to
_timeouts = {}           # storeKey -> consecutive lost replies, to bound the retry

# Capped by BYTES, not count. One result is resolution^2 * 4 channels * 4 bytes.
_RESULT_BYTES_BUDGET = 64 * 1024 * 1024
_JOB_TIMEOUT_S = 30.0

# Retire the sampler renderer after this long unused.
# Must stay under the server's 30s PREVIEW_RENDERER_MAX_IDLE_TIME.
_RENDERER_MAX_IDLE_S = 20.0


class _InFlight(NamedTuple):
    storeKey: str       # f"{bakeKey}@{resolution}" of the running job
    started: float      # time.monotonic() at start; the watchdog times the job out from it


class _TransientJobError(Exception):
    """ Job could not start, from a stale lookup or an unavailable renderer. """


class _ContentError(Exception):
    """ This subgraph cannot be sampled at all. Permanent for the key. """


def _hashPropGroup(pg, digest):
    for prop in pg.bl_rna.properties:
        ident = prop.identifier
        if ident in _SKIPPED_PROPS or prop.type in ('POINTER', 'COLLECTION'):
            continue
        try:
            value = getattr(pg, ident)
        except Exception:
            continue
        if getattr(prop, 'is_array', False):
            try:
                value = tuple(value)
            except TypeError:
                pass
        digest.update(f"{ident}={value!r};".encode())


def _ownHash(node: bpy.types.Node) -> str:
    """ Digest of the node's own propgroup and input socket values. Memoised; see _ownHashes.

        Keep the type and name check on the pointer. A freed node's address can be handed to a different
        node.
    """
    key = node.as_pointer()
    if (held := _ownHashes.get(key)) is not None:
        idname, name, ownHash = held
        if (idname == node.bl_idname) and (name == node.name):
            return ownHash

    digest = hashlib.md5()

    pluginType = getattr(node, 'vray_plugin', None)
    if pluginType and pluginType != 'NONE':
        if (pg := getattr(node, pluginType, None)) is not None:
            _hashPropGroup(pg, digest)

    for sock in node.inputs:
        value = getattr(sock, 'value', None)
        if value is not None:
            try:
                value = tuple(value)
            except TypeError:
                pass
            digest.update(f"{sock.name}={value!r};".encode())

    ownHash = digest.hexdigest()
    _ownHashes[key] = (node.bl_idname, node.name, ownHash)
    return ownHash


def dropOwnHash(node: bpy.types.Node):
    """ Drops one node's memoised digest. """
    _ownHashes.pop(node.as_pointer(), None)


def clearOwnHashes():
    """ Drops every memoised digest. For anything that can free nodes. """
    _ownHashes.clear()


def subtreeHash(vnode: bpy.types.Node) -> str:
    """ Content hash of the subtree rooted at 'vnode', over nodes, values, links, images and ramps. """
    digest = hashlib.md5()
    visited = set()

    def walk(node):
        if node.name in visited:
            return
        visited.add(node.name)
        digest.update(f"{node.bl_idname}|{node.name}|{getattr(node, 'mute', False)};".encode())

        if node.bl_idname == 'VRayNodeMetaImageTexture':
            image = node.texture.image if getattr(node, 'texture', None) else None
            digest.update(f"{image.name}|{image.filepath}".encode() if image else b'noimg')

        if (tex := getattr(node, 'texture', None)) and getattr(tex, 'color_ramp', None):
            ramp = tex.color_ramp
            digest.update(ramp.interpolation.encode())
            for elem in ramp.elements:
                digest.update(f"{elem.position:.6f}{tuple(elem.color)};".encode())

        # Transform helper nodes hash the RESOLVED matrix, not their socket values
        if node.bl_idname in _PLACEMENT_NODES:
            try:
                try:
                    matrix = node.getValue(bpy.context)
                except TypeError:   # VRayNodeMatrix.getValue takes no context
                    matrix = node.getValue()
                digest.update(repr(tuple(tuple(row) for row in matrix)).encode())
            except Exception:
                pass

        digest.update(_ownHash(node).encode())

        for sock in node.inputs:
            if link := getFarNodeLink(sock):
                digest.update(f"{sock.identifier}<{link.from_socket.identifier};".encode())
                walk(link.from_node)

    walk(vnode)
    return digest.hexdigest()


class _Request(NamedTuple):
    ownerKind: str      # 'MTL', 'WORLD', 'LIGHT' or 'OBJECT'; picks the collection to resolve the tree from
    ownerName: str      # the datablock the node's plugins are named under
    groupName: str      # a group inside the owner's tree, or ''
    nodeName: str       # looked up in the group when groupName is set, else in the owner's tree
    attrName: str       # the input socket whose subtree is sampled, or None for the node's OWN output
    resolution: int     # image height in pixels; the width is resolution * aspect
    aspect: float       # width/height of the sampled image, 2.0 for lat-long

    @property
    def slot(self):
        """ What the request samples. A new request supersedes the pending ones for its slot. """
        return (self.ownerName, self.groupName, self.nodeName, self.attrName)


def requestBake(bakeKey: str, materialName: str, nodeName: str, attrName: str):
    """ Record a texture bake request. Flatten-time, main thread, never blocks.

        A new request supersedes the pending ones for its slot (see _Request.slot).
    """
    _requestImpl(bakeKey, 'MTL', materialName, '', nodeName, attrName)


def requestNodeSample(bakeKey: str, ownerKind: str, ownerName: str, groupName: str, nodeName: str,
                      resolution: int, aspect: float = 1.0):
    """ Record a sample of a node's OWN output, at a caller-chosen resolution. Directional textures pass aspect=2.0. """
    _requestImpl(bakeKey, ownerKind, ownerName, groupName, nodeName, None, aspect, resolution)


def _requestImpl(bakeKey, ownerKind, ownerName, groupName, nodeName, attrName, aspect=1.0, resolution=None):
    resolution = _bakeResolution() if resolution is None else resolution
    storeKey = _storeKey(bakeKey, resolution)
    request = _Request(ownerKind, ownerName, groupName, nodeName, attrName, resolution, aspect)
    _keyToSlot[storeKey] = request.slot   # recorded even if already baked or in flight, for the fallback
    # Return before the supersede scan below when the key is already pending
    if storeKey in _pending or storeKey in _results or storeKey in _failed \
            or (_inFlight is not None and _inFlight.storeKey == storeKey):
        return
    for staleKey in [k for k, v in _pending.items() if v.slot == request.slot]:
        del _pending[staleKey]
    _pending[storeKey] = request


def _storeKey(bakeKey: str, resolution: int = None) -> str:
    """ Storage key for a content key at a resolution. """
    return f"{bakeKey}@{_bakeResolution() if resolution is None else resolution}"


def _gpuTextureFor(storeKey: str):
    """ GPUTexture for a store key (cached or built from its baked pixels), or None if nothing is baked for it yet.
        GPU context required.
    """
    import gpu

    if (tex := _textures.get(storeKey)) is not None:
        return tex
    pixels = _results.get(storeKey)
    if pixels is None:
        return None
    try:
        h, w = pixels.shape[0], pixels.shape[1]
        buf = gpu.types.Buffer('FLOAT', w * h * 4, pixels.ravel())
        tex = gpu.types.GPUTexture((w, h), format='RGBA16F', data=buf)
        _textures[storeKey] = tex
        return tex
    except Exception as ex:
        debug.printError(f"Preview bake: texture upload failed: {ex}")
        _failed.add(storeKey)
        _results.pop(storeKey, None)
        return None


def _evictResults():
    """ Drops the oldest results and their GPU textures until the cache is inside its byte budget. Dicts keep
        insertion order, so the first key is the oldest.
    """
    total = sum(p.nbytes for p in _results.values())
    while (len(_results) > 1) and (total > _RESULT_BYTES_BUDGET):
        oldest = next(iter(_results))
        total -= _results.pop(oldest).nbytes
        _textures.pop(oldest, None)
        # _keyToSlot gets an entry per REQUEST; evict it here too
        _keyToSlot.pop(oldest, None)


def textureFor(bakeKey: str, resolution: int = None):
    """ The baked GPUTexture for this content key, or the slot's previous one while a new bake is in flight. Needs a
        GPU context.
    """
    storeKey = _storeKey(bakeKey, resolution)
    if (tex := _gpuTextureFor(storeKey)) is not None:
        return tex
    slot = _keyToSlot.get(storeKey)
    if slot is not None:
        lastKey = _slotLastGood.get(slot)
        if lastKey is not None and lastKey != storeKey:
            return _gpuTextureFor(lastKey)
    return None


def pixelsFor(bakeKey: str, resolution: int = None):
    """ The baked pixels for this content key as a float32 (h, w, 4) array, or None. Needs no GPU context. """
    return _results.get(_storeKey(bakeKey, resolution))


def reset():
    """ Drops all bake state, on a feature toggle or a file load. Releases the renderer and the GPU handles. """
    global _inFlight, _renderer, _cbSampled, _strandedResult
    _pending.clear()
    _results.clear()
    _failed.clear()
    _textures.clear()
    _keyToSlot.clear()
    _slotLastGood.clear()
    _timeouts.clear()
    _ownHashes.clear()
    _inFlight = None
    _cbSampled = None
    with _strandedLock:
        _strandedResult = None
    _releaseRenderer()


def _releaseRenderer():
    """ Drops the sampler renderer; the next job builds a fresh one. """
    global _renderer, _cbSampled

    if _renderer:
        try:
            from vray_blender.bin import VRayBlenderLib as vray
            vray.deletePreviewRenderer(_renderer)
        except Exception:
            pass
        _renderer = 0
    _cbSampled = None


def onFileLoad():
    """ Load handler hook: drop all bake state carried over from the previous file. """
    reset()


def pendingCount() -> int:
    """ Queued plus in-flight bake jobs (for viewport status display). """
    return len(_pending) + (1 if _inFlight is not None else 0)


def isPending(bakeKey: str, resolution: int = None) -> bool:
    """ True while a bake for this key is queued or in flight, i.e. neither ready nor failed. """
    storeKey = _storeKey(bakeKey, resolution)
    if storeKey in _results or storeKey in _failed:
        return False
    return storeKey in _pending or (_inFlight is not None and _inFlight.storeKey == storeKey)


def isAvailable() -> bool:
    """ False on a build whose native side has no sampleTexture; requests then stay gray. """
    from vray_blender.bin import VRayBlenderLib as vray
    return hasattr(vray, 'sampleTexture')


def pump():
    """ Starts the next bake job if idle. Main thread only. """
    global _pumping

    if _pumping:
        return
    _pumping = True
    try:
        _pumpImpl()
    finally:
        _pumping = False


def _pumpImpl():
    global _inFlight, _strandedResult, _warnedUnavailable

    # Take a result the callback thread could not hand off through a timer
    with _strandedLock:
        stranded, _strandedResult = _strandedResult, None
    if stranded is not None:
        _finishJob(*stranded)

    # Lost-reply watchdog
    if _inFlight is not None and time.monotonic() - _inFlight.started > _JOB_TIMEOUT_S:
        storeKey = _inFlight.storeKey
        debug.printWarning(f"Preview bake: sampling timed out for {storeKey[:12]}")
        _failed.add(storeKey)
        _inFlight = None
        _releaseRenderer()
        # Counted, to bound the retry
        _timeouts[storeKey] = _timeouts.get(storeKey, 0) + 1
        if _timeouts[storeKey] < 2:
            _failed.discard(storeKey)

    # Retire a renderer the server is about to reap
    if (_renderer and (_inFlight is None)
            and (time.monotonic() - _rendererLastUsed > _RENDERER_MAX_IDLE_S)):
        _releaseRenderer()

    if _inFlight is not None or not _pending:
        return

    if not isAvailable():
        if not _warnedUnavailable:
            _warnedUnavailable = True
            debug.printWarning("Preview bake: baked texture previews are unavailable: "
                               "this build has no vray.sampleTexture (older server or "
                               "open-source build); unsupported texture subgraphs stay "
                               "neutral gray")
        return

    # Never compete with a user-facing render for the license
    from vray_blender.engine.renderer_prod import VRayRendererProd
    if VRayRendererProd.isActive():
        return

    # Thumbnails must never START the server. Defer instead; the next request pumps again.
    # ensureRunning() is also wrong here.
    from vray_blender.engine.zmq_process import ZMQ
    if not ZMQ.isRunning():
        return

    storeKey, request = next(iter(_pending.items()))
    del _pending[storeKey]
    _inFlight = _InFlight(storeKey, time.monotonic())
    try:
        _startJob(storeKey, *request)
    except _ContentError as ex:
        debug.printWarning(f"Preview bake: nothing to sample: {ex}")
        _failed.add(storeKey)
        _inFlight = None
    except _TransientJobError as ex:
        debug.printWarning(f"Preview bake: job deferred: {ex}")
        _inFlight = None
    except Exception as ex:
        debug.printError(f"Preview bake: job start failed: {ex}")
        _failed.add(storeKey)
        _inFlight = None


def _bakeResolution():
    from vray_blender.lib.blender_utils import getVRayPreferences
    try:
        return int(getVRayPreferences().viewport_bake_resolution)
    except Exception:
        return 512


def _makeExporterContext(renderer):
    from vray_blender.engine import NODE_TRACKERS, OBJ_TRACKERS
    from vray_blender.exporting.plugin_tracker import FakeScopedNodeTracker, ObjTracker
    from vray_blender.exporting.tools import FakeTimeStats
    from vray_blender.lib.common_settings import CommonSettings
    from vray_blender.lib.defs import ExporterContext, PersistedState, RendererMode

    commonSettings = CommonSettings(bpy.context.scene, isInteractive=False, isPreview=True)
    commonSettings.updateFromScene()

    ctx = ExporterContext()
    ctx.renderer        = renderer
    ctx.engine          = None
    ctx.commonSettings  = commonSettings
    ctx.objTrackers     = dict((t, ObjTracker(t)) for t in OBJ_TRACKERS)
    ctx.nodeTrackers    = dict((t, FakeScopedNodeTracker()) for t in NODE_TRACKERS)
    ctx.ctx             = bpy.context
    ctx.dg              = bpy.context.evaluated_depsgraph_get()
    ctx.fullExport      = True
    ctx.ts              = FakeTimeStats()
    ctx.persistedState  = PersistedState()
    ctx.rendererMode    = RendererMode.Preview
    ctx.uiRegionContext = None
    return ctx


def ownTree(ownerKind, owner):
    """ The tree an owner holds itself. Object/fur/decal trees are on the object. """
    return owner.vray.ntree if ownerKind == 'OBJECT' else owner.node_tree


def _resolveOwnerTree(ownerKind, ownerName, groupName):
    """ The owner and the tree to look the node up in: the group when groupName is set. Node groups export under
        the owner the editor entered them from.
    """
    if ownerKind == 'WORLD':
        owner = bpy.data.worlds.get(ownerName)
    elif ownerKind == 'LIGHT':
        owner = bpy.data.lights.get(ownerName)
    elif ownerKind == 'OBJECT':
        owner = bpy.data.objects.get(ownerName)
    else:
        owner = bpy.data.materials.get(ownerName)
    if owner is None:
        return None, None
    ntree = bpy.data.node_groups.get(groupName) if groupName else ownTree(ownerKind, owner)

    from vray_blender.lib.names import syncObjectUniqueName
    for target in (owner, ntree):
        if target is not None:
            syncObjectUniqueName(target, reset=False)
    return owner, ntree


def _startJob(storeKey, ownerKind, ownerName, groupName, nodeName, attrName, resolution, aspect):
    from vray_blender.bin import VRayBlenderLib as vray
    from vray_blender.exporting.node_export import exportVRayNode
    from vray_blender.lib.defs import ExporterType, NodeContext

    # Phase 1: resolve the source. A stale name is transient, not a content failure.
    owner, ntree = _resolveOwnerTree(ownerKind, ownerName, groupName)
    vnode = ntree.nodes.get(nodeName) if ntree else None
    if vnode is None:
        raise _TransientJobError(f"bake source node '{nodeName}' not found")

    if attrName is None:
        # Node-addressed: sample the node's own output (node thumbnails)
        from vray_blender.exporting.tools import FarNodeLink
        outSock = next((s for s in vnode.outputs if s.enabled and not s.hide), None)
        if outSock is None:
            raise _ContentError(f"bake source node '{nodeName}' has no visible output")
        link = FarNodeLink(outSock, outSock)
    else:
        from vray_blender.exporting.tools import getInputSocketByAttr
        sock = getInputSocketByAttr(vnode, attrName)
        link = getFarNodeLink(sock) if sock else None
        if link is None:
            raise _ContentError(f"bake source link '{nodeName}.{attrName}' not found")

    # Phase 2: renderer availability - also transient (server may be down)
    global _renderer, _cbSampled
    try:
        if not _renderer:
            # A dedicated non-main renderer instance hosting the UV texture sampler
            settings = vray.ExporterSettings()
            settings.exporterType = ExporterType.PREVIEW
            settings.renderThreads = 2
            from vray_blender.lib import path_utils
            settings.previewDir = path_utils.getPreviewDir()
            _renderer = vray.createPreviewRenderer(settings)
        if not _renderer:
            raise _TransientJobError("bake renderer unavailable")
        vray.clearScene(_renderer)
    except _TransientJobError:
        raise
    except Exception as ex:
        raise _TransientJobError(f"bake renderer unavailable: {ex}")

    exporterCtx = _makeExporterContext(_renderer)
    nodeCtx = NodeContext(exporterCtx, bpy.context.scene, bpy.data, _renderer)
    nodeCtx.rootObj = owner
    nodeCtx.nodeTracker = exporterCtx.nodeTrackers['MTL']
    nodeCtx.ntree = ntree
    nodeCtx.material = owner if ownerKind == 'MTL' else None

    with nodeCtx, nodeCtx.push(vnode):
        if vnode.bl_idname in _PLACEMENT_NODES:
            # Not a V-Ray plugin. Its matrix, applied to the placement image.
            from vray_blender.exporting.node_export import exportPlacementPreviewBitmap
            texPlugin = exportPlacementPreviewBitmap(nodeCtx, vnode)
        else:
            texPlugin = exportVRayNode(nodeCtx, link)
            texPlugin = _wrapForSampling(nodeCtx, texPlugin, link.from_socket)

    if texPlugin.isEmpty():
        # An empty plugin name gets no reply, blocking the sampler slot until the watchdog fires
        raise _ContentError(f"bake source '{nodeName}' exported nothing")

    # UV-sampled over [0,1)^2, following the C4D node-preview scheme
    _cbSampled = lambda pixels: _onSampleReady(storeKey, pixels)
    w, h = int(round(resolution * aspect)), resolution
    vray.sampleTexture(_renderer, texPlugin.name, w, h, _cbSampled)

    global _rendererLastUsed
    _rendererLastUsed = time.monotonic()


def _wrapForSampling(nodeCtx, texPlugin, fromSock):
    """ Wraps a plugin the UV sampler cannot take directly, which accepts only a color output addressed by its
        default: UVWGen a TexBitmap on the built-in UV grid float texture TexFloatToColor named output
        TexAColor
    """
    from vray_blender.exporting.node_export import _exportTexConverter, exportPluginWithStats, exportUVGridBitmap
    from vray_blender.exporting.tools import getVRayBaseSockType, isFloatSocket, isUVWSocket
    from vray_blender.lib.defs import PluginDesc
    from vray_blender.lib.names import Names

    if texPlugin.isEmpty():
        return texPlugin

    sockType = getVRayBaseSockType(fromSock)

    if isUVWSocket(sockType) and ('UVWGen' in (texPlugin.pluginType or '')):
        return exportUVGridBitmap(nodeCtx, texPlugin)

    if isFloatSocket(sockType):
        return _exportTexConverter(nodeCtx, texPlugin, 'TexFloatToColor')

    if texPlugin.output:
        plDesc = PluginDesc(Names.nextVirtualNode(nodeCtx, "TexAColor"), "TexAColor")
        plDesc.attrs['texture'] = texPlugin
        return exportPluginWithStats(nodeCtx, plDesc)

    return texPlugin


def _onSampleReady(storeKey, image):
    """ Native thread. Hops to the main thread on a one-shot timer, once per completion. """
    global _strandedResult

    def finish():
        _finishJob(storeKey, image)
        return None

    try:
        bpy.app.timers.register(finish, first_interval=0.0)
    except Exception:
        # No bpy access from this thread; stash the result for pump() to drain
        with _strandedLock:
            _strandedResult = (storeKey, image)


def _finishJob(storeKey, image):
    """ Main thread. Stores the result, frees the in-flight slot unless a newer job took it, and keeps the queue
        moving.
    """
    global _inFlight, _rendererLastUsed

    # Restarted on the reply as well as on the send
    _rendererLastUsed = time.monotonic()

    # A late reply after a timeout must not clear the newer in-flight job
    isCurrent = _inFlight is not None and _inFlight.storeKey == storeKey
    try:
        if image is None:
            debug.printError(f"Preview bake: sampling failed for {storeKey[:12]}")
            _failed.add(storeKey)
        else:
            # sampleArea returns its rows starting at v=1. Row 0 must be v=0 here and in a
            # GPUTexture, so they are reversed on the way in.
            pixels = np.ascontiguousarray(np.asarray(image, np.float32)[::-1])
            _results[storeKey] = pixels
            _evictResults()
            _failed.discard(storeKey)  # a late valid result overrides a timeout
            slot = _keyToSlot.get(storeKey)
            if slot is not None:
                _slotLastGood[slot] = storeKey  # newest good bake for the slot
    finally:
        if isCurrent:
            _inFlight = None

    # Both consumers must repaint - the GL viewport preview AND the node editor
    from vray_blender.nodes.utils import tagRedrawNodeEditor, tagRedrawViewport
    tagRedrawViewport()
    tagRedrawNodeEditor()
    pump()  # start the next queued bake without waiting for a user edit
