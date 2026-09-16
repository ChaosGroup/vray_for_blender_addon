# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

""" Assemble the preview compute request from the evaluated scene.

    The request is a plain dict (the backend translates it to the wire format):
      scatterId     curve_ns of the scatter object
      time          scene frame (float)
      params        scalar GeomScatter params (params.buildScatterParams, forPreview=True)
      targets       ORDERED, in resolve.resolveTargets order - splines expand IN PLACE so the
                    index the scatter core sees matches the render's. Each entry carries 'kind':
                      TARGET_MESH   {kind, object, resourceId, tm (16 floats row-major)} - the
                                     geometry itself is read by the backend via
                                     vray.exportGeometry, never copied here
                      TARGET_SPLINE {kind, vertices [(x,y,z),...] world-space, triangulate int}
                                    (a face-less curve target -> GeomScatterSpline, as in Maya)
      targetFactors one float per targets entry
      splines       {vertices, counts, tms} (world-space polylines; only 1D mode fills them)
      areas         {vertices, counts, operation, falloffNear, falloffFar, scale, density,
                     axis} (parallel arrays, one entry per polyline)
      models        [{bboxMin, bboxMax, preservedLinear, frequency, clusterGroupId}]
      densityMap    {resourceId, filePath} or {resourceId, width, height, pixels} or None
      lookAtTarget  16 floats (world matrix of the look-at object) or None
      falloffCurves {geomScatterAttr: [(x, y, 0.0), ...]}, only the enabled ones
      camera        {tm, fovRad, focalDistance, aspect, imgWidth, imgHeight, isOrtho, orthoWidth,
                     clipStart, clipEnd} or None - the camera clipping frustum, from whichever
                     camera params.clippingCameraObject resolves for the current mode

    Mesh resource ids are name + triangle count: a target that did not change reuses the
    server-side plugin without re-uploading.
"""

import hashlib
import json
import math
import os

import bpy
import numpy as np

from chaos_scatter import params as scatter_params
from chaos_scatter import resolve
from chaos_scatter import utils


def buildScatterRequest(obj: bpy.types.Object, depsgraph: bpy.types.Depsgraph,
                        resolvedTargets=None, resolvedModels=None, childMap=None) -> dict:
    cs = obj.chaos_scatter
    scene = depsgraph.scene

    # One ordered target/model list, shared with the render exporter and the paint tools. In 1D
    # spline mode there are no target plugins at all, so the list is empty on both sides and
    # painted strokes have nothing to address - matching scatter_export.
    # _submit passes the lists it already resolved; resolving again would re-tessellate every
    # curve target (see resolveTargets).
    if resolvedTargets is None:
        resolvedTargets = [] if cs.scatter_type == '0' else resolve.resolveTargets(cs, depsgraph)
    if resolvedModels is None:
        resolvedModels = resolve.resolveModels(cs, expandHierarchy=False, childMap=childMap)

    request = {
        'scatterId': cs.curve_ns,
        'time': float(scene.frame_current),
        'params': scatter_params.buildScatterParams(cs, forPreview=True),
        'clusterArrays': scatter_params.buildClusterArrays(
            cs, utils.makeStrokePointResolver(depsgraph, cs), resolvedTargets, resolvedModels),
        'instanceOverride': scatter_params.buildInstanceOverrideArrays(
            cs, utils.makeStrokePointResolver(depsgraph, cs), resolvedTargets),
        'targets': [],
        'targetFactors': [],
        'models': [],
        'densityMap': None,
        'lookAtTarget': None,
        'falloffCurves': {},
        'camera': None,
        # The backend reads target geometry by pointer at submit time (same thread, same tick)
        'depsgraph': depsgraph,
    }

    # Filled only in 1D mode, but always present so switching away from it clears the polylines.
    request['splines'] = _collectSplines(
        (i.object for i in cs.targets) if cs.scatter_type == '0' else (), depsgraph)

    if cs.scatter_type != '0':
        # ONE ordered list: splines expand in place, exactly as the render exports them, so the
        # scatter core sees the same target index on both sides. Nothing may be skipped here -
        # resolveTargets already dropped the degenerate entries, and a second filter would shift
        # every later index on the preview side only.
        for entry in resolvedTargets:
            if entry.kind == resolve.TARGET_SPLINE:
                packed = {
                    'kind': resolve.TARGET_SPLINE,
                    'vertices': [tuple(c) for c in entry.polyline],
                    'triangulate': 1 if entry.closed else 0,
                }
            else:
                # A reference to the target's geometry, NOT a copy: the backend hands the mesh to
                # V-Ray through vray.exportGeometry, which reads Blender's arrays by pointer.
                # resourceId is name + triangle count + the depsgraph's geometry generation. The
                # generation is what makes a same-count DEFORMATION invalidate the backend's
                # cached upload; without it the preview keeps scattering on the stale surface.
                original = entry.object.original
                packed = {
                    'kind': resolve.TARGET_MESH,
                    'object': entry.object,
                    'resourceId': f"{entry.object.name}:{entry.triCount}"
                                  f":{utils.geometryGeneration(original.session_uid)}",
                    'tm': _flatMatrix(entry.object.matrix_world),
                }
            request['targets'].append(packed)
            request['targetFactors'].append(entry.factor)

    if childMap is None:
        childMap = resolve.buildChildMap()
    for entry in resolvedModels:
        # Local bbox + the model's PRESERVED linear transform (only the rotation/scale the user
        # chose to preserve; never translation) - see params.preservedLinearMatrix.
        bboxMin, bboxMax = resolve.hierarchyLocalBounds(entry.object, depsgraph, childMap)
        request['models'].append({
            'bboxMin': bboxMin,
            'bboxMax': bboxMax,
            'preservedLinear': _flatMatrix(scatter_params.preservedLinearMatrix(cs, entry.object)),
            'frequency': entry.frequency,
            'clusterGroupId': entry.clusterGroupId,
            # The Color Map clustering MATCH KEY, not a tint - same source the render exports
            # from (scatter_export._fillModels).
            'color': tuple(entry.object.color[:3]),
        })

    request['areas'] = _collectAreas(cs, depsgraph)
    request['falloffCurves'] = _collectFalloffCurves(cs)
    request['colorMaps'] = _collectColorMaps(cs)

    # Custom Map pattern only - see scatter_export._fillDensityMap for why the pattern gates this.
    if (img := cs.surface.surface_random_density_map) and cs.surface.surface_random_density_map_pattern == '1':
        request['densityMap'] = packImage(img)

    lookAt = cs.look_at
    if lookAt.look_at_enabled and lookAt.look_at_target is not None:
        request['lookAtTarget'] = _flatMatrix(lookAt.look_at_target.matrix_world)

    if (camObj := scatter_params.clippingCameraObject(cs, scene)) is not None:
        request['camera'] = packCamera(camObj, scene)

    return request


def _collectColorMaps(cs) -> dict:
    """ {geomScatterAttr: packedImage | None} for the COLOUR-typed texture params.

        The gates mirror scatter_export._fillClusterColorMap / _fillTransformMaps exactly: the core
        attaches its callbacks on the presence of the texture alone, never on the mode meant to
        consume it, so a map left wired stays live after the user switches away. An entry that is
        None is written EMPTY by the backend rather than skipped.
    """
    clusters, tr = cs.clusters, cs.transforms
    colorMapActive = scatter_params.colorMapClusteringActive(cs)
    return {
        'cluster_instances_color_map':
            packImage(clusters.cluster_instances_color_map, asData=False) if colorMapActive else None,
        'transforms_translation_map': packImage(tr.transforms_translation_map, asData=False),
        'transforms_rotation_map':    packImage(tr.transforms_rotation_map, asData=False),
        'transforms_scale_map':       packImage(tr.transforms_scale_map, asData=False),
    }


def _collectFalloffCurves(cs) -> dict:
    """ {geomScatterAttr: denseSamples} for the falloff curves that are switched on. Uses the same
        serialized-curve decoder the render exporter uses, so both send identical samples.
    """
    from chaos_scatter.curves import parseFalloffData

    out = {}
    for attr, data in (('surface_altitude_limit_falloff_curve',
                        cs.surface.surface_altitude_limit_falloff_data),
                       ('look_at_falloff_curve', cs.look_at.look_at_falloff_data)):
        if (points := parseFalloffData(data)) is not None:
            out[attr] = points
    return out


def computeFingerprint(obj: bpy.types.Object, depsgraph: bpy.types.Depsgraph) -> str:
    """ Hash of everything that determines the preview result. Cheap by design: dependency
        matrices + mesh counts, not full geometry (a same-count deformation slips through -
        covered by the manual Refresh).

        Every value is read through `depsgraph`, so the same scene state hashes the same from
        the viewport and from a render, and a render frame hashes as itself.
    """
    cs = obj.evaluated_get(depsgraph).chaos_scatter
    h = hashlib.sha1()
    h.update(json.dumps(scatter_params.buildScatterParams(cs, forPreview=True),
                        sort_keys=True, default=str).encode())
    # Painted data is not scalar params - fold it in so painting invalidates. Hash the RAW stored
    # strokes (cheap), not the re-resolved arrays: mesh changes are already covered by addObj below
    # (target matrices + vertex/poly counts), same as the rest of the fingerprint.
    clusters = cs.clusters
    h.update(f"{clusters.clustered_distribution_enabled}:{clusters.clustered_distribution_mode}".encode())
    for layer in clusters.layers:
        h.update(b"L")
        for m in layer.models:
            h.update(np.int32(m.model_index).tobytes())
        for stroke in layer.strokes:
            h.update(f"{stroke.target_index}:{stroke.erase}:{stroke.sub_layer}"
                     f":{stroke.radius}:{stroke.num_points}".encode())
            h.update(stroke.points_blob.encode())
    # The STORED painted instances, not the built arrays: the triangle index and barycentrics are
    # derived from the target meshes, whose topology this fingerprint already covers below.
    ip = cs.instance_paint
    h.update(f"ip:{bool(ip.instance_paint_enabled)}:{len(ip.instances)}".encode())
    for inst in ip.instances:
        h.update(f"{inst.uid}:{inst.target.name if inst.target else ''}"
                 f":{inst.px}:{inst.py}:{inst.pz}".encode())

    def hashOne(o):
        h.update(o.name.encode())
        h.update(np.asarray(o.evaluated_get(depsgraph).matrix_world, dtype=np.float32).tobytes())
        # Viewport display color: the Color Map clustering MATCH KEY, so it is a PLACEMENT input
        # (model_instance_colors), not just a display setting.
        h.update(np.asarray(o.color, dtype=np.float32).tobytes())
        data = o.data
        if isinstance(data, bpy.types.Mesh):
            h.update(f"{len(data.vertices)}:{len(data.polygons)}".encode())

    # One map for all ~12 dependencies: children_recursive would rebuild it per access, and this
    # runs for every scatter on every undo/redo.
    childMap = resolve.buildChildMap()

    def addObj(o):
        if o is None:
            return
        for obj in resolve.hierarchyObjects(o, childMap):
            hashOne(obj)

    for item in cs.targets:
        addObj(item.object)
    for item in cs.models:
        addObj(item.object)
    for item in cs.area_modifiers:
        addObj(item.object)
        h.update(f"{item.operation}:{item.falloff_near}:{item.falloff_far}"
                 f":{item.scale}:{item.density}:{item.axis}".encode())
    addObj(cs.look_at.look_at_target)
    # The camera the frustum is actually built from - in Render Camera mode that is scene.camera,
    # NOT camera_clipping_selected_cam. Hashing the wrong one left the preview clipping against
    # where the render camera used to be until something else dirtied the scatter.
    if (camObj := scatter_params.clippingCameraObject(cs, depsgraph.scene)) is not None:
        addObj(camObj)
        # The frustum's vertical extent comes from the IMAGE aspect (packCamera), so a resolution
        # change reshapes it without touching any object.
        render = depsgraph.scene.render
        h.update(f"cam:{render.resolution_x}:{render.resolution_y}:{render.resolution_percentage}"
                 f":{render.pixel_aspect_x}:{render.pixel_aspect_y}".encode())

    for item in cs.targets:
        h.update(np.float32(item.factor).tobytes())
    for item in cs.models:
        h.update(np.float32(item.frequency).tobytes())
        h.update(np.int32(item.cluster_group_id).tobytes())

    return h.hexdigest()


def packImage(img, asData: bool = True) -> dict | None:
    """ File-backed unmodified images travel as a path; everything else as baked float pixels.

        asData=True collapses the baked pixels to single-channel luminance, which is all a density
        map needs. The cluster colour map and the transform maps are read as COLOUR - the core
        matches a model's viewport colour against the sampled texel - so they keep RGB, and the
        resourceId is hashed over what is actually uploaded so the two never share a cache slot.
    """
    if img is None:
        return None

    filePath = ""
    if img.source == 'FILE' and not img.is_dirty and img.packed_file is None:
        filePath = bpy.path.abspath(img.filepath, library=img.library)
        if not os.path.isfile(filePath):
            filePath = ""

    if filePath:
        return {'resourceId': f"img:{filePath}", 'filePath': filePath, 'asData': asData}

    w, h = img.size
    if w == 0 or h == 0:
        return None
    if w * h > 2048 * 2048:
        utils.printError(f"Scatter map '{img.name}' is larger than 2048x2048; "
                         "preview upload may be slow")
    pixels = np.empty(w * h * img.channels, dtype=np.float32)
    img.pixels.foreach_get(pixels)
    perPixel = pixels.reshape(-1, img.channels)
    if asData:
        # Single channel: luminance of the first 3, or the only one there is
        values = perPixel[:, :3].mean(axis=1).astype(np.float32) if img.channels >= 3 \
            else perPixel[:, 0].copy()
        channels = 1
    elif img.channels >= 3:
        values = np.ascontiguousarray(perPixel[:, :3], dtype=np.float32)
        channels = 3
    else:
        # A single-channel image used as a colour map is grey: replicate it across RGB
        values = np.repeat(perPixel[:, :1], 3, axis=1).astype(np.float32)
        channels = 3

    digest = hashlib.sha1(values.tobytes()).hexdigest()
    return {'resourceId': f"img:{digest}", 'width': w, 'height': h,
            'pixels': values, 'channels': channels, 'asData': asData}


def _collectSplines(curveObjects, depsgraph) -> dict:
    # Vertices are pre-transformed to WORLD space (identity transforms), matching the render
    # export (scatter_export._collectSplinePolylines) so preview == render. Sending LOCAL verts
    # placed the scatter at the curve's local origin/scale: on a scaled curve the shorter local
    # polyline fits far fewer instances (spacing is bounded by the model bbox), so the preview
    # showed a wrong count in the wrong place (VBLD: "extra cube that doesn't render").
    from mathutils import Matrix
    vertices, counts, tms = [], [], []
    for curveObj in curveObjects:
        if curveObj is None or curveObj.type != 'CURVE':
            continue
        for coords, _closed in resolve.curvePolylines(curveObj, depsgraph):
            vertices.extend(tuple(c) for c in coords)
            counts.append(len(coords))
            tms.append(_flatMatrix(Matrix()))
    return {'vertices': vertices, 'counts': counts, 'tms': tms}


def _collectAreas(cs, depsgraph) -> dict:
    vertices, counts = [], []
    operation, foNear, foFar, scales, densities, axes = [], [], [], [], [], []

    for item in cs.area_modifiers:
        if item.object is None or item.object.type != 'CURVE':
            continue
        objCounts = []
        for coords, _closed in resolve.curvePolylines(item.object, depsgraph):
            vertices.extend(tuple(c) for c in coords)
            objCounts.append(len(coords))
        counts.extend(objCounts)
        for _ in objCounts:
            operation.append(int(item.operation))
            foNear.append(item.falloff_near)
            foFar.append(item.falloff_far)
            scales.append(item.scale)
            densities.append(item.density)
            axes.append(int(item.axis))

    return {'vertices': vertices, 'counts': counts, 'operation': operation,
            'falloffNear': foNear, 'falloffFar': foFar, 'scale': scales,
            'density': densities, 'axis': axes}


def packCamera(camObj, scene) -> dict:
    """ One camera's clipping frustum, as the scatter core wants it. Shared with the render
        exporter (scatter_export._fillClippingCamera) so a Selected Camera clips identically on
        both sides.
    """
    cam = camObj.data
    render = scene.render
    resX = max(1, int(render.resolution_x * render.resolution_percentage / 100.0))
    resY = max(1, int(render.resolution_y * render.resolution_percentage / 100.0))
    aspect = (resX * render.pixel_aspect_x) / max(1e-6, resY * render.pixel_aspect_y)

    isOrtho, orthoWidth = _cameraProjection(cam, aspect)
    fov, focalDistance = _clippingFrustum(cam, isOrtho, orthoWidth, aspect)

    return {
        'tm': _flatMatrix(camObj.matrix_world),
        'fovRad': fov,
        'focalDistance': focalDistance,
        'aspect': aspect,
        'imgWidth': resX,
        'imgHeight': resY,
        'isOrtho': isOrtho,
        'orthoWidth': orthoWidth,
        'clipStart': cam.clip_start,
        'clipEnd': cam.clip_end,
    }


def _cameraProjection(cam, aspect: float) -> tuple[bool, float]:
    """ (isOrthographic, orthoWidth) as the ENGINE that will render sees them.

        Under Cycles the Blender camera is the whole truth. Under V-Ray it is not: a camera can be
        rendered orthographically through SettingsCamera.override_camera_settings while
        cam.type still says PERSP, so the preview has to ask V-Ray's own predicate or it would clip
        a perspective frustum against an orthographic render. Everything else agrees by
        construction - the exporter takes the ortho width from cam.ortho_scale
        (view_export.py:930), and CameraPhysical proxies its focal length and fov straight back to
        the Blender camera (CameraPhysical.fovGet), so there is no separate V-Ray fov to chase.
    """
    isOrtho = (cam.type == 'ORTHO')
    if utils.isVRayEngine():
        try:
            from vray_blender.lib import camera_utils
            isOrtho = camera_utils.isOrthographicCamera(cam)
        except ImportError:
            pass   # engine says V-Ray but the addon is absent; Blender's own answer will do

    # Portrait renders: the exporter narrows the ortho width by the aspect
    # (camera_utils.aspectCorrectForFovOrtho), and the perspective equivalent is handled by the
    # sensor-fit branch in _clippingFrustum.
    orthoWidth = cam.ortho_scale * aspect if aspect < 1.0 else cam.ortho_scale
    return isOrtho, orthoWidth


def _clippingFrustum(cam, isOrtho: bool, orthoWidth: float, aspect: float) -> tuple[float, float]:
    """ (fov, focalDistance) describing the camera's HORIZONTAL half-extent to the scatter core.

        The core has one frustum model for every camera type it supports:
            width = tan(0.5 * fov) * focalDistance;  height = width / aspect
        so both the perspective pyramid and the orthographic cuboid have to be expressed through
        that pair. Nothing else about the camera reaches it - orthographicWidth is never read.
    """
    if isOrtho:
        # Encode the half-width as fov=2*atan(0.5), focalDistance=ortho_scale, which gives
        # width = 0.5 * ortho_scale for ANY scale. The direct spelling (focalDistance=1,
        # fov=2*atan(ortho_scale/2)) saturates towards pi on a large scene and gets clamped.
        return 2.0 * math.atan(0.5), max(1e-6, orthoWidth)

    # cam.angle is measured along whatever axis the sensor fit resolves to; the core wants the
    # HORIZONTAL one. Blender's AUTO fit puts the sensor on the longer image axis.
    fit = cam.sensor_fit
    if fit == 'AUTO':
        fit = 'HORIZONTAL' if aspect >= 1.0 else 'VERTICAL'
    fovH = cam.angle if fit == 'HORIZONTAL' else 2.0 * math.atan(math.tan(0.5 * cam.angle) * aspect)
    # The pyramid is defined by its corners, so any positive distance describes the same infinite
    # frustum; 1.0 keeps the numbers small.
    return fovH, 1.0


def _flatMatrix(matrix) -> tuple:
    """ 4x4 mathutils.Matrix -> 16 floats, row-major. """
    return tuple(v for row in matrix for v in row)
