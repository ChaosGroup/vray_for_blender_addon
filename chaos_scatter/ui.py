# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

""" Chaos Scatter UI.

    The SAME panel set is exposed in two hosts sharing the draw code via mixins:
      - the 3D viewport sidebar (N-panel), tab "Chaos Scatter" - the primary workflow home,
        like other scatter addons;
      - Properties editor > Object Data tab - the carrier is a PointCloud whose data tab is
        otherwise empty (Blender's own point-cloud data panels poll on COMPAT_ENGINES and are
        hidden under V-Ray), so the scatter parameters live where the object's data lives.

    Engine-agnostic on purpose: panels poll only on the scatter object, never on the active
    render engine, so scatter is editable under Cycles/EEVEE/V-Ray alike.
"""

import bpy

from chaos_scatter import apply as apply_result
from chaos_scatter import curves, resolve, utils
from chaos_scatter.backend import getBackend


def _activeScatter(context):
    obj = context.object
    return obj if utils.isScatterObject(obj) else None


def _activeTargetMesh(cs):
    """ The active target's Mesh, for the UV map picker. Only real MESH objects expose named UV
        layers - a curve/text target has them only after evaluation, with no stable name to pick.
    """
    idx = cs.targets_active_index
    if not (0 <= idx < len(cs.targets)):
        return None
    obj = cs.targets[idx].object
    if obj is None or obj.type != 'MESH' or not obj.data.uv_layers:
        return None
    return obj.data


# --------------------------------------------------------------------------
# UILists
# --------------------------------------------------------------------------

class CSCATTER_UL_targets(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_property, index):
        row = layout.row(align=True)
        row.prop(item, "object", text="")
        row.prop(item, "factor", text="Factor")


class CSCATTER_UL_models(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_property, index):
        row = layout.row(align=True)
        row.prop(item, "object", text="")
        row.prop(item, "frequency", text="Freq")
        # The cluster group only feeds the clustered distribution, so it is meaningless while
        # clustering is off. Same gate as V-Ray for SketchUp.
        if data.clusters.clustered_distribution_enabled:
            row.prop(item, "cluster_group_id", text="Grp")


class CSCATTER_UL_areas(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_property, index):
        row = layout.row(align=True)
        row.prop(item, "object", text="")
        row.prop(item, "operation", text="")


class CSCATTER_UL_cluster_layers(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_property, index):
        row = layout.row(align=True)
        isBase = (index == 0)
        row.label(text=item.name or ("Base" if isBase else f"Layer {index}"),
                  icon='LOCKED' if isBase else 'BRUSH_DATA')
        if not isBase:
            row.label(text=f"{len(item.strokes)}")
            row.prop(item, "color", text="")


class CSCATTER_UL_layer_models(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_property, index):
        cs = item.id_data.chaos_scatter
        row = layout.row(align=True)
        row.prop(item, "model_index", text="#")
        name = "<invalid>"
        if 0 <= item.model_index < len(cs.models) and cs.models[item.model_index].object:
            name = cs.models[item.model_index].object.name
        row.label(text=name)


def _splitRow(layout):
    """ One row shaped exactly like Blender's own property-split row - see
        uiItemPropertySplitWrapperCreate in interface_layout.cc: a row, split at Blender's
        UI_ITEM_PROP_SEP_DIVIDE (0.4) with align on, whose first child is the right-aligned label
        column. Returns (labelColumn, valueLayout).

        Needed wherever a row cannot be a plain prop() - a widget that ignores property_split, or
        two fields sharing one label. A hand-rolled split(0.4) is NOT the same: without align the
        split inserts item spacing between the halves, so the value column starts a few pixels left
        of every prop() row and the whole block looks off at wide panel widths.
    """
    row = layout.row(align=True)
    split = row.split(factor=0.4, align=True)
    label = split.column(align=True)
    label.alignment = 'RIGHT'
    return label, split


def _drawListPanel(layout, cs, group, listClass, indexProp):
    row = layout.row()
    row.template_list(listClass, "", cs, group, cs, indexProp, rows=3)
    col = row.column(align=True)
    col.operator("chaos_scatter.list_add", icon='ADD', text="").group = group
    col.operator("chaos_scatter.list_remove", icon='REMOVE', text="").group = group
    col.separator()
    up = col.operator("chaos_scatter.list_move", icon='TRIA_UP', text="")
    up.group = group
    up.direction = -1
    down = col.operator("chaos_scatter.list_move", icon='TRIA_DOWN', text="")
    down.group = group
    down.direction = 1


# --------------------------------------------------------------------------
# Shared draw bodies
# --------------------------------------------------------------------------

class _MainDraw:
    def draw(self, context):
        layout = self.layout
        obj = _activeScatter(context)

        if obj is None:
            # Offered only on a selection that can actually be scattered on - the selected
            # targets are what the new scatter is built from
            if resolve.selectedTargets(context):
                layout.operator("chaos_scatter.add", icon='OUTLINER_OB_POINTCLOUD')
            else:
                layout.label(text="Select a surface or spline", icon='INFO')
            return

        cs = obj.chaos_scatter

        backend = getBackend()
        if not backend.isAvailable():
            box = layout.box()
            box.label(text="Viewport preview unavailable:", icon='ERROR')
            box.label(text=backend.unavailableReason())
            box.label(text="V-Ray renders are not affected")

        status = apply_result.getStatus(obj)
        if errorText := status.get('error'):
            box = layout.box()
            box.label(text=f"Preview error: {errorText}", icon='ERROR')
        elif (count := status.get('count')) is not None:
            row = layout.row()
            row.label(text=f"Instances: {count:,}")
            if status.get('limitHit'):
                row.label(text="Limit reached", icon='ERROR')

        if not any(item.object for item in cs.targets):
            layout.label(text="Add a surface in Distribution Targets", icon='INFO')
        if not any(item.object for item in cs.models):
            layout.label(text="Add objects to scatter in Scattered Models", icon='INFO')

        layout.operator("chaos_scatter.refresh", icon='FILE_REFRESH')

        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.prop(cs, "scatter_type")
        layout.prop(cs, "seed")
        layout.prop(cs, "instance_count_limit")

        # A toggle owning exactly one value goes on a single row - the checkbox in the label
        # column, the value beside it - the way Cycles draws its Noise Threshold.
        row = layout.row(align=True, heading="Avoid Collisions")
        row.prop(cs, "avoid_collisions", text="")
        sub = row.row(align=True)
        sub.enabled = cs.avoid_collisions
        sub.prop(cs, "avoid_collisions_spacing", text="")

        row = layout.row(align=True, heading="Temporal Consistency")
        row.prop(cs, "rest_pose_enabled", text="")
        sub = row.row(align=True)
        sub.enabled = cs.rest_pose_enabled
        # Named inline: with the checkbox owning the row's label there is otherwise nothing
        # saying the number is a frame.
        sub.prop(cs, "rest_pose_frame", text="Frame")

        # Trims instances against the target's boundary, which a 1D spline distribution does not
        # have - the core's edgeTrimming pass works off the target surface's element triangles.
        if cs.scatter_type != '0':
            layout.prop(cs, "edge_trimming_enabled")


class _TargetsDraw:
    def draw(self, context):
        cs = _activeScatter(context).chaos_scatter
        self.layout.label(text="Surfaces (2D/3D) or curves (1D) to scatter on:")
        _drawListPanel(self.layout, cs, 'targets', "CSCATTER_UL_targets", 'targets_active_index')


class _ModelsDraw:
    def draw(self, context):
        cs = _activeScatter(context).chaos_scatter
        self.layout.label(text="Objects to be scattered (instanced):")
        _drawListPanel(self.layout, cs, 'models', "CSCATTER_UL_models", 'models_active_index')


class _DistributionDraw:
    def draw(self, context):
        layout = self.layout
        obj = _activeScatter(context)
        cs = obj.chaos_scatter
        layout.use_property_split = True
        layout.use_property_decorate = False

        match cs.scatter_type:
            case '0':
                self._drawSpline(layout, cs)
            case '1':
                self._drawSurface(layout, obj, cs)
            case '2':
                self._drawVolume(layout, cs)

    def _drawSpline(self, layout, cs):
        spline = cs.spline
        layout.prop(spline, "spline_spacing")
        layout.prop(spline, "spline_jitter")
        layout.prop(spline, "spline_offset")
        layout.prop(spline, "spline_follow_amount")

    def _drawVolume(self, layout, cs):
        volume = cs.volume
        layout.prop(volume, "volume_random_count")
        layout.prop(volume, "volume_random_use_density")
        sub = layout.column()
        sub.enabled = volume.volume_random_use_density
        sub.prop(volume, "volume_random_edge_size")

    def _drawMapGrid(self, layout, surface):
        """ Spacing / jitter / offset as a labelled U-by-V grid, matching the 3ds Max scatter panel:
            one pair of captions over two value columns, with the V lock on the caption row.
        """
        grid = layout.column(align=True)
        grid.use_property_split = False
        locked = surface.surface_map_lock_v

        # The V lock is a padlock in the caption row's LABEL column, drawn the way Blender draws
        # its transform locks (icon only, no emboss). It goes in the label column, not in a slot
        # past the split, because a trailing item takes an even share of the row rather than an
        # icon's width - which would make the grid rows narrower than the prop() rows around them.
        # An icon-only button sizes to its content, so unlike a checkbox it does right-align.
        # The glyph is picked here: the selected-state icon+1 in interface.cc applies only to
        # properties flagged PROP_ICONS_CONSECUTIVE, a C-only RNA call bpy.props cannot reach.
        label, split = _splitRow(grid)
        # A nested row, because alignment on the label COLUMN centres an icon-only button instead
        # of pushing it right - a row packs content-sized items against its alignment edge.
        lock = label.row(align=True)
        lock.alignment = 'RIGHT'
        lock.prop(surface, "surface_map_lock_v", text="", emboss=False,
                  icon='LOCKED' if locked else 'UNLOCKED')
        captions = split.row(align=True)
        captions.label(text="U")
        captions.label(text="V")

        for text, attr in (("Spacing", "spacing"), ("Jitter", "jitter"), ("Offset", "offset")):
            label, split = _splitRow(grid)
            label.label(text=text)
            values = split.row(align=True)
            values.prop(surface, f"surface_map_{attr}_u", text="")
            sub = values.row(align=True)
            sub.enabled = not locked
            sub.prop(surface, f"surface_map_{attr}_v", text="")

    def _drawSurface(self, layout, obj, cs):
        surface = cs.surface
        layout.prop(surface, "surface_scatter_mode")

        if surface.surface_scatter_mode == '0':
            layout.prop(surface, "surface_random_count")
            layout.prop(surface, "surface_random_use_density")
            sub = layout.column()
            sub.enabled = surface.surface_random_use_density
            sub.prop(surface, "surface_random_edge_size")
            layout.prop(surface, "surface_random_density_map_pattern")
            sub = layout.column()
            sub.enabled = surface.surface_random_density_map_pattern == '1'
            # text= makes template_ID put its label in the split's label column
            # (uiItemL_respect_property_split), instead of spanning the whole panel unlabelled.
            sub.template_ID(surface, "surface_random_density_map", open="image.open",
                            text="Density Map")
        else:
            layout.prop(surface, "surface_map_pattern")
            layout.prop(surface, "surface_map_planar")
            self._drawMapGrid(layout, surface)

        # Drawn for both distribution modes: the channel feeds the UV grid AND the density map,
        # which GeomScatter samples through the same UVW. Maya gates these on Surface scattering
        # only, 3ds Max keeps a separate spinner per mode.
        # The name wins when set (GeomScatter resolves it per target); the index is the
        # fallback for targets whose UV maps are not named consistently, since the core takes
        # a single channel for all of them.
        col = layout.column(align=True)
        uvSource = _activeTargetMesh(cs)
        if uvSource is not None:
            # Same picker as UVWGenMayaPlace2dTexture. Anchored on the ACTIVE target because
            # that is where the UV maps live; the name still applies to every target.
            col.prop_search(surface, "map_channel_name", uvSource, "uv_layers", text="UV Map",
                            icon='GROUP_UVS')
        else:
            col.prop(surface, "map_channel_name", icon='GROUP_UVS')
        # The two are ALTERNATIVES, not a pair: GeomScatter ignores map_channel whenever
        # map_channel_name is set. Showing the index only when there is no name keeps exactly one
        # live control on screen. The index is still worth having - it addresses "the Nth UV map
        # on every target", which one shared name cannot express when the targets name their maps
        # differently, and the core takes a single channel for all of them.
        if not surface.map_channel_name:
            col.prop(surface, "map_channel")


def _drawCurve(layout, curveNode, text):
    """ A curve widget with a heading. template_curve_mapping takes no text and is inherently
        full-width (Blender's own curve templates switch property_split off), so it gets a label
        line of its own rather than being left as an unlabelled block in a split layout.
    """
    col = layout.column(align=True)
    col.use_property_split = False
    col.label(text=text)
    col.template_curve_mapping(curveNode, "mapping")


class _LimitsDraw:
    def draw(self, context):
        layout = self.layout
        obj = _activeScatter(context)
        cs = obj.chaos_scatter
        surface = cs.surface
        layout.use_property_split = True
        layout.use_property_decorate = False

        box = layout.box()
        box.prop(surface, "surface_slope_limit_enabled")
        sub = box.column()
        sub.enabled = surface.surface_slope_limit_enabled
        sub.row(align=True).prop(surface, "surface_slope_limit_mode", expand=True)
        sub.prop(surface, "surface_slope_limit_min")
        sub.prop(surface, "surface_slope_limit_max")

        box = layout.box()
        box.prop(surface, "surface_altitude_limit_enabled")
        sub = box.column()
        sub.enabled = surface.surface_altitude_limit_enabled
        sub.row(align=True).prop(surface, "surface_altitude_limit_mode", expand=True)
        sub.prop(surface, "surface_altitude_limit_min")
        sub.prop(surface, "surface_altitude_limit_max")
        if curveNode := curves.getCurveNode(cs, 'altitude'):
            _drawCurve(sub, curveNode, "Altitude Falloff")


def _drawTransformMap(box, tr, kind):
    """ The map row for one transform channel. The core samples the texture with sampleColorTex,
        so RGB drives XYZ; the axis toggles are the plugin's own per-axis enables and the mode is
        its Fixed / Random amount enum. Only shown expanded once a map is set, so the three
        controls do not clutter the panel while unused.
    """
    box.prop(tr, f"transforms_{kind}_map")
    if getattr(tr, f"transforms_{kind}_map") is None:
        return
    box.prop(tr, f"transforms_{kind}_map_mode")
    row = box.row(align=True)
    row.prop(tr, f"transforms_{kind}_map_x", toggle=True)
    row.prop(tr, f"transforms_{kind}_map_y", toggle=True)
    row.prop(tr, f"transforms_{kind}_map_z", toggle=True)

    col = box.column(align=True)
    col.use_property_split = False
    col.label(text="The map's RGB drives XYZ, sampled in the", icon='INFO')
    col.label(text="distribution UV channel above.")
    # The map DRIVES the From/To range, it does not add to it, so an unset range makes the map a
    # no-op. Translation and scale both default to From == To, which is the state a user is most
    # likely to attach a map in.
    fromVal = getattr(tr, f"transforms_{kind}_from", None)
    toVal = getattr(tr, f"transforms_{kind}_to", None)
    if fromVal is not None and toVal is not None and tuple(fromVal) == tuple(toVal):
        col.label(text="From and To are equal, so this map has no", icon='ERROR')
        col.label(text="effect. Set a range for it to drive.")


class _TransformsDraw:
    def draw(self, context):
        layout = self.layout
        cs = _activeScatter(context).chaos_scatter
        tr = cs.transforms
        layout.use_property_split = True
        layout.use_property_decorate = False

        box = layout.box()
        box.label(text="Translation")
        box.prop(tr, "transforms_translation_from")
        box.prop(tr, "transforms_translation_to")
        box.prop(tr, "transforms_translation_step")
        row = box.row(align=True)
        row.enabled = tr.transforms_translation_step > 0.0
        row.prop(tr, "transforms_translation_step_x", toggle=True)
        row.prop(tr, "transforms_translation_step_y", toggle=True)
        row.prop(tr, "transforms_translation_step_z", toggle=True)
        _drawTransformMap(box, tr, 'translation')

        box = layout.box()
        box.label(text="Rotation")
        box.prop(tr, "transforms_rotation_from")
        box.prop(tr, "transforms_rotation_to")
        box.prop(tr, "transforms_rotation_step")
        row = box.row(align=True)
        row.enabled = tr.transforms_rotation_step > 0.0
        row.prop(tr, "transforms_rotation_step_x", toggle=True)
        row.prop(tr, "transforms_rotation_step_y", toggle=True)
        row.prop(tr, "transforms_rotation_step_z", toggle=True)
        box.prop(tr, "transforms_rotation_normal_alignment")
        box.prop(tr, "transforms_rotation_preserve")
        _drawTransformMap(box, tr, 'rotation')

        box = layout.box()
        box.label(text="Scale")
        box.prop(tr, "transforms_scale_uniform")
        if tr.transforms_scale_uniform:
            row = box.row(align=True)
            row.prop(tr, "transforms_scale_from", index=0, text="From")
            row.prop(tr, "transforms_scale_to", index=0, text="To")
        else:
            box.prop(tr, "transforms_scale_from")
            box.prop(tr, "transforms_scale_to")
        box.prop(tr, "transforms_scale_step")
        row = box.row(align=True)
        row.enabled = tr.transforms_scale_step > 0.0
        row.prop(tr, "transforms_scale_step_x", toggle=True)
        row.prop(tr, "transforms_scale_step_y", toggle=True)
        row.prop(tr, "transforms_scale_step_z", toggle=True)
        box.prop(tr, "transforms_scale_preserve")
        _drawTransformMap(box, tr, 'scale')


class _LookAtDraw:
    def draw_header(self, context):
        cs = _activeScatter(context).chaos_scatter
        self.layout.prop(cs.look_at, "look_at_enabled", text="")

    def draw(self, context):
        layout = self.layout
        cs = _activeScatter(context).chaos_scatter
        lookAt = cs.look_at
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.enabled = lookAt.look_at_enabled

        layout.prop(lookAt, "look_at_target")
        layout.prop(lookAt, "look_at_falloff_distance")
        layout.prop(lookAt, "look_at_models_orientation_axis")
        row = layout.row(align=True)
        row.use_property_split = False
        row.prop(lookAt, "look_at_models_orientation_axis_invert")
        row.prop(lookAt, "look_at_horizontal")
        if curveNode := curves.getCurveNode(cs, 'lookat'):
            _drawCurve(layout, curveNode, "Look At Falloff")


class _ClustersDraw:
    def draw_header(self, context):
        cs = _activeScatter(context).chaos_scatter
        self.layout.prop(cs.clusters, "clustered_distribution_enabled", text="")

    def draw(self, context):
        layout = self.layout
        clusters = _activeScatter(context).chaos_scatter.clusters
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.enabled = clusters.clustered_distribution_enabled

        layout.prop(clusters, "clustered_distribution_mode")
        mode = clusters.clustered_distribution_mode
        if mode == '0':
            layout.prop(clusters, "clusters_gen_mapping")
            layout.prop(clusters, "clusters_gen_seed")
            layout.prop(clusters, "clusters_gen_size")
            layout.prop(clusters, "clusters_gen_scale")
            layout.prop(clusters, "clusters_gen_rotation")
            if clusters.clusters_gen_mapping == '0':
                col = layout.column(align=True)
                col.prop(clusters, "clusters_gen_offset_x")
                col.prop(clusters, "clusters_gen_offset_y")
            else:
                col = layout.column(align=True)
                col.prop(clusters, "clusters_gen_offset_u")
                col.prop(clusters, "clusters_gen_offset_v")
                layout.prop(clusters, "clusters_gen_uv_chanel")
            layout.prop(clusters, "clusters_gen_roughness")
            layout.prop(clusters, "clusters_gen_edge_blend")
            layout.prop(clusters, "clusters_gen_diversity")
        elif mode == '1':
            layout.prop(clusters, "cluster_instances_color_map")
            col = layout.column(align=True)
            col.use_property_split = False
            # The match key is each model's viewport display color (Object Properties > Viewport
            # Display > Color), which is what model_instance_colors is exported from. Without
            # saying so there is nothing in this panel to tell the user what drives the choice.
            col.label(text="A model is placed where the map matches its", icon='INFO')
            col.label(text="viewport display color (Object > Viewport Display).")
            # Measured: a map whose colors match NO model still filled the surface (coverage went
            # up, not to zero). The previous wording - "a color missing from the map is never
            # placed" - promised a guarantee the core does not give, so state the observation.
            col.label(text="A map that matches no model still fills the", icon='INFO')
            col.label(text="surface, so a wrong map can look like it works.")
        else:  # '2' Paint
            self._drawPaint(layout, clusters)

    def _drawPaint(self, layout, clusters):
        layout.use_property_split = False
        if len(clusters.layers) == 0:
            layout.operator("chaos_scatter.cluster_add_layer", icon='ADD',
                            text="Set Up Paint Layers")
            layout.label(text="Creates a base layer + a paint layer", icon='INFO')
            return

        row = layout.row()
        row.template_list("CSCATTER_UL_cluster_layers", "", clusters, "layers",
                          clusters, "layers_active_index", rows=3)
        col = row.column(align=True)
        col.operator("chaos_scatter.cluster_add_layer", icon='ADD', text="")
        col.operator("chaos_scatter.cluster_remove_layer", icon='REMOVE', text="")

        idx = clusters.layers_active_index
        if not (0 <= idx < len(clusters.layers)):
            return
        layer = clusters.layers[idx]
        isBase = (idx == 0)

        box = layout.box()
        box.label(text="Base layer - fills the whole surface" if isBase
                  else f"Paint layer: {layer.name}",
                  icon='LOCKED' if isBase else 'BRUSH_DATA')
        if not isBase:
            box.prop(layer, "name")

        box.label(text="Models in this layer:")
        r = box.row()
        r.template_list("CSCATTER_UL_layer_models", "", layer, "models",
                        layer, "models_active_index", rows=2)
        c = r.column(align=True)
        c.operator("chaos_scatter.cluster_layer_model_add", icon='ADD', text="")
        c.operator("chaos_scatter.cluster_layer_model_remove", icon='REMOVE', text="")

        if isBase:
            box.label(text="Add paint layers above to paint model clumps", icon='INFO')
            return

        box.separator()
        box.prop(layer, "color")
        brow = box.row(align=True)
        brow.prop(clusters, "paint_radius")
        brow.prop(clusters, "paint_erase", toggle=True)
        # Stroke stacking order within the layer: a stroke only overrides strokes on a LOWER
        # sublayer. Emitted with every stroke record (params.buildClusterArrays), so leaving it
        # undrawn pinned the whole feature to 0.
        box.prop(clusters, "paint_sub_layer")
        box.operator("chaos_scatter.paint_cluster", icon='BRUSH_DATA', text="Paint")
        box.operator("chaos_scatter.cluster_clear_strokes", icon='X', text="Clear Strokes")
        box.label(text=f"{len(layer.strokes)} stroke(s)")


class _InstancePaintDraw:
    def draw_header(self, context):
        cs = _activeScatter(context).chaos_scatter
        self.layout.prop(cs.instance_paint, "instance_paint_enabled", text="")

    def draw(self, context):
        layout = self.layout
        ip = _activeScatter(context).chaos_scatter.instance_paint
        layout.enabled = ip.instance_paint_enabled
        layout.use_property_split = True
        layout.use_property_decorate = False

        layout.label(text="Paint individual instances (model follows the distribution)", icon='INFO')
        col = layout.column(align=True)
        col.prop(ip, "paint_radius")
        col.prop(ip, "paint_spacing")
        row = layout.row(align=True)
        row.prop(ip, "paint_erase", toggle=True)
        layout.operator("chaos_scatter.paint_instances", icon='BRUSH_DATA', text="Paint Instances")
        layout.operator("chaos_scatter.instance_clear", icon='X', text="Clear All")
        layout.label(text=f"{len(ip.instances)} painted instance(s)")


class _AreasDraw:
    def draw(self, context):
        layout = self.layout
        cs = _activeScatter(context).chaos_scatter
        layout.label(text="Curve objects limiting the scatter area:")
        _drawListPanel(layout, cs, 'area_modifiers', "CSCATTER_UL_areas", 'area_modifiers_active_index')

        if 0 <= cs.area_modifiers_active_index < len(cs.area_modifiers):
            item = cs.area_modifiers[cs.area_modifiers_active_index]
            col = layout.column()
            col.use_property_split = True
            col.use_property_decorate = False
            col.prop(item, "falloff_near")
            col.prop(item, "falloff_far")
            col.prop(item, "scale")
            col.prop(item, "density")
            col.prop(item, "axis")


class _CameraDraw:
    def draw_header(self, context):
        cs = _activeScatter(context).chaos_scatter
        self.layout.prop(cs.camera_clipping, "camera_clipping_enabled", text="")

    def draw(self, context):
        layout = self.layout
        clipping = _activeScatter(context).chaos_scatter.camera_clipping
        layout.use_property_split = True
        layout.use_property_decorate = False
        layout.enabled = clipping.camera_clipping_enabled

        layout.prop(clipping, "camera_clipping_mode")
        # Greyed rather than hidden, matching C4D: the picker stays visible in Render Camera mode
        # so it is obvious the mode is what makes it inactive.
        sub = layout.column()
        sub.enabled = clipping.camera_clipping_mode == '1'
        sub.prop(clipping, "camera_clipping_selected_cam")
        if clipping.camera_clipping_mode == '1' and clipping.camera_clipping_selected_cam is None:
            col = layout.column(align=True)
            col.use_property_split = False
            col.label(text="No camera picked - clipping against", icon='INFO')
            col.label(text="the render camera.")

        layout.prop(clipping, "camera_clipping_extend_view")
        layout.prop(clipping, "camera_clipping_near_far_enabled")
        sub = layout.column()
        sub.enabled = clipping.camera_clipping_near_far_enabled
        sub.prop(clipping, "camera_clipping_near_distance")
        sub.prop(clipping, "camera_clipping_far_distance")


class _DisplayDraw:
    def draw(self, context):
        layout = self.layout
        display = _activeScatter(context).chaos_scatter.display
        layout.use_property_split = True
        layout.use_property_decorate = False

        # Forced to Full under non-V-Ray engines (see lifecycle.syncModifierInputs)
        modeRow = layout.row()
        modeRow.enabled = utils.isVRayEngine(context)
        modeRow.prop(display, "preview_mode")
        layout.prop(display, "display_percentage")
        layout.prop(display, "display_limit")
        sub = layout.column()
        sub.enabled = display.preview_mode == '1'
        sub.prop(display, "dot_size")


# --------------------------------------------------------------------------
# Panel hosts: viewport sidebar (primary) + Properties editor
# --------------------------------------------------------------------------

class _SidebarHost:
    """ Own "Chaos Scatter" sidebar tab; hidden under V-Ray, whose N-panel hosts the params. """
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "Chaos Scatter"

    @classmethod
    def _enginePoll(cls, context):
        return not utils.isVRayEngine(context)

    @classmethod
    def poll(cls, context):
        return cls._enginePoll(context) and _activeScatter(context) is not None


class _VRaySidebarHost:
    """ The same panels inside the V-Ray N-panel tab when V-Ray is the render engine. """
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "V-Ray"

    @classmethod
    def _enginePoll(cls, context):
        return utils.isVRayEngine(context)

    @classmethod
    def poll(cls, context):
        return cls._enginePoll(context) and _activeScatter(context) is not None


class _PropertiesHost:
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = 'data'

    @classmethod
    def poll(cls, context):
        return _activeScatter(context) is not None


# (label, mixin, options, scatterTypes) - scatterTypes is None for "every distribution type", or
# the set of cs.scatter_type values the section applies to. A section outside its types is not
# drawn at all: its controls either address something the mode has no notion of (the surface-only
# slope/altitude limits, clustering - config.surfaceScattering in the core) or are silently
# discarded (paint strokes in 1D, where resolveTargets is skipped and there is nothing to paint on).
_SECTIONS = (
    ("Distribution Targets", _TargetsDraw, set(), None),
    ("Scattered Models", _ModelsDraw, set(), None),
    ("Distribution", _DistributionDraw, set(), None),
    ("Slope / Altitude Limits", _LimitsDraw, {'DEFAULT_CLOSED'}, {'1'}),
    ("Transformations", _TransformsDraw, {'DEFAULT_CLOSED'}, None),
    ("Camera Clipping", _CameraDraw, {'DEFAULT_CLOSED'}, None),
    ("Look At", _LookAtDraw, {'DEFAULT_CLOSED'}, None),
    ("Clustering", _ClustersDraw, {'DEFAULT_CLOSED'}, {'1'}),
    ("Instance Paint", _InstancePaintDraw, {'DEFAULT_CLOSED'}, {'1', '2'}),
    ("Include / Exclude Areas", _AreasDraw, {'DEFAULT_CLOSED'}, None),
    ("Viewport Display", _DisplayDraw, {'DEFAULT_CLOSED'}, None),
)


def _sectionPoll(host, scatterTypes):
    """ Panel poll for a section limited to some distribution types. Blender rejects a poll whose
        signature is not exactly (cls, context), so the two values are closed over rather than
        passed as default arguments.
    """
    def poll(cls, context):
        return (host.poll(context)
                and _activeScatter(context).chaos_scatter.scatter_type in scatterTypes)
    return classmethod(poll)


def _makePanels(host, prefix, mainName):
    """ Generate the main panel + one sub-panel per section for a host. """
    panels = []

    mainAttrs = {'bl_label': "Chaos Scatter"}
    if host in (_SidebarHost, _VRaySidebarHost):
        # The sidebar main panel is visible (engine permitting) even without an active
        # scatter object, so the Add button is reachable
        mainAttrs['poll'] = classmethod(lambda cls, context: cls._enginePoll(context))
    mainPanel = type(mainName, (host, _MainDraw, bpy.types.Panel), mainAttrs)
    panels.append(mainPanel)

    for i, (label, mixin, options, scatterTypes) in enumerate(_SECTIONS):
        attrs = {
            'bl_label': label,
            'bl_parent_id': mainName,
            'bl_options': options,
        }
        if scatterTypes is not None:
            # The host's poll wins over a mixin's under this MRO, so the combined one has to be
            # set on the generated class itself.
            attrs['poll'] = _sectionPoll(host, scatterTypes)
        panels.append(type(f"{prefix}_{i}", (host, mixin, bpy.types.Panel), attrs))
    return panels


_CLASSES = [
    CSCATTER_UL_targets,
    CSCATTER_UL_models,
    CSCATTER_UL_areas,
    CSCATTER_UL_cluster_layers,
    CSCATTER_UL_layer_models,
]
_CLASSES += _makePanels(_SidebarHost, "CSCATTER_PT_SB", "CSCATTER_PT_sidebar_main")
_CLASSES += _makePanels(_VRaySidebarHost, "CSCATTER_PT_VR", "CSCATTER_PT_vray_main")
_CLASSES += _makePanels(_PropertiesHost, "CSCATTER_PT_OB", "CSCATTER_PT_main")


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
