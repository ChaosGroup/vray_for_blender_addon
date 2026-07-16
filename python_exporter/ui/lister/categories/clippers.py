# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

# Clippers section: lists objects acting as V-Ray clippers and edits their VRayClipper
# propgroup.

import bpy

from vray_blender.ui.lister.core import ListerCategory, ColumnSpec, drawSelectCell, drawNameCell


def _vrayClipper(obj: bpy.types.Object):
    vrayObj = getattr(obj, 'vray', None)
    return getattr(vrayObj, 'VRayClipper', None) if vrayObj is not None else None


def _isClipper(obj: bpy.types.Object) -> bool:
    clipper = _vrayClipper(obj)
    return clipper is not None and getattr(clipper, 'clipper_enabled', False)


def _drawOperation(row, obj, propGroup):
    # 'Invert Inside' only applies when the clipper uses the object's own mesh
    row.enabled = getattr(propGroup, 'use_obj_mesh', False)
    row.prop(propGroup, 'invert_inside', text="")


def _drawClipperMaterial(row, obj, propGroup):
    # The override material is ignored when the clipper keeps the object material.
    if propGroup is None or 'selectedMaterial' not in propGroup.bl_rna.properties:
        row.label(text="")
        return
    row.enabled = not getattr(propGroup, 'use_obj_mtl', False)
    row.prop(propGroup, 'selectedMaterial', text="")


class ClippersCategory(ListerCategory):
    id = 'CLIPPERS'
    label = "V-Ray Clippers"
    icon = 'MOD_BOOLEAN'
    enumIndex = 8
    geometryRole = 'split'

    def enumerate(self, context):
        return [o for o in context.scene.objects if _isClipper(o)]

    def groupKey(self, obj):
        return 'CLIPPER'

    def groupLabel(self, key):
        return "V-Ray Clippers"

    def groupOrder(self):
        return ['CLIPPER']

    def getPropGroup(self, obj):
        return _vrayClipper(obj)

    def columns(self, key):
        return [
            ColumnSpec('select', "", draw=drawSelectCell, fixedWidth=1.5, center=True),
            ColumnSpec('name', "Name", draw=drawNameCell, width=2.0),
            ColumnSpec('affect_light', "Affect Lights", attr='affect_light', width=1.1),
            ColumnSpec('clip_lights', "Clip Lights", attr='clip_lights', width=1.0),
            ColumnSpec('only_camera_rays', "Camera Only", attr='only_camera_rays', width=1.1),
            ColumnSpec('use_obj_mesh', "Use Mesh", attr='use_obj_mesh', width=1.0),
            ColumnSpec('operation', "Operation", draw=_drawOperation, width=1.8),
            ColumnSpec('use_obj_mtl', "Use Obj Mtl", attr='use_obj_mtl', width=1.1),
            ColumnSpec('material', "Material", draw=_drawClipperMaterial, width=1.8),
            ColumnSpec('exclusion_mode', "Exclude Mode", attr='exclusion_mode', width=1.6),
        ]


category = ClippersCategory()
