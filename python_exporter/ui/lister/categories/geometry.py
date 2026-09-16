# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

# Geometry sections. By default each V-Ray geometry kind is its own section (Proxies,
# Gaussian Splats, Decals, Fur, V-Ray Scenes). 'Combine Geometry' instead shows them
# all - plus clippers - in one 'Geometry' section grouped by kind.

import bpy

from vray_blender.exporting import tools
from vray_blender.ui.lister.core import (
    ListerCategory, ColumnSpec, drawSelectCell, drawNameCell,
    makeStatusCell as _makeStatusCell, makeFileCell as _makeFileCell,
)
from vray_blender.ui.lister.categories import clippers
from vray_blender.nodes.utils import getNodeByType, treeHasNodes


def _drawDecalBend(row, obj, propGroup):
    """ Bend only applies to cylindrical decals (decal_type == '1'); grey it out for
        planar decals, matching the V-Ray decal properties panel.
    """
    row.enabled = getattr(propGroup, 'decal_type', '0') == '1'
    row.prop(propGroup, 'bend', text="")


def _drawLightingBlend(row, obj, propGroup):
    """ Lighting blend only applies when lighting is enabled for the splat. """
    row.enabled = getattr(propGroup, 'lighting_on', False)
    row.prop(propGroup, 'lighting_blend', text="")


def _drawDecalMaterial(row, obj, propGroup):
    """ Show and change the material in the decal mesh's first slot - the material the
        decal projects. Blank when the object has no material slot. """
    slots = getattr(obj, 'material_slots', None)
    if slots and len(slots) > 0:
        row.prop(slots[0], 'material', text="")
    else:
        row.label(text="")


def _furUsesPerArea(propGroup) -> bool:
    """ Whether the fur's hair count comes from the per-unit-area field (distribution
        'Per area', == '1') rather than the per-face field ('Per face', == '0'). """
    return getattr(propGroup, 'distribution', '1') == '1'


def _drawFurHairs(row, obj, propGroup):
    """ Hair count for a V-Ray Fur object: the per-unit-area count when Distribution is
        'Per area', the per-face count when 'Per face' - the field the fur properties
        panel keeps active for the chosen distribution. """
    if propGroup is None:
        row.label(text="")
        return
    row.prop(propGroup, 'perArea' if _furUsesPerArea(propGroup) else 'perFace', text="")


def _furHairsSortValue(obj, propGroup):
    attr = 'perArea' if _furUsesPerArea(propGroup) else 'perFace'
    return getattr(propGroup, attr, 0)


def _geomKind(obj: bpy.types.Object):
    """ The V-Ray geometry kind an object represents, or None. Uses the same predicates as
        the exporter (exporting/tools) so the lister and the export agree on what an object
        is. Fur has no dedicated predicate, so its flag is read directly. """
    if tools.isObjectVRayGaussian(obj):
        return 'GAUSSIAN'
    if tools.isObjectVRayDecal(obj):
        return 'DECAL'
    if obj.vray.isVRayFur:
        return 'FUR'
    if tools.isObjectVrayProxy(obj):
        return 'PROXY'
    if tools.isObjectVrayScene(obj):
        return 'VRSCENE'
    return None


_COL_SELECT = ColumnSpec('select', "", draw=drawSelectCell, fixedWidth=1.5, center=True)
_COL_NAME   = ColumnSpec('name', "Name", draw=drawNameCell, width=2.0)


def _columnsForKind(kind: str):
    if kind == 'PROXY':
        return [
            _COL_SELECT, _COL_NAME,
            ColumnSpec('file_status', "Status", draw=_makeStatusCell('PROXY', 'file'), width=1.0, center=True),
            ColumnSpec('file', "File", attr='file', draw=_makeFileCell('PROXY', 'file'), width=3.0),
            ColumnSpec('scale', "Scale", attr='scale', width=1.2),
            ColumnSpec('previewType', "Preview", attr='previewType', width=1.6),
            ColumnSpec('flip_axis', "Flip Axis", attr='flip_axis', width=1.4, defaultHidden=True),
            ColumnSpec('anim_type', "Anim Type", attr='anim_type', width=1.6, defaultHidden=True),
            ColumnSpec('anim_speed', "Anim Speed", attr='anim_speed', width=1.4, defaultHidden=True),
        ]
    if kind == 'VRSCENE':
        return [
            _COL_SELECT, _COL_NAME,
            ColumnSpec('file_status', "Status", draw=_makeStatusCell('SCENE', 'filepath'), width=1.0, center=True),
            # Same id 'file' as proxies/splats so the File column lines up in the unified
            # grid, while editing VRayScene.filepath.
            ColumnSpec('file', "File", attr='filepath', draw=_makeFileCell('SCENE', 'filepath'), width=3.0),
            ColumnSpec('add_nodes', "Geom", attr='add_nodes', width=0.8),
            ColumnSpec('add_lights', "Lights", attr='add_lights', width=0.8),
            ColumnSpec('anim_type', "Anim Type", attr='anim_type', width=1.6, defaultHidden=True),
            ColumnSpec('anim_speed', "Anim Speed", attr='anim_speed', width=1.4, defaultHidden=True),
        ]
    if kind == 'GAUSSIAN':
        return [
            _COL_SELECT, _COL_NAME,
            ColumnSpec('file_status', "Status", draw=_makeStatusCell('GAUSSIAN', 'file'), width=1.0, center=True),
            ColumnSpec('file', "File", attr='file', draw=_makeFileCell('GAUSSIAN', 'file'), width=3.0),
            ColumnSpec('scale', "Scale", attr='scale', width=1.2),
            ColumnSpec('intensity', "Intensity", attr='intensity', width=1.4),
            ColumnSpec('color', "Color", attr='color', width=1.4),
            ColumnSpec('flip_axis', "Flip Axis", attr='flip_axis', width=1.2, defaultHidden=True),
            ColumnSpec('affect_camera',         "Camera",   attr='affect_camera',         width=0.8, center=True, defaultHidden=True),
            ColumnSpec('affect_shadows',        "Shadows",  attr='affect_shadows',        width=0.9, center=True, defaultHidden=True),
            ColumnSpec('affect_reflections',    "Reflect",  attr='affect_reflections',    width=0.8, center=True, defaultHidden=True),
            ColumnSpec('affect_refractions',    "Refract",  attr='affect_refractions',    width=0.8, center=True, defaultHidden=True),
            ColumnSpec('affect_matte_surfaces', "Matte",    attr='affect_matte_surfaces', width=0.8, center=True, defaultHidden=True),
            ColumnSpec('lighting_on', "Lighting", attr='lighting_on', width=0.8, defaultHidden=True),
            ColumnSpec('lighting_blend', "Light Blend", draw=_drawLightingBlend, width=1.3, defaultHidden=True),
            ColumnSpec('preview_type', "Preview", attr='preview_type', width=1.8, defaultHidden=True),
        ]
    if kind == 'DECAL':
        return [
            _COL_SELECT,
            ColumnSpec('enabled', "On", attr='enabled', width=0.6),
            _COL_NAME,
            ColumnSpec('material', "Material", draw=_drawDecalMaterial, width=2.0),
            ColumnSpec('decal_type', "Type", attr='decal_type', width=1.6),
            ColumnSpec('width', "Width", attr='width', width=1.4),
            ColumnSpec('length', "Length", attr='length', width=1.4),
            ColumnSpec('height', "Height", attr='height', width=1.4),
            ColumnSpec('bend', "Bend", draw=_drawDecalBend, width=1.2),
            ColumnSpec('project_on_back', "Project Back", attr='project_on_back', width=1.0),
            ColumnSpec('displacement_multiplier', "Displacement", attr='displacement_multiplier', width=1.4, defaultHidden=True),
            ColumnSpec('normal_angle', "Normal Ang.", attr='normal_angle', width=1.6, defaultHidden=True),
            ColumnSpec('z_order', "Z Order", attr='z_order', width=1.2, defaultHidden=True),
            ColumnSpec('fade_on', "Fade", attr='fade_on', width=0.8, defaultHidden=True),
        ]
    if kind == 'FUR':
        return [
            _COL_SELECT, _COL_NAME,
            ColumnSpec('distribution', "Distribution", attr='distribution', width=1.6),
            ColumnSpec('hairs', "Hairs/Unit", draw=_drawFurHairs, sortValue=_furHairsSortValue, width=1.6),
            ColumnSpec('length_base', "Length", attr='length_base', width=1.4),
            ColumnSpec('thickness_base', "Thickness", attr='thickness_base', width=1.4),
            ColumnSpec('gravity_base', "Gravity", attr='gravity_base', width=1.4),
            ColumnSpec('bend', "Bend", attr='bend', width=1.2),
            ColumnSpec('taper', "Taper", attr='taper', width=1.2, defaultHidden=True),
            ColumnSpec('scale', "Scale", attr='scale', width=1.2, defaultHidden=True),
            ColumnSpec('curl_enabled', "Curl", attr='curl_enabled', width=0.8, defaultHidden=True),
            ColumnSpec('curl_radius', "Curl Radius", attr='curl_radius', width=1.4, defaultHidden=True),
            ColumnSpec('hair_knots', "Knots", attr='hair_knots', width=1.0, defaultHidden=True),
        ]
    return [_COL_SELECT, _COL_NAME]


def _outputNodePropGroup(obj: bpy.types.Object, nodeType: str):
    """ The propgroup of a fur / decal object's output node, or None when the object has no
        node tree. Once such a node exists it holds the settings the properties panel edits
        and the exporter reads, so the lister has to edit it too - the object's own propgroup
        is left behind and only used while there is no node tree. """
    if not treeHasNodes(obj.vray.ntree):
        return None
    node = getNodeByType(obj.vray.ntree, nodeType)
    return getattr(node, node.vray_plugin) if node is not None else None


def _propGroupForKind(obj: bpy.types.Object, kind: str):
    if kind == 'GAUSSIAN':
        return getattr(obj.vray, 'GeomGaussians', None)
    if kind == 'FUR':
        if (propGroup := _outputNodePropGroup(obj, 'VRayNodeFurOutput')) is not None:
            return propGroup
    elif kind == 'DECAL':
        if (propGroup := _outputNodePropGroup(obj, 'VRayNodeDecalOutput')) is not None:
            return propGroup
    vrayData = getattr(obj.data, 'vray', None)
    if vrayData is None:
        return None
    return {
        'PROXY':   lambda: getattr(vrayData, 'GeomMeshFile', None),
        'VRSCENE': lambda: getattr(vrayData, 'VRayScene', None),
        'DECAL':   lambda: getattr(vrayData, 'VRayDecal', None),
        'FUR':     lambda: getattr(vrayData, 'GeomHair', None),
    }.get(kind, lambda: None)()


_GEOM_GROUP_LABELS = {
    'PROXY':    "Proxies",
    'GAUSSIAN': "Gaussian Splats",
    'DECAL':    "Decals",
    'FUR':      "Fur",
    'VRSCENE':  "V-Ray Scenes",
    'CLIPPER':  "V-Ray Clippers",
}

# Order of the groups in the combined Geometry section.
_GEOM_KIND_ORDER = ['PROXY', 'GAUSSIAN', 'DECAL', 'FUR', 'VRSCENE', 'CLIPPER']


class _GeomKindCategory(ListerCategory):
    """ A lister section for a single V-Ray geometry kind. """
    kind = ''
    geometryRole = 'split'

    def enumerate(self, context):
        return [o for o in context.scene.objects if _geomKind(o) == self.kind]

    def groupKey(self, obj):
        return self.kind

    def groupLabel(self, key):
        return self.label

    def groupOrder(self):
        return [self.kind]

    def getPropGroup(self, obj):
        return _propGroupForKind(obj, self.kind)

    def columns(self, key):
        return _columnsForKind(self.kind)


class ProxiesCategory(_GeomKindCategory):
    id = 'PROXIES'
    label = "V-Ray Proxies"
    icon = 'MESH_DATA'
    enumIndex = 3
    kind = 'PROXY'


class SplatsCategory(_GeomKindCategory):
    id = 'SPLATS'
    label = "Gaussian Splats"
    icon = 'OUTLINER_OB_POINTCLOUD'
    enumIndex = 4
    kind = 'GAUSSIAN'


class DecalsCategory(_GeomKindCategory):
    id = 'DECALS'
    label = "V-Ray Decals"
    icon = 'TEXTURE'
    enumIndex = 5
    kind = 'DECAL'


class FurCategory(_GeomKindCategory):
    id = 'FUR'
    label = "V-Ray Fur"
    icon = 'CURVES'
    enumIndex = 6
    kind = 'FUR'


class ScenesCategory(_GeomKindCategory):
    id = 'SCENES'
    label = "V-Ray Scenes"
    icon = 'SCENE_DATA'
    enumIndex = 7
    kind = 'VRSCENE'


def _combinedKind(obj: bpy.types.Object):
    """ The group an object belongs to in the combined Geometry section: a geometry
        kind, or 'CLIPPER' for clipper objects, or None. """
    kind = _geomKind(obj)
    if kind is not None:
        return kind
    return 'CLIPPER' if clippers._isClipper(obj) else None


class GeometryCategory(ListerCategory):
    """ Combined view of every V-Ray geometry kind (and clippers) grouped by kind,
        shown instead of the per-kind sections when 'Combine Geometry' is on. """
    id = 'GEOMETRY'
    label = "Geometry"
    icon = 'MESH_DATA'
    enumIndex = 2
    geometryRole = 'combined'

    def enumerate(self, context):
        return [o for o in context.scene.objects if _combinedKind(o) is not None]

    def groupKey(self, obj):
        return _combinedKind(obj)

    def groupLabel(self, key):
        return _GEOM_GROUP_LABELS.get(key, key)

    def groupOrder(self):
        return _GEOM_KIND_ORDER

    def getPropGroup(self, obj):
        kind = _combinedKind(obj)
        if kind == 'CLIPPER':
            return clippers._vrayClipper(obj)
        return _propGroupForKind(obj, kind)

    def columns(self, key):
        if key == 'CLIPPER':
            return clippers.category.columns(key)
        return _columnsForKind(key)


# Order here = order of the section tabs on the left nav. The combined view and the
# per-kind sections are shown mutually exclusively (by the 'Combine Geometry' toggle).
categories = (
    GeometryCategory(),
    ProxiesCategory(),
    SplatsCategory(),
    DecalsCategory(),
    FurCategory(),
    ScenesCategory(),
)
