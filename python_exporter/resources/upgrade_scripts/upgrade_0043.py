# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

# scene.vray.Exporter.image_to_blender went from default=False (dead/unused) to
# default=True (now gates pixel transfer to Blender for the whole render).
# Scenes saved on the old default would silently keep image_to_blender=False and
# get no rendered image in Blender; force them to the new default. Pre-bump there
# was no UI for this property, so there can't be an intentional user override to
# clobber.

import bpy
from vray_blender.utils.upgrade_scene import scoped

UPGRADE_INFO = {'nodes': {}}

def run():
    for scene in scoped(bpy.data.scenes):
        scene.vray.Exporter.image_to_blender = True

def check():
    return True
