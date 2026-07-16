# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy

def run():
    for cam in bpy.data.cameras:
        cameraOverridesEnabled = cam.vray.SettingsCamera.override_camera_settings
        defaultClippingValueUsed = not cam.vray.RenderView.is_property_set("clipping")

        # If scenes were using the old default clipping value (clipping=True) and camera overrides are enabled
        # (the clipping toggle is taken into account), explicitly set clipping to True to maintain the previous behavior.
        if defaultClippingValueUsed and cameraOverridesEnabled:
            cam.vray.RenderView.clipping = True

def check():
    for camera in bpy.data.cameras:
        cameraOverridesEnabled = camera.vray.SettingsCamera.override_camera_settings
        if cameraOverridesEnabled and not camera.vray.RenderView.is_property_set("clipping"):
            return True

    return False
