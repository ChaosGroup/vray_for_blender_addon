# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

""" Which V-Ray nodes get a thumbnail, and how its pixels are produced.

    Textures read as a flat swatch, materials as a sphere on a backdrop:

      SAMPLE           UV-sample the plugin with the AppSDK sampler. No render, no camera,
                       exact. The default for textures and UVW generators.
      RENDER_PLANE     a camera-facing quad carrying a 0..1 UV channel.
      RENDER_SPHERE    the sphere, without the backdrop.
      RENDER_CORNER    the scene's concave corner.
      RENDER_CURVE     a second, larger copy of that corner.
      RENDER_MATERIAL  the sphere's material, on the backdrop. BRDFs and materials.

    Everything else gets none: a per-category default plus the explicit sets below.
"""

from dataclasses import dataclass
from enum import Enum

import bpy


class PreviewMethod(Enum):
    SAMPLE          = 'SAMPLE'
    RENDER_PLANE    = 'RENDER_PLANE'
    RENDER_SPHERE   = 'RENDER_SPHERE'
    RENDER_CORNER   = 'RENDER_CORNER'
    RENDER_CURVE    = 'RENDER_CURVE'
    RENDER_MATERIAL = 'RENDER_MATERIAL'


@dataclass(frozen=True)
class Preview:
    """ How to produce a thumbnail for one node.

        plugin  the effective V-Ray plugin type; meta nodes resolve theirs from their own state
        aspect  width/height of the produced image, 2.0 for the equirect textures
    """
    method: PreviewMethod
    plugin: str
    aspect: float = 1.0


# Default producer per node category (node.vray_type)
_METHOD_BY_CATEGORY = {
    'TEXTURE'  : PreviewMethod.SAMPLE,
    'UVWGEN'   : PreviewMethod.SAMPLE,
    'BRDF'     : PreviewMethod.RENDER_MATERIAL,
    'MATERIAL' : PreviewMethod.RENDER_MATERIAL,
}

# No thumbnail: the result exists only in a real scene, or the plugin is pure render metadata.
# Enumerate against every plugin with a node class, not against the add menu.
_NO_PREVIEW = frozenset({
    'TexHairSampler', 'TexSampler',
    'TexUserColor', 'TexUserScalar', 'TexMulti', 'TexVectorProduct',
    'TexMayaFluid', 'TexMayaFluidTransformed',
    'TexMayaHairColor', 'TexMayaHairIncandescence', 'TexMayaHairTransparency',
    'TexModoRayType',
    'MtlMaterialID', 'MtlSelectRE', 'MtlRenderStats',

    # Read scene state no sampler can supply
    'TexScatterSurfaceColor',   # surface_nodes: the colour of OTHER scene objects
    'TexSurfaceLuminance',      # luminance_mode: incident direct/diffuse light
    'TexVertexColorDirect',     # color_set_name: a mesh colour attribute
    'TexMaxHairInfo',           # position along strand; the TexHairSampler fallback
    'UVWGenSelect',             # searches the intersected geometry instance for an id
})

# Read a surface position or a normal frame, which the sampler cannot supply
_RENDER_PLANE = frozenset({
    'TexTriPlanar', 'TexParallax',
    'TexNormalBump', 'TexBlendBumpNormal',
    'MayaProjectionTex',
    'UVWGenProjection', 'UVWGenObject', 'UVWGenObjectBBox', 'UVWGenPlanarWorld',
})

# Purely view-dependent. The only requirement here that PROPAGATES downstream; see _contextMethod.
_VIEW_DEPENDENT = frozenset({
    'TexFresnel', 'TexFalloff', 'TexModoFresnelSchlick', 'TexModoUVResetAndFalloff',
})

# Plus TexEdges, which needs more than two triangles to read as a wireframe
_RENDER_SPHERE = _VIEW_DEPENDENT | frozenset({'TexEdges'})

# Ambient occlusion needs an occluder, not a lone convex surface
_RENDER_CORNER = frozenset({'TexDirt'})

# Curvature samples a region sized in WORLD units and gets a 10-unit corner of its own.
# Do not shrink sample_spread or normal*scale onto a smaller subject.
_RENDER_CURVE = frozenset({'TexCurvature'})

# Flat colour with nothing in its object list; see render._aimDistanceAtProbe
_DISTANCE = 'TexDistance'

# Anything whose result only exists at a real shading point
_CONTEXT_DEPENDENT = _RENDER_PLANE | _RENDER_SPHERE

# Directional textures, sampled 2:1. sampleArea feeds an equirect view direction per texel (AppSDK VRT-715).
_EQUIRECT = frozenset({'TexSky', 'UVWGenEnvironment', 'TexModoEnvironmentColor'})

# Not V-Ray plugins. A matrix applied to the placement reference image.
# Keyed by bl_idname; vray_plugin is 'NONE'.
_PREVIEW_BY_NODE = {
    'VRayNodeTransform': Preview(PreviewMethod.SAMPLE, 'Transform'),
    'VRayNodeMatrix'   : Preview(PreviewMethod.SAMPLE, 'Matrix'),
}


def _contextMethod(node: bpy.types.Node) -> PreviewMethod | None:
    """ The method a sampled node must be promoted to for what its subtree READS, or None when nothing in it needs a
        real shading point.
    """
    from vray_blender.exporting.tools import getFarNodeLink

    found = None
    seen, pending = set(), [node]
    while pending:
        current = pending.pop()
        key = current.as_pointer()
        if key in seen:
            continue
        seen.add(key)

        plugin = getattr(current, 'vray_plugin', '')
        if plugin in _VIEW_DEPENDENT:
            return PreviewMethod.RENDER_SPHERE
        if plugin in _CONTEXT_DEPENDENT:
            found = PreviewMethod.RENDER_PLANE

        for sock in current.inputs:
            if (link := getFarNodeLink(sock)) is not None:
                pending.append(link.from_node)

    return found


def previewOf(node: bpy.types.Node) -> Preview | None:
    """ How to preview this node, or None when it gets no thumbnail. """
    if node.mute:
        return None

    if (byNode := _PREVIEW_BY_NODE.get(node.bl_idname)) is not None:
        return byNode

    category = getattr(node, 'vray_type', None)
    if category is None:
        # Not a V-Ray node (reroute, frame, group i/o, Cycles node)
        return None

    method = _METHOD_BY_CATEGORY.get(category)
    if method is None:
        return None

    plugin = getattr(node, 'vray_plugin', '')
    if (not plugin) or (plugin == 'NONE') or (plugin in _NO_PREVIEW):
        return None

    # Reached through the generic plugin node. V-Ray core cannot evaluate these at all.
    from vray_blender.plugins.skipped_plugins import THIRD_PARTY_INTEGRATION_PLUGINS
    if plugin in THIRD_PARTY_INTEGRATION_PLUGINS:
        return None

    if plugin in _EQUIRECT:
        return Preview(PreviewMethod.SAMPLE, plugin, aspect=2.0)
    if plugin in _RENDER_SPHERE:
        return Preview(PreviewMethod.RENDER_SPHERE, plugin)
    if plugin in _RENDER_CORNER:
        return Preview(PreviewMethod.RENDER_CORNER, plugin)
    if plugin in _RENDER_CURVE:
        return Preview(PreviewMethod.RENDER_CURVE, plugin)
    if plugin == _DISTANCE:
        return Preview(PreviewMethod.RENDER_PLANE, plugin)
    if plugin in _RENDER_PLANE:
        return Preview(PreviewMethod.RENDER_PLANE, plugin)
    if (method is PreviewMethod.SAMPLE) and ((promoted := _contextMethod(node)) is not None):
        return Preview(promoted, plugin)
    return Preview(method, plugin)
