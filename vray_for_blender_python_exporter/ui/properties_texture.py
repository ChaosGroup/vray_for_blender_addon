# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later


import bpy

from vray_blender.ui import classes


def register():
    from bl_ui import properties_texture
    for member in dir(properties_texture):
        subclass = getattr(properties_texture, member)
        try:
            for compatEngine in classes.VRayEngines:
                subclass.COMPAT_ENGINES.add(compatEngine)
        except:
            pass
    del properties_texture


def unregister():
    from bl_ui import properties_texture
    for member in dir(properties_texture):
        subclass = getattr(properties_texture, member)
        try:
            for compatEngine in classes.VRayEngines:
                subclass.COMPAT_ENGINES.remove(compatEngine)
        except:
            pass
    del properties_texture
