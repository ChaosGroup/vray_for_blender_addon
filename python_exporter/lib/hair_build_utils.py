# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

""" Fast construction of Blender hair Curves from V-Ray GeomMayaHair strand arrays.

    Mirrors lib/mesh_build_utils.py: the caller pre-scales and pre-shapes the arrays, this
    module does only the bulk foreach_set fill so the idioms cannot drift apart.
"""

import bpy
import numpy as np

# Radius used when a GeomMayaHair provides no usable widths (in Blender units).
_DEFAULT_HAIR_RADIUS = 0.0001


def _getOrNewAttr(curves: bpy.types.Curves, name: str, blType: str, domain: str):
    # radius / surface_uv_coordinate may be built-in in some Blender versions; get-or-create
    # avoids a duplicate-name error while still creating custom ones (color).
    attr = curves.attributes.get(name)
    if attr is None:
        attr = curves.attributes.new(name, blType, domain)
    return attr


def buildHairCurvesBase(name: str, strandSizes, positions, radii=None, colors=None,
                        colorDomain: str = 'POINT', surfaceUV=None,
                        curveType: str = 'POLY') -> bpy.types.Curves:
    """ Create a hair Curves datablock and bulk-fill it from final Blender-space arrays.

    Args:
        name: name for the new Curves datablock.
        strandSizes: (S,) int per-strand point counts (each >= 1).
        positions: (V, 3) float point positions, V == sum(strandSizes).
        radii: (V,) float per-point radius, or None.
        colors: (N, 4) float RGBA, N == V for 'POINT' or S for 'CURVE', or None.
        colorDomain: 'POINT' or 'CURVE'.
        surfaceUV: (S, 2) float per-strand root UV, or None.
        curveType: 'POLY' | 'CATMULL_ROM' | 'BEZIER' | 'NURBS'.

    Returns:
        The new Curves datablock.
    """
    curves = bpy.data.hair_curves.new(name)
    # add_curves is the only way to set the per-strand offsets; call once on the fresh datablock.
    curves.add_curves(np.ascontiguousarray(strandSizes, dtype=np.int32).tolist())

    curves.attributes['position'].data.foreach_set(
        'vector', np.ascontiguousarray(positions, dtype=np.float32).ravel())

    if radii is not None:
        rAttr = _getOrNewAttr(curves, 'radius', 'FLOAT', 'POINT')
        rAttr.data.foreach_set('value', np.ascontiguousarray(radii, dtype=np.float32).ravel())

    if colors is not None:
        cAttr = _getOrNewAttr(curves, 'color', 'FLOAT_COLOR', colorDomain)
        cAttr.data.foreach_set('color', np.ascontiguousarray(colors, dtype=np.float32).ravel())

    if surfaceUV is not None:
        uvAttr = _getOrNewAttr(curves, 'surface_uv_coordinate', 'FLOAT2', 'CURVE')
        uvAttr.data.foreach_set('vector', np.ascontiguousarray(surfaceUV, dtype=np.float32).ravel())

    if curveType != 'CATMULL_ROM':
        curves.set_types(type=curveType)

    # Flag the datablock for depsgraph re-evaluation (the Curves analog of mesh.update()).
    # Without it the freshly-built curves render stale/empty until an unrelated edit forces
    # a depsgraph refresh.
    curves.update_tag()
    return curves
