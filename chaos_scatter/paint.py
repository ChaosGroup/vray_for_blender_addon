# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

""" Interactive paint tools for Chaos Scatter.

    Two viewport modal brushes share one raycast + GPU-cursor core (_PaintModalMixin):
      - Cluster paint ('Paint' clustering mode): records barycentric strokes on the active PAINTED
        layer (per-target triangle index; override the base's model choice in the painted region).
      - Instance paint: places individual GeomScatter instances via instance_override_* PLACED
        (GLOBAL triangle index + barycentric); add or erase. Model is distribution-driven.

    See reference_geomscatter_cluster_layers and reference_instance_override_declarative for the core
    contracts (layer semantics, barycentric convention u=weight(v1)/v=weight(v2), global vs per-target
    triangle index). GPU/modal idioms mirror the vray_blender addon's ui/draw_callbacks.py.
"""

import math

from typing import NamedTuple

import bpy
from bpy_extras import view3d_utils
from mathutils import Vector

from chaos_scatter import recompute, resolve, utils


_shader = None


def _getShader():
    global _shader
    if _shader is None:
        import gpu
        _shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    return _shader


def _activeScatter(context):
    obj = context.object
    return obj if utils.isScatterObject(obj) else None


def _activePaintedLayer(cs):
    idx = cs.clusters.layers_active_index
    if 1 <= idx < len(cs.clusters.layers):
        return cs.clusters.layers[idx]
    return None


def ensureBaseLayer(cs):
    """ Guarantee a base layer (index 0) exists, seeded with all current models. Idempotent. """
    layers = cs.clusters.layers
    if len(layers) == 0:
        base = layers.add()
        base.name = "Base"
        for i in range(len(cs.models)):
            base.models.add().model_index = i


class PaintTarget(NamedTuple):
    """ One paintable target, as the modal caches it for the life of the brush.

        itemIndex is the cs.targets index a stroke stores. No global triangle index is baked here:
        strokes and painted instances are stored as OBJECT-SPACE points and re-resolved against the
        current evaluated meshes at build time (params.buildInstanceOverrideArrays).
    """
    evalObj: bpy.types.Object
    mesh: bpy.types.Mesh
    worldMat: object
    worldInv: object
    triMap: object
    itemIndex: int


def _buildPaintTargets(depsgraph, resolvedTargets: list[resolve.ResolvedTarget]) -> list[PaintTarget]:
    """ The paintable subset of the SHARED resolved target list (resolve.resolveTargets), so the
        order here, the order the preview builds and the order the render exports are one and the
        same.
    """
    targets = []
    for entry in resolvedTargets:
        if entry.kind != resolve.TARGET_MESH:
            continue
        # Only objects with a real evaluated Mesh can be painted: the triMap and the raycast both
        # index it for the whole life of the modal, and a curve/text target's tessellation only
        # exists as a temp mesh that any other to_mesh() call would free under us.
        mesh = resolve.evaluatedMeshData(entry.object, depsgraph)
        if mesh is None:
            continue
        mesh.calc_loop_triangles()
        if len(mesh.loop_triangles) == 0:
            continue
        evalObj = entry.object.evaluated_get(depsgraph)
        worldMat = entry.object.matrix_world.copy()
        targets.append(PaintTarget(evalObj, mesh, worldMat, worldMat.inverted_safe(),
                                   utils.buildTriMap(mesh), entry.itemIndex))
    return targets


def _hoveredView3D(context, event):
    """ (area, region, rv3d, regionCoord) for the 3D viewport under the cursor, or None.

        A modal operator's context is frozen to the region it was INVOKED from, and both paint
        buttons live in the sidebar / Object Data tab, neither of which has a RegionView3D. So the
        viewport has to be found from the window-absolute mouse position instead of context.region.
    """
    screen = context.window.screen if context.window else None
    if screen is None:
        return None
    for area in screen.areas:
        if area.type != 'VIEW_3D':
            continue
        for region in area.regions:
            if region.type != 'WINDOW':
                continue
            x = event.mouse_x - region.x
            y = event.mouse_y - region.y
            if 0 <= x < region.width and 0 <= y < region.height:
                rv3d = region.data   # per-region: in quad view they differ
                return (area, region, rv3d, (x, y)) if rv3d is not None else None
    return None


def _raycast(region, rv3d, targets, mouseCoord):
    """ Nearest target hit -> (targetIndex, localTriIndex, u, v, worldHit, worldNormal) or None. """
    origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, mouseCoord)
    direction = view3d_utils.region_2d_to_vector_3d(region, rv3d, mouseCoord)
    best = None
    for tIdx, target in enumerate(targets):
        worldInv = target.worldInv
        hit, loc, nrm, faceIdx = target.evalObj.ray_cast(worldInv @ origin,
                                                         worldInv.to_3x3() @ direction)
        if not hit:
            continue
        worldHit = target.worldMat @ loc
        dist = (worldHit - origin).length
        resolved = utils.resolveTriangleAt(target.mesh, faceIdx, loc, target.triMap)
        if resolved is None:
            continue
        triIdx, u, v = resolved
        if best is None or dist < best[0]:
            best = (dist, tIdx, triIdx, u, v, worldHit,
                    (target.worldMat.to_3x3() @ nrm).normalized())
    if best is None:
        return None
    return best[1], best[2], best[3], best[4], best[5], best[6]


def _drawBrushRing(worldHit, normal, radius, color):
    import gpu
    from gpu_extras.batch import batch_for_shader
    n = normal.normalized()
    ref = Vector((0.0, 0.0, 1.0)) if abs(n.z) < 0.9 else Vector((1.0, 0.0, 0.0))
    t = n.cross(ref).normalized()
    b = n.cross(t).normalized()
    segs = 48
    coords = [worldHit + (t * math.cos(2.0 * math.pi * i / segs) + b * math.sin(2.0 * math.pi * i / segs)) * radius
              for i in range(segs + 1)]
    shader = _getShader()
    batch = batch_for_shader(shader, 'LINE_STRIP', {"pos": coords})
    prevWidth = gpu.state.line_width_get()
    gpu.state.blend_set('ALPHA')
    gpu.state.line_width_set(2.0)
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)
    gpu.state.line_width_set(prevWidth)
    gpu.state.blend_set('NONE')


# --------------------------------------------------------------------------
# Cluster layer / model-subset management operators
# --------------------------------------------------------------------------

class CSCATTER_OT_cluster_add_layer(bpy.types.Operator):
    bl_idname = "chaos_scatter.cluster_add_layer"
    bl_label = "Add Paint Layer"
    bl_description = "Add a painted cluster layer above the base layer"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _activeScatter(context) is not None

    def execute(self, context):
        obj = _activeScatter(context)
        cs = obj.chaos_scatter
        ensureBaseLayer(cs)
        layer = cs.clusters.layers.add()
        layer.name = f"Layer {len(cs.clusters.layers) - 1}"
        if len(cs.models) > 0:
            layer.models.add().model_index = 0
        cs.clusters.layers_active_index = len(cs.clusters.layers) - 1
        recompute.markDirty(obj)
        return {'FINISHED'}


class CSCATTER_OT_cluster_remove_layer(bpy.types.Operator):
    bl_idname = "chaos_scatter.cluster_remove_layer"
    bl_label = "Remove Paint Layer"
    bl_description = "Remove the active painted layer (the base layer cannot be removed)"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = _activeScatter(context)
        return obj is not None and obj.chaos_scatter.clusters.layers_active_index >= 1

    def execute(self, context):
        obj = _activeScatter(context)
        cs = obj.chaos_scatter
        idx = cs.clusters.layers_active_index
        if idx >= 1:
            cs.clusters.layers.remove(idx)
            cs.clusters.layers_active_index = min(idx, len(cs.clusters.layers) - 1)
            recompute.markDirty(obj)
        return {'FINISHED'}


class CSCATTER_OT_cluster_layer_model_add(bpy.types.Operator):
    bl_idname = "chaos_scatter.cluster_layer_model_add"
    bl_label = "Add Model To Layer"
    bl_description = "Add a model slot to the active layer's model subset"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = _activeScatter(context)
        if obj is None:
            return False
        clusters = obj.chaos_scatter.clusters
        return 0 <= clusters.layers_active_index < len(clusters.layers)

    def execute(self, context):
        obj = _activeScatter(context)
        cs = obj.chaos_scatter
        layer = cs.clusters.layers[cs.clusters.layers_active_index]
        layer.models.add().model_index = 0
        layer.models_active_index = len(layer.models) - 1
        recompute.markDirty(obj)
        return {'FINISHED'}


class CSCATTER_OT_cluster_layer_model_remove(bpy.types.Operator):
    bl_idname = "chaos_scatter.cluster_layer_model_remove"
    bl_label = "Remove Model From Layer"
    bl_description = "Remove the active model slot from the active layer's model subset"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = _activeScatter(context)
        if obj is None:
            return False
        clusters = obj.chaos_scatter.clusters
        return 0 <= clusters.layers_active_index < len(clusters.layers)

    def execute(self, context):
        obj = _activeScatter(context)
        cs = obj.chaos_scatter
        layer = cs.clusters.layers[cs.clusters.layers_active_index]
        i = layer.models_active_index
        if 0 <= i < len(layer.models):
            layer.models.remove(i)
            layer.models_active_index = min(i, len(layer.models) - 1)
            recompute.markDirty(obj)
        return {'FINISHED'}


class CSCATTER_OT_cluster_clear_strokes(bpy.types.Operator):
    bl_idname = "chaos_scatter.cluster_clear_strokes"
    bl_label = "Clear Strokes"
    bl_description = "Remove all painted strokes from the active layer"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = _activeScatter(context)
        return obj is not None and _activePaintedLayer(obj.chaos_scatter) is not None

    def execute(self, context):
        obj = _activeScatter(context)
        _activePaintedLayer(obj.chaos_scatter).strokes.clear()
        recompute.markDirty(obj)
        return {'FINISHED'}


class CSCATTER_OT_instance_clear(bpy.types.Operator):
    bl_idname = "chaos_scatter.instance_clear"
    bl_label = "Clear Painted Instances"
    bl_description = "Remove all hand-painted instances"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _activeScatter(context) is not None

    def execute(self, context):
        obj = _activeScatter(context)
        obj.chaos_scatter.instance_paint.instances.clear()
        recompute.markDirty(obj)
        return {'FINISHED'}


# --------------------------------------------------------------------------
# Shared modal paint core
# --------------------------------------------------------------------------

class _PaintModalMixin:
    bl_options = {'REGISTER'}

    # _resolvedTargets is optionally pre-filled by a subclass invoke(); _hoveredArea only exists
    # after the first mouse move. Declared so both always read as a defined value.
    _resolvedTargets = None
    _hoveredArea = None

    # ---- subclass hooks ----
    def _radius(self):            raise NotImplementedError
    def _spacingWorld(self):      return self._radius() * 0.3
    def _isErase(self):           return False
    def _brushColor(self):        return (0.9, 0.3, 0.1, 1.0)
    def _adjustRadius(self, m):   pass
    def _toggleErase(self):       pass
    def _endStroke(self, context, points):  return False   # points: [(tIdx,localTri,u,v,worldHit)]

    # ---- modal machinery ----
    def invoke(self, context, event):
        self._obj = _activeScatter(context)
        self._cs = self._obj.chaos_scatter
        self._depsgraph = context.evaluated_depsgraph_get()
        if self._resolvedTargets is None:
            self._resolvedTargets = resolve.resolveTargets(self._cs, self._depsgraph)
        self._targets = _buildPaintTargets(self._depsgraph, self._resolvedTargets)
        if not self._targets:
            self.report({'WARNING'}, "Chaos Scatter: no valid mesh targets to paint on")
            return {'CANCELLED'}
        self._painting = False
        self._targetsStale = False
        self._cursor = None            # (worldHit, worldNormal)
        self._points = []
        self._lastWorld = None
        self._handle = bpy.types.SpaceView3D.draw_handler_add(self._draw, (), 'WINDOW', 'POST_VIEW')
        context.window_manager.modal_handler_add(self)
        context.workspace.status_text_set("LMB: paint   ESC/RMB: finish   [ ]: radius   E: erase")
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if event.type in {'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE'}:
            return {'PASS_THROUGH'}
        if event.type in {'RIGHTMOUSE', 'ESC'}:
            self._finish(context)
            return {'FINISHED'}
        if event.type == 'LEFT_BRACKET' and event.value == 'PRESS':
            self._adjustRadius(0.8); self._tagRedraw(context); return {'RUNNING_MODAL'}
        if event.type == 'RIGHT_BRACKET' and event.value == 'PRESS':
            self._adjustRadius(1.25); self._tagRedraw(context); return {'RUNNING_MODAL'}
        if event.type == 'E' and event.value == 'PRESS':
            self._toggleErase(); self._tagRedraw(context); return {'RUNNING_MODAL'}

        if event.type == 'MOUSEMOVE':
            self._cursor = None
            hit = self._raycastHovered(context, event)
            if hit is not None:
                self._cursor = (hit[4], hit[5])
                if self._painting:
                    self._accumulate(hit)
            self._tagRedraw(context)
            return {'RUNNING_MODAL'}

        if event.type == 'LEFTMOUSE':
            if event.value == 'PRESS':
                self._painting = True
                self._points = []
                self._lastWorld = None
                hit = self._raycastHovered(context, event)
                if hit is not None:
                    self._accumulate(hit)
                return {'RUNNING_MODAL'}
            if event.value == 'RELEASE':
                self._painting = False
                if self._points and self._endStroke(context, self._points):
                    recompute.markDirty(self._obj)
                    self._targetsStale = True   # the recompute invalidates our cached meshes
                    bpy.ops.ed.undo_push(message="Chaos Scatter: Paint")
                self._points = []
                return {'RUNNING_MODAL'}
        return {'RUNNING_MODAL'}

    def _raycastHovered(self, context, event):
        if self._targetsStale:
            # A recompute since the last stroke re-evaluated the depsgraph and swapped the
            # carrier's datablock, so the cached evaluated meshes and triMaps are dead references.
            self._depsgraph = context.evaluated_depsgraph_get()
            self._targets = _buildPaintTargets(
                self._depsgraph, resolve.resolveTargets(self._cs, self._depsgraph))
            self._targetsStale = False
        hovered = _hoveredView3D(context, event)
        self._hoveredArea = hovered[0] if hovered else None
        if hovered is None:
            return None
        _area, region, rv3d, regionCoord = hovered
        return _raycast(region, rv3d, self._targets, regionCoord)

    def _accumulate(self, hit):
        tIdx, triIdx, u, v, worldHit, _n = hit
        if self._lastWorld is not None and (worldHit - self._lastWorld).length < self._spacingWorld():
            return
        self._points.append((tIdx, triIdx, u, v, worldHit))
        self._lastWorld = worldHit

    def _draw(self):
        if self._cursor is None:
            return
        worldHit, normal = self._cursor
        color = (1.0, 0.15, 0.15, 1.0) if self._isErase() else self._brushColor()
        _drawBrushRing(worldHit, normal, self._radius(), color)

    def _tagRedraw(self, context):
        # The invoking area is the sidebar / properties editor, so redraw the hovered viewport
        area = self._hoveredArea or context.area
        if area is not None:
            area.tag_redraw()

    def cancel(self, context):
        # Blender tears a modal down on file load / quit WITHOUT calling modal(), and only runs
        # cleanup if cancel() exists. Drop the cached Area first: it belongs to the screen being
        # freed and, not being an ID, gets no RNA invalidation.
        self._hoveredArea = None
        self._finish(context)

    def _finish(self, context):
        self._cursor = None
        if context.workspace is not None:
            context.workspace.status_text_set(None)
        if getattr(self, '_handle', None) is not None:
            try:
                bpy.types.SpaceView3D.draw_handler_remove(self._handle, 'WINDOW')
            except (ValueError, RuntimeError):
                pass
            self._handle = None
        self._tagRedraw(context)


class CSCATTER_OT_paint_cluster(_PaintModalMixin, bpy.types.Operator):
    bl_idname = "chaos_scatter.paint_cluster"
    bl_label = "Paint Clusters"
    bl_description = ("Paint the active cluster layer onto the scatter targets. "
                      "LMB drag to paint, ESC/RMB to finish. [ ] radius, E erase")

    @classmethod
    def poll(cls, context):
        obj = _activeScatter(context)
        if obj is None:
            return False
        clusters = obj.chaos_scatter.clusters
        # No VIEW_3D area requirement: the button is reachable from the Object Data properties tab
        # too, and the modal finds the viewport under the cursor itself (_hoveredView3D).
        return (clusters.clustered_distribution_enabled
                and clusters.clustered_distribution_mode == '2'
                and _activePaintedLayer(obj.chaos_scatter) is not None)

    def _radius(self):       return self._cs.clusters.paint_radius
    def _isErase(self):      return self._cs.clusters.paint_erase
    def _brushColor(self):
        layer = _activePaintedLayer(self._cs)
        col = layer.color if layer is not None else (0.9, 0.3, 0.1)
        return (col[0], col[1], col[2], 1.0)

    def _adjustRadius(self, m):
        self._cs.clusters.paint_radius = max(0.0001, self._cs.clusters.paint_radius * m)

    def _toggleErase(self):
        self._cs.clusters.paint_erase = not self._cs.clusters.paint_erase

    def _endStroke(self, context, points):
        layer = _activePaintedLayer(self._cs)
        if layer is None:
            return False
        clusters = self._cs.clusters
        # Store OBJECT-SPACE surface positions per target (re-resolved to bary at build time so the
        # stroke survives target mesh edits/deformation). One stroke record per target.
        byTarget = {}
        for tIdx, _triIdx, _u, _v, worldHit in points:
            local = self._targets[tIdx].worldInv @ worldHit
            byTarget.setdefault(tIdx, []).extend((local.x, local.y, local.z))
        for tIdx, xyzFlat in byTarget.items():
            blob, n = utils.encodeStrokePoints(xyzFlat)
            stroke = layer.strokes.add()
            # The cs.targets index, NOT the paint-list index - utils.makeStrokePointResolver
            # and params.buildClusterArrays both read it as storage-space.
            stroke.target_index = self._targets[tIdx].itemIndex
            stroke.erase = clusters.paint_erase
            stroke.sub_layer = clusters.paint_sub_layer
            stroke.radius = clusters.paint_radius
            stroke.num_points = n
            stroke.points_blob = blob
        return True


class CSCATTER_OT_paint_instances(_PaintModalMixin, bpy.types.Operator):
    bl_idname = "chaos_scatter.paint_instances"
    bl_label = "Paint Instances"
    bl_description = ("Paint individual scatter instances onto the targets. "
                      "LMB drag to add (or erase), ESC/RMB to finish. [ ] radius, E erase")

    @classmethod
    def poll(cls, context):
        obj = _activeScatter(context)
        if obj is None:
            return False
        # Reachable from the Object Data properties tab too; the modal raycasts only over a viewport.
        return obj.chaos_scatter.instance_paint.instance_paint_enabled

    def _radius(self):       return self._cs.instance_paint.paint_radius
    def _spacingWorld(self):
        ip = self._cs.instance_paint
        return max(1e-4, ip.paint_radius * ip.paint_spacing)
    def _isErase(self):      return self._cs.instance_paint.paint_erase
    def _brushColor(self):   return (0.2, 0.7, 1.0, 1.0)

    def _adjustRadius(self, m):
        self._cs.instance_paint.paint_radius = max(0.0001, self._cs.instance_paint.paint_radius * m)

    def _toggleErase(self):
        self._cs.instance_paint.paint_erase = not self._cs.instance_paint.paint_erase

    def invoke(self, context, event):
        # Resolve once and hand it to the base invoke: resolveTargets tessellates every curve
        # target, so paying for it twice per brush activation is pure waste.
        cs = _activeScatter(context).chaos_scatter
        self._resolvedTargets = resolve.resolveTargets(cs, context.evaluated_depsgraph_get())
        return super().invoke(context, event)

    def _endStroke(self, context, points):
        ip = self._cs.instance_paint
        if ip.paint_erase:
            return self._eraseNear(points)
        targetItems = self._cs.targets
        for tIdx, _triIdx, _u, _v, worldHit in points:
            target = self._targets[tIdx]
            # Store the OBJECT-SPACE point and the target OBJECT; the triangle and the barycentric
            # coordinates are re-resolved at build time, so the instance follows target edits and
            # survives the target list being reordered or extended.
            local = target.worldInv @ worldHit
            inst = ip.instances.add()
            inst.uid = ip.next_uid
            ip.next_uid += 1
            inst.target = targetItems[target.itemIndex].object
            inst.px, inst.py, inst.pz = local.x, local.y, local.z
        return True

    def _eraseNear(self, points):
        ip = self._cs.instance_paint
        r2 = ip.paint_radius ** 2
        # Bring each stored object-space point to world through its target's CURRENT matrix. Using
        # a world position cached at paint time left the hit box behind the moment the target moved.
        toRemove = set()
        for _t, _tri, _u, _v, worldHit in points:
            for i, inst in enumerate(ip.instances):
                if inst.target is None:
                    continue
                world = inst.target.matrix_world @ Vector((inst.px, inst.py, inst.pz))
                if (world - worldHit).length_squared <= r2:
                    toRemove.add(i)
        for i in sorted(toRemove, reverse=True):
            ip.instances.remove(i)
        return bool(toRemove)


_CLASSES = (
    CSCATTER_OT_cluster_add_layer,
    CSCATTER_OT_cluster_remove_layer,
    CSCATTER_OT_cluster_layer_model_add,
    CSCATTER_OT_cluster_layer_model_remove,
    CSCATTER_OT_cluster_clear_strokes,
    CSCATTER_OT_instance_clear,
    CSCATTER_OT_paint_cluster,
    CSCATTER_OT_paint_instances,
)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
