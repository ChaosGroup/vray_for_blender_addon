# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import math

import bpy
from vray_blender.nodes.utils import getPropGroupOfNode
from vray_blender.utils.upgrade_scene import scopedForUpgrade

# The regenerated SunLight descriptor changed two things for saved scenes:
# - stars_time_zone is FLOAT instead of INT. Blender ignores an int ID property behind a
#   FloatProperty, so the saved time zone would reset to 0.
# - These params are now angles, stored in radians by Blender. Saved values are degrees.
_TIME_ZONE = 'stars_time_zone'
_ANGLES = ('moon_azimuth', 'moon_elevation', 'moon_phase', 'moon_rotation',
           'stars_latitude', 'stars_longitude')


def _sunPropGroups():
    for light in scopedForUpgrade(bpy.data.lights):
        yield light.vray.SunLight
        if light.node_tree:
            for node in light.node_tree.nodes:
                if node.bl_idname == 'VRayNodeSunLight':
                    yield getPropGroupOfNode(node)


def _needsUpgrade(pg):
    return any(pg.get(attr) is not None for attr in (_TIME_ZONE, *_ANGLES))


def run():
    for pg in _sunPropGroups():
        if (timeZone := pg.get(_TIME_ZONE)) is not None:
            del pg[_TIME_ZONE]
            pg[_TIME_ZONE] = float(timeZone)
        for attr in _ANGLES:
            if (degrees := pg.get(attr)) is not None:
                pg[attr] = math.radians(degrees)


def check():
    return any(_needsUpgrade(pg) for pg in _sunPropGroups())
