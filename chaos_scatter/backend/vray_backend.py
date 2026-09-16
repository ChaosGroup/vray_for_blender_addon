# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

""" V-Ray-backed scatter preview compute.

    Builds the scene for one scatter setup on the server's private headless renderer through the
    STANDARD plugin API (vray.pluginCreate / plugin_utils.updateValue) over a SCATTER_PREVIEW
    session, then triggers GeomUtils::readScatterData via requestScatterPreview and receives the
    instance transforms back. This is the ONLY V-Ray touch point of the chaos_scatter addon; all
    vray_blender imports happen lazily inside functions (the vray_blender ADDON may be disabled;
    its package is still importable from the addons path).

    Server startup goes through the shared ZMQProcess.ensureRunning() guard (single code path
    for both addons), never through vray.start() directly.
"""

import queue

import bpy
import numpy as np

from chaos_scatter import params as scatter_params
from chaos_scatter import resolve
from chaos_scatter import utils
from chaos_scatter.backend import ScatterComputeBackend, ScatterResult, SessionNotReady


# 12-triangle cube topology shared by every model stand-in box (corner order matches _boxVerts).
_UNIT_BOX_TRIS = [
    (0, 2, 1), (0, 3, 2), (4, 5, 6), (4, 6, 7),
    (0, 1, 5), (0, 5, 4), (1, 2, 6), (1, 6, 5),
    (2, 3, 7), (2, 7, 6), (3, 0, 4), (3, 4, 7),
]
# Per-corner normals (direction from the box center), just so the channel is non-null; the
# model stand-in geometry is never shaded, only its bounding box is used.
_INV_SQRT3 = 0.57735026
_UNIT_BOX_NORMALS = [
    (-_INV_SQRT3, -_INV_SQRT3, -_INV_SQRT3), (_INV_SQRT3, -_INV_SQRT3, -_INV_SQRT3),
    (_INV_SQRT3, _INV_SQRT3, -_INV_SQRT3), (-_INV_SQRT3, _INV_SQRT3, -_INV_SQRT3),
    (-_INV_SQRT3, -_INV_SQRT3, _INV_SQRT3), (_INV_SQRT3, -_INV_SQRT3, _INV_SQRT3),
    (_INV_SQRT3, _INV_SQRT3, _INV_SQRT3), (-_INV_SQRT3, _INV_SQRT3, _INV_SQRT3),
]

# Seconds readPreset blocks the Cosmos import tick waiting for the server's reply.
_PRESET_READ_TIMEOUT = 60.0


def _tryImportLib():
    try:
        from vray_blender.bin import VRayBlenderLib as vray
        return vray
    except Exception:
        return None


class VRayScatterBackend(ScatterComputeBackend):

    def __init__(self):
        self._unavailableReason = ""
        self._lib = None               # VRayBlenderLib, cached by submit()
        self._pluginUtils = None       # vray_blender.lib.plugin_utils, cached by submit()
        self._renderer = None          # SCATTER_PREVIEW SceneExporter handle (int)
        self._builtResources = set()   # content-hash plugin names already created this session
        self._usedBy = {}              # scatterId -> plugin names referenced by its last request
        self._defaultMtl = None
        self._nextRequestId = 1
        self._pending = {}             # requestId -> (onFinished, scatterPluginName)
        self._completed = queue.Queue()  # (requestId, status, errorText) from the msg thread
        self._deferredCancels = set()  # flushed on the next submit; see cancel()
        # bpy.app.timers keys on the callable's identity, and self.drainCompleted mints a
        # fresh bound method on every access - so the is_registered() guard only works
        # against one stable object.
        self._pump = self.drainCompleted

    def isAvailable(self) -> bool:
        if _tryImportLib() is None:
            self._unavailableReason = "V-Ray for Blender package not found"
            return False
        return True

    def isSessionReady(self) -> bool:
        """ Passive readiness test: "could I submit right now", without CAUSING a server start.

            Skips _ensureServer() - not because it blocks (measured at 0 ms, it does not), but
            because it starts the server as a side effect, and the load path must be able to ask
            the question without answering it in the affirmative. scatterPreviewStart() returns 0
            while ZmqServer is down or restarting (python_api.cpp), which is the whole test.
        """
        if (lib := _tryImportLib()) is None:
            return False
        return self._ensureSession(lib)

    def unavailableReason(self) -> str:
        return self._unavailableReason

    def submit(self, request: dict, onFinished) -> int:
        lib = _tryImportLib()
        self._ensureServer()
        if not self._ensureSession(lib):
            raise SessionNotReady("Scatter preview session unavailable (ZMQ server not running)")

        from vray_blender.lib import plugin_utils
        self._lib = lib
        self._pluginUtils = plugin_utils

        # Now that the session is known-good, tell the server to skip anything cancelled since
        # the last submit (see cancel()). Ids from a previous session are harmless - the server
        # simply never sees a request with that id.
        for cancelledId in self._deferredCancels:
            lib.cancelScatterPreview(self._renderer, cancelledId)
        self._deferredCancels.clear()

        scatterName = self._buildScene(request)

        requestId = self._nextRequestId
        self._nextRequestId += 1
        # The submitted cap travels with the request: the core reports how many instances it
        # PRODUCED, never that it clamped, so "limit reached" can only be recovered by comparing
        # the two - and by then the request params are gone.
        self._pending[requestId] = (onFinished, scatterName,
                                    int(request['params'].get('instance_count_limit', 0) or 0))

        lib.requestScatterPreview(self._renderer, requestId, scatterName, request['time'])
        return requestId

    def readPreset(self, filePath: str, unitRescale: float):
        """ Have the server read a Chaos Scatter preset config (.mbc) with
            GeomUtils::readScatterPreset and return (plugins, modelAssetIds) - see the base class.

            Blocking, driven by the native callback rather than a bpy.app.timers pump: the caller
            is the Cosmos import tick, which needs the parameters before it can build anything, and
            a timer would never fire under --background. queue.get releases the GIL while waiting,
            so the message thread can deliver the reply.
        """
        lib = _tryImportLib()
        self._ensureServer()
        if not self._ensureSession(lib):
            raise SessionNotReady("Scatter preset session unavailable (ZMQ server not running)")

        requestId = self._nextRequestId
        self._nextRequestId += 1

        replies = queue.Queue()

        def onPresetResult(resultId, status, errorText):
            if resultId == requestId:
                replies.put((status, errorText))
            else:
                # A reply to a request that already timed out: its own release call ran before the
                # result existed, so nothing else would ever free it.
                lib.scatterPresetReleaseResult(resultId)

        lib.setScatterPresetCallback(onPresetResult)
        try:
            lib.requestScatterPreset(self._renderer, requestId, filePath, unitRescale)
            try:
                status, errorText = replies.get(timeout=_PRESET_READ_TIMEOUT)
            except queue.Empty:
                raise RuntimeError(f"Timed out after {_PRESET_READ_TIMEOUT}s reading '{filePath}'")

            if status != ScatterResult.STATUS_OK:
                raise RuntimeError(errorText or f"Failed to read '{filePath}'")

            plugins, assetIds = lib.scatterPresetGetResult(requestId)
            if plugins is None:
                raise RuntimeError(f"No preset data was returned for '{filePath}'")
            return plugins, (assetIds or [])
        finally:
            lib.scatterPresetReleaseResult(requestId)

    def cancel(self, requestId: int):
        """ Forget a request. Dropping it locally is what matters - a result that still arrives
            is discarded upstream by the requestId mismatch; skipping the queued work on the
            server is only an optimization.

            Deferred to the next submit on purpose: the only way to re-validate self._renderer is
            scatterPreviewStart(), which CONSTRUCTS a session rather than querying one.
        """
        self._pending.pop(requestId, None)
        self._deferredCancels.add(requestId)

    def shutdown(self):
        """ Stop this addon's scatter preview session so disabling Chaos Scatter leaves no live
            session on the shared server. Native calls are made only while the server is up
            (isRunning() is safe even after vray.exit()); otherwise we just drop our state.
        """
        lib = _tryImportLib()
        if lib is not None and self._renderer is not None:
            try:
                if lib.isRunning():
                    lib.scatterPreviewStop()
            except Exception:
                pass
        self._renderer = None
        self._builtResources.clear()
        self._usedBy.clear()
        self._defaultMtl = None
        self._pending.clear()
        self._deferredCancels.clear()
        while not self._completed.empty():
            self._completed.get_nowait()
        if bpy.app.timers.is_registered(self._pump):
            bpy.app.timers.unregister(self._pump)

    # ---- server / session ----

    def _ensureServer(self):
        import vray_blender
        from vray_blender.engine.zmq_process import ZMQ
        vray_blender.initVRay()
        ZMQ.ensureRunning()

    def _ensureSession(self, lib) -> bool:
        renderer = lib.scatterPreviewStart()
        if not renderer:
            return False
        if renderer != self._renderer:
            # Fresh session (first use or after a server restart): rebuild everything
            self._renderer = renderer
            self._builtResources.clear()
            self._usedBy.clear()
            self._defaultMtl = None
        # Unconditional: ZmqServer::stop() clears the whole python callback registry, so a sticky
        # "already registered" flag would leave the preview permanently deaf after any restart.
        lib.setScatterPreviewCallback(self._onResultFromLib)
        return True

    # ---- scene building (via the standard plugin API) ----

    def _buildScene(self, request: dict) -> str:
        lib, plugin_utils = self._lib, self._pluginUtils
        renderer = self._renderer
        scatterId = request['scatterId']
        scatterName = f"cs:scatter:{scatterId}"

        self._ensureDefaultMaterial()

        usedPlugins = set()

        # One ordered pass. Splines stay where resolve put them rather than being appended
        # last, because that order is what the render emits and what the core's indices mean.
        targets = []
        for i, target in enumerate(request['targets']):
            if target['kind'] == resolve.TARGET_SPLINE:
                name = f"cs:tgtspline:{scatterId}:{i}"
                self._ensureSplineTarget(name, target)
                usedPlugins.add(name)
            else:
                meshName = self._ensureMesh(target, request['depsgraph'])
                name = f"cs:tgtnode:{scatterId}:{i}"
                self._createNode(geomName=meshName, tm=target['tm'], nodeName=name)
                usedPlugins.update((meshName, name))
            targets.append(name)

        models = []
        for i, model in enumerate(request['models']):
            # The stand-in box node sits at the model's PRESERVED linear transform, matching the
            # proxy objects - see params.preservedLinearMatrix.
            boxName = self._ensureModelBox(scatterId, i, model)
            nodeName = f"cs:modelnode:{scatterId}:{i}"
            self._createNode(geomName=boxName, tm=model['preservedLinear'], nodeName=nodeName)
            models.append(nodeName)
            usedPlugins.update((boxName, nodeName))

        densityMapName = ""
        if request['densityMap'] is not None:
            densityMapName = self._ensureBitmapTex(request['densityMap'], "densityTex")
            # The uvwgen must be claimed too, or _reapUnusedResources removes it on the next
            # request and _builtResources keeps _ensureBitmapTex from ever rebuilding it.
            usedPlugins.update((densityMapName, densityMapName + ":buf", densityMapName + ":uvw"))

        lib.pluginCreate(renderer, scatterName, "GeomScatter", False)
        for name, value in request['params'].items():
            plugin_utils.updateValue(renderer, scatterName, name, value)

        # Spline distribution and surface targets are mutually exclusive; collect fills exactly
        # one of the two, and both are written so switching modes clears the other.
        self._applySplines(scatterName, request['splines'])
        self._setPluginList(scatterName, "targets", targets)
        self._setFloatList(scatterName, "target_factors", request['targetFactors'])

        self._setPluginList(scatterName, "models", models)
        # Preview models are all roots; send the per-model arrays so frequency (the relative
        # selection weight) and cluster grouping match the render.
        self._setIntList(scatterName, "model_parents", [-1] * len(models))
        self._setFloatList(scatterName, "model_frequencies",
                           [m['frequency'] for m in request['models']])
        self._setIntList(scatterName, "model_cluster_group_ids",
                         [m['clusterGroupId'] for m in request['models']])
        # Color Map clustering's match key. The stand-in boxes are never shaded, so this tints
        # nothing - it decides WHICH model is placed where, so the preview needs it to put the
        # models in the same places the render does.
        self._setColorList(scatterName, "model_instance_colors",
                           [m['color'] for m in request['models']])

        # 'out_intensity': the param is a float texture and TexBitmap only exposes a float
        # interface through that output - see scatter_export._fillDensityMap.
        self._setPluginRef(scatterName, "surface_random_density_map", densityMapName, "out_intensity")

        # The COLOUR-typed maps (cluster colour + the three transform maps) take the plugin
        # reference directly, no output suffix. Written on every request, EMPTY included: the core
        # attaches its callbacks on the presence of the texture alone, so a map left wired would
        # stay live after the user cleared it.
        for attr, packed in request['colorMaps'].items():
            texName = self._ensureBitmapTex(packed, "colorTex") if packed is not None else ""
            if texName:
                usedPlugins.update((texName, texName + ":buf", texName + ":uvw"))
            self._setPluginRef(scatterName, attr, texName)
        if viewName := self._applyCamera(scatterId, request['camera']):
            usedPlugins.add(viewName)
            # Preview-only override of the shared params: ALWAYS SelectedCamera, whichever mode the
            # user picked. In RenderCamera mode the core reads the frustum out of the FRAME DATA
            # (fov, focalDistance, camera transform) - and this renderer is never start()ed, so all
            # of it stays at defaults. SelectedCamera reads fov/focalDistance/transform/orthographic
            # straight off the camera plugin instead, which is the only way the preview can clip
            # against a real camera. collect.buildScatterRequest already resolved WHICH camera that
            # is (params.clippingCameraObject). The render keeps the user's mode: it HAS frame data.
            plugin_utils.updateValue(renderer, scatterName, "camera_clipping_mode", 1)
            self._setPluginRef(scatterName, "camera_clipping_selected_cam", viewName)
        else:
            self._setPluginRef(scatterName, "camera_clipping_selected_cam", "")
        if helperName := self._applyLookAt(scatterName, scatterId, request['lookAtTarget']):
            usedPlugins.add(helperName)
        self._applyFalloffCurves(scatterName, request['falloffCurves'])
        self._applyAreas(scatterName, request['areas'])
        self._applyClusterArrays(scatterName, request['clusterArrays'])
        self._applyInstanceOverride(scatterName, request['instanceOverride'])

        self._reapUnusedResources(scatterId, usedPlugins)
        return scatterName

    def _ensureDefaultMaterial(self):
        # A Node without a material is dropped by V-Ray, so every target/model Node needs one.
        if self._defaultMtl is not None:
            return
        lib = self._lib
        brdfName = "cs:defaultBRDF"
        mtlName = "cs:defaultMtl"
        lib.pluginCreate(self._renderer, brdfName, "BRDFDiffuse", False)
        lib.pluginCreate(self._renderer, mtlName, "MtlSingleBRDF", False)
        self._pluginUtils.updateValue(self._renderer, mtlName, "brdf", _attrPlugin(lib, brdfName))
        self._defaultMtl = mtlName

    def _ensureMesh(self, target: dict, depsgraph) -> str:
        """ Upload a target's geometry as a GeomStaticMesh, by pointer.

            vray.exportGeometry reads Blender's own arrays through MeshData - no verts/faces/uvs
            are copied into Python first. asyncExport=False on purpose: the pointers must not
            outlive this call, and _buildScene tessellates further curve targets afterwards
            (Object.to_mesh frees the previous temp mesh on entry).
        """
        from vray_blender.lib.defs import MeshData

        meshName = f"cs:tgtmesh:{_stableHash(target['resourceId'])}"
        if meshName in self._builtResources:
            return meshName

        def upload(mesh):
            meshData = MeshData.fromMesh(mesh, meshName)
            meshData.options.forceDynamicGeometry = False
            meshData.options.exportEdgeVisibility = True
            self._lib.exportGeometry(self._renderer, meshData, False)
            return True

        obj = target['object']
        if (mesh := resolve.evaluatedMeshData(obj, depsgraph)) is not None:
            upload(mesh)
        else:
            # A curve/text/surface target: its evaluated data is a Curve, so tessellate briefly
            resolve.withTemporaryMesh(obj, depsgraph, upload)

        self._builtResources.add(meshName)
        return meshName

    def _ensureSplineTarget(self, name: str, splineTgt: dict):
        # A face-less curve target as a GeomScatterSpline (vertices + triangulate flag). Rebuilt
        # per request (cheap, curve-dependent) rather than content-cached like meshes.
        lib = self._lib
        lib.pluginCreate(self._renderer, name, "GeomScatterSpline", False)
        lib.pluginUpdateVectorList(self._renderer, name, "vertices", _flatFloats(splineTgt['vertices']))
        lib.pluginUpdateInt(self._renderer, name, "triangulate", splineTgt['triangulate'], True)

    def _ensureModelBox(self, scatterId: str, index: int, model: dict) -> str:
        # A box mesh (local-space) approximating the model's bounding box, used only as the
        # scatter core's spacing/placement stand-in - the viewport instances the real geometry.
        lib = self._lib
        boxName = f"cs:modelbox:{scatterId}:{index}"
        verts = _boxVerts(model['bboxMin'], model['bboxMax'])
        lib.pluginCreate(self._renderer, boxName, "GeomStaticMesh", False)
        lib.pluginUpdateVectorList(self._renderer, boxName, "vertices", _flatFloats(verts))
        lib.pluginUpdateIntList(self._renderer, boxName, "faces", _flatInts(_UNIT_BOX_TRIS))
        lib.pluginUpdateVectorList(self._renderer, boxName, "normals", _flatFloats(_UNIT_BOX_NORMALS))
        lib.pluginUpdateIntList(self._renderer, boxName, "faceNormals", _flatInts(_UNIT_BOX_TRIS))
        return boxName

    def _ensureBitmapTex(self, packed: dict, prefix: str) -> str:
        """ A user image as a TexBitmap on the preview renderer, content-cached by resourceId.

            packed['asData'] picks the colour management, exactly as the two render-side helpers do:
            a density map is DATA (raw/linear, sampled for its intensity), while the cluster colour
            map and the transform maps are COLOUR - the core matches a model's viewport colour
            against the sampled texel, so a gamma applied here would break the match.
        """
        lib, plugin_utils = self._lib, self._pluginUtils
        asData = packed.get('asData', True)
        texName = f"cs:{prefix}:{_stableHash(packed['resourceId'])}"
        if texName in self._builtResources:
            return texName

        bufName = texName + ":buf"
        if 'filePath' in packed:
            lib.pluginCreate(self._renderer, bufName, "BitmapBuffer", False)
            plugin_utils.updateValue(self._renderer, bufName, "file", packed['filePath'])
        else:
            lib.pluginCreate(self._renderer, bufName, "RawBitmapBuffer", False)
            # 'pixels' is an INT_LIST that V-Ray reinterpret-casts per pixels_type - NOT a float
            # list. pixels_type 1 (float RGBA) means four float32 bit patterns per pixel, and
            # V-Ray rows run top-to-bottom while Blender's buffer is bottom-to-top. See the
            # decoder for the same layout in nodes/importing/creators.py:_imageFromRawBitmap.
            w, h = packed['width'], packed['height']
            values = np.asarray(packed['pixels'], dtype=np.float32)
            rgba = np.empty((h, w, 4), dtype=np.float32)
            if packed.get('channels', 1) == 3:
                rgba[:, :, :3] = values.reshape(h, w, 3)
            else:
                rgba[:, :, 0] = rgba[:, :, 1] = rgba[:, :, 2] = values.reshape(h, w)
            rgba[:, :, 3] = 1.0
            rgba = np.ascontiguousarray(np.flipud(rgba))
            lib.pluginUpdateIntList(self._renderer, bufName, "pixels", rgba.ravel().view(np.int32))
            plugin_utils.updateValue(self._renderer, bufName, "width", w)
            plugin_utils.updateValue(self._renderer, bufName, "height", h)
            plugin_utils.updateValue(self._renderer, bufName, "pixels_type", 1)  # float RGBA

        if asData:
            plugin_utils.updateValue(self._renderer, bufName, "transfer_function", 0)  # linear
            plugin_utils.updateValue(self._renderer, bufName, "gamma", 1.0)

        lib.pluginCreate(self._renderer, texName, "TexBitmap", False)
        plugin_utils.updateValue(self._renderer, texName, "bitmap", _attrPlugin(lib, bufName))

        # Without a uvwgen a TexBitmap returns nouvw_color (0.5) and the map is inert. Channel -1
        # hands the UVW back to GeomScatter's map_channel - see scatter_export._fillDensityMap.
        uvwName = texName + ":uvw"
        lib.pluginCreate(self._renderer, uvwName, "UVWGenChannel", False)
        plugin_utils.updateValue(self._renderer, uvwName, "uvw_channel", -1)
        plugin_utils.updateValue(self._renderer, texName, "uvwgen", _attrPlugin(lib, uvwName))

        self._builtResources.add(texName)
        return texName

    def _createNode(self, geomName: str, tm, nodeName: str):
        lib, plugin_utils = self._lib, self._pluginUtils
        lib.pluginCreate(self._renderer, nodeName, "Node", False)
        plugin_utils.updateValue(self._renderer, nodeName, "geometry", _attrPlugin(lib, geomName))
        plugin_utils.updateValue(self._renderer, nodeName, "material", _attrPlugin(lib, self._defaultMtl))
        plugin_utils.updateValue(self._renderer, nodeName, "transform", _matrix(tm))

    # ---- list writers -------------------------------------------------------------------
    # Every list attribute below is written on EVERY request, empty included. An attribute that
    # is simply not written keeps its previous value on the server, so a removed area modifier or
    # a cleared paint layer would otherwise persist for the life of the preview session.

    def _setIntList(self, pluginName: str, attr: str, values):
        values = [int(v) for v in values]
        if values:
            self._lib.pluginUpdateIntList(self._renderer, pluginName, attr, values)
        else:
            self._lib.pluginResetValue(self._renderer, pluginName, attr)

    def _setFloatList(self, pluginName: str, attr: str, values):
        values = [float(v) for v in values]
        if values:
            self._lib.pluginUpdateFloatList(self._renderer, pluginName, attr, values)
        else:
            self._lib.pluginResetValue(self._renderer, pluginName, attr)

    def _setVectorList(self, pluginName: str, attr: str, values):
        # values: already-flat x,y,z floats
        if len(values):
            self._lib.pluginUpdateVectorList(self._renderer, pluginName, attr, _flatFloats(values))
        else:
            self._lib.pluginResetValue(self._renderer, pluginName, attr)

    def _setColorList(self, pluginName: str, attr: str, colors):
        """ A COLOR_LIST param. Goes out as the generic element-typed list ('c'), the same wire the
            render uses for model_instance_colors (scatter_export._colorListValue). """
        from mathutils import Color
        from vray_blender.lib.defs import AttrListValue

        if not len(colors):
            self._lib.pluginResetValue(self._renderer, pluginName, attr)
            return
        listValue = AttrListValue()
        for c in colors:
            listValue.append(Color((c[0], c[1], c[2])))
        self._pluginUtils.updateValue(self._renderer, pluginName, attr, listValue)

    def _setPluginList(self, pluginName: str, attr: str, names: list[str]):
        # updateValue maps an empty list to pluginResetValue, so this needs no branch
        self._pluginUtils.updateValue(self._renderer, pluginName, attr,
                                      [_attrPlugin(self._lib, n) for n in names])

    def _setPluginRef(self, pluginName: str, attr: str, name: str, output: str = None):
        if name:
            self._pluginUtils.updateValue(self._renderer, pluginName, attr,
                                          _attrPlugin(self._lib, name, output))
        else:
            self._lib.pluginResetValue(self._renderer, pluginName, attr)

    def _applyCamera(self, scatterId: str, camera) -> str:
        """ A RenderView on the private preview renderer, so camera clipping culls against the
            USER's camera. The session has no scene camera of its own, so without this the core
            clips against a default view at the origin and the preview disagrees with the render
            wherever camera_clipping is on.

            Returns the view's plugin name, or "" when there is no camera (so the caller leaves it
            out of the used set and _reapUnusedResources removes any previous view).
        """
        if camera is None:
            return ""
        plugin_utils = self._pluginUtils
        viewName = f"cs:renderview:{scatterId}"
        self._lib.pluginCreate(self._renderer, viewName, "RenderView", False)
        plugin_utils.updateValue(self._renderer, viewName, "transform",
                                 _matrix(camera['tm']) @ _aspectFix(camera['aspect']))
        plugin_utils.updateValue(self._renderer, viewName, "fov", camera['fovRad'])
        # Read straight off this plugin in SelectedCamera mode - see _buildScene for why we use it
        plugin_utils.updateValue(self._renderer, viewName, "focalDistance", camera['focalDistance'])
        plugin_utils.updateValue(self._renderer, viewName, "orthographic", camera['isOrtho'])
        plugin_utils.updateValue(self._renderer, viewName, "orthographicWidth", camera['orthoWidth'])
        plugin_utils.updateValue(self._renderer, viewName, "clipping", True)
        plugin_utils.updateValue(self._renderer, viewName, "clipping_near", camera['clipStart'])
        plugin_utils.updateValue(self._renderer, viewName, "clipping_far", camera['clipEnd'])
        return viewName

    def _applyLookAt(self, scatterName: str, scatterId: str, lookAtTm) -> str:
        """ The look-at target is referenced through a helper Node carrying only its world
            transform, exactly as scatter_export._fillLookAt does for the render.

            Returns the helper's plugin name, or "" when look-at is off (see _applyCamera).
        """
        if lookAtTm is None:
            self._setPluginRef(scatterName, "look_at_target", "")
            return ""
        helperName = f"cs:lookat:{scatterId}"
        self._lib.pluginCreate(self._renderer, helperName, "Node", False)
        self._pluginUtils.updateValue(self._renderer, helperName, "transform", _matrix(lookAtTm))
        self._pluginUtils.updateValue(self._renderer, helperName, "visible", False)
        self._setPluginRef(scatterName, "look_at_target", helperName)
        return helperName

    def _applyFalloffCurves(self, scatterName: str, curves: dict):
        """ Both falloff curves. The samples come from the shared decoder
            (chaos_scatter.curves.parseFalloffData), so they are byte-identical to what
            scatter_export sends for the same curve.
        """
        for attr in ('surface_altitude_limit_falloff_curve', 'look_at_falloff_curve'):
            points = curves.get(attr, [])
            self._setVectorList(scatterName, attr, [c for p in points for c in p])
            self._pluginUtils.updateValue(self._renderer, scatterName, attr + "_interp",
                                          scatter_params.FALLOFF_INTERP_LINEAR)

    def _reapUnusedResources(self, scatterId: str, usedPlugins: set):
        """ Remove the plugins this scatter built on a previous request and no longer references.
            Content-hashed meshes are shared between scatter objects, so a name is only removed
            once no scatter claims it - otherwise resizing one scatter's target list would delete
            a mesh another scatter is still pointing at.
        """
        previous = self._usedBy.get(scatterId, set())
        self._usedBy[scatterId] = usedPlugins

        # Drop the claims of scatter objects that no longer exist, or a deleted scatter would pin
        # its meshes in the session forever - which is the leak this whole method exists to close.
        live = {obj.chaos_scatter.curve_ns for obj in utils.allScatterObjects()}
        for goneId in [k for k in self._usedBy if k not in live and k != scatterId]:
            previous |= self._usedBy.pop(goneId)

        stillClaimed = set()
        for otherId, names in self._usedBy.items():
            if otherId != scatterId:
                stillClaimed |= names

        for name in previous - usedPlugins - stillClaimed:
            self._lib.pluginRemove(self._renderer, name)
            self._builtResources.discard(name)

    def _applySplines(self, scatterName: str, splines: dict):
        self._setVectorList(scatterName, "spline_vertices", splines['vertices'])
        self._setIntList(scatterName, "spline_vertex_counts", splines['counts'])

    def _applyClusterArrays(self, scatterName: str, arrays: dict):
        # ClustersByLayers list params (model_handles + clusters_layer_*). Sent as raw int/float
        # lists, not via the scalar params dict. Empty stroke lists (base-only) are valid.
        floatLists = ('clusters_layer_stroke_radiuses', 'clusters_layer_stroke_points')
        intLists = tuple(n for n in scatter_params.CLUSTER_ARRAY_NAMES if n not in floatLists)
        for name in intLists:
            self._setIntList(scatterName, name, arrays[name])
        for name in floatLists:
            self._setFloatList(scatterName, name, arrays[name])

    def _applyInstanceOverride(self, scatterName: str, ov: dict):
        # Hand-painted PLACED instances: info packs (3 ints each) + barycentric placements
        # (VECTOR_LIST, flat x,y,z per point where z = float(globalTriangleIndex)).
        self._setIntList(scatterName, "instance_override_info_packs", ov['info_packs'])
        flat = [c for p in ov['placements'] for c in (float(p[0]), float(p[1]), float(p[2]))]
        self._setVectorList(scatterName, "instance_override_placements", flat)
        # A PLACED instance carries no transform, so this list is always empty - but it still has
        # to be written every request or a stale one survives for the life of the session.
        assert not ov['transforms'], "instance_override transforms are not implemented yet"
        self._lib.pluginResetValue(self._renderer, scatterName, "instance_override_transforms")

    def _applyAreas(self, scatterName: str, areas: dict):
        self._setVectorList(scatterName, "area_modifiers_vertices", areas['vertices'])
        self._setIntList(scatterName, "area_modifiers_vertex_counts", areas['counts'])
        self._setIntList(scatterName, "area_modifiers_operation", areas['operation'])
        self._setFloatList(scatterName, "area_modifiers_falloff_near", areas['falloffNear'])
        self._setFloatList(scatterName, "area_modifiers_falloff_far", areas['falloffFar'])
        self._setFloatList(scatterName, "area_modifiers_scale", areas['scale'])
        self._setFloatList(scatterName, "area_modifiers_density", areas['density'])
        self._setIntList(scatterName, "area_modifiers_axis", areas['axis'])

    # ---- result marshaling ----

    def _onResultFromLib(self, requestId, status, errorText, instanceCount):
        """ Invoked by VRayBlenderLib on the exporter's message thread. Only marshal to the main
            thread; bpy data must not be touched here, and nothing native may be read either -
            scatterPreviewGetResult is called from the pump below, on the main thread.

            bpy.app.timers.register is not thread-safe (BLI_timer_register does an unlocked list
            append), so this queues and registers ONE guarded pump rather than a timer per result.
            That single cross-thread registration is the same house pattern zmq_process.py uses;
            removing it entirely needs a main-thread wake primitive the addon does not have.
        """
        self._completed.put((requestId, status, errorText))
        if not bpy.app.timers.is_registered(self._pump):
            bpy.app.timers.register(self._pump)

    def drainCompleted(self):
        """ Main thread: fetch each finished result, hand it to its owner, release it. """
        lib = _tryImportLib()
        while True:
            try:
                requestId, status, errorText = self._completed.get_nowait()
            except queue.Empty:
                break

            entry = self._pending.pop(requestId, None)
            if entry is None:
                # Cancelled while in flight - nobody wants the payload, just free it
                self._releaseResult(lib, requestId)
                continue
            onFinished, _scatterName, countLimit = entry

            result = ScatterResult()
            if status != 0:
                result.status = ScatterResult.STATUS_ERROR
                result.errorText = errorText or "Scatter preview failed"
            else:
                try:
                    transforms, topo = lib.scatterPreviewGetResult(requestId)
                    result.transforms = transforms
                    result.topo = topo
                    # Landing exactly on the cap is the only evidence of clamping the core gives
                    # us - it returns the produced count, not the count it wanted to produce.
                    result.countLimitHit = (countLimit > 0) and (len(transforms) >= countLimit)
                except Exception as exc:
                    result.status = ScatterResult.STATUS_ERROR
                    result.errorText = str(exc)
            try:
                onFinished(requestId, result)
            finally:
                self._releaseResult(lib, requestId)
        # same re-arm race as recompute._applyPending
        return None if self._completed.empty() else 0.0

    @staticmethod
    def _releaseResult(lib, requestId):
        try:
            lib.scatterPreviewReleaseResult(requestId)
        except RuntimeError:
            pass


# --------------------------------------------------------------------------
# AttrValue construction helpers (via the AttrListValue machinery in lib.defs)
# --------------------------------------------------------------------------

def _attrPlugin(lib, name, output=None):
    # 'output' defaults to AttrPlugin.OUTPUT_UNDEFINED, which is NOT the same as '' (the plugin's
    # default output) - every caller that omits it must keep the value it had before.
    from vray_blender.lib.defs import AttrPlugin
    return AttrPlugin(name, output)


def _matrix(tm16):
    from mathutils import Matrix
    return Matrix((tm16[0:4], tm16[4:8], tm16[8:12], tm16[12:16]))


# The image aspect the scatter core will use for the PREVIEW frustum. It reads imgWidth/imgHeight
# off the frame data even in SelectedCamera mode, and this renderer is never start()ed - so the
# frame data stays at VRayFrameData::setDefaults(), 640x480, whatever the render resolution is.
_PREVIEW_FRAME_ASPECT = 640.0 / 480.0


def _aspectFix(aspect: float):
    """ Camera-space correction that makes the core's fixed-aspect frustum match the real one.

        The core builds the frustum corners as width = tan(fov/2) * focalDistance and
        height = width / imageAspect, then transforms them by the camera matrix. imageAspect is not
        ours to set (see _PREVIEW_FRAME_ASPECT), and one (fov, focalDistance) pair cannot express
        both extents - but a non-uniform camera transform can: scaling camera-space Y by
        previewAspect/realAspect leaves the width alone and gives the height its real value.
        Camera-space Z is untouched, so the view direction the core derives is unaffected.
    """
    from mathutils import Matrix
    return Matrix.Diagonal((1.0, _PREVIEW_FRAME_ASPECT / max(1e-6, aspect), 1.0, 1.0))


def _flatFloats(verts):
    """ (N,3)/(N,2) array or list of vectors -> contiguous float32 array for pluginUpdateVectorList. """
    return np.ascontiguousarray(np.asarray(verts, dtype=np.float32).reshape(-1))


def _flatInts(indices):
    return np.ascontiguousarray(np.asarray(indices, dtype=np.int32).reshape(-1))


def _boxVerts(bboxMin, bboxMax):
    # 8 corners in the box's local space, ordered to match _UNIT_BOX_TRIS / _UNIT_BOX_NORMALS.
    x0, y0, z0 = bboxMin
    x1, y1, z1 = bboxMax
    return [
        (x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
        (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1),
    ]


def _stableHash(text: str) -> str:
    import hashlib
    return hashlib.sha1(text.encode()).hexdigest()[:12]
