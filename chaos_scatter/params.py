# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

""" Single source of truth for the GeomScatter parameter dict.

    Both consumers read ONLY this module so viewport preview and V-Ray render placement cannot
    drift:
      - the addon's preview request (collect.py, forPreview=True)
      - vray_blender's render exporter (exporting/scatter_export.py, forPreview=False)

    Not covered here (each consumer resolves them from the scene itself): targets/models plugin
    or mesh references, spline polylines, area modifier polylines, falloff curve samples,
    look-at target reference, texture maps.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Type-only: this module must stay free of chaos_scatter imports at runtime so it can be read
    # by both consumers without pulling the rest of the addon in.
    from chaos_scatter.resolve import ResolvedModel, ResolvedTarget

# Hard cap for the viewport preview instance count. readScatterData is blocking and
# non-cancellable, so the preview must be bounded no matter what the user asks for.
PREVIEW_INSTANCE_CAP = 5000000

# Values pinned identically for preview and render.
# flip_axis 2 = 'Z up' (Blender is a Z-up right-handed host); targets_tangent_mode 1 =
# 'UV aligned' (forced by every reference integration).
PINNED_PARAMS = {
    'flip_axis':                     2,
    'targets_tangent_mode':          1,
    'presample_surface_color_map':   True,
    'instances_cryptomatte_override': True,
}

# Compatibility switches, pinned to the plugin defaults EXCEPT the documented-legacy behaviors:
# cross-platform-inconsistent transforms, altitude falloff (default true = legacy) and the light
# bounding box mode. This is a new integration with no legacy scenes to match.
COMPAT_FLAGS = {
    'compatibility_skip_transformations_rebase':             True,
    'compatibility_skip_original_rotation':                  True,
    'compatibility_sample_model_tm_at_rest_pose':            True,
    'compatibility_spline_scatter_normals_refdir':           True,
    'compatibility_planar_map_no_scale_swap':                True,
    # 0 = the light's object-space box. The plugin default (2) approximates it from the WORLD box,
    # so a rotated light collides as its enclosing axis-aligned box rotated back.
    'compatibility_light_bounding_box_mode':                 0,
    'compatibility_single_channel_limitation':               True,
    'compatibility_edited_instances_avoidance_legacy':       False,
    'compatibility_omit_rest_pose_transformations':          False,
    'compatibility_tiny_triangles_scattering':               False,
    'compatibility_altitude_falloff_unstable_randomization': False,
    'compatibility_altitude_falloff_inconsistency':          False,
    'compatibility_transforms_inconsistency':                False,
    'compatibility_use_volatile_edited_instance_ids':        False,
    'compatibility_cyclic_map_channel_selection':            True,
}

# Overrides for a scatter whose settings came from a Cosmos preset (cs.preset_compat). Presets are
# authored in 3ds Max and the scatter core forces exactly these while reading one, so a preset
# exported with our defaults instead would place its instances differently than authored.
PRESET_COMPAT_FLAGS = {
    'compatibility_skip_transformations_rebase':   False,
    'compatibility_skip_original_rotation':        False,
    'compatibility_sample_model_tm_at_rest_pose':  False,
    'compatibility_spline_scatter_normals_refdir': False,
    'compatibility_light_bounding_box_mode':       1,
}

# Linear interpolation, matching the densely sampled falloff curves both consumers serialize.
FALLOFF_INTERP_LINEAR = 1

# Propgroup fields that ARE GeomScatter inputs but are not scalars, mapped to the attributes a
# consumer must resolve them into. Both consumers must handle every entry - see OWNED_ATTRS.
_SKIP_RESOLVED = {
    'surface_altitude_limit_falloff_data': ('surface_altitude_limit_falloff_curve',
                                            'surface_altitude_limit_falloff_curve_interp'),
    'look_at_falloff_data':                ('look_at_falloff_curve',
                                            'look_at_falloff_curve_interp'),
    'look_at_target':                      ('look_at_target',),
    'surface_random_density_map':          ('surface_random_density_map',),
    'transforms_translation_map':          ('transforms_translation_map',),
    'transforms_rotation_map':             ('transforms_rotation_map',),
    'transforms_scale_map':                ('transforms_scale_map',),
    'cluster_instances_color_map':         ('cluster_instances_color_map',),
    'camera_clipping_selected_cam':        ('camera_clipping_selected_cam',),
}

# Propgroup fields that are pure UI bookkeeping and have no GeomScatter counterpart at all.
_SKIP_UI = {
    'is_scatter', 'curve_ns', 'preset_compat',
    'targets_active_index', 'models_active_index', 'area_modifiers_active_index',
    'layers_active_index', 'paint_radius', 'paint_erase', 'paint_sub_layer',
}

# The sub-propgroups whose scalars ARE emitted, in the order buildScatterParams walks them.
# ('display' is viewport-only, and the collections are resolved by the consumers.) preset.py reads
# the same tuple, so the import and the export cover the same fields.
EMITTED_GROUPS = ('surface', 'spline', 'volume', 'transforms', 'look_at',
                  'clusters', 'camera_clipping')

# Sub-propgroups and item collections walked separately, never emitted as a scalar.
_SUBGROUPS = {
    'surface', 'spline', 'volume', 'transforms', 'look_at', 'clusters', 'camera_clipping',
    'display', 'instance_paint', 'targets', 'models', 'area_modifiers', 'layers', 'strokes',
    'instances',
}

# Every GeomScatter attribute resolved outside the scalar dict, and which emitter writes it
# (vray_backend = preview, scatter_export = render). Each must write every entry naming its side,
# list-typed ones even when empty - an unwritten attribute goes stale in a live IPR. A one-sided
# entry needs a reason, so "handled on one side only" cannot happen silently again.
#
# Currently every entry is BOTH: the preview no longer skips any placement input. RENDER stays in
# the vocabulary because the table's whole point is that a one-sided entry must be declarable and
# justified rather than simply absent.
BOTH, RENDER = 'both', 'render'
OWNED_ATTRS = {
    'targets':                                     BOTH,
    'target_factors':                              BOTH,
    'spline_vertices':                             BOTH,
    'spline_vertex_counts':                        BOTH,
    'models':                                      BOTH,
    'model_parents':                               BOTH,
    'model_frequencies':                           BOTH,
    'model_cluster_group_ids':                     BOTH,
    'model_handles':                               BOTH,
    # A placement MATCH KEY, not a tint: the core places the model whose color the cluster color
    # map samples to. Both sides, or Color Map clustering would put the preview's models in
    # different PLACES - it is useless without cluster_instances_color_map and vice versa.
    'model_instance_colors':                       BOTH,
    'look_at_target':                              BOTH,
    'look_at_falloff_curve':                       BOTH,
    'look_at_falloff_curve_interp':                BOTH,
    'surface_altitude_limit_falloff_curve':        BOTH,
    'surface_altitude_limit_falloff_curve_interp': BOTH,
    'surface_random_density_map':                  BOTH,
    'area_modifiers_vertices':                     BOTH,
    'area_modifiers_vertex_counts':                BOTH,
    'area_modifiers_operation':                    BOTH,
    'area_modifiers_falloff_near':                 BOTH,
    'area_modifiers_falloff_far':                  BOTH,
    'area_modifiers_scale':                        BOTH,
    'area_modifiers_density':                      BOTH,
    'area_modifiers_axis':                         BOTH,
    'clusters_layer_model_subset_sizes':           BOTH,
    'clusters_layer_models':                       BOTH,
    'clusters_layer_stroke_records':               BOTH,
    'clusters_layer_stroke_radiuses':              BOTH,
    'clusters_layer_stroke_points':                BOTH,
    'clusters_layer_stroke_points_target_face':    BOTH,
    # Every COLOUR-typed map: uploaded as a TexBitmap by both emitters (collect._collectColorMaps
    # + vray_backend._ensureBitmapTex on one side, scatter_export._fillColorTextureMap on the
    # other), and written EMPTY when unused - the core attaches its callbacks on the presence of
    # the texture alone, never on the mode meant to consume it.
    'cluster_instances_color_map':                 BOTH,
    'transforms_translation_map':                  BOTH,
    'transforms_rotation_map':                     BOTH,
    'transforms_scale_map':                        BOTH,
    'instance_override_info_packs':                BOTH,
    'instance_override_placements':                BOTH,
    # Always written EMPTY: every painted instance is PLACED, which the core reads without any
    # transform. Listed and written anyway because an unwritten list attribute goes stale in a
    # live IPR, which is the rule this table exists to enforce.
    'instance_override_transforms':                BOTH,
    # A RenderView carrying the clipping camera's frustum, in SelectedCamera mode. The preview
    # ALWAYS writes one (see vray_backend._buildScene); the render only when the user picked a
    # camera - with no frame data of its own the preview has no other way to clip.
    'camera_clipping_selected_cam':                BOTH,
}


# The ClustersByLayers arrays, in the order buildClusterArrays returns them. Consumers use this
# to CLEAR the whole group when clustering is off (see the always-write rule above).
CLUSTER_ARRAY_NAMES = ('model_handles', 'clusters_layer_model_subset_sizes',
                       'clusters_layer_models', 'clusters_layer_stroke_records',
                       'clusters_layer_stroke_radiuses', 'clusters_layer_stroke_points',
                       'clusters_layer_stroke_points_target_face')


# Non-scalar fields declared in the propgroup but not yet resolved by either emitter. Listing them
# keeps _emitGroup's guard loud while acknowledging they are unfinished, not forgotten.
_NOT_YET_WIRED = set()


def _geomScatterAttrs() -> set | None:
    """ Every parameter name GeomScatter declares, from the plugin descriptor shipped with
        vray_blender - or None when that addon is not installed (soft dependency). """
    import importlib.util
    import json
    import os

    try:
        spec = importlib.util.find_spec('vray_blender')
    except (ImportError, ValueError):
        return None
    if spec is None or not spec.origin:
        return None
    path = os.path.join(os.path.dirname(spec.origin),
                        'plugins_desc', 'geometry', 'GeomScatter.json')
    try:
        with open(path, encoding='utf-8') as f:
            return {p['attr'] for p in json.load(f)['Parameters']}
    except (OSError, KeyError, ValueError):
        return None


def checkContract() -> None:
    """ Every non-scalar propgroup field resolves into attributes an emitter actually claims,
        and nothing that IS a GeomScatter parameter is filed as UI bookkeeping.

        The second half is the one that catches a one-sided attribute: camera_clipping_selected_cam
        sat in _SKIP_UI - "no GeomScatter counterpart at all" - while the preview wrote it as a real
        plugin reference and the render did not. Checking the names against the plugin descriptor
        makes that a register-time error rather than a rendering difference nobody notices.
    """
    for field, attrs in _SKIP_RESOLVED.items():
        if field in _NOT_YET_WIRED:
            continue
        for attr in attrs:
            if attr not in OWNED_ATTRS:
                raise RuntimeError(f"chaos_scatter: '{field}' resolves to '{attr}', "
                                   "which no emitter claims in OWNED_ATTRS")

    if (pluginAttrs := _geomScatterAttrs()) is None:
        return
    for field in _SKIP_UI:
        if field in pluginAttrs:
            raise RuntimeError(f"chaos_scatter: '{field}' is filed as UI bookkeeping in _SKIP_UI "
                               "but IS a GeomScatter parameter. Move it to _SKIP_RESOLVED with an "
                               "OWNED_ATTRS entry naming the side(s) that write it.")
    for attr in OWNED_ATTRS:
        if attr not in pluginAttrs:
            raise RuntimeError(f"chaos_scatter: OWNED_ATTRS names '{attr}', which GeomScatter "
                               "does not declare")


# Bool propgroup fields whose plugin parameter is int-typed.
_BOOL_AS_INT = {'edge_trimming_enabled', 'clustered_distribution_enabled'}

# Float fields the propgroup stores as a PERCENTAGE while the plugin takes the plain fraction
# (100 propgroup == 1.0 plugin). The panel labels them "%" the way the other Chaos Scatter
# integrations do; this is the one place the two scales meet on the way out, and preset.py's
# _coerce is its inverse on the way in.
PERCENT_FIELDS = {
    'avoid_collisions_spacing',
    'surface_map_spacing_u', 'surface_map_spacing_v',
    'surface_map_jitter_u', 'surface_map_jitter_v',
    'surface_map_offset_u', 'surface_map_offset_v',
}
PERCENT_SCALE = 100.0


def _emitGroup(group, out: dict) -> None:
    for prop in group.bl_rna.properties:
        name = prop.identifier
        if prop.is_readonly or name in ('rna_type', 'name'):
            continue
        if name in _SKIP_RESOLVED or name in _SKIP_UI:
            continue
        if prop.type in ('POINTER', 'COLLECTION'):
            # A new pointer/collection field is a parameter somebody has to resolve. Dropping it
            # silently is how the density map shipped preview-only, so make it a hard error here
            # rather than a rendering difference nobody notices.
            if name not in _SUBGROUPS:
                raise RuntimeError(
                    f"chaos_scatter: non-scalar field '{name}' is classified nowhere. Add it to "
                    "_SKIP_RESOLVED (with its GeomScatter attributes and an OWNED_ATTRS entry), "
                    "to _SKIP_UI, or to _SUBGROUPS.")
            continue

        value = getattr(group, name)
        match prop.type:
            case 'ENUM':
                value = int(value)
            case 'FLOAT':
                if getattr(prop, 'is_array', False):
                    # The exporter type dispatch accepts mathutils types, not plain tuples
                    from mathutils import Vector
                    value = Vector(value)
                elif name in PERCENT_FIELDS:
                    value /= PERCENT_SCALE
            case 'BOOLEAN':
                if name in _BOOL_AS_INT:
                    value = int(value)
        out[name] = value


def preservedLinearMatrix(cs, modelObj):
    """ The model object's rotation/scale linear transform, restricted to the components the user
        chose to preserve (transforms_rotation_preserve / transforms_scale_preserve); translation
        is always excluded. Returned as a 4x4 mathutils Matrix (identity when neither is preserved).

        Both the preview's readScatterData source node (backend) and its proxy objects (lifecycle)
        are built at THIS matrix. The scatter core folds its inverse into the returned transforms
        and the proxy re-applies it, so the two cancel and the per-point quaternion+scale attributes
        stay shear-free (the FULL rotation*scale would leave shear they cannot represent).
    """
    from mathutils import Matrix
    tr = cs.transforms
    _, quat, scale = modelObj.matrix_world.decompose()
    m = Matrix.Identity(3)
    if tr.transforms_rotation_preserve:
        m = quat.to_matrix()
    if tr.transforms_scale_preserve:
        m = m @ Matrix.Diagonal(scale)
    return m.to_4x4()


def colorMapClusteringActive(cs) -> bool:
    """ True when Color Map clustering is switched on, i.e. when cluster_instances_color_map and
        model_instance_colors are live PLACEMENT inputs. Shared by the two emitters' texture gates
        and by the preview's invalidation, which has to watch obj.color only in this mode. """
    clusters = cs.clusters
    return clusters.clustered_distribution_enabled and clusters.clustered_distribution_mode == '1'


def clusterLayersValid(cs) -> bool:
    """ True when the ClustersByLayers config can produce output: at least a base layer (index 0)
        with >=1 model. An empty base makes the core return 0 instances (see
        reference_geomscatter_cluster_layers). """
    layers = cs.clusters.layers
    return len(layers) >= 1 and len(layers[0].models) >= 1


def buildClusterArrays(cs, resolvePoint, resolvedTargets: list["ResolvedTarget"],
                       resolvedModels: list["ResolvedModel"]) -> dict:
    """ Build the list-typed ClustersByLayers params (model_handles + clusters_layer_*), all empty
        when clustering-by-layers is off or the layer config is invalid. Returned lists are applied
        by the consumers via their list-update calls (they are NOT scalar params).

        `resolvePoint(targetIndex, (x,y,z)) -> (loopTriIndex, u, v) | None` re-resolves each stored
        OBJECT-SPACE stroke point onto the target's current evaluated mesh (see
        utils.makeStrokePointResolver) - so strokes survive mesh edits/deformation. Points that no
        longer resolve (e.g. target deleted) are dropped.

        Contract (see reference_geomscatter_cluster_layers): layer 0 is the always-full base and
        carries no strokes; painted layers (index >= 1) override the base's model choice in painted
        regions. subLayerIndex parity = paint(even)/erase(odd).

        Storage vs emitted index spaces: see the resolve.py module docstring.
    """
    clusters = cs.clusters
    empty = {name: [] for name in CLUSTER_ARRAY_NAMES}
    if not clusters.clustered_distribution_enabled or clusters.clustered_distribution_mode != '2':
        return empty
    if not clusterLayersValid(cs):
        return empty

    from chaos_scatter import utils
    from chaos_scatter.resolve import TARGET_MESH, modelHandles

    allHandles = modelHandles(resolvedModels)
    handleSpace = set(allHandles)
    # Storage target index -> emitted list index. Splines expand to several entries; a stroke can
    # only sit on a mesh target, so only those are addressable.
    listIndexOf = {t.itemIndex: t.listIndex for t in resolvedTargets if t.kind == TARGET_MESH}

    subsetSizes, layerModels = [], []
    records, radiuses, points, faces = [], [], [], []
    for layerIdx, layer in enumerate(clusters.layers):
        # Filter against the models that actually survived into the emitted list, not against
        # len(cs.models): an item whose object is None never reaches the core.
        handles = [m.model_index for m in layer.models if m.model_index in handleSpace]
        subsetSizes.append(len(handles))
        layerModels.extend(handles)
        if layerIdx == 0:
            continue  # base layer carries no strokes
        for stroke in layer.strokes:
            if stroke.num_points <= 0 or not stroke.points_blob:
                continue
            targetListIndex = listIndexOf.get(stroke.target_index)
            if targetListIndex is None:
                continue  # target removed, or turned into a spline
            xyz = utils.decodeStrokePoints(stroke.points_blob, stroke.num_points)
            strokePoints, strokeFaces = [], []
            for i in range(stroke.num_points):
                resolved = resolvePoint(stroke.target_index, xyz[3 * i:3 * i + 3])
                if resolved is None:
                    continue
                triIdx, u, v = resolved
                strokePoints.extend((u, v))
                strokeFaces.append(triIdx)
            if not strokeFaces:
                continue  # whole stroke fell off the (edited/removed) target
            subLayer = 2 * stroke.sub_layer + (1 if stroke.erase else 0)
            records.extend([len(strokeFaces), targetListIndex, layerIdx, subLayer])
            radiuses.append(stroke.radius)
            points.extend(strokePoints)
            faces.extend(strokeFaces)

    return {
        'model_handles': allHandles,
        'clusters_layer_model_subset_sizes': subsetSizes,
        'clusters_layer_models': layerModels,
        'clusters_layer_stroke_records': records,
        'clusters_layer_stroke_radiuses': radiuses,
        'clusters_layer_stroke_points': points,
        'clusters_layer_stroke_points_target_face': faces,
    }


def buildInstanceOverrideArrays(cs, resolvePoint=None, resolvedTargets=None) -> dict:
    """ Build the instance_override_* arrays for hand-painted individual instances, all empty when
        instance paint is off / empty. Every painted instance is PLACED (flag 1); transforms are a
        native follow-up, so instance_override_transforms is written EMPTY rather than left unset.
        See reference_instance_override_declarative.

        `resolvePoint(targetIndex, (x,y,z)) -> (loopTriIndex, u, v) | None` re-resolves each stored
        OBJECT-SPACE point onto the target's current evaluated mesh, exactly as for cluster strokes
        (utils.makeStrokePointResolver). The GLOBAL triangle index the core wants is that target's
        triBase plus the local index, computed HERE rather than baked at paint time - so a
        reordered, extended or edited target list stays correct. Instances whose target is gone or
        no longer resolves are dropped.

        Returns {'info_packs': [idHi,idLo,flags]*N, 'placements': [(u,v,float(globalTri))]*N,
                 'transforms': []}.
    """
    from chaos_scatter.resolve import TARGET_MESH

    infoPacks, placements = [], []
    empty = {'info_packs': infoPacks, 'placements': placements, 'transforms': []}
    ip = getattr(cs, 'instance_paint', None)
    if ip is None or not ip.instance_paint_enabled or len(ip.instances) == 0:
        return empty
    if resolvePoint is None or resolvedTargets is None:
        return empty

    # triBase is a running count over the MESH entries only, in emitted order (resolve.py).
    triBaseOf = {t.itemIndex: t.triBase for t in resolvedTargets if t.kind == TARGET_MESH}
    # Each instance names its target object; find where that object sits in cs.targets NOW, so a
    # reordered list resolves to the same surface it was painted on.
    indexOf = {item.object: i for i, item in enumerate(cs.targets) if item.object is not None}

    PLACED = 1
    for inst in ip.instances:
        targetIndex = indexOf.get(inst.target)
        if targetIndex is None:
            continue
        triBase = triBaseOf.get(targetIndex)
        if triBase is None:
            continue
        resolved = resolvePoint(targetIndex, (inst.px, inst.py, inst.pz))
        if resolved is None:
            continue
        localTri, u, v = resolved
        infoPacks += [0, int(inst.uid), PLACED]        # idHigh=0, idLow=uid, flags=PLACED
        placements.append((float(u), float(v), float(triBase + localTri)))
    return {'info_packs': infoPacks, 'placements': placements, 'transforms': []}


def buildScatterParams(cs, forPreview: bool) -> dict:
    """ Build the scalar GeomScatter parameter dict from a ChaosScatterSettings propgroup. """
    params = {}

    _emitGroup(cs, params)
    for groupName in EMITTED_GROUPS:
        _emitGroup(getattr(cs, groupName), params)

    # 'display' is viewport-only and intentionally not emitted

    # ClustersByLayers safety: enabling Paint mode before a valid base layer exists would make the
    # core return 0 instances for the WHOLE object. Fall back to un-clustered until it is valid.
    if params.get('clustered_distribution_enabled') and params.get('clustered_distribution_mode') == 2:
        if not clusterLayersValid(cs):
            params['clustered_distribution_enabled'] = 0

    # SelectedCamera with nothing selected leaves the core with no frustum at all; mirror the C4D
    # fixup and fall back to the render camera.
    if params.get('camera_clipping_mode') == 1 and cs.camera_clipping.camera_clipping_selected_cam is None:
        params['camera_clipping_mode'] = 0

    params.update(PINNED_PARAMS)
    params.update(COMPAT_FLAGS)

    # An imported Cosmos preset keeps the compatibility semantics it was authored with.
    if cs.preset_compat:
        params.update(PRESET_COMPAT_FLAGS)

    if forPreview:
        params['instance_count_limit'] = min(params['instance_count_limit'], PREVIEW_INSTANCE_CAP)

    return params


def clippingCameraObject(cs, scene):
    """ The camera object the clipping frustum is built from, or None when camera clipping is off.

        Both consumers resolve it HERE so the preview cannot end up clipping against a different
        camera than the render - and so the preview's invalidation (collect.computeFingerprint,
        recompute._dependencyObjects) watches the camera it actually uses. Mirrors the
        SelectedCamera-with-no-camera fallback buildScatterParams applies to the mode itself.
    """
    clipping = cs.camera_clipping
    if not clipping.camera_clipping_enabled:
        return None
    if clipping.camera_clipping_mode == '1' and clipping.camera_clipping_selected_cam is not None:
        return clipping.camera_clipping_selected_cam
    return scene.camera
