# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later


import bpy

from vray_blender.ui import classes
from vray_blender.lib.draw_utils import UIPainter
from vray_blender.plugins import getPluginModule


class VRAY_PT_VRayGaussians(classes.VRayDataPanel):
    """ Object-data panel for V-Ray Gaussian splat objects (Empties).

        The whole UI (General / Lighting / Animation / Clipping / Viewport Preview) is described
        by the GeomGaussians Widget in GeomGaussians.custom.json and rendered by the UIPainter.
    """
    bl_label  = "V-Ray Gaussians"
    bl_idname = "VRAY_PT_VRayGaussians"
    vray_icon = "VRAY_PLACEHOLDER"

    @classmethod
    def poll(cls, context):
        obj = context.object
        return (obj is not None
                and obj.type == 'EMPTY'
                and obj.vray.isVRayGaussian
                and classes.VRayDataPanel.poll(context))

    def draw(self, context):
        propGroup = context.object.vray.GeomGaussians
        pluginModule = getPluginModule('GeomGaussians')
        UIPainter(context, pluginModule, propGroup).renderPluginUI(self.layout)


def register():
    bpy.utils.register_class(VRAY_PT_VRayGaussians)


def unregister():
    bpy.utils.unregister_class(VRAY_PT_VRayGaussians)
