# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

""" Chaos Scatter data model.

    Field names inside the property groups are the verbatim GeomScatter plugin parameter names
    (authoritative list: V-Ray SDK vray_scatter_private/scatter.cpp), stored in V-Ray-native
    ranges: enums as numeric-string identifiers, angles in radians, distances in Blender scene
    units. This makes params.buildScatterParams() a mechanical name-for-name copy.

    Texture-map parameters hold bpy Image pointers; procedural textures are a follow-up.
"""

import math
import bpy

from bpy.props import (
    BoolProperty, CollectionProperty, EnumProperty, FloatProperty, FloatVectorProperty,
    IntProperty, PointerProperty, StringProperty,
)


_2PI = 2.0 * math.pi


def onScatterParamUpdate(self, context):
    """ Any placement-affecting parameter changed: schedule a preview recompute. """
    from chaos_scatter import recompute
    obj = self.id_data
    if isinstance(obj, bpy.types.Object):
        recompute.markDirty(obj)


def onDisplayParamUpdate(self, context):
    """ Display-only parameter changed: sync the GN modifier inputs, no recompute. """
    from chaos_scatter import lifecycle
    obj = self.id_data
    if isinstance(obj, bpy.types.Object):
        lifecycle.syncModifierInputs(obj)


def onModelsChanged(self, context):
    """ Model list content changed: prototype collections must be rebuilt before recompute. """
    from chaos_scatter import lifecycle, recompute
    obj = self.id_data
    if isinstance(obj, bpy.types.Object):
        lifecycle.rebuildProtoCollections(obj)
        recompute.markDirty(obj)


def _pollMeshTarget(self, obj):
    from chaos_scatter.resolve import TARGET_TYPES
    from chaos_scatter.utils import isScatterObject
    # A carrier's points ARE scatter output, so it is never a valid distribution surface
    return (obj.type in TARGET_TYPES) and not isScatterObject(obj)


def _pollCurveObject(self, obj):
    return obj.type == 'CURVE'


def _pollCameraObject(self, obj):
    return obj.type == 'CAMERA'


def _pollModelObject(self, obj):
    from chaos_scatter.resolve import isModelObject
    # An EMPTY is offered because resolveModels treats a root parenting models as a valid model
    # and boxes the whole hierarchy. Whether it actually holds any is left to resolveModels rather
    # than tested here: obj.children_recursive is O(len(bpy.data.objects)) and a poll runs once per
    # object in the picker, so the walk would be quadratic on a large scene.
    # Scatter carriers are refused by isModelObject itself (nesting scatters is not supported).
    return isModelObject(obj) or obj.type == 'EMPTY'


class ChaosScatterTargetItem(bpy.types.PropertyGroup):
    object: PointerProperty(
        type = bpy.types.Object,
        name = "Object",
        description = "Distribution object to scatter on (surface for 2D/3D, curve for 1D)",
        poll = _pollMeshTarget,
        update = onScatterParamUpdate,
    )
    factor: FloatProperty(
        name = "Density Factor",
        description = "Relative scatter probability factor for this target",
        default = 1.0, min = 0.0, soft_max = 1.0,
        update = onScatterParamUpdate,
    )


class ChaosScatterModelItem(bpy.types.PropertyGroup):
    object: PointerProperty(
        type = bpy.types.Object,
        name = "Object",
        description = "Object to be scattered (instanced)",
        poll = _pollModelObject,
        update = onModelsChanged,
    )
    frequency: FloatProperty(
        name = "Frequency",
        description = "Relative frequency with which this model is selected",
        default = 1.0, min = 0.0, soft_max = 1.0,
        update = onScatterParamUpdate,
    )
    cluster_group_id: IntProperty(
        name = "Cluster Group",
        description = "Cluster group ID of the model (-1 for no group)",
        default = -1, min = -1,
        update = onScatterParamUpdate,
    )


class ChaosScatterAreaItem(bpy.types.PropertyGroup):
    object: PointerProperty(
        type = bpy.types.Object,
        name = "Curve",
        description = "Spline object defining an include/exclude area",
        poll = _pollCurveObject,
        update = onScatterParamUpdate,
    )
    operation: EnumProperty(
        name = "Operation",
        description = "Whether the area adds instances inside it or removes them",
        items = (
            ('0', "Include", "Scatter only inside this area"),
            ('1', "Exclude", "Do not scatter inside this area"),
        ),
        default = '0',
        update = onScatterParamUpdate,
    )
    falloff_near: FloatProperty(
        name = "Falloff Near",
        description = "Falloff start distance from the area boundary",
        default = 0.0, min = 0.0, subtype = 'DISTANCE',
        update = onScatterParamUpdate,
    )
    falloff_far: FloatProperty(
        name = "Falloff Far",
        description = "Falloff end distance from the area boundary",
        default = 0.0, min = 0.0, subtype = 'DISTANCE',
        update = onScatterParamUpdate,
    )
    scale: FloatProperty(
        name = "Scale",
        description = "Instance scale inside the falloff zone",
        default = 0.0, min = 0.0, max = 1.0, subtype = 'FACTOR',
        update = onScatterParamUpdate,
    )
    density: FloatProperty(
        name = "Density",
        description = "Instance density inside the falloff zone",
        default = 0.0, min = 0.0, max = 1.0, subtype = 'FACTOR',
        update = onScatterParamUpdate,
    )
    axis: EnumProperty(
        name = "Axis",
        description = "Projection axis of the area",
        items = (('0', "X", "Project the area along the X axis"),
                 ('1', "Y", "Project the area along the Y axis"),
                 ('2', "Z", "Project the area along the Z axis")),
        default = '2',
        update = onScatterParamUpdate,
    )


class ChaosScatterSurface(bpy.types.PropertyGroup):
    surface_scatter_mode: EnumProperty(
        name = "Mode",
        description = "How instances are placed on the surface: at random, or on a regular "
                      "pattern in UV space",
        items = (
            ('0', "Random", "Random distribution over the surface"),
            ('1', "UV Map", "Regular UV-pattern distribution"),
        ),
        default = '0',
        update = onScatterParamUpdate,
    )

    # Random mode
    surface_random_count: IntProperty(
        name = "Count",
        description = "Number of instances to scatter",
        default = 1000, min = 0, soft_max = 100000,
        update = onScatterParamUpdate,
    )
    surface_random_use_density: BoolProperty(
        name = "Density per Area",
        description = "Interpret the count as density per square edge size instead of a total count",
        default = False,
        update = onScatterParamUpdate,
    )
    surface_random_edge_size: FloatProperty(
        name = "Edge Size",
        description = "Edge size of the density reference square",
        default = 10.0, min = 0.0, soft_max = 100.0, subtype = 'DISTANCE',
        update = onScatterParamUpdate,
    )
    surface_random_density_map: PointerProperty(
        type = bpy.types.Image,
        name = "Density Map",
        description = "Texture modulating the scatter density over the surface UVs",
        update = onScatterParamUpdate,
    )
    surface_random_density_map_pattern: EnumProperty(
        name = "Density Pattern",
        description = "Pattern that modulates the scatter density over the surface: one of the "
                      "built-in presets, or Custom Map to use your own texture",
        items = (
            ('0', "None", "Scatter at an even density over the whole surface"),
            ('1', "Custom Map", "Modulate the density with the texture picked below"),
            ('2', "UV Grid", "Test pattern: regular square grid in UV space"),
            ('3', "UV Grid Running", "Test pattern: square grid with every other row offset "
                                     "by half a cell"),
            ('4', "UV Grid Hexagonal", "Test pattern: hexagonal grid in UV space"),
            ('5', "Distorted Streaks High", "Wavy torn streaks, strong density contrast"),
            ('6', "Distorted Streaks Low", "Wavy torn streaks, mild density contrast"),
            ('7', "Groups High", "Rounded clumps with bare gaps between them, strong contrast"),
            ('8', "Groups Low", "Rounded clumps with bare gaps between them, mild contrast"),
            ('9', "Fractal Patches High", "Ragged fractal patches, strong density contrast"),
            ('10', "Fractal Patches Low", "Ragged fractal patches, mild density contrast"),
            ('11', "Straight Lines High", "Straight parallel bands, strong density contrast"),
            ('12', "Straight Lines Low", "Straight parallel bands, mild density contrast"),
            ('13', "Stretched Patches High", "Elongated patches, strong density contrast"),
            ('14', "Stretched Patches Low", "Elongated patches, mild density contrast"),
        ),
        default = '0',
        update = onScatterParamUpdate,
    )

    # UV map mode
    surface_map_pattern: EnumProperty(
        name = "Pattern",
        description = "Arrangement of the cells the instances are placed on in UV space",
        items = (
            ('0', "Grid", "Place one instance per cell of a square grid"),
            ('1', "Running Grid", "Square grid with every other row offset by half a cell"),
            ('2', "Hex Grid", "Place one instance per cell of a hexagonal grid"),
        ),
        default = '0',
        update = onScatterParamUpdate,
    )
    surface_map_planar: BoolProperty(
        name = "Planar Projection",
        description = "Use planar projection instead of the surface UVs",
        default = False,
        update = onScatterParamUpdate,
    )
    # Not drawn: Blender is Z-up and every other host offers planar mapping without an axis
    # choice, so this stays at Z. Kept as a field rather than dropped so a 3ds Max-authored Cosmos
    # preset that picked X or Y still projects the way it was authored (preset.py writes it).
    surface_map_planar_axis: EnumProperty(
        name = "Planar Axis",
        description = "Axis the planar projection is made along",
        items = (('0', "X", "Project the pattern along the X axis"),
                 ('1', "Y", "Project the pattern along the Y axis"),
                 ('2', "Z", "Project the pattern along the Z axis")),
        default = '2',
        update = onScatterParamUpdate,
    )
    surface_map_lock_v: BoolProperty(
        name = "Lock V to U",
        description = "Use the U value for V as well in spacing, jitter and offset",
        default = True,
        update = onScatterParamUpdate,
    )
    # The six UV-grid values and avoid_collisions_spacing are stored as PERCENTAGES, not as the
    # plugin's 0..1 fractions - the label reads "10%" the way every other Chaos Scatter integration
    # shows it. params.PERCENT_FIELDS scales them back on export and preset.py scales them in.
    surface_map_spacing_u: FloatProperty(
        name = "Spacing U",
        description = "Distance between pattern cells along U, as a percentage of the UV range",
        default = 10.0, min = 0.0, soft_max = 100.0, precision = 1, subtype = 'PERCENTAGE',
        update = onScatterParamUpdate,
    )
    surface_map_spacing_v: FloatProperty(
        name = "Spacing V",
        description = "Distance between pattern cells along V, as a percentage of the UV range",
        default = 10.0, min = 0.0, soft_max = 100.0, precision = 1, subtype = 'PERCENTAGE',
        update = onScatterParamUpdate,
    )
    surface_map_jitter_u: FloatProperty(
        name = "Jitter U",
        description = "Random displacement of each instance along U, as a percentage of the cell",
        default = 0.0, min = 0.0, max = 200.0, soft_max = 100.0, precision = 1,
        subtype = 'PERCENTAGE',
        update = onScatterParamUpdate,
    )
    surface_map_jitter_v: FloatProperty(
        name = "Jitter V",
        description = "Random displacement of each instance along V, as a percentage of the cell",
        default = 0.0, min = 0.0, max = 200.0, soft_max = 100.0, precision = 1,
        subtype = 'PERCENTAGE',
        update = onScatterParamUpdate,
    )
    surface_map_offset_u: FloatProperty(
        name = "Offset U",
        description = "Shift of the whole pattern along U, as a percentage of the cell",
        default = 50.0, precision = 1, subtype = 'PERCENTAGE',
        update = onScatterParamUpdate,
    )
    surface_map_offset_v: FloatProperty(
        name = "Offset V",
        description = "Shift of the whole pattern along V, as a percentage of the cell",
        default = 50.0, precision = 1, subtype = 'PERCENTAGE',
        update = onScatterParamUpdate,
    )
    map_channel: IntProperty(
        name = "Map Channel",
        description = "UV channel index used for UV-based distribution and maps",
        default = 0, min = 0, max = 100,
        update = onScatterParamUpdate,
    )
    map_channel_name: StringProperty(
        name = "UV Map",
        description = "UV map name used for UV-based distribution and maps (overrides the index)",
        default = "",
        update = onScatterParamUpdate,
    )

    # Slope limit
    surface_slope_limit_enabled: BoolProperty(
        name = "Limit by Slope",
        description = "Scatter only where the surface slope falls inside the range below",
        default = True,
        update = onScatterParamUpdate,
    )
    surface_slope_limit_mode: EnumProperty(
        name = "Slope Mode",
        description = "Up axis the surface slope is measured against",
        items = (('0', "Local", "Measure the slope against the target object's own up axis"),
                 ('1', "World", "Measure the slope against the world up axis")),
        default = '0',
        update = onScatterParamUpdate,
    )
    surface_slope_limit_min: FloatProperty(
        name = "Slope Min",
        description = "Shallowest surface slope that still receives instances",
        default = 0.0, min = 0.0, max = math.pi, subtype = 'ANGLE',
        update = onScatterParamUpdate,
    )
    surface_slope_limit_max: FloatProperty(
        name = "Slope Max",
        description = "Steepest surface slope that still receives instances",
        default = math.pi, min = 0.0, max = math.pi, subtype = 'ANGLE',
        update = onScatterParamUpdate,
    )

    # Altitude limit
    surface_altitude_limit_enabled: BoolProperty(
        name = "Limit by Altitude",
        description = "Scatter only where the altitude falls inside the range below",
        default = False,
        update = onScatterParamUpdate,
    )
    surface_altitude_limit_mode: EnumProperty(
        name = "Altitude Mode",
        description = "Origin the altitude is measured from",
        items = (('0', "Position Independent",
                  "Measure the altitude from the target's own base, ignoring where it sits"),
                 ('1', "World", "Measure the altitude from the world origin")),
        default = '0',
        update = onScatterParamUpdate,
    )
    surface_altitude_limit_min: FloatProperty(
        name = "Altitude Min",
        description = "Lowest altitude that receives instances",
        default = 0.0, subtype = 'DISTANCE',
        update = onScatterParamUpdate,
    )
    surface_altitude_limit_max: FloatProperty(
        name = "Altitude Max",
        description = "Highest altitude that receives instances",
        default = 0.0, subtype = 'DISTANCE',
        update = onScatterParamUpdate,
    )
    # Serialized altitude falloff curve, kept in sync with the curve widget (curves.py)
    surface_altitude_limit_falloff_data: StringProperty(default = "", options = {'HIDDEN'})


class ChaosScatterSpline(bpy.types.PropertyGroup):
    spline_spacing: FloatProperty(
        name = "Spacing",
        description = "Distance between instances along the spline",
        default = 0.5, min = 0.0, subtype = 'DISTANCE',
        update = onScatterParamUpdate,
    )
    spline_jitter: FloatProperty(
        name = "Jitter",
        description = "Amount of randomness in the placement of instances along the spline",
        default = 0.0, min = 0.0, max = 2.0,
        update = onScatterParamUpdate,
    )
    spline_offset: FloatProperty(
        name = "Offset",
        description = "Shift all instances along the spline; 1 shifts them by one full spacing",
        default = 0.0, soft_min = -1.0, soft_max = 1.0,
        update = onScatterParamUpdate,
    )
    spline_follow_amount: FloatProperty(
        name = "Follow Spline",
        description = "How much instances are turned to follow the spline direction "
                      "(0 = unchanged, 1 = fully aligned)",
        default = 1.0, min = 0.0, max = 1.0, subtype = 'FACTOR',
        update = onScatterParamUpdate,
    )


class ChaosScatterVolume(bpy.types.PropertyGroup):
    volume_random_count: IntProperty(
        name = "Count",
        description = "Number of instances to scatter inside the bounding box",
        default = 1000, min = 0, soft_max = 100000,
        update = onScatterParamUpdate,
    )
    volume_random_use_density: BoolProperty(
        name = "Density per Volume",
        description = "Interpret the count as density per cubic edge size instead of a total count",
        default = False,
        update = onScatterParamUpdate,
    )
    volume_random_edge_size: FloatProperty(
        name = "Edge Size",
        description = "Edge size of the density reference cube",
        default = 10.0, min = 0.0, soft_max = 100.0, subtype = 'DISTANCE',
        update = onScatterParamUpdate,
    )


class ChaosScatterTransforms(bpy.types.PropertyGroup):
    # Translation randomization
    transforms_translation_from: FloatVectorProperty(
        name = "Translation From",
        description = "Lower bound of the random offset added to each instance's position",
        size = 3, default = (0.0, 0.0, 0.0), subtype = 'TRANSLATION',
        update = onScatterParamUpdate,
    )
    transforms_translation_to: FloatVectorProperty(
        name = "Translation To",
        description = "Upper bound of the random offset added to each instance's position",
        size = 3, default = (0.0, 0.0, 0.0), subtype = 'TRANSLATION',
        update = onScatterParamUpdate,
    )
    transforms_translation_step: FloatProperty(
        name = "Translation Step",
        description = "Quantize the random offset to whole multiples of this distance "
                      "(0 disables stepping)",
        default = 0.0, min = 0.0, soft_max = 100.0, subtype = 'DISTANCE',
        update = onScatterParamUpdate,
    )
    transforms_translation_step_x: BoolProperty(
        name = "X", description = "Apply translation stepping on the X axis",
        default = True, update = onScatterParamUpdate,
    )
    transforms_translation_step_y: BoolProperty(
        name = "Y", description = "Apply translation stepping on the Y axis",
        default = True, update = onScatterParamUpdate,
    )
    transforms_translation_step_z: BoolProperty(
        name = "Z", description = "Apply translation stepping on the Z axis",
        default = True, update = onScatterParamUpdate,
    )
    transforms_translation_map: PointerProperty(
        type = bpy.types.Image, name = "Translation Map",
        description = "Texture driving the per-instance translation",
        update = onScatterParamUpdate,
    )
    transforms_translation_map_mode: EnumProperty(
        name = "Map Mode",
        description = "How the values read from the translation map are interpreted",
        items = (('0', "Fixed",
                  "Black uses the From value, white uses the To value - the map replaces the "
                  "per-instance random pick"),
                 ('1', "Random Amount",
                  "The map scales how much randomization is applied: black leaves the instance "
                  "untransformed, white allows the full From/To range")),
        default = '0',
        update = onScatterParamUpdate,
    )
    transforms_translation_map_x: BoolProperty(
        name = "X", description = "Apply the translation map on the X axis",
        default = True, update = onScatterParamUpdate,
    )
    transforms_translation_map_y: BoolProperty(
        name = "Y", description = "Apply the translation map on the Y axis",
        default = True, update = onScatterParamUpdate,
    )
    transforms_translation_map_z: BoolProperty(
        name = "Z", description = "Apply the translation map on the Z axis",
        default = True, update = onScatterParamUpdate,
    )

    # Rotation randomization
    transforms_rotation_from: FloatVectorProperty(
        name = "Rotation From",
        description = "Lower bound of the random rotation added to each instance",
        size = 3, default = (0.0, 0.0, 0.0), subtype = 'EULER',
        update = onScatterParamUpdate,
    )
    transforms_rotation_to: FloatVectorProperty(
        name = "Rotation To",
        description = "Upper bound of the random rotation added to each instance",
        size = 3, default = (0.0, 0.0, _2PI), subtype = 'EULER',
        update = onScatterParamUpdate,
    )
    transforms_rotation_step: FloatProperty(
        name = "Rotation Step",
        description = "Quantize the random rotation to whole multiples of this angle "
                      "(0 disables stepping)",
        default = 0.0, min = 0.0, max = _2PI, subtype = 'ANGLE',
        update = onScatterParamUpdate,
    )
    transforms_rotation_step_x: BoolProperty(
        name = "X", description = "Apply rotation stepping on the X axis",
        default = True, update = onScatterParamUpdate,
    )
    transforms_rotation_step_y: BoolProperty(
        name = "Y", description = "Apply rotation stepping on the Y axis",
        default = True, update = onScatterParamUpdate,
    )
    transforms_rotation_step_z: BoolProperty(
        name = "Z", description = "Apply rotation stepping on the Z axis",
        default = True, update = onScatterParamUpdate,
    )
    transforms_rotation_normal_alignment: FloatProperty(
        name = "Normal Alignment",
        description = "How much instances align to the surface normal (0 = world up, 1 = normal, "
                      "-1 = inverted normal)",
        default = 0.0, min = -1.0, max = 1.0, subtype = 'FACTOR',
        update = onScatterParamUpdate,
    )
    transforms_rotation_map: PointerProperty(
        type = bpy.types.Image, name = "Rotation Map",
        description = "Texture driving the per-instance rotation",
        update = onScatterParamUpdate,
    )
    transforms_rotation_map_mode: EnumProperty(
        name = "Map Mode",
        description = "How the values read from the rotation map are interpreted",
        items = (('0', "Fixed",
                  "Black uses the From value, white uses the To value - the map replaces the "
                  "per-instance random pick"),
                 ('1', "Random Amount",
                  "The map scales how much randomization is applied: black leaves the instance "
                  "untransformed, white allows the full From/To range")),
        default = '0',
        update = onScatterParamUpdate,
    )
    transforms_rotation_map_x: BoolProperty(
        name = "X", description = "Apply the rotation map on the X axis",
        default = True, update = onScatterParamUpdate,
    )
    transforms_rotation_map_y: BoolProperty(
        name = "Y", description = "Apply the rotation map on the Y axis",
        default = True, update = onScatterParamUpdate,
    )
    transforms_rotation_map_z: BoolProperty(
        name = "Z", description = "Apply the rotation map on the Z axis",
        default = True, update = onScatterParamUpdate,
    )
    transforms_rotation_preserve: BoolProperty(
        name = "Preserve Model Rotation",
        description = "Keep the model object's own rotation in the scattered instances",
        default = True,
        update = onScatterParamUpdate,
    )

    # Scale randomization
    transforms_scale_from: FloatVectorProperty(
        name = "Scale From",
        description = "Lower bound of the random scale applied to each instance",
        size = 3, default = (1.0, 1.0, 1.0), subtype = 'XYZ',
        update = onScatterParamUpdate,
    )
    transforms_scale_to: FloatVectorProperty(
        name = "Scale To",
        description = "Upper bound of the random scale applied to each instance",
        size = 3, default = (1.0, 1.0, 1.0), subtype = 'XYZ',
        update = onScatterParamUpdate,
    )
    transforms_scale_step: FloatProperty(
        name = "Scale Step",
        description = "Quantize the random scale to whole multiples of this amount "
                      "(0 disables stepping)",
        default = 0.0, min = 0.0, soft_max = 1.0,
        update = onScatterParamUpdate,
    )
    transforms_scale_step_x: BoolProperty(
        name = "X", description = "Apply scale stepping on the X axis",
        default = True, update = onScatterParamUpdate,
    )
    transforms_scale_step_y: BoolProperty(
        name = "Y", description = "Apply scale stepping on the Y axis",
        default = True, update = onScatterParamUpdate,
    )
    transforms_scale_step_z: BoolProperty(
        name = "Z", description = "Apply scale stepping on the Z axis",
        default = True, update = onScatterParamUpdate,
    )
    transforms_scale_uniform: BoolProperty(
        name = "Uniform Scale",
        description = "Scale all three axes by the same random factor",
        default = True,
        update = onScatterParamUpdate,
    )
    transforms_scale_map: PointerProperty(
        type = bpy.types.Image, name = "Scale Map",
        description = "Texture driving the per-instance scale",
        update = onScatterParamUpdate,
    )
    transforms_scale_map_mode: EnumProperty(
        name = "Map Mode",
        description = "How the values read from the scale map are interpreted",
        items = (('0', "Fixed",
                  "Black uses the From value, white uses the To value - the map replaces the "
                  "per-instance random pick"),
                 ('1', "Random Amount",
                  "The map scales how much randomization is applied: black leaves the instance "
                  "untransformed, white allows the full From/To range")),
        default = '0',
        update = onScatterParamUpdate,
    )
    transforms_scale_map_x: BoolProperty(
        name = "X", description = "Apply the scale map on the X axis",
        default = True, update = onScatterParamUpdate,
    )
    transforms_scale_map_y: BoolProperty(
        name = "Y", description = "Apply the scale map on the Y axis",
        default = True, update = onScatterParamUpdate,
    )
    transforms_scale_map_z: BoolProperty(
        name = "Z", description = "Apply the scale map on the Z axis",
        default = True, update = onScatterParamUpdate,
    )
    transforms_scale_preserve: BoolProperty(
        name = "Preserve Model Scale",
        description = "Keep the model object's own scale in the scattered instances",
        default = True,
        update = onScatterParamUpdate,
    )


class ChaosScatterLookAt(bpy.types.PropertyGroup):
    look_at_enabled: BoolProperty(
        name = "Look At",
        description = "Rotate the instances so that they face a target object",
        default = False,
        update = onScatterParamUpdate,
    )
    look_at_target: PointerProperty(
        type = bpy.types.Object,
        name = "Target",
        description = "Object the scattered instances orient towards",
        update = onScatterParamUpdate,
    )
    look_at_falloff_distance: FloatProperty(
        name = "Falloff Distance",
        description = "Distance from the target past which instances stop turning towards it",
        default = 0.0, min = 0.0, subtype = 'DISTANCE',
        update = onScatterParamUpdate,
    )
    look_at_models_orientation_axis: EnumProperty(
        name = "Orientation Axis",
        description = "Model axis that is aimed at the target",
        items = (('0', "X", "Aim the model's X axis at the target"),
                 ('1', "Y", "Aim the model's Y axis at the target"),
                 ('2', "Z", "Aim the model's Z axis at the target")),
        default = '2',
        update = onScatterParamUpdate,
    )
    look_at_models_orientation_axis_invert: BoolProperty(
        name = "Invert Axis",
        description = "Aim the negative orientation axis at the target instead",
        default = False,
        update = onScatterParamUpdate,
    )
    look_at_horizontal: BoolProperty(
        name = "Horizontal Only",
        description = "Rotate only around the vertical axis",
        default = False,
        update = onScatterParamUpdate,
    )
    # Serialized look-at falloff curve, kept in sync with the curve widget (curves.py)
    look_at_falloff_data: StringProperty(default = "", options = {'HIDDEN'})


class ChaosScatterLayerModelItem(bpy.types.PropertyGroup):
    """ One model of a cluster layer's subset. `model_index` indexes cs.models. """
    model_index: IntProperty(
        description = "Index in the Models list of the model this layer uses",
        default = 0, min = 0, update = onScatterParamUpdate,
    )


class ChaosScatterClusterStroke(bpy.types.PropertyGroup):
    """ One painted stroke on a cluster layer. Point data is the stroke's OBJECT-SPACE surface
        positions packed into `points_blob` (base64) - see utils.encodeStrokePoints. They are
        re-resolved to (triangle, barycentric) on the target's current evaluated mesh at build time
        (utils.makeStrokePointResolver) so strokes survive target mesh edits/deformation. Written by
        the paint operator; never edited field-by-field, so no per-field update callbacks. """
    target_index: IntProperty(default = 0, options = {'HIDDEN'})
    # subLayerIndex parity encodes paint (even) vs erase (odd); magnitude is the stacking order.
    erase: BoolProperty(default = False, options = {'HIDDEN'})
    sub_layer: IntProperty(default = 0, min = 0, options = {'HIDDEN'})
    radius: FloatProperty(default = 0.5, min = 0.0, options = {'HIDDEN'})
    num_points: IntProperty(default = 0, options = {'HIDDEN'})
    points_blob: StringProperty(default = "", options = {'HIDDEN'})


class ChaosScatterClusterLayer(bpy.types.PropertyGroup):
    """ A cluster layer. Layer 0 (index 0 in `clusters.layers`) is the always-full BASE layer: it
        must have >=1 model and carries no strokes. Painted layers (index >= 1) override the base's
        model choice within their painted regions. """
    name: StringProperty(
        name = "Name",
        description = "Name of this cluster layer",
        default = "Layer",
    )
    models: CollectionProperty(type = ChaosScatterLayerModelItem)
    models_active_index: IntProperty(default = 0)
    strokes: CollectionProperty(type = ChaosScatterClusterStroke)
    color: FloatVectorProperty(
        name = "Brush Color", subtype = 'COLOR', size = 3,
        default = (0.9, 0.3, 0.1), min = 0.0, max = 1.0,
        description = "Viewport tint for this layer's painted strokes",
    )


class ChaosScatterClusters(bpy.types.PropertyGroup):
    clustered_distribution_enabled: BoolProperty(
        name = "Clustering",
        description = "Pick the models in clumps over regions of the surface instead of "
                      "independently per instance",
        default = False,
        update = onScatterParamUpdate,
    )
    clustered_distribution_mode: EnumProperty(
        name = "Mode",
        description = "How the cluster regions are defined",
        items = (
            ('0', "Generate", "Procedurally generated clusters"),
            ('1', "Color Map", "Clusters driven by a color map"),
            ('2', "Paint", "Hand-painted cluster layers (paint model clumps over a base layer)"),
        ),
        default = '0',
        update = onScatterParamUpdate,
    )
    clusters_gen_mapping: EnumProperty(
        name = "Mapping",
        description = "Space the cluster pattern is generated in",
        items = (('0', "XY", "Generate the pattern in world XY space, sized in scene units"),
                 ('1', "UV", "Generate the pattern in the target's UV space")),
        default = '0',
        update = onScatterParamUpdate,
    )
    clusters_gen_uv_chanel: IntProperty(
        name = "UV Channel", default = 0, min = 0,
        description = "UV channel index the cluster generator maps into. Index only - unlike the "
                      "distribution channel there is no name-based equivalent on GeomScatter",
        update = onScatterParamUpdate,
    )
    clusters_gen_seed: IntProperty(
        name = "Cluster Seed",
        description = "Seed of the cluster pattern randomization",
        default = 1, min = 0,
        update = onScatterParamUpdate,
    )
    clusters_gen_scale: FloatProperty(
        name = "Scale",
        description = "Scale of the cluster pattern in UV mapping mode",
        default = 1.0, min = 0.0001, max = 100000.0,
        update = onScatterParamUpdate,
    )
    clusters_gen_size: FloatProperty(
        name = "Size",
        description = "Average size of a single cluster in scene units, in XY mapping mode",
        default = 5.0,
        update = onScatterParamUpdate,
    )
    clusters_gen_rotation: FloatProperty(
        name = "Rotation",
        description = "Rotation of the cluster pattern",
        default = 0.0, subtype = 'ANGLE',
        update = onScatterParamUpdate,
    )
    clusters_gen_offset_x: FloatProperty(
        name = "Offset X", description = "Offset of the cluster pattern along X, in scene units",
        default = 0.0, update = onScatterParamUpdate,
    )
    clusters_gen_offset_y: FloatProperty(
        name = "Offset Y", description = "Offset of the cluster pattern along Y, in scene units",
        default = 0.0, update = onScatterParamUpdate,
    )
    clusters_gen_offset_u: FloatProperty(
        name = "Offset U", description = "Offset of the cluster pattern along U, in UV space",
        default = 0.0, update = onScatterParamUpdate,
    )
    clusters_gen_offset_v: FloatProperty(
        name = "Offset V", description = "Offset of the cluster pattern along V, in UV space",
        default = 0.0, update = onScatterParamUpdate,
    )
    clusters_gen_roughness: FloatProperty(
        name = "Roughness",
        description = "How ragged the cluster borders are, in percent",
        default = 0.0, min = 0.0, max = 100.0,
        update = onScatterParamUpdate,
    )
    clusters_gen_edge_blend: FloatProperty(
        name = "Edge Blend",
        description = "How far the model choice blends across a cluster border, in percent",
        default = 0.0, min = 0.0, max = 100.0,
        update = onScatterParamUpdate,
    )
    clusters_gen_diversity: FloatProperty(
        name = "Diversity",
        description = "How much the generated clusters vary in size and shape, in percent",
        default = 0.0, min = 0.0, max = 100.0,
        update = onScatterParamUpdate,
    )
    cluster_instances_color_map: PointerProperty(
        type = bpy.types.Image, name = "Cluster Color Map",
        description = "Texture whose colors select the cluster group an instance takes its "
                      "model from",
        update = onScatterParamUpdate,
    )
    # ClustersByLayers ('Paint' mode) layer stack. Index 0 is the base layer; 1.. are painted.
    layers: CollectionProperty(type = ChaosScatterClusterLayer)
    layers_active_index: IntProperty(default = 0)

    # Paint-tool state (viewport-only; NOT GeomScatter params - listed in params._SKIP_UI).
    paint_radius: FloatProperty(
        name = "Brush Radius",
        description = "Radius of the cluster paint brush",
        default = 0.5, min = 0.0001, soft_max = 5.0, subtype = 'DISTANCE',
    )
    paint_erase: BoolProperty(
        name = "Erase",
        description = "Erase painted cluster strokes instead of adding them",
        default = False,
    )
    paint_sub_layer: IntProperty(
        name = "Sublayer",
        description = "Stacking order of new strokes inside the layer: a stroke only overrides "
                      "strokes on a lower sublayer",
        default = 0, min = 0,
    )


class ChaosScatterPlacedInstance(bpy.types.PropertyGroup):
    """ One hand-painted scatter instance (instance_override PLACED). Identified by a host-minted
        uid (the low 32 bits of the extended instance id; high bits are 0).

        Stored as an OBJECT-SPACE point on one target, exactly like a cluster stroke point: the
        triangle index and the barycentric coordinates are re-resolved against the target's current
        evaluated mesh at build time (params.buildInstanceOverrideArrays). Baking the GLOBAL
        triangle index at paint time instead made every painted instance silently repoint whenever
        the target list changed order or length, or the target mesh was edited, and left the erase
        hit box behind whenever the target moved.
    """
    uid: IntProperty(options = {'HIDDEN'})
    # The target OBJECT, not its index in cs.targets: that list can be reordered from the UI
    # (chaos_scatter.list_move), which would silently move every instance stored by index onto a
    # different object. A pointer also survives a rename and nulls itself when the target is
    # deleted, which drops the instance instead of orphaning it.
    target: PointerProperty(type = bpy.types.Object, options = {'HIDDEN'})
    px: FloatProperty(options = {'HIDDEN'})    # object space of `target`
    py: FloatProperty(options = {'HIDDEN'})
    pz: FloatProperty(options = {'HIDDEN'})


class ChaosScatterInstancePaint(bpy.types.PropertyGroup):
    """ Hand-painted individual scatter instances, layered on top of the automatic distribution via
        GeomScatter's instance_override_* params. Add/erase only (v1); model is distribution-driven.
        Not a GeomScatter param group - the arrays are built by params.buildInstanceOverrideArrays. """
    # Namespaced like every sibling group's flag: params.py works with flat, un-namespaced name
    # sets, where a bare 'enabled' is the field most likely to collide.
    instance_paint_enabled: BoolProperty(
        name = "Instance Paint",
        description = "Add individual instances by hand on top of the automatic distribution",
        default = False, update = onScatterParamUpdate,
    )
    instances: CollectionProperty(type = ChaosScatterPlacedInstance)
    next_uid: IntProperty(default = 1, options = {'HIDDEN'})
    paint_radius: FloatProperty(
        name = "Brush Radius",
        description = "Radius of the instance paint brush",
        default = 0.5, min = 0.0001, soft_max = 5.0, subtype = 'DISTANCE',
    )
    paint_spacing: FloatProperty(
        name = "Spacing", default = 1.0, min = 0.05, soft_max = 4.0, subtype = 'FACTOR',
        description = "Distance between placed instances, in brush-radius units",
    )
    paint_erase: BoolProperty(
        name = "Erase",
        description = "Remove painted instances instead of adding them",
        default = False,
    )


class ChaosScatterCameraClipping(bpy.types.PropertyGroup):
    camera_clipping_enabled: BoolProperty(
        name = "Camera Clipping",
        description = "Generate instances only inside the camera frustum",
        default = False,
        update = onScatterParamUpdate,
    )
    camera_clipping_mode: EnumProperty(
        name = "Mode",
        description = "Which camera's frustum the instances are clipped against",
        items = (
            ('0', "Render Camera", "The scene's active render camera"),
            ('1', "Selected Camera", "A camera picked below, independent of the render camera"),
        ),
        default = '0',
        update = onScatterParamUpdate,
    )
    camera_clipping_selected_cam: PointerProperty(
        type = bpy.types.Object,
        name = "Camera",
        description = "Camera to clip against in Selected Camera mode. "
                      "The render camera is used while this is empty",
        poll = _pollCameraObject,
        update = onScatterParamUpdate,
    )
    camera_clipping_extend_view: FloatProperty(
        name = "Extend View", default = 10.0, min = 0.0, subtype = 'DISTANCE',
        description = "Extend the clipping frustum outward to keep shadows/reflections stable",
        update = onScatterParamUpdate,
    )
    camera_clipping_near_far_enabled: BoolProperty(
        name = "Near/Far Clipping", default = False,
        description = "Also clip instances by distance along the camera's view direction",
        update = onScatterParamUpdate,
    )
    camera_clipping_near_distance: FloatProperty(
        name = "Near Distance", default = 0.0, min = 0.0, subtype = 'DISTANCE',
        description = "Instances closer to the camera than this are not generated",
        update = onScatterParamUpdate,
    )
    camera_clipping_far_distance: FloatProperty(
        name = "Far Distance", default = 10000.0, min = 0.0, subtype = 'DISTANCE',
        description = "Instances further from the camera than this are not generated",
        update = onScatterParamUpdate,
    )


class ChaosScatterDisplay(bpy.types.PropertyGroup):
    """ Viewport-only display settings; never exported, never trigger a recompute. """
    preview_mode: EnumProperty(
        name = "Preview As",
        description = "How the scattered instances are drawn in the viewport",
        items = (
            ('0', "None", "Hide the preview instances"),
            ('1', "Dots", "Draw a point per instance"),
            ('2', "Boxes", "Draw solid bounding boxes"),
            ('3', "Wire Boxes", "Draw wireframe bounding boxes"),
            ('4', "Full", "Instance the full model geometry"),
        ),
        default = '4',
        update = onDisplayParamUpdate,
    )
    display_percentage: FloatProperty(
        name = "Percentage",
        description = "Percentage of the instances shown in the viewport (renders always use all)",
        default = 100.0, min = 0.0, max = 100.0, subtype = 'PERCENTAGE',
        update = onDisplayParamUpdate,
    )
    display_limit: IntProperty(
        name = "Display Limit",
        description = "Maximum number of instances shown in the viewport",
        default = 2000000, min = 0,
        update = onDisplayParamUpdate,
    )
    dot_size: FloatProperty(
        name = "Dot Size",
        description = "Size of the viewport dots in Dots preview mode",
        default = 0.02, min = 0.0, soft_max = 1.0, subtype = 'DISTANCE',
        update = onDisplayParamUpdate,
    )


class ChaosScatterSettings(bpy.types.PropertyGroup):
    is_scatter: BoolProperty(default = False, options = {'HIDDEN'})
    # Per-object namespace key for the hidden falloff-curve nodes (curves.py)
    curve_ns: StringProperty(default = "", options = {'HIDDEN'})
    # These settings came from a Chaos Cosmos preset, which is authored in 3ds Max. Makes
    # params.buildScatterParams keep the compatibility semantics the scatter core reads a preset
    # with (params.PRESET_COMPAT_FLAGS), so the result matches the authored look.
    preset_compat: BoolProperty(default = False, options = {'HIDDEN'})

    scatter_type: EnumProperty(
        name = "Scatter Type",
        description = "Kind of target geometry the instances are distributed on",
        items = (
            ('0', "On Splines (1D)", "Scatter along spline objects"),
            ('1', "On Surfaces (2D)", "Scatter over surface objects"),
            ('2', "In Bounding Box (3D)", "Scatter inside the targets' bounding box volume"),
        ),
        default = '1',
        update = onScatterParamUpdate,
    )
    seed: IntProperty(
        name = "Seed",
        description = "Seed of the random distribution",
        default = 1, min = 1, max = 31337, soft_max = 100,
        update = onScatterParamUpdate,
    )
    instance_count_limit: IntProperty(
        name = "Instance Limit",
        description = "Maximum number of scattered instances",
        default = 1000000, min = 0,
        update = onScatterParamUpdate,
    )
    avoid_collisions: BoolProperty(
        name = "Avoid Collisions",
        description = "Discard instances whose bounding boxes overlap another instance",
        default = False,
        update = onScatterParamUpdate,
    )
    avoid_collisions_spacing: FloatProperty(
        name = "Spacing",
        description = "Collision-avoidance spacing over the models' bounding boxes, as a "
                      "percentage of the box size",
        default = 100.0, min = 1.0, soft_max = 500.0, max = 10000.0, precision = 1,
        subtype = 'PERCENTAGE',
        update = onScatterParamUpdate,
    )
    rest_pose_enabled: BoolProperty(
        name = "Temporal Consistency",
        description = "Sample the distribution at a fixed frame so it does not change over "
                      "the animation",
        default = False,
        update = onScatterParamUpdate,
    )
    rest_pose_frame: FloatProperty(
        name = "Rest Frame",
        description = "Frame the distribution is sampled at when Temporal Consistency is enabled",
        default = 0.0, min = 0.0,
        update = onScatterParamUpdate,
    )
    edge_trimming_enabled: BoolProperty(
        name = "Edge Trimming",
        description = "Trim instance geometry that overhangs the target surface boundary",
        default = False,
        update = onScatterParamUpdate,
    )

    targets: CollectionProperty(type = ChaosScatterTargetItem)
    targets_active_index: IntProperty(default = 0)
    models: CollectionProperty(type = ChaosScatterModelItem)
    models_active_index: IntProperty(default = 0)
    area_modifiers: CollectionProperty(type = ChaosScatterAreaItem)
    area_modifiers_active_index: IntProperty(default = 0)

    surface: PointerProperty(type = ChaosScatterSurface)
    spline: PointerProperty(type = ChaosScatterSpline)
    volume: PointerProperty(type = ChaosScatterVolume)
    transforms: PointerProperty(type = ChaosScatterTransforms)
    look_at: PointerProperty(type = ChaosScatterLookAt)
    clusters: PointerProperty(type = ChaosScatterClusters)
    instance_paint: PointerProperty(type = ChaosScatterInstancePaint)
    camera_clipping: PointerProperty(type = ChaosScatterCameraClipping)
    display: PointerProperty(type = ChaosScatterDisplay)


_CLASSES = (
    ChaosScatterTargetItem,
    ChaosScatterModelItem,
    ChaosScatterAreaItem,
    ChaosScatterSurface,
    ChaosScatterSpline,
    ChaosScatterVolume,
    ChaosScatterTransforms,
    ChaosScatterLookAt,
    ChaosScatterLayerModelItem,
    ChaosScatterClusterStroke,
    ChaosScatterClusterLayer,
    ChaosScatterClusters,
    ChaosScatterPlacedInstance,
    ChaosScatterInstancePaint,
    ChaosScatterCameraClipping,
    ChaosScatterDisplay,
    ChaosScatterSettings,
)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Object.chaos_scatter = PointerProperty(type = ChaosScatterSettings)


def unregister():
    del bpy.types.Object.chaos_scatter
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
