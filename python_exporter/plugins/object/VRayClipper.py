# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
from vray_blender.lib import plugin_utils
from vray_blender.lib.condition_processor import evaluateCondition
from vray_blender.lib.defs import ExporterContext
plugin_utils.loadPluginOnModule(globals(), __name__)

def drawMaterialID(context: bpy.types.Context, layout: bpy.types.UILayout, propGroup, widgetAttr):
    # The toggle owns exactly one value, so both go on one row - the checkbox in the label
    # column, the value beside it - the way Cycles draws its Noise Threshold.
    # Same structure as Blender's Limit Location constraint rows.
    decorate = layout.use_property_decorate
    row = layout.row(heading="Material ID", align=True)
    row.use_property_decorate = False
    sub = row.row(align=True)
    sub.prop(propGroup, 'set_material_id', text="")
    subsub = sub.row(align=True)
    # Same semantics as the painter's 'active' conditions
    subsub.active = subsub.enabled = evaluateCondition(propGroup, None, widgetAttr['value_active'])
    subsub.prop(propGroup, 'material_id', text="")
    if decorate:
        row.prop_decorator(propGroup, 'material_id')


def drawExclusionMode(context: bpy.types.Context, layout: bpy.types.UILayout, propGroup, widgetAttr):
    # Include / Exclude buttons over the object list, as in the V-Ray Decal
    row = layout.row(align=True)
    row.use_property_split = False
    row.use_property_decorate = False
    row.prop(propGroup, 'exclusion_mode', text="Include", toggle=True, invert_checkbox=True)
    row.prop(propGroup, 'exclusion_mode', text="Exclude", toggle=True)


def registerCustomProperties(classMembers):

    def onEnable(self, context: bpy.types.Context):
        obj = context.object
        obj.display_type = 'WIRE' if self.clipper_enabled else 'SOLID'
        obj.vray.VRayClipper.enabled = self.clipper_enabled

    classMembers['clipper_enabled'] = bpy.props.BoolProperty(
        name = 'Enabled',
        description = 'Use this object as a clipper',
        update = onEnable
    )

