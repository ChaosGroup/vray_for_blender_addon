# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

""" Building blocks for the transient world-space overlays drawn over scene objects: the Scene
    Lister's material drop-target tint (ui/lister/assign_drag.py) and the object selectors'
    viewport highlight (ui/highlight_objects.py).

    Both need the same pieces: the builtin flat-color shader, a batch of an object's evaluated
    faces, the depth bias that keeps a tint from z-fighting with the surface it covers, and the
    theme color used to mark objects.
"""

import bpy
import gpu
import numpy as np
from gpu_extras.batch import batch_for_shader


# The builtin flat-color shader, created lazily on first use: from_builtin() raises in background
# mode, so it must not run at import time (both users are imported during register, incl. headless).
_colorShader = None


def colorShader():
    global _colorShader
    if _colorShader is None:
        _colorShader = gpu.shader.from_builtin('UNIFORM_COLOR')
    return _colorShader


# Object types to_mesh() can convert. It raises (not returns None) for CURVES / POINTCLOUD / VOLUME,
# so this check cannot be relaxed into a to_mesh() attempt.
_MESH_LIKE_TYPES = {'MESH', 'CURVE', 'SURFACE', 'FONT', 'META'}

# A tint is coplanar with the real faces, and equal depths do not survive the depth test reliably,
# so it has to be pushed towards the viewer (there is no glPolygonOffset in gpu.state). Scaling
# clip-space Z biases the depth comparison alone, leaving the geometry exact. The bias is a fraction
# of the depth range rather than a distance, so it tracks the depth buffer's resolution at the
# current view - which is what z-fighting actually depends on, unlike anything about the object.
_DEPTH_BIAS = 1e-6

# The 12 edges of Blender's Object.bound_box (8 local-space corners) as index pairs.
_BOX_EDGES = (
    (0, 1), (1, 2), (2, 3), (3, 0),   # -X face loop
    (4, 5), (5, 6), (6, 7), (7, 4),   # +X face loop
    (0, 4), (1, 5), (2, 6), (3, 7),   # connecting edges
)

# Stand-in box for objects that report an all-zero bound_box (lights, empties, cameras). Fixed size,
# like the light gizmo radii in ui/draw_callbacks.py. Corner order matches Object.bound_box.
_MARKER_CORNERS = ((-0.5, -0.5, -0.5), (-0.5, -0.5, 0.5), (-0.5, 0.5, 0.5), (-0.5, 0.5, -0.5),
                   ( 0.5, -0.5, -0.5), ( 0.5, -0.5, 0.5), ( 0.5, 0.5, 0.5), ( 0.5, 0.5, -0.5))


def tagRedrawView3D():
    """ Repaint every 3D viewport in every window, so an overlay appears / clears everywhere. All
        windows, because the editor hosting the button may not be the one showing the viewport.
    """
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


def markedObjectColor(context: bpy.types.Context, alpha: float):
    """ The theme color used to mark objects in the viewport, with the requested alpha. """
    return (*context.preferences.themes[0].view_3d.object_active[:], alpha)


def depthBiasedProjection():
    """ The current projection matrix, biased so a tint draws just in front of the surface it
        covers. Load it for the duration of the draw with gpu.matrix.push_pop_projection().
    """
    projection = gpu.matrix.get_projection_matrix().copy()

    for col in range(4):
        projection[2][col] *= (1.0 - _DEPTH_BIAS)

    return projection


def frozenWorldMatrix(obj: bpy.types.Object):
    """ A frozen copy of the object's world matrix, to draw a local-space batch under. """
    matrix = obj.matrix_world.copy()
    matrix.freeze()
    return matrix


def buildBoxBatch(obj: bpy.types.Object):
    """ Build a LINES batch of the object's local-space bounding box, or of the fixed-size marker
        box for objects that have none.
    """
    corners = [tuple(c) for c in obj.bound_box]

    # Lights, empties and cameras report an all-zero bound_box, which would draw as an invisible
    # degenerate box.
    if len(set(corners)) == 1:
        corners = _MARKER_CORNERS

    positions = [corners[i] for edge in _BOX_EDGES for i in edge]

    return batch_for_shader(colorShader(), 'LINES', {'pos': positions})


def buildTintBatch(obj: bpy.types.Object, depsgraph: bpy.types.Depsgraph, materialIndex: int = None):
    """ Build a TRIS batch of the object's evaluated faces, to be drawn as a tint over the real
        surface under depthBiasedProjection().

    Args:
        materialIndex (int, optional): when set, only the faces of that material slot are included.

    Returns:
        batch: the batch, or None when the object has no convertible / non-empty mesh.
    """
    if obj.type not in _MESH_LIKE_TYPES:
        return None

    evalObj = obj.evaluated_get(depsgraph)
    mesh = evalObj.to_mesh()
    if mesh is None:
        return None

    try:
        mesh.calc_loop_triangles()
        triCount = len(mesh.loop_triangles)
        vertCount = len(mesh.vertices)
        if triCount == 0 or vertCount == 0:
            return None

        verts = np.empty((vertCount, 3), dtype=np.float32)
        mesh.vertices.foreach_get('co', verts.ravel())

        tris = np.empty((triCount, 3), dtype=np.int32)
        mesh.loop_triangles.foreach_get('vertices', tris.ravel())

        if materialIndex is not None:
            triMat = np.empty(triCount, dtype=np.int32)
            mesh.loop_triangles.foreach_get('material_index', triMat)
            tris = tris[triMat == materialIndex]

        if len(tris) == 0:
            return None

        return batch_for_shader(colorShader(), 'TRIS', {'pos': verts}, indices=tris)
    finally:
        evalObj.to_mesh_clear()


def drawObjectOverlays(items, depthTest: str = 'LESS_EQUAL', lineWidth: float = None):
    """ Draw (batch, matrix, color) triples as a depth-biased, alpha-blended POST_VIEW overlay.
        depthTest='NONE' draws through other geometry; lineWidth is for wireframe batches. """
    shader = colorShader()

    prevDepthTest = gpu.state.depth_test_get()
    prevBlend = gpu.state.blend_get()
    prevLineWidth = gpu.state.line_width_get()
    gpu.state.depth_test_set(depthTest)
    gpu.state.blend_set('ALPHA')
    if lineWidth is not None:
        gpu.state.line_width_set(lineWidth)

    try:
        shader.bind()
        with gpu.matrix.push_pop_projection():
            gpu.matrix.load_projection_matrix(depthBiasedProjection())
            for batch, matrix, color in items:
                shader.uniform_float('color', color)
                with gpu.matrix.push_pop():
                    gpu.matrix.multiply_matrix(matrix)
                    batch.draw(shader)
    finally:
        gpu.state.line_width_set(prevLineWidth)
        gpu.state.blend_set(prevBlend)
        gpu.state.depth_test_set(prevDepthTest)
