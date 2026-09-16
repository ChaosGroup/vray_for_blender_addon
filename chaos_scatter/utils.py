# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

""" Small helpers shared across the Chaos Scatter addon.

    IMPORTANT: this module (and everything it imports) must not import vray_blender.
    The only V-Ray touch point of the addon is backend/vray_backend.py.
"""


def printInfo(msg: str):
    print(f"Chaos Scatter: {msg}")


def printError(msg: str):
    print(f"Chaos Scatter ERROR: {msg}")


def encodeStrokePoints(xyzFlat) -> tuple[str, int]:
    """ Pack a stroke's OBJECT-SPACE surface positions [x0,y0,z0, x1,y1,z1, ...] (3*N floats) into a
        base64 blob for a StringProperty.

        Positions - not baked (triangleIndex, barycentric) - are stored so cluster strokes survive
        target mesh edits/deformation: they are re-resolved to a triangle + barycentric at build time
        (see makeStrokePointResolver). Returns (base64Blob, numPoints).
    """
    import base64
    import numpy as np
    xyz = np.asarray(xyzFlat, dtype=np.float32).reshape(-1)
    return base64.b64encode(xyz.tobytes()).decode('ascii'), xyz.size // 3


def decodeStrokePoints(blob: str, numPoints: int):
    """ Inverse of encodeStrokePoints. Returns a flat float list [x0,y0,z0, ...] (3*numPoints). """
    import base64
    import numpy as np
    raw = base64.b64decode(blob.encode('ascii'))
    return np.frombuffer(raw[:numPoints * 3 * 4], dtype=np.float32).tolist()


def baryCoords(p, a, b, c):
    """ TRUE signed barycentric coords (w0,w1,w2) of p in triangle (a,b,c): p = a*w0+b*w1+c*w2. A
        point outside the triangle yields a negative weight (unlike mathutils.poly_3d_calc, whose
        generalized weights stay positive outside and cannot be used for containment). """
    v0 = b - a
    v1 = c - a
    v2 = p - a
    d00 = v0.dot(v0); d01 = v0.dot(v1); d11 = v1.dot(v1)
    d20 = v2.dot(v0); d21 = v2.dot(v1)
    denom = d00 * d11 - d01 * d01
    if abs(denom) < 1e-12:
        return None
    w1 = (d11 * d20 - d01 * d21) / denom
    w2 = (d00 * d21 - d01 * d20) / denom
    return (1.0 - w1 - w2, w1, w2)


def buildTriMap(mesh):
    """ polygon index -> [(loopTriIndex, (i0,i1,i2))]. loopTriIndex matches the faces order collect.py
        sends (mesh.loop_triangles iteration order). Caller must have run mesh.calc_loop_triangles(). """
    triMap = {}
    for ti, lt in enumerate(mesh.loop_triangles):
        triMap.setdefault(lt.polygon_index, []).append((ti, tuple(lt.vertices)))
    return triMap


def resolveTriangleAt(mesh, faceIdx, loc, triMap):
    """ Map a polygon hit (loc in the mesh's local space) to (loopTriIndex, u=weight(v1), v=weight(v2))
        using true-barycentric containment; nearest triangle as fallback. """
    cands = triMap.get(faceIdx)
    if not cands:
        return None
    best = None
    for triIdx, (i0, i1, i2) in cands:
        bary = baryCoords(loc, mesh.vertices[i0].co, mesh.vertices[i1].co, mesh.vertices[i2].co)
        if bary is None:
            continue
        w0, w1, w2 = bary
        mn = min(w0, w1, w2)
        if mn >= -1e-4:
            return triIdx, w1, w2
        if best is None or mn > best[0]:
            best = (mn, triIdx, w1, w2)
    return None if best is None else (best[1], best[2], best[3])


def makeStrokePointResolver(depsgraph, cs):
    """ Return resolve(targetIndex, (x,y,z)) -> (loopTriIndex, u, v) or None.

        Re-resolves a stored OBJECT-SPACE cluster-stroke point onto the target's CURRENT evaluated
        mesh (closest_point_on_mesh), so strokes stay correct across mesh edits / deformation instead
        of pointing at a stale baked triangle index. Per-target evaluated mesh + triangle map are
        cached for the lifetime of the returned closure.
    """
    import bpy
    from mathutils import Vector
    targets = cs.targets
    cache = {}

    def _entry(targetIndex):
        if targetIndex in cache:
            return cache[targetIndex]
        entry = None
        if 0 <= targetIndex < len(targets):
            tobj = targets[targetIndex].object
            if tobj is not None:
                evalObj = tobj.evaluated_get(depsgraph)
                mesh = getattr(evalObj, 'data', None)
                if isinstance(mesh, bpy.types.Mesh):
                    try:
                        mesh.calc_loop_triangles()
                        if len(mesh.loop_triangles) > 0:
                            entry = (evalObj, mesh, buildTriMap(mesh))
                    except RuntimeError:
                        entry = None
        cache[targetIndex] = entry
        return entry

    def resolve(targetIndex, xyz):
        entry = _entry(targetIndex)
        if entry is None:
            return None
        evalObj, mesh, triMap = entry
        hit, loc, _normal, faceIdx = evalObj.closest_point_on_mesh(Vector(xyz))
        if not hit:
            return None
        return resolveTriangleAt(mesh, faceIdx, loc, triMap)

    return resolve


def getScatterSettings(obj):
    """ Return the ChaosScatterSettings of a scatter carrier object, or None. """
    cs = getattr(obj, 'chaos_scatter', None)
    if (cs is not None) and cs.is_scatter:
        return cs
    return None


def pointCloudResizable() -> bool:
    """ True when the carrier should be a PointCloud, else a vertices-only Mesh. Both expose the
        same POINT-domain attribute API.

        4.5 has no PointCloud.resize(). 5.1 has it, but rna_PointCloud_resize reallocates the
        shared attribute storage without tagging the ID or the draw cache, so both keep buffers
        sized for the old point count. It also asserts size > 0, and an empty scatter resizes to 0.
    """
    import bpy
    if bpy.app.version < (5, 2, 0):
        return False
    return "resize" in bpy.types.PointCloud.bl_rna.functions


def isScatterObject(obj) -> bool:
    # The carrier is a PointCloud, or a vertices-only Mesh where a PointCloud cannot be sized
    # from Python (see pointCloudResizable). The propgroup flag is the actual marker.
    return (obj is not None) and (obj.type in ('POINTCLOUD', 'MESH')) \
        and (getScatterSettings(obj) is not None)


def sceneScatterObjects(scene):
    """ Yield all scatter carrier objects in the scene. """
    for obj in scene.objects:
        if isScatterObject(obj):
            yield obj


def allScatterObjects():
    """ Every scatter carrier in the file, including ones outside the active scene.
        Anything that decides what data may be FREED must use this, not sceneScatterObjects.

        A list, not a generator: bpy.data.objects' iterator pre-fetches the next element, so a
        caller that frees proxy objects as it goes would step onto freed memory - an access
        violation, not a Python exception. """
    import bpy
    return [obj for obj in bpy.data.objects if isScatterObject(obj)]


# Bumped whenever Blender reports a geometry update for an object, and folded into the target
# resourceId so the backend re-uploads a deformed mesh. Counts alone cannot see a deformation that
# keeps the vertex count, and hashing positions every tick would put an O(verts) walk on the
# interactive path - the depsgraph already knows, so just take its word for it.
_geomGenerations = {}   # session_uid -> int


def bumpGeometryGeneration(sessionUid: int) -> None:
    _geomGenerations[sessionUid] = _geomGenerations.get(sessionUid, 0) + 1


def geometryGeneration(sessionUid: int) -> int:
    return _geomGenerations.get(sessionUid, 0)


def isVRayEngine(context=None) -> bool:
    """ True when the scene renders with V-Ray. V-Ray exports GeomScatter natively, so the
        GN preview is free to stay lightweight; any other engine renders the GN preview
        itself and needs the full geometry. """
    import bpy
    scene = (context or bpy.context).scene
    return (scene is not None) and (scene.render.engine == 'VRAY_RENDER_RT')
