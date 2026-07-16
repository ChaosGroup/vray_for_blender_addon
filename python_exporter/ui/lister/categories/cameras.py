# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

# Cameras category, grouped into Physical, Dome and Standard. A camera switches
# between these modes via the 'use_physical' / 'use_dome' toggles on the same object,
# which the lister exposes so you can switch in place.

import bpy

from vray_blender.ui.lister.core import ListerCategory, ColumnSpec, drawSelectCell, drawNameCell


_GROUP_LABELS = {
    'PHYSICAL': "Physical Cameras",
    'DOME':     "Dome Cameras",
    'STANDARD': "Standard Cameras",
}

_GROUP_ORDER = ['PHYSICAL', 'DOME', 'STANDARD']


def _vrayCam(obj):
    return getattr(obj.data, 'vray', None)


def _settingsCamera(obj):
    vrayCam = _vrayCam(obj)
    return getattr(vrayCam, 'SettingsCamera', None) if vrayCam is not None else None


def _drawActive(row, obj, propGroup):
    # Filled radio dot = the scene's active camera, empty = the rest. No button box.
    isActive = bpy.context.scene.camera == obj
    op = row.operator(
        "vray.lister_set_active_camera",
        text="",
        icon='RADIOBUT_ON' if isActive else 'RADIOBUT_OFF',
        emboss=False,
    )
    op.camera = obj.name


def _drawMakePhysical(row, obj, propGroup):
    vrayCam = _vrayCam(obj)
    if vrayCam is not None:
        row.prop(vrayCam, 'use_physical', text="")


def _drawMakeDome(row, obj, propGroup):
    vrayCam = _vrayCam(obj)
    if vrayCam is not None:
        row.prop(vrayCam, 'use_dome', text="")


def _drawVRayOverride(row, obj, propGroup):
    settingsCamera = _settingsCamera(obj)
    if settingsCamera is not None and 'override_camera_settings' in settingsCamera.bl_rna.properties:
        row.prop(settingsCamera, 'override_camera_settings', text="")


def _drawVRayCamType(row, obj, propGroup):
    # V-Ray camera type only applies when camera overrides are enabled; gate on it.
    settingsCamera = _settingsCamera(obj)
    if settingsCamera is not None and 'type' in settingsCamera.bl_rna.properties:
        row.enabled = settingsCamera.override_camera_settings
        row.prop(settingsCamera, 'type', text="")


def _drawVRayCamFov(row, obj, propGroup):
    settingsCamera = _settingsCamera(obj)
    if settingsCamera is not None and 'fov' in settingsCamera.bl_rna.properties:
        row.enabled = settingsCamera.override_camera_settings
        row.prop(settingsCamera, 'fov', text="")


def _drawFocusObject(row, obj, propGroup):
    row.prop(obj.data.dof, 'focus_object', text="")


def _drawFocusDistance(row, obj, propGroup):
    # Focus is driven by Blender's depth-of-field (the exporter reads camera.dof). With
    # a focus object set, the computed distance is shown read-only.
    dof = obj.data.dof
    if dof.focus_object is None:
        row.prop(dof, 'focus_distance', text="")
    else:
        row.prop(obj.data.vray, 'focus_distance', text="")


def _drawFocalLength(row, obj, propGroup):
    """ One shared 'Focal Length' cell: a physical camera edits CameraPhysical.focal_length,
        a standard camera the Blender lens; a dome camera has neither (it uses FOV), so the
        cell is blank. Lets the focal length line up in a single column across camera types. """
    if propGroup is not None:
        for attr in ('focal_length', 'lens'):
            if attr in propGroup.bl_rna.properties:
                row.prop(propGroup, attr, text="")
                return
    row.label(text="")


def _focalValue(obj, propGroup):
    """ Sort key for the merged Focal Length column (physical focal_length / standard lens). """
    if propGroup is not None:
        for attr in ('focal_length', 'lens'):
            if attr in propGroup.bl_rna.properties:
                return getattr(propGroup, attr, None)
    return None


# Columns shared by every camera group: select toggle, active-camera radio, name, and the
# Physical / Dome mode toggles. Each group's columns() starts from this set and appends its
# mode-specific parameters.
_COMMON_COLUMNS = [
    ColumnSpec('select',   "",         draw=drawSelectCell,    fixedWidth=1.5, center=True),
    ColumnSpec('active',   "Active",   draw=_drawActive,       width=0.8,      center=True),
    ColumnSpec('name',     "Name",     draw=drawNameCell,      width=2.0),
    ColumnSpec('physical', "Physical", draw=_drawMakePhysical, width=0.9,      center=True),
    ColumnSpec('dome',     "Dome",     draw=_drawMakeDome,     width=0.9,      center=True, defaultHidden=True),
]

# Type-dispatched focal-length column: physical focal_length / standard lens in one slot
# (a dome camera has neither, so its cell is blank).
_COL_FOCAL = ColumnSpec('focal', "Focal Length", draw=_drawFocalLength, sortValue=_focalValue, width=1.5)


class CamerasCategory(ListerCategory):
    id = 'CAMERAS'
    label = "Cameras"
    icon = 'CAMERA_DATA'
    enumIndex = 1

    def enumerate(self, context):
        return [o for o in context.scene.objects if o.type == 'CAMERA']

    def groupKey(self, obj):
        vrayCam = _vrayCam(obj)
        if vrayCam is not None and getattr(vrayCam, 'use_physical', False):
            return 'PHYSICAL'
        if vrayCam is not None and getattr(vrayCam, 'use_dome', False):
            return 'DOME'
        return 'STANDARD'

    def groupLabel(self, key):
        return _GROUP_LABELS.get(key, key)

    def groupOrder(self):
        return _GROUP_ORDER

    def pickerSections(self, context, state):
        # Column picker: a common "Cameras" section (active/mode toggles) plus the Physical,
        # Dome and Standard parameter sets and the override columns. Each id appears once
        # (the shared FOV column is listed under Dome).
        from vray_blender.ui.lister import core
        OVERRIDE = {'vray_override', 'vray_type'}
        COMMON = {'active', 'physical', 'dome'}

        common = [c for c in core._effectiveColumns(self, 'PHYSICAL')
                  if c.id == 'hide' or c.id in COMMON]
        seen = {c.id for c in common} | {'select', 'name'}

        sections = [("Cameras", common)]
        for key, label, drop in (
            ('PHYSICAL', "Physical", set()),
            ('DOME',     "Dome",     set()),
            ('STANDARD', "Standard", OVERRIDE),   # overrides go in their own section
        ):
            cols = [c for c in self.columns(key) if c.id not in seen and c.id not in drop]
            seen.update(c.id for c in cols)
            if cols:
                sections.append((label, cols))

        overrides = [c for c in self.columns('STANDARD') if c.id in OVERRIDE]
        if overrides:
            sections.append(("Overrides", overrides))
        return sections

    def getPropGroup(self, obj):
        key = self.groupKey(obj)
        if key == 'STANDARD':
            return obj.data  # Blender camera data
        if key == 'DOME':
            return obj.data.vray.CameraDome
        return obj.data.vray.CameraPhysical  # PHYSICAL

    def columns(self, key):
        # Physical 'focal_length' and standard 'lens' share the _COL_FOCAL column.
        if key == 'STANDARD':
            # Blender camera data (lens/sensor/clip) plus the V-Ray camera type and
            # FOV from SettingsCamera (gated on the camera-overrides toggle).
            return [
                *_COMMON_COLUMNS,
                ColumnSpec('blender_type', "Lens", attr='type', width=1.4, defaultHidden=True),
                _COL_FOCAL,
                # Shares the 'fov' id with the Dome camera so the Unified layout shows one FOV
                # column. A standard camera's FOV comes from the V-Ray override (_drawVRayCamFov).
                ColumnSpec('fov', "FOV", draw=_drawVRayCamFov, width=1.6),
                ColumnSpec('vray_override', "V-Ray Override", draw=_drawVRayOverride, width=1.2, defaultHidden=True),
                ColumnSpec('vray_type', "V-Ray Type", draw=_drawVRayCamType, width=1.8, defaultHidden=True),
                ColumnSpec('sensor_fit', "Sensor Fit", attr='sensor_fit', width=1.4, defaultHidden=True),
                ColumnSpec('sensor_width', "Sensor W", attr='sensor_width', width=1.4, defaultHidden=True),
                ColumnSpec('sensor_height', "Sensor H", attr='sensor_height', width=1.4, defaultHidden=True),
                ColumnSpec('shift_x', "Shift X", attr='shift_x', width=1.2, defaultHidden=True),
                ColumnSpec('shift_y', "Shift Y", attr='shift_y', width=1.2, defaultHidden=True),
                ColumnSpec('clip_start', "Clip Start", attr='clip_start', width=1.4, defaultHidden=True),
                ColumnSpec('clip_end', "Clip End", attr='clip_end', width=1.4, defaultHidden=True),
                ColumnSpec('ortho_scale', "Ortho Scale", attr='ortho_scale', width=1.4, defaultHidden=True),
            ]

        if key == 'DOME':
            return [
                *_COMMON_COLUMNS,
                ColumnSpec('fov', "FOV", attr='fov', width=1.6),
            ]

        # Physical cameras edit CameraPhysical; focus uses Blender's depth-of-field.
        return [
            *_COMMON_COLUMNS,
            ColumnSpec('type', "Type", attr='type', width=1.4, defaultHidden=True),
            _COL_FOCAL,
            ColumnSpec('f_number', "F-Number", attr='f_number', width=1.3),
            ColumnSpec('ISO', "ISO", attr='ISO', width=1.1),
            ColumnSpec('shutter_speed', "Shutter", attr='shutter_speed', width=1.3),
            ColumnSpec('white_balance', "White Bal.", attr='white_balance', width=1.2, defaultHidden=True),
            ColumnSpec('use_dof', "DoF", attr='use_dof', width=0.7),
            ColumnSpec('use_moblur', "Mo. Blur", attr='use_moblur', width=0.8, defaultHidden=True),
            ColumnSpec('focus_object', "Focus Obj", draw=_drawFocusObject, width=1.6, defaultHidden=True),
            ColumnSpec('focus_dist', "Focus Dist.", draw=_drawFocusDistance, width=1.5),
            ColumnSpec('exposure', "Exposure", attr='exposure', width=1.2, defaultHidden=True),
            ColumnSpec('exposure_value', "EV", attr='exposure_value', width=1.0, defaultHidden=True),
            ColumnSpec('zoom_factor', "Zoom", attr='zoom_factor', width=1.0, defaultHidden=True),
            ColumnSpec('vignetting', "Vignetting", attr='vignetting', width=1.2, defaultHidden=True),
            ColumnSpec('shutter_angle', "Sh. Angle", attr='shutter_angle', width=1.2, defaultHidden=True),
            ColumnSpec('shutter_offset', "Sh. Offset", attr='shutter_offset', width=1.2, defaultHidden=True),
            ColumnSpec('blades_enable', "Bokeh", attr='blades_enable', width=0.8, defaultHidden=True),
            ColumnSpec('blades_num', "Blades", attr='blades_num', width=1.0, defaultHidden=True),
            ColumnSpec('distortion', "Distort", attr='distortion', width=1.2, defaultHidden=True),
            ColumnSpec('distortion_type', "Dist. Type", attr='distortion_type', width=1.6, defaultHidden=True),
            ColumnSpec('lens_shift', "Lens Shift", attr='lens_shift', width=1.2, defaultHidden=True),
        ]


category = CamerasCategory()
