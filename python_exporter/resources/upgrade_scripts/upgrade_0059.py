# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

# SettingsUnitsInfo.photometric_scale used to be exported multiplied by scale_length^2 (unless the
# unit system is None). V-Ray already divides flux-unit lights (lumens, watts) by meters_scale^2,
# so on a scene with scale_length != 1 the extra factor dimmed luminance/radiance lights and the
# sun by scale_length^2 and cancelled the flux division. It is now exported as-is.
#
# Fold the old factor into the stored value so scenes saved before version 59 export exactly the
# photometric_scale they did and render the same. Only for scenes saved with V-Ray: a scene that
# never went through the old export has no look to keep and gets the new behavior.

import bpy
from vray_blender.utils.upgrade_scene import isImportScopeActive, scopedForUpgrade
from vray_blender.version import getSceneVersionString


def _savedWithVRay(scene):
    # Only the active scene is stamped on save; on a full load it speaks for the whole file.
    return bool(getSceneVersionString(scene)) or \
        (not isImportScopeActive() and bool(getSceneVersionString()))


def _scenesToUpgrade():
    for scene in scopedForUpgrade(bpy.data.scenes):
        units = scene.unit_settings
        if units.system != 'NONE' and units.scale_length != 1.0 and _savedWithVRay(scene):
            yield scene


def run():
    for scene in _scenesToUpgrade():
        scene.vray.SettingsUnitsInfo.photometric_scale *= scene.unit_settings.scale_length ** 2


def check():
    return any(True for _ in _scenesToUpgrade())
