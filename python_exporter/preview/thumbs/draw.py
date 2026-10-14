# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

""" Draws the thumbnails above the nodes of the V-Ray node editor.

    A POST_PIXEL handler on SpaceNodeEditor. Only visible nodes are drawn, and asking for a
    node's thumbnail is what schedules it.

    UI SCALE: node.dimensions is already in ui_scale units but node.location is not. Multiply
    node coordinates by ui_scale BEFORE view_to_region().
"""

import gpu
from gpu_extras.batch import batch_for_shader

import bpy

from vray_blender import debug
from vray_blender.preview.thumbs import service

# Below this on-screen size (in pixels) a thumbnail is too small to read
_MIN_THUMB_WIDTH_PX = 24.0

# Nodes that have no thumbnail of their own and no plugin behind them
_SKIPPED_NODES = {'NodeFrame', 'NodeReroute'}

# Matte behind the image, plus its outline
_MATTE_COLOR = (0.05, 0.05, 0.05, 0.85)
_OUTLINE_COLOR = (0.45, 0.68, 0.95, 1.0)

_handle = None
_thumbShader = None     # exposure + sRGB encode; see _createThumbShader
_colorShaderCache = None
_quadBatch = None       # unit quad, reused for every thumbnail
_lastDrawError = None   # one log line per distinct draw failure, not one per redraw


def register():
    global _handle
    if _handle is None:
        _handle = bpy.types.SpaceNodeEditor.draw_handler_add(_draw, (), 'WINDOW', 'POST_PIXEL')


def unregister():
    global _handle, _thumbShader, _colorShaderCache, _quadBatch
    if _handle is not None:
        try:
            bpy.types.SpaceNodeEditor.draw_handler_remove(_handle, 'WINDOW')
        except (ValueError, RuntimeError):
            pass
        _handle = None
    # GPU handles must not outlive the handler that draws with them
    _thumbShader = None
    _colorShaderCache = None
    _quadBatch = None


def _draw():
    """ Draw entry point. Must never raise - a draw handler that does runs at redraw rate. """
    global _lastDrawError

    try:
        _drawImpl()
        _lastDrawError = None
    except Exception as ex:
        signature = f"{type(ex).__name__}: {ex}"
        if signature != _lastDrawError:
            _lastDrawError = signature
            debug.printError(f"Node thumbnails: draw failed: {signature}")


def _drawImpl():
    from vray_blender.preview.thumbs import isEnabled

    context = bpy.context
    space = context.space_data
    if (space is None) or (space.type != 'NODE_EDITOR') or (space.tree_type != 'VRayNodeTreeEditor'):
        return
    if not isEnabled():
        return
    if (ntree := getattr(space, 'edit_tree', None)) is None:
        return

    region = context.region
    view2d = region.view2d
    # Read ui_scale inside the draw callback. Outside a window draw context it can be stale.
    uiScale = context.preferences.system.ui_scale
    sizeFactor = _sizeFactor()
    gap = 6.0 * uiScale
    pad = 2.0 * uiScale

    # Visible region in view space, for rejecting off-screen nodes before any view_to_region call
    viewMinX, viewMinY = view2d.region_to_view(0, 0)
    viewMaxX, viewMaxY = view2d.region_to_view(region.width, region.height)

    prevBlend = gpu.state.blend_get()
    gpu.state.blend_set('ALPHA')
    try:
        # Matte, then images, then outline. The matte must be drawn before the images.
        mattes, images = [], []

        for node in ntree.nodes:
            if node.hide or (node.bl_idname in _SKIPPED_NODES):
                # A collapsed node has no width to hang a thumbnail on
                continue

            loc = node.location_absolute
            vx, vy = loc[0] * uiScale, loc[1] * uiScale
            # A thumbnail extends right by the node's width and up by at most that much again
            vw = node.width * uiScale
            if (vx + vw < viewMinX) or (vx > viewMaxX) or (vy + vw < viewMinY) or (vy > viewMaxY):
                continue

            x0, y0 = view2d.view_to_region(vx, vy, clip=False)
            x1, _ = view2d.view_to_region(vx + vw, vy, clip=False)
            nodeWidth = x1 - x0
            width = nodeWidth * sizeFactor
            if width < _MIN_THUMB_WIDTH_PX:
                continue

            # Centred on the node, sitting just above its header
            xa = x0 + (nodeWidth - width) * 0.5
            if not _isVisible(region, xa, y0, width):
                continue

            # 'width' is the on-screen size, which the AUTO resolution setting matches
            thumb = service.thumbnailFor(node, ntree, space.id, width)
            if thumb is None:
                continue
            texture, exposure, aspect = thumb

            height = width / max(aspect, 0.001)
            top = y0 + gap + height
            mattes.append((xa - pad, y0 + gap - pad, xa + width + pad, top + pad))
            images.append((texture, exposure, xa, y0 + gap, xa + width, top))

        if mattes:
            _drawQuads(_MATTE_COLOR, mattes)
            for texture, exposure, x0, y0, x1, y1 in images:
                _drawTexture(texture, exposure, x0, y0, x1, y1)
            _drawOutlines(_OUTLINE_COLOR, mattes)
    finally:
        gpu.state.blend_set(prevBlend)


def _sizeFactor() -> float:
    """ Thumbnail width as a fraction of the node width. """
    from vray_blender.lib.blender_utils import getVRayPreferences
    return {'SMALL': 0.5, 'MEDIUM': 0.75, 'LARGE': 1.0}[getVRayPreferences().node_thumbnail_size]


def _isVisible(region, xa, y0, width) -> bool:
    """ Whether a thumbnail at xa, above a node header at y0, can be on screen. """
    return (xa + width > 0) and (xa < region.width) and (y0 + width > 0) and (y0 < region.height)


def _createThumbShader():
    """ Shader drawing a thumbnail from raw linear pixels, with the display exposure and the sRGB encode applied per
        fragment.
    """
    vertOut = gpu.types.GPUStageInterfaceInfo("vray_thumb_iface")
    vertOut.smooth('VEC2', "uvInterp")

    info = gpu.types.GPUShaderCreateInfo()
    # 72 bytes. Keep the block under the 128-byte Vulkan floor.
    info.push_constant('MAT4', "ModelViewProjectionMatrix")
    info.push_constant('FLOAT', "exposure")
    # Blender sets this on bind for any shader declaring it, builtin or not: true means the region's
    # framebuffer encodes sRGB itself, so the fragment must hand it linear.
    info.push_constant('BOOL', "srgbTarget")
    info.sampler(0, 'FLOAT_2D', "image")
    info.vertex_in(0, 'VEC2', "position")
    info.vertex_in(1, 'VEC2', "uv")
    info.vertex_out(vertOut)
    info.fragment_out(0, 'VEC4', "colorOut")

    info.vertex_source(
        "void main()"
        "{"
        "  uvInterp = uv;"
        "  gl_Position = ModelViewProjectionMatrix * vec4(position, 0.0, 1.0);"
        "}"
    )
    info.fragment_source(
        "void main()"
        "{"
        "  vec4 texel = texture(image, uvInterp);"
        "  vec3 c = clamp(texel.rgb * exposure, 0.0, 1.0);"
        "  vec3 lo = c * 12.92;"
        "  vec3 hi = 1.055 * pow(c, vec3(1.0 / 2.4)) - 0.055;"
        "  vec3 encoded = srgbTarget ? c : mix(hi, lo, lessThanEqual(c, vec3(0.0031308)));"
        "  colorOut = vec4(encoded, texel.a);"
        "}"
    )

    shader = gpu.shader.create_from_info(info)
    del vertOut
    del info
    return shader


def _drawTexture(texture, exposure, x0, y0, x1, y1):
    """ One thumbnail, as the cached unit quad placed by the model-view matrix. """
    global _thumbShader, _quadBatch
    if _thumbShader is None:
        _thumbShader = _createThumbShader()
    if _quadBatch is None:
        # Do not switch to TRI_FAN - the Python GPU module deprecates it and Metal has none.
        # Vertex order: bottom-left, bottom-right, top-left, top-right.
        _quadBatch = batch_for_shader(_thumbShader, 'TRI_STRIP', {
            'position': ((0, 0), (1, 0), (0, 1), (1, 1)),
            'uv': ((0, 0), (1, 0), (0, 1), (1, 1)),
        })

    with gpu.matrix.push_pop():
        gpu.matrix.translate((x0, y0))
        gpu.matrix.scale((x1 - x0, y1 - y0))
        _thumbShader.bind()
        _thumbShader.uniform_float('exposure', exposure)
        _thumbShader.uniform_sampler('image', texture)
        _quadBatch.draw(_thumbShader)


def _colorShader():
    global _colorShaderCache
    if _colorShaderCache is None:
        _colorShaderCache = gpu.shader.from_builtin('UNIFORM_COLOR')
    return _colorShaderCache


def _drawQuads(color, rects):
    """ All the thumbnail mattes as one triangle batch. """
    pos = []
    for x0, y0, x1, y1 in rects:
        pos.extend(((x0, y0), (x1, y0), (x1, y1), (x0, y0), (x1, y1), (x0, y1)))

    shader = _colorShader()
    batch = batch_for_shader(shader, 'TRIS', {'pos': pos})
    shader.bind()
    shader.uniform_float('color', color)
    batch.draw(shader)


def _drawOutlines(color, rects):
    """ All the thumbnail outlines as one line batch. """
    pos = []
    for x0, y0, x1, y1 in rects:
        pos.extend(((x0, y0), (x1, y0), (x1, y0), (x1, y1),
                    (x1, y1), (x0, y1), (x0, y1), (x0, y0)))

    shader = _colorShader()
    batch = batch_for_shader(shader, 'LINES', {'pos': pos})
    shader.bind()
    shader.uniform_float('color', color)
    batch.draw(shader)
