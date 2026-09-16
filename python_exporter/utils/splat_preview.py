# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

""" Viewport preview for V-Ray Gaussian splat objects.

    A Gaussian splat is represented by an Empty object with obj.vray.isVRayGaussian == True. Its
    render parameters and its viewport-preview parameters (preview_type, preview_point_count/radius,
    preview_auto_radius) all live on obj.vray.GeomGaussians; the preview_* ones are 'derived' and
    are never exported to V-Ray, which reads the .ply directly.

    The preview is modeled on the V-Ray for C4D 'Preview' group:
      - Disabled / Bounding Box / Limited Point Cloud / Unlimited Point Cloud
      - per-point colors (average color of each Gaussian)
      - point radius, either a fixed pixel size or an automatic one derived from the spacing
        between the points being drawn
      - the preview honors the splat's flip_axis and scale so it matches the render

    The preview points are *derived* data (like the V-Ray Proxy preview): vray_tools.vray_splat
    generates them from the .ply via the 'vraytools' utility; this module caches them in memory
    (keyed by file path), builds the GPU batches and draws them. Nothing is stored in the .blend.
"""

import math
import os

import bpy
import gpu
import numpy as np
from gpu_extras.batch import batch_for_shader
from mathutils import Matrix, Vector, kdtree

from vray_blender.lib import blender_utils
from vray_blender.vray_tools import vray_splat


# Largest point size, in pixels, we will ever request (safety clamp).
_MAX_POINT_PIXELS = 64.0

# Pixels per unit of preview_point_radius when auto radius is off.
_BASE_POINT_PX = 4.0

# How many points to measure when estimating the cloud's inter-point spacing.
_SPACING_SAMPLES = 2000

# How many spacings wide an automatically sized point is drawn. Points that just touch leave
# gaps wherever the spacing varies, so they are overlapped a little to read as a surface.
_POINT_OVERLAP = 2.0

# GeomGaussians.preview_type enum values (see GeomGaussians.custom.json).
_PREVIEW_DISABLED  = '0'
_PREVIEW_BBOX      = '1'
_PREVIEW_LIMITED   = '2'
_PREVIEW_UNLIMITED = '3'

# filePath -> {'coords': (N,3) float32, 'colors': (N,4) float32, 'bbox': (Vector, Vector)} or None.
_previewData: dict[str, dict | None] = {}

# (filePath, count) -> (GPU batch of subsampled points, inter-point spacing), (None, 0.0) if
# there is nothing to draw.
_previewBatches: dict = {}

# Points are sized in world space, so the shader has to divide by the clip-space w; the builtin
# POINT_FLAT_COLOR takes a uniform pixel size only. Built on first draw (needs a GPU context).
_pointShader = None

# Names of Gaussian splat objects in the current scene (None = cache invalid, rebuilt lazily on next draw).
_splatObjectNames: set[str] | None = None

_drawHandler = None

# Local indices of the 12 bounding-box edges, for the corner ordering produced in _drawBoundingBox.
_BBOX_EDGES = ((0, 1), (2, 3), (4, 5), (6, 7), (0, 2), (1, 3), (4, 6), (5, 7), (0, 4), (1, 5), (2, 6), (3, 7))


def _getPointShader():
    """ Shader drawing each point at a world-space radius.

        'worldSizeFactor' folds the radius and the projection scale into a single number, so the
        per-vertex work is one divide by the clip-space w - the point's own view depth. A point
        therefore keeps a fixed size *in the scene* and foreshortens with distance, instead of the
        whole cloud sharing one pixel size derived from the camera. Setting 'worldSizeFactor' to 0
        selects the fixed 'pixelSize' instead (manual radius).

        w is 1 under an orthographic view, so the same expression covers both projections.
    """
    global _pointShader
    if _pointShader is None:
        iface = gpu.types.GPUStageInterfaceInfo("vray_splat_point_iface")
        iface.flat('VEC4', "finalColor")

        info = gpu.types.GPUShaderCreateInfo()
        info.push_constant('MAT4', "ModelViewProjectionMatrix")
        info.push_constant('FLOAT', "worldSizeFactor")
        info.push_constant('FLOAT', "pixelSize")
        info.vertex_in(0, 'VEC3', "pos")
        info.vertex_in(1, 'VEC4', "color")
        info.vertex_out(iface)
        info.fragment_out(0, 'VEC4', "fragColor")
        info.vertex_source(
            "void main()"
            "{"
            "  vec4 clipPos = ModelViewProjectionMatrix * vec4(pos, 1.0f);"
            "  gl_Position = clipPos;"
            "  float size = (worldSizeFactor > 0.0f) ? worldSizeFactor / max(clipPos.w, 1e-4f) : pixelSize;"
            f"  gl_PointSize = clamp(size, 1.0f, {_MAX_POINT_PIXELS}f);"
            "  finalColor = color;"
            "}"
        )
        info.fragment_source(
            "void main()"
            "{"
            "  fragColor = finalColor;"
            "}"
        )
        _pointShader = gpu.shader.create_from_info(info)
    return _pointShader


def _pointSpacing(coords) -> float:
    """ Median distance between neighbouring points, in the model's own units.

        This is the cloud's actual density, which is what an automatic point radius wants: it does
        not depend on the camera, so the drawn size can never jump as the view moves. Sampling a
        fixed number of points keeps the cost flat (~0.1s for a 100k-point cloud) and it is paid
        once per cached batch.
    """
    kd = kdtree.KDTree(coords.shape[0])
    for i, co in enumerate(coords):
        kd.insert(co, i)
    kd.balance()

    step = max(1, coords.shape[0] // _SPACING_SAMPLES)
    # find_n's first hit is the query point itself, so the neighbour is the second.
    return float(np.median([kd.find_n(co, 2)[1][2] for co in coords[::step]]))


def _getPreviewData(filePath: str):
    if filePath not in _previewData:
        # NOTE: This runs the vraytools utility synchronously, which may briefly stall the
        # viewport the first time a splat is shown (or after a .blend reload). The result is
        # cached, so the cost is paid only once per file path.
        _previewData[filePath] = vray_splat.loadVRaySplatPreview(filePath)
    return _previewData[filePath]


def _dropBatches(filePath: str):
    """ Release any cached GPU batches built for a file path. """
    for key in [k for k in _previewBatches if k[0] == filePath]:
        del _previewBatches[key]


def _getPointsBatch(filePath: str, count: int):
    """ Return a cached (GPU points batch, inter-point spacing) for up to `count` (subsampled)
        points. The spacing is measured on the points actually drawn, so thinning the preview
        widens it and the points grow to match.

        At most one batch is kept per file path: building a batch for a new `count`
        first drops the file's previous batch. Otherwise dragging the preview_point_count
        slider (a different `count` on every redraw) would accumulate a fresh, never-
        revisited GPU batch per intermediate value for the whole session.
    """
    key = (filePath, count)
    if key in _previewBatches:
        return _previewBatches[key]

    _dropBatches(filePath)

    data = _getPreviewData(filePath)
    if not data or data['coords'].shape[0] < 2:
        _previewBatches[key] = (None, 0.0)
        return _previewBatches[key]

    coords = data['coords']
    colors = data['colors']
    total = coords.shape[0]
    if 0 < count < total:
        # Ceil (not floor) so the sample spans the whole cloud: floor gives step == 1 for
        # count <= total < 2*count, which would take a contiguous prefix (one side of the model).
        step = max(1, math.ceil(total / count))
        coords = np.ascontiguousarray(coords[::step][:count])
        colors = np.ascontiguousarray(colors[::step][:count])

    batch = batch_for_shader(_getPointShader(), 'POINTS', {"pos": coords, "color": colors})
    _previewBatches[key] = (batch, _pointSpacing(coords))
    return _previewBatches[key]


def getSplatFile(obj: bpy.types.Object) -> str:
    """ Return the absolute .ply path configured on a Gaussian splat object. """
    filePath = obj.vray.GeomGaussians.file
    return bpy.path.abspath(filePath) if filePath else ""


def _emptyDisplayTypeFor(previewType: str) -> str:
    """ The Empty shows the bbox cube in every preview mode except 'Bounding Box', where the GPU
        preview already draws the exact box - there the Empty reverts to plain axes so the two
        boxes don't overlap. """
    return 'PLAIN_AXES' if previewType == _PREVIEW_BBOX else 'CUBE'


def _applyEmptyDisplay(obj: bpy.types.Object, bbox):
    """ Size the splat's Empty to the largest side of the model's bounding box and set its
        display type for the current preview mode (see _emptyDisplayTypeFor), so the viewport
        shows the splat's extent instead of the default plain axes.

        The bbox comes from the (cached) preview data, so it is computed only once per file, and
        the resulting size is stored on the Object - drawing the cube is then a free, native
        Empty display with no per-redraw cost.
    """
    obj.empty_display_type = _emptyDisplayTypeFor(obj.vray.GeomGaussians.preview_type)
    if bbox is None:
        return
    bbMin, bbMax = bbox
    maxSide = max(bbMax.x - bbMin.x, bbMax.y - bbMin.y, bbMax.z - bbMin.z)
    if maxSide > 0.0:
        # A 'CUBE' Empty spans [-size, +size] on each axis, so its edge is 2*empty_display_size:
        # halve the largest bbox side so the cube edge matches it.
        obj.empty_display_size = maxSide / 2.0


def loadPreview(obj: bpy.types.Object):
    """ Generate and cache the preview points for a splat object's file and size the object's
        Empty to the model's bounding box. Returns an error message on failure (None on
        success). Safe to call outside of a draw context.
    """
    filePath = getSplatFile(obj)
    if not filePath:
        return "No Gaussian splat file set"

    data = vray_splat.loadVRaySplatPreview(filePath)
    _previewData[filePath] = data
    # Drop any batches built for the previous content of this file path.
    _dropBatches(filePath)

    if data is None:
        return f"Can't load the Gaussian splat preview - the file format of '{os.path.basename(filePath)}' is not supported"

    _applyEmptyDisplay(obj, data['bbox'])
    return None


def _gaussianTransform(obj: bpy.types.Object) -> Matrix:
    """ Object transform combined with the splat's flip_axis rotation and scale, so the preview
        matches how V-Ray will place the model. Mirrors the C4D getGaussianTransformation(). """
    pg = obj.vray.GeomGaussians
    flip = pg.flip_axis
    flipMatrix = Matrix.Identity(4)
    if flip == '1':
        # flip_axis 1: rotation of -90 degrees around X (see GeomGaussians.flip_axis description).
        flipMatrix = Matrix.Rotation(math.radians(-90.0), 4, 'X')
    elif flip == '2':
        # flip_axis 2: 90 degrees around Y, then flip Y.
        flipMatrix = Matrix.Diagonal((1.0, -1.0, 1.0, 1.0)) @ Matrix.Rotation(math.radians(90.0), 4, 'Y')

    return obj.matrix_world @ flipMatrix @ Matrix.Scale(pg.scale, 4)


def _computeSizeUniforms(obj: bpy.types.Object, transform: Matrix, spacing: float):
    """ The (worldSizeFactor, pixelSize) pair for _getPointShader.
        - Auto radius: each point is drawn a fixed multiple of the spacing between the points
          being drawn, so the cloud reads as a surface at every zoom level and thinning it with
          preview_point_count widens the points to compensate (preview_point_radius fine-tunes it).
        - Manual: preview_point_radius scaled to a fixed pixel size.
    """
    gg = obj.vray.GeomGaussians
    radius = gg.preview_point_radius

    if not gg.preview_auto_radius or spacing <= 0.0:
        return 0.0, radius * _BASE_POINT_PX

    region = bpy.context.region
    if not region:
        return 0.0, radius * _BASE_POINT_PX

    # The spacing is in the model's own units; the transform carries it into the scene.
    worldSpacing = spacing * sum(transform.col[i].to_3d().length for i in range(3)) / 3.0
    # Clip space spans [-1, 1] over the region height, so a length L at depth w covers
    # L * projScale * (height / 2) / w pixels. The shader supplies the division by w.
    projScale = gpu.matrix.get_projection_matrix()[1][1]
    return worldSpacing * _POINT_OVERLAP * projScale * (region.height / 2.0) * radius, 0.0


def _drawBoundingBox(obj: bpy.types.Object, transform: Matrix, bbox):
    bbMin, bbMax = bbox
    corners = [Vector((x, y, z)) for x in (bbMin.x, bbMax.x) for y in (bbMin.y, bbMax.y) for z in (bbMin.z, bbMax.z)]
    coords = [corners[i] for edge in _BBOX_EDGES for i in edge]

    theme = bpy.context.preferences.themes[0].view_3d
    color = (*theme.object_active, 1.0) if obj == bpy.context.active_object else (*theme.wire, 1.0)

    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    batch = batch_for_shader(shader, 'LINES', {"pos": coords})

    gpu.matrix.push()
    gpu.matrix.multiply_matrix(transform)
    shader.bind()
    shader.uniform_float("color", color)
    batch.draw(shader)
    gpu.matrix.pop()


def drawSplatPreview():
    """ Draw the viewport preview for all Gaussian splat objects. """
    global _splatObjectNames

    space3d = bpy.context.space_data
    if not isinstance(space3d, bpy.types.SpaceView3D):
        return

    if space3d.shading.type != 'SOLID':
        return

    # Don't draw the preview while editing another object.
    if bpy.context.active_object and bpy.context.active_object.mode != 'OBJECT':
        return

    if _splatObjectNames is None:
        _splatObjectNames = {o.name for o in bpy.context.scene.objects if o.vray.isVRayGaussian}

    pointShader = _getPointShader()

    for objName in _splatObjectNames:
        obj = bpy.data.objects.get(objName)
        if not obj or not obj.visible_get():
            continue

        gg = obj.vray.GeomGaussians
        previewType = gg.preview_type
        if previewType == _PREVIEW_DISABLED:
            continue

        if space3d.local_view and not obj.local_view_get(space3d):
            continue

        filePath = getSplatFile(obj)
        if not filePath:
            continue

        data = _getPreviewData(filePath)
        if not data or data['bbox'] is None:
            continue

        transform = _gaussianTransform(obj)

        if previewType == _PREVIEW_BBOX:
            _drawBoundingBox(obj, transform, data['bbox'])
            continue

        total = data['coords'].shape[0]
        count = total if previewType == _PREVIEW_UNLIMITED else min(gg.preview_point_count, total)
        batch, spacing = _getPointsBatch(filePath, count)
        if not batch:
            continue

        worldSizeFactor, pixelSize = _computeSizeUniforms(obj, transform, spacing)

        prevDepthTest = gpu.state.depth_test_get()
        gpu.state.depth_test_set('LESS_EQUAL')
        gpu.state.program_point_size_set(True)   # honor gl_PointSize written by the shader
        gpu.matrix.push()
        gpu.matrix.multiply_matrix(transform)
        pointShader.bind()
        pointShader.uniform_float("worldSizeFactor", worldSizeFactor)
        pointShader.uniform_float("pixelSize", pixelSize)
        batch.draw(pointShader)
        gpu.matrix.pop()
        gpu.state.program_point_size_set(False)
        gpu.state.depth_test_set(prevDepthTest)


def onSplatFileUpdate(propGroup, context, attrName):
    """ JSON 'update' callback for GeomGaussians.file: regenerate the preview and resize the
        splat's Empty to the new model's bounding box.

        The file string itself is never animated: V-Ray resolves frame patterns (%04d / <frame>)
        in the path per-frame at render time (see GeomGaussians::readFromFile), driven by
        'anim_offset'.
    """
    obj = propGroup.id_data
    if obj and obj.vray.isVRayGaussian:
        loadPreview(obj)


def onPreviewTypeUpdate(propGroup, context, attrName):
    """ JSON 'update' callback for preview_type: switch the Empty's display type to match the
        new mode (bbox cube, except plain axes in 'Bounding Box' mode). The bbox-derived size
        already stored on the Empty is preserved, so no reload is needed. The viewport redraw is
        handled by the default selectedObjectTagUpdate that runs after this callback. """
    obj = propGroup.id_data
    if obj and obj.vray.isVRayGaussian:
        obj.empty_display_type = _emptyDisplayTypeFor(propGroup.preview_type)


@bpy.app.handlers.persistent
def _resetPreviewCache(_e):
    global _splatObjectNames
    _previewData.clear()
    _previewBatches.clear()
    _splatObjectNames = None


@bpy.app.handlers.persistent
def _onSceneUpdate(_scene, _depsgraph):
    """ Invalidate the splat object cache on every scene change, so the draw handler picks up
        added/removed/hidden objects on the next frame. Also prune cached preview data/batches
        for files no longer referenced by any splat object (depsgraph does not report deletions,
        so we reconcile against the live set). Cheap no-op until at least one splat has been
        previewed.
    """
    global _splatObjectNames
    _splatObjectNames = None
    if not _previewData and not _previewBatches:
        return
    liveFiles = {getSplatFile(o) for o in bpy.data.objects if o.vray.isVRayGaussian}
    for filePath in [f for f in _previewData if f not in liveFiles]:
        del _previewData[filePath]
    for key in [k for k in _previewBatches if k[0] not in liveFiles]:
        del _previewBatches[key]


def register():
    global _drawHandler
    if _drawHandler is None:
        _drawHandler = bpy.types.SpaceView3D.draw_handler_add(drawSplatPreview, (), 'WINDOW', 'POST_VIEW')
    blender_utils.addEvent(bpy.app.handlers.load_pre, _resetPreviewCache)
    blender_utils.addEvent(bpy.app.handlers.depsgraph_update_post, _onSceneUpdate)


def unregister():
    global _drawHandler
    if _drawHandler is not None:
        bpy.types.SpaceView3D.draw_handler_remove(_drawHandler, 'WINDOW')
        _drawHandler = None
    blender_utils.delEvent(bpy.app.handlers.load_pre, _resetPreviewCache)
    blender_utils.delEvent(bpy.app.handlers.depsgraph_update_post, _onSceneUpdate)
