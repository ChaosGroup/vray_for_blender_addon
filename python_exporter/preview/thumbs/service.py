# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

""" Thumbnail bookkeeping: content keys, request routing, and the texture lookup the draw
    handler uses.

    Only visible nodes are ever requested. SAMPLE pixels come from preview.bake, RENDER_* from
    preview.thumbs.render, and each producer owns its own queue, watchdog and results.

    Nothing here polls: a dict write during draw, then a one-shot timer.
"""

import hashlib

import bpy

from vray_blender.preview import bake
from vray_blender.preview.thumbs import render
from vray_blender.preview.thumbs.classify import PreviewMethod, previewOf

# Everything holding pixels is keyed by displayKey = f"{key}@{resolution}"

# displayKey -> display exposure, computed once from the produced pixels
_exposures = {}

# displayKey -> GPUTexture of the RAW produced pixels. The display transform is the draw shader's.
_textures = {}

# displayKeys whose pixels came back flat black. Nothing is drawn for these.
_blankKeys = set()

# slot -> the displayKey currently shown for that node
_lastGood = {}

# (treePtr, nodeName) -> content key, held until the depsgraph reports a shading datablock changed
_keyCache = {}

# (treePtr, nodeName) -> Preview. Same lifecycle as _keyCache.
_previewCache = {}

# True while a one-shot pump timer is already scheduled
_pumpScheduled = False

_DIRTYING_ID_TYPES = {'NODETREE', 'MATERIAL', 'WORLD', 'LIGHT'}

# treePtr -> topology fingerprint, which is what tells a STRUCTURAL edit from a property edit
_treeTopology = {}


# Resolutions AUTO can pick. Keep them quantised.
_RES_STEPS = (64, 128, 256, 512)

# Held value of the node_thumbnail_resolution preference; None until read
_resolutionPref = None


def resolution(drawnWidth: float = 0.0) -> int:
    """ The resolution to produce a thumbnail at. 'drawnWidth' is its on-screen width, used only by AUTO. """
    global _resolutionPref

    if _resolutionPref is None:
        from vray_blender.lib.blender_utils import getVRayPreferences
        _resolutionPref = getVRayPreferences().node_thumbnail_resolution

    if _resolutionPref != 'AUTO':
        return int(_resolutionPref)

    return next((step for step in _RES_STEPS if drawnWidth <= step), _RES_STEPS[-1])


def onResolutionPrefChanged():
    """ Drop the held preference value; see resolution(). """
    global _resolutionPref
    _resolutionPref = None


def slotOf(node: bpy.types.Node, ntree: bpy.types.NodeTree):
    """ Identity of the node itself, as opposed to the content it currently holds. """
    return (ntree.as_pointer(), node.name)


def _displayKey(key: str, res: int) -> str:
    """ Storage key for a content key at a resolution. """
    return f"{key}@{res}"


def _producer(preview):
    """ The module that owns this preview's pixels. Both expose pixelsFor and isPending. """
    return bake if preview.method is PreviewMethod.SAMPLE else render


def previewFor(node: bpy.types.Node, ntree: bpy.types.NodeTree):
    """ Cached classify.previewOf - see _previewCache. """
    cacheKey = slotOf(node, ntree)
    if cacheKey in _previewCache:
        return _previewCache[cacheKey]
    preview = previewOf(node)
    _previewCache[cacheKey] = preview
    return preview


def keyOf(node: bpy.types.Node, ntree: bpy.types.NodeTree, preview) -> str:
    """ Content key for a node's thumbnail: the subtree's content plus how the image is produced. """
    cacheKey = slotOf(node, ntree)
    if (key := _keyCache.get(cacheKey)) is not None:
        return key

    digest = hashlib.md5(bake.subtreeHash(node).encode())
    digest.update(f"|{preview.method.value}|{preview.plugin}|{preview.aspect:.3f}".encode())
    key = digest.hexdigest()
    _keyCache[cacheKey] = key
    return key


def onDepsgraphUpdate(scene: bpy.types.Scene, depsgraph: bpy.types.Depsgraph):
    """ Drops the cached keys when a node graph changed STRUCTURALLY. """
    if not (_keyCache or _previewCache or _lastGood):
        return

    for update in depsgraph.updates:
        if getattr(update.id, 'id_type', '') not in _DIRTYING_ID_TYPES:
            continue
        ntree = _treeOf(update.id)
        if (ntree is not None) and not _topologyChanged(ntree):
            continue
        _keyCache.clear()
        _previewCache.clear()
        # A structural edit can free nodes, and bake's memo is keyed by node pointer
        bake.clearOwnHashes()
        _pruneDeadSlots()
        return


def _treeOf(datablock):
    """ The ORIGINAL node tree a depsgraph update is about, or None when it is not one we track.

        depsgraph.updates hands back EVALUATED datablocks, whose pointers do not match the caches.
    """
    datablock = getattr(datablock, 'original', datablock)
    if isinstance(datablock, bpy.types.NodeTree):
        return datablock
    ntree = getattr(datablock, 'node_tree', None)
    if ntree is None:
        return None
    ntree = getattr(ntree, 'original', ntree)
    return ntree if isinstance(ntree, bpy.types.NodeTree) else None


def _topologyChanged(ntree: bpy.types.NodeTree) -> bool:
    """ Whether this tree gained, lost or rewired anything since the last check.

        Do not simplify this to link counts or link pointers. A re-link leaves the counts equal, and Blender
        reuses the freed link's address.
    """
    linkFingerprint = 0
    for link in ntree.links:
        linkFingerprint += link.from_socket.as_pointer() * 31 + link.to_socket.as_pointer()

    fingerprint = (len(ntree.nodes), len(ntree.links), linkFingerprint)
    treePtr = ntree.as_pointer()
    changed = _treeTopology.get(treePtr) != fingerprint
    _treeTopology[treePtr] = fingerprint
    return changed


def invalidateNode(node: bpy.types.Node):
    """ Marks a node's thumbnail stale, along with every thumbnail downstream whose subtree hash includes it. """
    # Always dropped, cached key or not
    bake.dropOwnHash(node)

    if not (_keyCache or _previewCache):
        return
    ntree = getattr(node, 'id_data', None)
    if not isinstance(ntree, bpy.types.NodeTree):
        return

    # A change in a group tree reaches parent trees through their group nodes
    if getattr(getattr(ntree, 'vray', None), 'tree_type', '') == 'GROUP':
        _keyCache.clear()
        _previewCache.clear()
    else:
        treePtr = ntree.as_pointer()
        pending, seen = [node], set()
        while pending:
            current = pending.pop()
            if current.name in seen:
                continue
            seen.add(current.name)
            _keyCache.pop((treePtr, current.name), None)
            _previewCache.pop((treePtr, current.name), None)
            for sock in current.outputs:
                for link in sock.links:
                    pending.append(link.to_node)

    # Editing from the sidebar repaints that editor, not this one
    from vray_blender.nodes.utils import tagRedrawNodeEditor
    tagRedrawNodeEditor()


_OWNER_KINDS = ((bpy.types.Material, 'MTL'), (bpy.types.World, 'WORLD'), (bpy.types.Light, 'LIGHT'),
                (bpy.types.Object, 'OBJECT'))


def _ownerOf(ntree: bpy.types.NodeTree, ownerId: bpy.types.ID):
    """ The editor's datablock (space.id) as (ownerKind, ownerName, groupName). groupName names ntree when it is a
        group entered from the owner's tree, else ''. A job may hold only names.
    """
    ownerKind = next((kind for idType, kind in _OWNER_KINDS if isinstance(ownerId, idType)), None)
    if ownerKind is None:
        return None, None, None
    groupName = '' if bake.ownTree(ownerKind, ownerId) == ntree else ntree.name
    return ownerKind, ownerId.name, groupName


def thumbnailFor(node: bpy.types.Node, ntree: bpy.types.NodeTree, ownerId: bpy.types.ID, drawnWidth: float = 0.0):
    """ The thumbnail for a node, requesting one if it is not there yet: its raw-pixel texture, the display exposure
        the draw shader applies to it, and the image aspect. None if there is nothing to draw. Runs per node
        from the draw handler.
    """
    preview = previewFor(node, ntree)
    if preview is None:
        return None

    if (preview.method is PreviewMethod.SAMPLE) and (not bake.isAvailable()):
        return None

    slot = slotOf(node, ntree)
    key = keyOf(node, ntree, preview)
    res = resolution(drawnWidth)
    dispKey = _displayKey(key, res)
    if dispKey in _blankKeys:
        return None

    if (tex := _textureFor(dispKey, key, preview, res)) is not None:
        _promote(slot, dispKey)
        return tex, _exposures.get(dispKey, 1.0), preview.aspect

    _request(key, node, ntree, ownerId, preview, res)

    # Stands in only while the new thumbnail is genuinely on its way. A failed request must drop it.
    prev = _lastGood.get(slot)
    if (prev is not None) and (prev != dispKey) and _isPending(key, preview, res):
        if (tex := _textures.get(prev)) is not None:
            return tex, _exposures.get(prev, 1.0), preview.aspect
    return None


def _isPending(key: str, preview, res: int) -> bool:
    """ Whether the producer that owns this preview is still working on the key. """
    return _producer(preview).isPending(key, res)


def _promote(slot, dispKey: str):
    """ Records the key a node shows now, dropping the texture it replaces once no other node shows it. """
    prev = _lastGood.get(slot)
    if prev == dispKey:
        return
    _lastGood[slot] = dispKey
    if (prev is not None) and (prev not in _lastGood.values()):
        _textures.pop(prev, None)
        _exposures.pop(prev, None)


def _pruneDeadSlots():
    """ Drops slots whose node no longer exists. """
    live = set()
    trees = [owner.node_tree for collection in (bpy.data.materials, bpy.data.worlds, bpy.data.lights)
             for owner in collection if owner.node_tree is not None]
    trees.extend(bpy.data.node_groups)
    for ntree in trees:
        treePtr = ntree.as_pointer()
        live.update((treePtr, node.name) for node in ntree.nodes)

    for slot in [s for s in _lastGood if s not in live]:
        dispKey = _lastGood.pop(slot)
        if dispKey not in _lastGood.values():
            _textures.pop(dispKey, None)
            _exposures.pop(dispKey, None)


def _exposureFor(dispKey: str, pixels, method) -> float:
    """ Display exposure for a thumbnail. """
    if method is PreviewMethod.RENDER_CURVE:
        if (exposure := _exposures.get(dispKey)) is not None:
            return exposure
        peak = float(pixels[:, :, :3].max())
        exposure = 1.0 if peak <= 1e-4 else min(0.9 / peak, 50.0)
        _exposures[dispKey] = exposure
        return exposure

    if method is not PreviewMethod.SAMPLE:
        return 1.0
    if (exposure := _exposures.get(dispKey)) is not None:
        return exposure

    import numpy as np

    exposure = 1.0
    rgb = pixels[:, :, :3]
    if rgb.size and (rgb.max() > 1.0):
        luma = 0.2126 * rgb[:, :, 0] + 0.7152 * rgb[:, :, 1] + 0.0722 * rgb[:, :, 2]
        logAvg = float(np.exp(np.mean(np.log(np.maximum(luma, 1e-4)))))
        exposure = 0.18 / max(logAvg, 1e-4)
    _exposures[dispKey] = exposure
    return exposure


def _pixelsFor(dispKey: str, key: str, preview, res: int):
    """ Raw produced pixels for a key, from whichever producer owns the preview. """
    pixels = _producer(preview).pixelsFor(key, res)
    if pixels is None:
        return None

    rgb = pixels[:, :, :3]
    if (rgb.max() <= 1e-4) and (rgb.std() <= 1e-6):
        _blankKeys.add(dispKey)
        return None
    return pixels


def _textureFor(dispKey: str, key: str, preview, res: int):
    """ GPUTexture of the RAW produced pixels for a key, or None if nothing is ready yet. """
    if (tex := _textures.get(dispKey)) is not None:
        return tex

    pixels = _pixelsFor(dispKey, key, preview, res)
    if pixels is None:
        return None

    import gpu

    # Fills _exposures for the key; thumbnailFor reads the value back out of it
    _exposureFor(dispKey, pixels, preview.method)

    h, w = pixels.shape[0], pixels.shape[1]
    try:
        buf = gpu.types.Buffer('FLOAT', w * h * 4, pixels.ravel())
        tex = gpu.types.GPUTexture((w, h), format='RGBA16F', data=buf)
    except Exception as ex:
        # Recorded as blank rather than retried. This runs in the draw handler.
        from vray_blender import debug
        debug.printError(f"Node thumbnails: texture upload failed: {ex}")
        _blankKeys.add(dispKey)
        return None
    _textures[dispKey] = tex
    return tex


def _request(key: str, node: bpy.types.Node, ntree: bpy.types.NodeTree, ownerId: bpy.types.ID, preview, res: int):
    ownerKind, ownerName, groupName = _ownerOf(ntree, ownerId)
    if ownerKind is None:
        return

    if preview.method is PreviewMethod.SAMPLE:
        bake.requestNodeSample(key, ownerKind, ownerName, groupName, node.name, res, preview.aspect)
    else:
        render.request(key, ownerKind, ownerName, groupName, node.name, preview.method.value, res, preview.aspect)
    _schedulePump()


def _schedulePump():
    """ Starts the producers' queues right after the current draw. """
    global _pumpScheduled

    if _pumpScheduled:
        return
    _pumpScheduled = True

    def pump():
        global _pumpScheduled
        _pumpScheduled = False
        for producer in (bake, render):
            try:
                producer.pump()
            except Exception as ex:
                from vray_blender import debug
                debug.printError(f"Node thumbnails: pump failed: {ex}")
        return None

    bpy.app.timers.register(pump, first_interval=0.0)


def invalidateAll():
    """ Drops everything describing CONTENT, keeping the warm renderers and their pixel caches. """
    _keyCache.clear()
    _previewCache.clear()
    _treeTopology.clear()
    bake.clearOwnHashes()
    _pruneDeadSlots()


def reset():
    """ Drops everything cached for thumbnails, on a file load or when the feature goes off. """
    global _pumpScheduled

    render.reset()
    bake.reset()
    _keyCache.clear()
    _previewCache.clear()
    _treeTopology.clear()
    _exposures.clear()
    _textures.clear()
    _blankKeys.clear()
    _lastGood.clear()
    # A file load purges every non-persistent timer, the pump timer included
    _pumpScheduled = False
