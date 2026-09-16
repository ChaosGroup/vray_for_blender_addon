# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

""" The single ordering authority for GeomScatter's targets and models lists.

    The preview, the render and the paint tools used to build these lists separately, which
    gave them different index spaces: a painted stroke stored an index from one and the scatter
    core read it as another. They all walk this module now.

    Ordering is the RENDER's - splines expand IN PLACE, one entry per polyline - because the
    core's global triangle index has to agree with the emitted list.
"""

import bpy
import numpy as np
from mathutils import Vector


TARGET_MESH, TARGET_SPLINE = 0, 1

# Geometry object types that can be a scatter MODEL. vray_blender's
# exporting.tools.GEOMETRY_OBJECT_TYPES without VOLUME - a volume has no surface and nothing
# GeomStaticMesh can instance. Kept local so this module needs no vray_blender. A LIGHT is a model
# too, on the extra condition in isScatterableLight - see isModelObject.
GEOMETRY_TYPES = ('MESH', 'META', 'SURFACE', 'FONT', 'CURVE', 'CURVES', 'POINTCLOUD')

# Object types that can be a scatter TARGET. Narrower than the model set: a target has to give
# the core a surface, and CURVES / POINTCLOUD tessellate to no triangles, so they can be neither
# scattered on nor painted on. Same list as vray_blender's exporting.tools.MESH_OBJECT_TYPES, and
# the same rule C4D applies (a target needs a polygon cache - see exporter_geometry.cpp).
TARGET_TYPES = ('MESH', 'META', 'SURFACE', 'FONT', 'CURVE')

# Light types with no per-instance position to scatter (an environment or infinitely distant
# light), plus MESH - a mesh light's plugins are named after its gizmos rather than after the
# object (light_export.getLightMeshInstanceNames), so the models list has no name to reference.
_UNSCATTERABLE_LIGHT_TYPES = ('SUN', 'DOME', 'AMBIENT', 'DIRECT', 'MESH')

# Smallest half-extent of a light model's stand-in box, on every axis. A point-like light (omni,
# spot, IES) has no emitting shape to measure at all, and a light that measures smaller than this
# is a speck in the viewport - a flat area light seen edge-on disappears completely.
_LIGHT_MIN_HALF_EXTENT = 0.25


def isScatterableLight(obj) -> bool:
    """ Whether a LIGHT object can be a scatter model - see _UNSCATTERABLE_LIGHT_TYPES. """
    light = obj.data
    # obj.data.vray is absent when the V-Ray addon is not installed; only Blender's SUN is infinite.
    vrayLight = getattr(light, 'vray', None)
    if (vrayLight is None) or (vrayLight.light_type == 'BLENDER'):
        return light.type != 'SUN'
    return vrayLight.light_type not in _UNSCATTERABLE_LIGHT_TYPES


def isModelObject(obj) -> bool:
    """ Whether an object can be a scatter model. The single membership rule: resolveModels and
        properties._pollModelObject (which also offers EMPTY roots) both walk it, so the picker
        cannot offer an object the resolver then drops.
    """
    from chaos_scatter.utils import isScatterObject
    if isScatterObject(obj):
        # A carrier as its own model makes the exported chain recursive (Node -> GeomInstancer ->
        # GeomScatter -> Node) and overflows the render thread's stack in compileGeometry. Enforced
        # here rather than only in the picker's poll, which is a UI filter and so misses both a
        # Python assignment and a carrier reached by hierarchy expansion under an EMPTY root.
        return False
    if obj.type == 'LIGHT':
        return isScatterableLight(obj)
    return obj.type in GEOMETRY_TYPES


def selectedTargets(context) -> list:
    """ The selected objects usable as distribution targets - the base a new scatter is built on.
        Same membership rule as properties._pollMeshTarget, applied to a selection.
    """
    from chaos_scatter.utils import isScatterObject
    return [o for o in context.selected_objects
            if (o.type in TARGET_TYPES) and not isScatterObject(o)]


class ResolvedTarget:
    """ One entry of the GeomScatter 'targets' list, in emitted order.

        listIndex is what the scatter core sees; itemIndex is the stable cs.targets index that
        painted strokes store. triBase is the running loop-triangle count of the preceding MESH
        entries, which is the GLOBAL triangle index instance_override_placements addresses.
    """
    __slots__ = ('kind', 'listIndex', 'itemIndex', 'object', 'factor',
                 'polyline', 'closed', 'triBase', 'triCount')

    def __init__(self, kind, listIndex, itemIndex, obj, factor):
        self.kind = kind
        self.listIndex = listIndex
        self.itemIndex = itemIndex
        self.object = obj
        self.factor = factor
        self.polyline = None    # TARGET_SPLINE: list of world-space vertex coords
        self.closed = False     # TARGET_SPLINE: closed polyline -> triangulate into a surface
        self.triBase = 0        # TARGET_MESH: loop triangles in all preceding MESH entries
        self.triCount = 0


class ResolvedModel:
    """ One entry of the GeomScatter 'models' list, in emitted order. """
    __slots__ = ('itemIndex', 'object', 'parentIndex', 'frequency', 'clusterGroupId')

    def __init__(self, itemIndex, obj, parentIndex, item):
        self.itemIndex = itemIndex
        self.object = obj
        self.parentIndex = parentIndex
        self.frequency = item.frequency
        self.clusterGroupId = item.cluster_group_id


def evaluatedMeshData(obj, depsgraph):
    """ The depsgraph-owned evaluated Mesh, or None if the object has none. Safe to hold.

        Only MESH objects have one. A CURVE, FONT or SURFACE evaluates to a Curve even when it
        does have faces, so those need withTemporaryMesh().
    """
    data = obj.evaluated_get(depsgraph).data
    return data if isinstance(data, bpy.types.Mesh) else None


def withTemporaryMesh(obj, depsgraph, fn):
    """ Call fn(mesh) on a tessellated copy of a curve-like object, then free it.

        The mesh must not outlive the call or be cached: to_mesh() keeps one temp mesh per
        object and frees the previous one on entry, so any later to_mesh() invalidates it.
    """
    evalObj = obj.evaluated_get(depsgraph)
    try:
        mesh = evalObj.to_mesh()
    except RuntimeError:
        return None
    if mesh is None:
        return None
    try:
        return fn(mesh)
    finally:
        evalObj.to_mesh_clear()


def _triangleCount(obj, depsgraph) -> int:
    """ Loop-triangle count of an object's evaluated geometry. """
    if (mesh := evaluatedMeshData(obj, depsgraph)) is not None:
        mesh.calc_loop_triangles()
        return len(mesh.loop_triangles)

    if obj.type not in ('CURVE', 'FONT', 'SURFACE', 'META'):
        return 0    # CURVES / POINTCLOUD: no triangles, and to_mesh() rejects them

    def count(mesh):
        mesh.calc_loop_triangles()
        return len(mesh.loop_triangles)

    return withTemporaryMesh(obj, depsgraph, count) or 0


def isSplineTarget(obj, depsgraph) -> bool:
    """ True for the targets resolveTargets emits as TARGET_SPLINE entries: a curve with no
        fill/bevel/extrude, so it has polylines but no surface.
    """
    return obj.type == 'CURVE' and _triangleCount(obj, depsgraph) == 0


def _edgeChains(mesh) -> list:
    """ Reassemble the ordered vertex chains of a wire mesh (all valences are 1 or 2). A cycle
        repeats its start vertex as the last element, which is how callers detect a closed
        polyline.
    """
    # 'vertices' is a pair of ints per edge - the buffer must be int32 or foreach_get falls back
    # to a per-element conversion instead of a straight copy. tolist() then hands back Python
    # ints, which is what the adjacency dict keys and the chain indices have to be.
    edgeVerts = np.empty(len(mesh.edges) * 2, dtype=np.int32)
    mesh.edges.foreach_get('vertices', edgeVerts)
    edgeVerts = edgeVerts.tolist()

    adjacency = {}
    for i in range(0, len(edgeVerts), 2):
        a, b = edgeVerts[i], edgeVerts[i + 1]
        adjacency.setdefault(a, []).append(b)
        adjacency.setdefault(b, []).append(a)

    visited = set()
    chains = []

    def walk(start):
        chain = [start]
        visited.add(start)
        prev, current = None, start
        while True:
            nextVerts = [v for v in adjacency[current] if v != prev and v not in visited]
            if not nextVerts:
                if prev is not None and start in adjacency[current] and len(chain) > 2:
                    chain.append(start)  # close the cycle explicitly
                break
            prev, current = current, nextVerts[0]
            chain.append(current)
            visited.add(current)
        return chain

    for vertIndex, neighbors in adjacency.items():
        if len(neighbors) == 1 and vertIndex not in visited:
            chains.append(walk(vertIndex))
    for vertIndex in adjacency:
        if vertIndex not in visited:
            chains.append(walk(vertIndex))

    return chains


def curvePolylines(curveObj, depsgraph: bpy.types.Depsgraph) -> list:
    """ [(worldSpaceVertexCoords, closed)] for every polyline of a face-less curve.

        Coordinates are copied out before the temp mesh is freed, so the result is safe to keep.
    """
    matrix = curveObj.evaluated_get(depsgraph).matrix_world

    def sample(mesh):
        # 'co' is float, so the buffer must be float32 - a float64 one makes foreach_get convert
        # element by element instead of copying. Transform the whole vertex array once rather
        # than doing a mathutils multiply per point: the 4x4 is applied as a point (implicit
        # w=1), which is exactly what `matrix @ vertex.co` did.
        co = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
        mesh.vertices.foreach_get('co', co)
        co = co.reshape(-1, 3)

        m = np.array(matrix, dtype=np.float64)
        world = co @ m[:3, :3].T + m[:3, 3]

        result = []
        for chain in _edgeChains(mesh):
            if len(chain) < 2:
                continue
            closed = len(chain) > 2 and chain[0] == chain[-1]
            # Plain [x, y, z] lists, not Vectors: building one mathutils object per point was the
            # single biggest cost here, and every consumer only does tuple(c) or iterates it.
            result.append((world[chain].tolist(), closed))
        return result

    return withTemporaryMesh(curveObj, depsgraph, sample) or []


def _localCorners(obj, depsgraph):
    """ The 8 corners of an object's geometry AABB in its own local space.

        Not Object.bound_box: it reports PRE-modifier bounds even on the evaluated object (a
        level-3 subdivided 2m cube still reads +-1.0 while its vertices reach +-0.84). Falls
        back to bound_box for objects with no mesh.
    """
    mesh = evaluatedMeshData(obj, depsgraph)
    if mesh is None or len(mesh.vertices) == 0:
        return [Vector(c) for c in obj.bound_box]

    co = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
    mesh.vertices.foreach_get('co', co)
    co = co.reshape(-1, 3)
    lo, hi = co.min(axis=0), co.max(axis=0)
    return [Vector((x, y, z)) for x in (lo[0], hi[0])
                              for y in (lo[1], hi[1])
                              for z in (lo[2], hi[2])]


def _vrayLightProps(light, pluginType: str):
    """ A V-Ray light's live property group: the one on its node tree when it has a node for the
        type, else the one on the light data. Mirrors vray_blender's lib_utils.getLightPropGroup,
        which this module cannot import; None when the V-Ray addon is not installed.
    """
    if light.node_tree is not None:
        for node in light.node_tree.nodes:
            if node.bl_idname == f'VRayNode{pluginType}':
                return getattr(node, pluginType, None)
    return getattr(getattr(light, 'vray', None), pluginType, None)


def _lightLocalCorners(obj):
    """ The 8 corners of a light model's stand-in box in its own local space. The render measures
        the light plugin itself instead (compatibility_light_bounding_box_mode), so this only has to
        be close enough for the preview's collision spacing and the box the viewport draws.
    """
    light = obj.data
    x = y = z = _LIGHT_MIN_HALF_EXTENT

    if light.type == 'AREA':
        # Half-sizes, exactly as the rectangle light is exported
        # (light_export._setLightRectLightAttrs): a disc or square shape ignores size_y.
        rect = _vrayLightProps(light, 'LightRectangle')
        isSquare = light.shape in ('SQUARE', 'DISK') or (rect is not None and rect.is_disc)
        x = max(light.size / 2.0, x)
        y = max((light.size if isSquare else light.size_y) / 2.0, y)
    elif getattr(getattr(light, 'vray', None), 'light_type', 'BLENDER') == 'SPHERE':
        # A sphere light's radius is on the V-Ray property group, not on the Blender light - the
        # same source the light gizmo reads (ui/draw_callbacks.py). The other point-like types have
        # no emitting shape at all: only a shadowRadius, which V-Ray does not treat as one either
        # (PhotoPointLight::getObjectBBox is a 1e-5 box whatever it is set to).
        if (sphere := _vrayLightProps(light, 'LightSphere')) is not None:
            x = y = z = max(sphere.radius, z)

    return [Vector((sx, sy, sz)) for sx in (-x, x) for sy in (-y, y) for sz in (-z, z)]


def buildChildMap() -> dict:
    """ parent -> [children] over the whole file. Object.children_recursive rebuilds exactly this
        map on EVERY access (O(len(bpy.data.objects)) - see its docstring), so anything expanding
        more than one root builds it once here and walks it with hierarchyObjects instead.
    """
    childMap = {}
    for obj in bpy.data.objects:
        if (parent := obj.parent) is not None:
            childMap.setdefault(parent, []).append(obj)
    return childMap


def hierarchyObjects(root, childMap: dict) -> list:
    """ root followed by every descendant, depth-first.

        Order matches [root] + root.children_recursive exactly, and must keep doing so: the
        expanded model order is the index space shared with scatter_export and with the
        model_index stored in clusters_layer_models, so it is not free to change.
    """
    out = [root]

    def recurse(parent):
        for child in childMap.get(parent, ()):
            out.append(child)
            recurse(child)

    recurse(root)
    return out


def hierarchyLocalBounds(rootObj, depsgraph, childMap: dict | None = None):
    """ Combined AABB of an object hierarchy in the ROOT's local space (children folded in via
        root^-1 @ child), so the model's world transform is excluded.

        The bounds come from the EVALUATED objects, i.e. with modifiers applied. That is what the
        preview wants: the stand-in box it sends the scatter core is what the core uses for
        collision spacing, and the render measures the real modified geometry. A light member has
        no geometry to measure and contributes _lightLocalCorners instead.

        Callers looping over several models should build one childMap and pass it in.
    """
    if childMap is None:
        childMap = buildChildMap()

    rootInv = rootObj.matrix_world.inverted_safe()
    lo = [float('inf')] * 3
    hi = [float('-inf')] * 3
    for o in hierarchyObjects(rootObj, childMap):
        if o.type == 'LIGHT':
            corners = _lightLocalCorners(o)
        elif o.type in GEOMETRY_TYPES:
            corners = _localCorners(o, depsgraph)
        else:
            continue
        toRoot = rootInv @ o.matrix_world
        for corner in corners:
            local = toRoot @ corner
            for axis in range(3):
                lo[axis] = min(lo[axis], local[axis])
                hi[axis] = max(hi[axis], local[axis])
    if lo[0] == float('inf'):
        lo, hi = [0.0] * 3, [0.0] * 3
    return lo, hi


def resolveTargets(cs, depsgraph: bpy.types.Depsgraph) -> list[ResolvedTarget]:
    """ The GeomScatter 'targets' list, in emitted order.

        Membership is decided here and nowhere else. If a consumer also skipped an entry it
        would shift every later index on its side only, so one that cannot represent an entry
        has to emit a placeholder rather than drop it.

        Resolving tessellates every curve target, so a caller that needs the list twice should
        keep it rather than call again.
    """
    resolved = []
    triRunning = 0

    for itemIndex, item in enumerate(cs.targets):
        # Must be the original. An evaluated copy reports session_uid 0, which is the
        # geometry cache key and the handle _findObject looks objects up by.
        obj = item.object.original if item.object is not None else None
        if obj is None or obj.type not in TARGET_TYPES:
            continue

        triCount = _triangleCount(obj, depsgraph)

        if obj.type == 'CURVE' and triCount == 0:
            # No fill/bevel/extrude: scatter on the polylines themselves, one entry each
            for coords, closed in curvePolylines(obj, depsgraph):
                entry = ResolvedTarget(TARGET_SPLINE, len(resolved), itemIndex, obj, item.factor)
                entry.polyline = coords
                entry.closed = closed
                resolved.append(entry)
            continue

        # Kept even at triCount == 0 (a degenerate mesh, or a curve with no points): the render
        # still gives it a Node, and dropping it here would desync the two lists.
        entry = ResolvedTarget(TARGET_MESH, len(resolved), itemIndex, obj, item.factor)
        entry.triBase = triRunning
        entry.triCount = triCount
        triRunning += triCount
        resolved.append(entry)

    return resolved


def resolveModels(cs, expandHierarchy: bool, childMap: dict | None = None) -> list[ResolvedModel]:
    """ The GeomScatter 'models' list, in emitted order.

        expandHierarchy is True for the render (each model item contributes its whole object
        hierarchy) and False for the preview (one stand-in box per item). The handle space is
        the same either way - see modelHandles.

        childMap is the caller's, when it has one; otherwise it is built on the first real model
        item and shared by the rest.
    """
    resolved = []

    for itemIndex, item in enumerate(cs.models):
        # Must be the original. buildChildMap keys on bpy.data.objects.
        root = item.object.original if item.object is not None else None
        if root is None:
            continue
        if childMap is None:
            childMap = buildChildMap()

        if not expandHierarchy:
            # An Empty root with scatterable children is a valid model - the preview boxes the
            # whole hierarchy. It must contain SOMETHING scatterable though, or its handle would
            # exist here but not on the render side and clusters_layer_models would disagree.
            if any(isModelObject(o) for o in hierarchyObjects(root, childMap)):
                resolved.append(ResolvedModel(itemIndex, root, -1, item))
            continue

        rootIndex = -1
        for obj in hierarchyObjects(root, childMap):
            if not isModelObject(obj):
                continue
            isRoot = (rootIndex == -1)
            if isRoot:
                rootIndex = len(resolved)
            resolved.append(ResolvedModel(itemIndex, obj, -1 if isRoot else rootIndex, item))

    return resolved


def modelHandles(resolved: list[ResolvedModel]) -> list[int]:
    """ GeomScatter's model_handles: one handle per emitted model, valued in the cs.models index
        space that layer.models[].model_index stores.

        Not range(len(cs.models)) - that is wrong on the render side (hierarchy expansion makes
        the emitted list longer) and on the preview side (items whose object is None are dropped),
        and either way it stops clusters_layer_models from matching.
    """
    return [m.itemIndex for m in resolved]
