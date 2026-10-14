# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

# VBLD-2876: the GeomGaussians lighting defaults now match V-Ray for 3ds Max and for C4D.
# A property is stored only if it was ever assigned, so a splat left at an old default would
# silently render with the new value. Pin those back.

import bpy
from vray_blender.utils.upgrade_scene import scopedForUpgrade


# The defaults these had before version 54.
_OLD_DEFAULTS = {
    'lighting_blend': 1.0,
    'lighting_shadow_blur': 1.0,
    'lighting_shadow_offset': 0.5,
}


def _splatPropGroups():
    """ Yield the GeomGaussians propGroup of every Gaussian splat object. """
    for obj in scopedForUpgrade(bpy.data.objects):
        if obj.vray.isVRayGaussian:
            yield obj.vray.GeomGaussians


def run():
    for propGroup in _splatPropGroups():
        for attr, oldDefault in _OLD_DEFAULTS.items():
            if not propGroup.is_property_set(attr):
                setattr(propGroup, attr, oldDefault)


def check():
    return any(not propGroup.is_property_set(attr)
               for propGroup in _splatPropGroups()
               for attr in _OLD_DEFAULTS)
