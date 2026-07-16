# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
from vray_blender.lib import plugin_utils
from vray_blender.lib.attribute_utils import createPropSearchPointerProp
from vray_blender.lib.defs import ExporterContext
plugin_utils.loadPluginOnModule(globals(), __name__)

def registerCustomProperties(classMembers):

    classMembers['selectedMaterial'] = createPropSearchPointerProp(
        "material",
        "selectedMaterial",
        bpy.types.Material,
        "Material",
        "Material for shading the surfaces created by clipping"
    )

    def onEnable(self, context: bpy.types.Context):
        obj = context.object
        obj.display_type = 'WIRE' if self.clipper_enabled else 'SOLID'
        obj.vray.VRayClipper.enabled = self.clipper_enabled

    classMembers['clipper_enabled'] = bpy.props.BoolProperty(
        name = 'Enabled',
        description = 'Use this object as a clipper',
        update = onEnable
    )

