# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

""" Render tier for node thumbnails: the nodes that cannot be UV-sampled.

    resources/thumbnail_scene.vrscene is loaded server-side into each renderer once. A request
    then exports only the previewed node's material plugins and repoints thumbNode.material.

    Renderers are never cleared or released between thumbnails - loading a scene marks one
    client-owned server-side (m_keepWarm). Several run at once, one job each.
"""

import os
import time
from typing import NamedTuple

import numpy as np

import bpy

from vray_blender import debug

# If a thumbnail renders black: dump with VRAY_THUMBS_DUMP=<path> and render that with standalone
# vray.exe. Compare attribute TYPES, not just values.

# storeKey -> float32 (h, w, 4)
_results = {}
_failed = set()
_pending = {}        # storeKey -> _Job
_timeouts = {}       # storeKey -> consecutive lost replies, to bound the retry
_workers = []
_pumping = False

# Capped by BYTES, not count. One result is resolution^2 * 4 channels * 4 bytes.
_RESULT_BYTES_BUDGET = 64 * 1024 * 1024

# Must stay BELOW the server's 30s PREVIEW_RENDERER_MAX_IDLE_TIME, which it measures from the
# last message received.
_JOB_TIMEOUT_S = 25.0

# Threads per warm renderer. Throughput comes from running several at once, not from widening one.
_RENDER_THREADS = 4

# Rebuild a worker after this many thumbnails. Nothing removes a previous node's plugins.
_JOBS_PER_WORKER = 32

# Retire a worker unused for this long. Must stay below the server's
# WARM_RENDERER_MAX_IDLE_TIME (10min). Checked on use, not on a timer.
_WORKER_MAX_IDLE_S = 5 * 60.0

# Plugin names the shipped scene guarantees; see the header of thumbnail_scene.vrscene
_CAMERA_NAME = "thumbCamera"
_SPHERE_NODE = "thumbNode"
_PLANE_NODE = "thumbPlaneNode"
_CORNER_NODE = "thumbCornerNode"
_CURVE_NODE = "thumbCurveNode"
_BACKDROP_NODE = "thumbBackdropNode"
_PROBE_NODE = "thumbProbeNode"

# The subjects that swap in and out of frame per method. The probe is not one.
_SUBJECT_NODES = (_SPHERE_NODE, _PLANE_NODE, _CORNER_NODE, _CURVE_NODE)

# A Toon BRDF gets thumbToonLight alone and no GI, as in V-Ray for C4D's previews. BRDFToonMtl picks
# its diffuse ramp colour from direct light intensity and adds GI on top without remapping it.
_AREA_LIGHTS = ("SquaredLight@13", "CircularLight@30")
_TOON_LIGHT = "thumbToonLight"
_GI = "_settingsGI"

# The shipped scene relaxes the sampler hard (maxSubdivs 2, threshold 0.15); TexDirt and TexCurvature trace
# their own rays and come out grainy there. Do not raise these without checking a glossy subject at 256px.
_IMAGE_SAMPLER = "_settingsImageSampler"
_SAMPLER_DEFAULT = (2, 0.15)
_SAMPLER_BY_METHOD = {
    'RENDER_CORNER': (4, 0.08),
    'RENDER_CURVE' : (6, 0.05),
}

# Which surface each method wears its material on, and whether the backdrop is in frame. Only a
# material or BRDF gets the backdrop.
_SUBJECT_BY_METHOD = {
    'RENDER_PLANE'   : (_PLANE_NODE,  False),
    'RENDER_SPHERE'  : (_SPHERE_NODE, False),
    'RENDER_CORNER'  : (_CORNER_NODE, False),
    'RENDER_CURVE'   : (_CURVE_NODE,  False),
    'RENDER_MATERIAL': (_SPHERE_NODE, True),
}


class _TransientJobError(Exception):
    """ Job could not start, from a stale lookup or an unavailable renderer. """


class _ContentError(Exception):
    """ This node cannot produce a thumbnail at all. Permanent for the key. """


class _Job(NamedTuple):
    ownerKind: str      # 'MTL', 'WORLD', 'LIGHT' or 'OBJECT'; picks the collection to resolve the tree from
    ownerName: str      # the datablock the node's plugins are named under
    groupName: str      # a group inside the owner's tree, or ''
    nodeName: str       # looked up in the group when groupName is set, else in the owner's tree
    method: str         # a PreviewMethod value such as 'RENDER_PLANE'; picks the subject in the scene
    resolution: int     # image height in pixels; the width is resolution * aspect
    aspect: float       # width/height of the image

    @property
    def slot(self):
        """ The node the job is for. """
        return (self.ownerName, self.groupName, self.nodeName)


class _Worker:
    """ One warm renderer with the thumbnail scene already loaded. """

    def __init__(self, renderer):
        self.renderer = renderer
        self.job = None             # storeKey of the job in flight, or None when free
        self.slot = None            # _Job.slot of that job
        self.started = 0.0
        self.lastUsed = time.monotonic()
        self.jobsDone = 0
        self.cbImageReady = None    # kept alive for the native side

    def isFree(self) -> bool:
        return self.job is None


def maxWorkers() -> int:
    """ How many thumbnails may render at once; 0 in the preferences picks from the CPU count. """
    from vray_blender.lib.blender_utils import getVRayPreferences
    try:
        configured = int(getVRayPreferences().node_thumbnail_workers)
    except Exception:
        configured = 0
    if configured > 0:
        return configured
    return max(2, min(3, (os.cpu_count() or 4) // 2))


def _storeKey(key: str, resolution: int) -> str:
    return f"{key}@{resolution}"


def request(key: str, ownerKind: str, ownerName: str, groupName: str, nodeName: str, method: str, resolution: int,
            aspect: float = 1.0):
    """ Record a render request. Main thread, never blocks. Supersedes any stale pending request for the same node. """
    storeKey = _storeKey(key, resolution)
    # Return before the supersede scan below when the key is already queued. Keep the insertion
    # order - _nextJobForFreeSlot reads it as "oldest first".
    if (storeKey in _pending) or (storeKey in _results) or (storeKey in _failed) \
            or _isInFlight(storeKey):
        return
    job = _Job(ownerKind, ownerName, groupName, nodeName, method, resolution, aspect)
    for staleKey in [k for k, v in _pending.items() if v.slot == job.slot]:
        del _pending[staleKey]
    _pending[storeKey] = job


def _isInFlight(storeKey: str) -> bool:
    return any(w.job == storeKey for w in _workers)


def _slotInFlight(slot) -> bool:
    return any(w.slot == slot for w in _workers if not w.isFree())


def _nextJobForFreeSlot():
    """ The oldest pending job whose NODE is not already rendering, or None. """
    for storeKey, job in _pending.items():
        if not _slotInFlight(job.slot):
            return storeKey, job
    return None


def _evictResults():
    """ Drops the oldest results until the cache is inside its byte budget. Dicts keep insertion order, so the first
        key is the oldest.
    """
    total = sum(p.nbytes for p in _results.values())
    while (len(_results) > 1) and (total > _RESULT_BYTES_BUDGET):
        oldest = next(iter(_results))
        total -= _results.pop(oldest).nbytes


def pixelsFor(key: str, resolution: int):
    """ Rendered pixels for THIS content key, or None until they arrive.

        Never fall back to the slot's previous render here - service._textureFor memoises whatever it is
        handed under the key it ASKED for. That fallback is thumbnailFor's job.
    """
    return _results.get(_storeKey(key, resolution))


def isPending(key: str, resolution: int) -> bool:
    storeKey = _storeKey(key, resolution)
    if (storeKey in _results) or (storeKey in _failed):
        return False
    return (storeKey in _pending) or _isInFlight(storeKey)


def reset():
    _pending.clear()
    _results.clear()
    _failed.clear()
    _timeouts.clear()
    for worker in list(_workers):
        _releaseWorker(worker)


def pump():
    global _pumping
    if _pumping:
        return
    _pumping = True
    try:
        _pumpImpl()
    finally:
        _pumping = False


def _pumpImpl():
    _reapTimedOut()

    if not _pending:
        return

    # Never compete with a user-facing render for the license
    from vray_blender.engine.renderer_prod import VRayRendererProd
    if VRayRendererProd.isActive():
        return

    # Thumbnails must never START the server. Defer instead; the next request pumps again.
    from vray_blender.engine.zmq_process import ZMQ
    if not ZMQ.isRunning():
        return

    while _pending:
        # Resolved BEFORE acquiring a worker, so none is built when every pending job belongs
        # to a node already rendering.
        nextJob = _nextJobForFreeSlot()
        if nextJob is None:
            break       # every pending job is for a node that is already rendering

        try:
            worker = _acquireWorker()
        except _TransientJobError as ex:
            # The work stays pending; the next request pumps again
            debug.printWarning(f"Node thumbnails: no render worker: {ex}")
            break

        if worker is None:
            break       # at the worker limit; a completion will pump the rest

        storeKey, job = nextJob
        del _pending[storeKey]
        worker.job = storeKey
        worker.slot = job.slot
        worker.started = time.monotonic()
        try:
            _startJob(worker, storeKey, *job)
        except _ContentError as ex:
            debug.printWarning(f"Node thumbnails: nothing to render: {ex}")
            _failed.add(storeKey)
            _freeWorker(worker)
            # The partial export still counts towards recycling the worker
            worker.jobsDone += 1
            if worker.jobsDone >= _JOBS_PER_WORKER:
                _releaseWorker(worker)
        except _TransientJobError as ex:
            debug.printWarning(f"Node thumbnails: render deferred: {ex}")
            _freeWorker(worker)
        except Exception as ex:
            debug.printError(f"Node thumbnails: render failed to start: {ex}")
            _failed.add(storeKey)
            _freeWorker(worker)
            # Do not reuse a renderer whose export failed
            _releaseWorker(worker)


def _freeWorker(worker):
    """ Hand a worker back without a completed job. """
    worker.job = None
    worker.slot = None
    worker.lastUsed = time.monotonic()


def _reapTimedOut():
    now = time.monotonic()
    for worker in [w for w in _workers if (not w.isFree()) and (now - w.started > _JOB_TIMEOUT_S)]:
        storeKey = worker.job
        debug.printWarning(f"Node thumbnails: render timed out for {storeKey[:12]}")
        worker.job = None
        worker.slot = None
        _releaseWorker(worker)

        # Counted, to bound the retry. Same rule as preview.bake's _timeouts.
        _timeouts[storeKey] = _timeouts.get(storeKey, 0) + 1
        if _timeouts[storeKey] >= 2:
            _failed.add(storeKey)


def _acquireWorker():
    """ A free warm worker, creating one up to the configured limit. """
    now = time.monotonic()
    for stale in [w for w in _workers if w.isFree() and (now - w.lastUsed > _WORKER_MAX_IDLE_S)]:
        _releaseWorker(stale)

    for worker in _workers:
        if worker.isFree():
            return worker
    if len(_workers) >= maxWorkers():
        return None
    return _createWorker()


def _createWorker():
    """ A renderer with the thumbnail scene loaded and a render session open. """
    from vray_blender.bin import VRayBlenderLib as vray
    from vray_blender.lib import path_utils, sys_utils
    from vray_blender.lib.defs import ExporterType

    scenePath = sys_utils.getThumbnailScenePath()
    if not os.path.isfile(scenePath):
        raise _TransientJobError(f"thumbnail scene missing: {scenePath}")

    settings = vray.ExporterSettings()
    settings.exporterType = ExporterType.PREVIEW
    settings.renderThreads = _RENDER_THREADS
    settings.previewDir = path_utils.getPreviewDir()
    renderer = vray.createPreviewRenderer(settings)
    if not renderer:
        raise _TransientJobError("thumbnail renderer unavailable")

    worker = _Worker(renderer)
    try:
        # createPreviewRenderer can hand out a RECYCLED renderer, and loadScene is additive. Keep this clear.
        vray.clearScene(renderer)

        # Loaded server-side. Also marks the renderer client-owned; see m_keepWarm.
        vray.loadScene(renderer, scenePath)
        vray.setCameraName(renderer, _CAMERA_NAME)

        viewSettings = vray.ViewSettings()
        # Production CPU. Do not ask for a GPU mode on a preview renderer.
        viewSettings.renderMode = -1
        vray.syncViewSettings(renderer, viewSettings)
    except Exception as ex:
        _destroyRenderer(renderer)
        raise _TransientJobError(f"thumbnail renderer setup failed: {ex}")

    _workers.append(worker)
    return worker


def _releaseWorker(worker):
    if worker in _workers:
        _workers.remove(worker)
    worker.job = None
    worker.slot = None
    _destroyRenderer(worker.renderer)
    worker.renderer = 0
    # After the renderer, not before
    worker.cbImageReady = None


def _destroyRenderer(renderer):
    if not renderer:
        return
    try:
        from vray_blender.bin import VRayBlenderLib as vray
        vray.renderEnd(renderer)
        vray.deletePreviewRenderer(renderer)
    except Exception:
        pass


# Wrapper plugins modify a BASE that a real export fills in from elsewhere. Mapped to the
# attribute wanting a stand-in.
_WRAPPER_BASE_ATTR = {
    'BRDFToonOverride'  : 'base_brdf',
    'BRDFBump'          : 'base_brdf',
    'MtlOverride'       : 'base_mtl',
    'MtlWrapper'        : 'base_material',
    'MtlRoundEdges'     : 'base_mtl',
    'MtlDisplacement'   : 'base_material',
    'MayaMtlMatte'      : 'base_mtl',
    'Mtl2Sided'         : 'front',
}


def _exportPlainBase(nodeCtx):
    """ A plain grey BRDFVRayMtl for a wrapper node to wrap. """
    from vray_blender.exporting.node_export import exportPluginWithStats
    from vray_blender.lib.defs import AColor, PluginDesc
    from vray_blender.lib.names import Names

    baseDesc = PluginDesc(Names.nextVirtualNode(nodeCtx, "BRDFVRayMtl"), "BRDFVRayMtl")
    baseDesc.setAttribute('diffuse', AColor((0.55, 0.55, 0.55, 1.0)))
    baseDesc.setAttribute('reflect', AColor((0.03, 0.03, 0.03, 1.0)))
    return exportPluginWithStats(nodeCtx, baseDesc)


def _exportWrapper(nodeCtx, vnode, pluginType: str, baseAttr: str):
    """ A wrapper node with a synthetic base for it to modify. """
    from vray_blender.exporting.node_export import exportNodeTree, exportPluginWithStats
    from vray_blender.exporting.tools import getFarNodeLink, getInputSocketByAttr
    from vray_blender.lib.defs import PluginDesc
    from vray_blender.lib.names import Names
    from vray_blender.nodes.curves_node import exportLineWidthCurves

    baseSock = getInputSocketByAttr(vnode, baseAttr)
    hasBase = (baseSock is not None) and (getFarNodeLink(baseSock) is not None)

    plDesc = PluginDesc(Names.nextVirtualNode(nodeCtx, pluginType), pluginType)
    if (pg := getattr(vnode, pluginType, None)) is not None:
        plDesc.vrayPropGroup = pg

    exportNodeTree(nodeCtx, plDesc, skippedSockets=() if hasBase else (baseAttr,))
    if not hasBase:
        base = _exportPlainBase(nodeCtx)
        if not pluginType.startswith('BRDF'):
            base = _wrapInSingleBRDF(nodeCtx, base)
        plDesc.setAttribute(baseAttr, base)

    if pluginType == 'BRDFToonOverride':
        exportLineWidthCurves(nodeCtx, plDesc, vnode)
    return exportPluginWithStats(nodeCtx, plDesc)


# Ceiling on TexDistance's 'distance', at roughly twice the swatch quad's 2-unit width. V-Ray
# for SketchUp uses the same ratio to clamp at 20 on its 10-unit scene.
_DISTANCE_MAX = 4.0


# TexDirt.custom.json keeps subdivs at 3 for render speed, which is heavy grain at a thumbnail's
# size. Do not do the same for TexCurvature.
_OCCLUSION_SUBDIVS = 16


def _raiseDirtSubdivs(nodeCtx, plugin):
    from vray_blender.lib.plugin_utils import updateValue

    updateValue(nodeCtx.renderer, plugin.name, 'subdivs', _OCCLUSION_SUBDIVS)


def _aimDistanceAtProbe(nodeCtx, vnode, plugin):
    """ Point an exported TexDistance at the scene's probe. """
    from vray_blender.exporting.tools import getInputSocketByAttr
    from vray_blender.lib.defs import AttrPlugin
    from vray_blender.lib.plugin_utils import updateValue

    updateValue(nodeCtx.renderer, plugin.name, 'objects',
                [AttrPlugin(_PROBE_NODE, AttrPlugin.OUTPUT_DEFAULT)])

    # Left alone when a texture drives it - writing a float would drop the link
    sock = getInputSocketByAttr(vnode, 'distance')
    if (sock is not None) and (not sock.is_linked) and (getattr(sock, 'value', 0.0) > _DISTANCE_MAX):
        updateValue(nodeCtx.renderer, plugin.name, 'distance', _DISTANCE_MAX)


def _wrapInSingleBRDF(nodeCtx, plugin):
    from vray_blender.exporting.node_export import exportPluginWithStats
    from vray_blender.lib.defs import PluginDesc
    from vray_blender.lib.names import Names

    plDesc = PluginDesc(Names.nextVirtualNode(nodeCtx, "MtlSingleBRDF"), "MtlSingleBRDF")
    plDesc.attrs['brdf'] = plugin
    return exportPluginWithStats(nodeCtx, plDesc)


def _exportThumbMaterial(nodeCtx, vnode):
    """ The previewed node, wrapped into something a surface can use: material -> as is BRDF -> MtlSingleBRDF uvwgen
        -> UV grid bitmap, lit by a BRDFLight wrapper -> a grey base for it to modify (see _exportWrapper)
        texture -> BRDFLight so it reads as its own color, unlit
    """
    from vray_blender.exporting.node_export import (_exportBrdfLightConverter, exportUVGridBitmap,
                                                    exportVRayNode)
    from vray_blender.exporting.tools import FarNodeLink

    outSock = next((s for s in vnode.outputs if s.enabled and not s.hide), None)
    if outSock is None:
        raise _ContentError(f"node '{vnode.name}' has no visible output")

    nodePlugin = getattr(vnode, 'vray_plugin', '')
    if (baseAttr := _WRAPPER_BASE_ATTR.get(nodePlugin)) is not None:
        with nodeCtx.push(vnode):
            plugin = _exportWrapper(nodeCtx, vnode, nodePlugin, baseAttr)
            if nodePlugin.startswith('BRDF'):
                plugin = _wrapInSingleBRDF(nodeCtx, plugin)
            return plugin

    with nodeCtx.push(vnode):
        plugin = exportVRayNode(nodeCtx, FarNodeLink(outSock, outSock))
        if plugin.isEmpty():
            raise _ContentError(f"node '{vnode.name}' exported nothing")

        if nodePlugin == 'TexDistance':
            _aimDistanceAtProbe(nodeCtx, vnode, plugin)
        elif nodePlugin == 'TexDirt':
            _raiseDirtSubdivs(nodeCtx, plugin)

        pluginType = plugin.pluginType or ''
        if pluginType.startswith('Mtl') or pluginType.startswith('MayaMtl'):
            return plugin

        if 'UVWGen' in pluginType:
            plugin = _exportBrdfLightConverter(nodeCtx, exportUVGridBitmap(nodeCtx, plugin))
        elif not pluginType.startswith('BRDF'):
            plugin = _exportBrdfLightConverter(nodeCtx, plugin)

        return _wrapInSingleBRDF(nodeCtx, plugin)


def _startJob(worker, storeKey, ownerKind, ownerName, groupName, nodeName, method, resolution, aspect):
    from vray_blender.bin import VRayBlenderLib as vray
    from vray_blender.exporting.tools import hasToonBRDF
    from vray_blender.lib.defs import NodeContext
    from vray_blender.lib.plugin_utils import updateValue
    from vray_blender.preview.bake import _makeExporterContext, _resolveOwnerTree

    owner, ntree = _resolveOwnerTree(ownerKind, ownerName, groupName)
    vnode = ntree.nodes.get(nodeName) if ntree else None
    if vnode is None:
        raise _TransientJobError(f"thumbnail source node '{nodeName}' not found")

    renderer = worker.renderer
    exporterCtx = _makeExporterContext(renderer)
    nodeCtx = NodeContext(exporterCtx, bpy.context.scene, bpy.data, renderer)
    nodeCtx.rootObj = owner
    nodeCtx.nodeTracker = exporterCtx.nodeTrackers['MTL']
    nodeCtx.ntree = ntree
    nodeCtx.material = owner if ownerKind == 'MTL' else None

    with nodeCtx:
        mtlPlugin = _exportThumbMaterial(nodeCtx, vnode)

    # All a request changes: the surface wearing the material, what is in frame, and the sampler
    subject, showBackdrop = _SUBJECT_BY_METHOD[method]
    updateValue(renderer, subject, "material", mtlPlugin)
    for node in _SUBJECT_NODES:
        updateValue(renderer, node, "visible", node == subject)
    updateValue(renderer, _BACKDROP_NODE, "visible", showBackdrop)

    toon = hasToonBRDF(vnode)
    for light in _AREA_LIGHTS:
        updateValue(renderer, light, "enabled", not toon)
    updateValue(renderer, _TOON_LIGHT, "enabled", toon)
    updateValue(renderer, _GI, "on", not toon)

    # Set on EVERY job, not just the traced methods
    maxSubdivs, threshold = _SAMPLER_BY_METHOD.get(method, _SAMPLER_DEFAULT)
    updateValue(renderer, _IMAGE_SAMPLER, "dmc_maxSubdivs", maxSubdivs)
    updateValue(renderer, _IMAGE_SAMPLER, "dmc_threshold", threshold)

    vray.setRenderSizes(renderer, _renderSizes(resolution, aspect))

    # Frame 0, not the scene's. Since the AppSDK setValueAsString rewrite the material assignment
    # only holds at frame 0; anywhere else thumbNode.material reverts to the loaded scene's own.
    vray.setRenderFrame(renderer, 0)

    # Completion is the image-ready event. A finished frame arrives as RendererState::Done, which
    # does not fire render-stopped. renderEnd() nulls this at the end of the job.
    worker.cbImageReady = lambda: _onImageReady(worker, storeKey)
    vray.setImageReadyCallback(renderer, worker.cbImageReady)

    vray.finishExport(renderer, False)

    if dumpPath := os.environ.get('VRAY_THUMBS_DUMP'):
        _dumpScene(renderer, method, dumpPath)

    # The SCENE is warm, the render SESSION is not. Leaving a session open makes the server
    # refuse every later scene change with "Cannot execute while rendering".
    vray.renderStart(renderer, 0, None, imageToBlender=True)
    vray.renderFrame(renderer)      # commits the changed material, then starts


def _dumpScene(renderer, method: str, dumpPath: str):
    """ Diagnostic: write the thumbnail scene so its plugins can be inspected. """
    from vray_blender.bin import VRayBlenderLib as vray

    exportSettings = vray.ExportSceneSettings()
    exportSettings.filePath = f"{dumpPath}_{method}.vrscene"
    exportSettings.compressed = False
    exportSettings.hostAppString = "Blender"
    exportSettings.pluginTypes = ""
    vray.writeVrscene(renderer, exportSettings)
    while vray.exportJobIsRunning(renderer):
        time.sleep(0.05)
    debug.printAlways(f"Node thumbnails: scene written to {exportSettings.filePath}")


def _renderSizes(resolution: int, aspect: float = 1.0):
    """ Frame size for a thumbnail. Square unless the preview is an equirect, which the draw handler lays out 2:1. """
    from vray_blender.lib.camera_utils import RenderSizes
    width = int(round(resolution * aspect))
    sizes = RenderSizes()
    sizes.bitmask = int(RenderSizes.Bitmask.ImgSize)
    sizes.imgWidth = width
    sizes.imgHeight = resolution
    sizes.bmpWidth = width
    sizes.bmpHeight = resolution
    return sizes


def _onImageReady(worker, storeKey):
    """ Native/ZMQ thread: hop to the main thread to read the image and store it. """
    def finish():
        _finishJob(worker, storeKey, False)
        return None

    try:
        bpy.app.timers.register(finish, first_interval=0.0)
    except Exception:
        debug.printError("Node thumbnails: could not schedule render completion")


def _finishJob(worker, storeKey, isAborted):
    from vray_blender.bin import VRayBlenderLib as vray

    if worker.job != storeKey:
        return      # superseded, timed out, or a late progressive notification

    try:
        image = None if isAborted else vray.getImage(worker.renderer)
        if image is None:
            debug.printWarning(f"Node thumbnails: no image for {storeKey[:12]}")
            _failed.add(storeKey)
        else:
            # getImage() reports (width, height, channels) but hands back the row-major image
            # already. Do not transpose it. The copy is required - the SHM buffer is reused.
            pixels = np.array(image, np.float32)
            _results[storeKey] = pixels
            _evictResults()
            _failed.discard(storeKey)
    except Exception as ex:
        debug.printError(f"Node thumbnails: reading the render failed: {ex}")
        _failed.add(storeKey)
    finally:
        # Close the render session; the loaded scene is not affected
        try:
            vray.renderEnd(worker.renderer)
        except Exception as ex:
            debug.printError(f"Node thumbnails: ending the render failed: {ex}")
        worker.job = None
        worker.slot = None
        worker.jobsDone += 1
        worker.lastUsed = time.monotonic()
        if worker.jobsDone >= _JOBS_PER_WORKER:
            _releaseWorker(worker)

    from vray_blender.nodes.utils import tagRedrawNodeEditor
    tagRedrawNodeEditor()
    pump()
