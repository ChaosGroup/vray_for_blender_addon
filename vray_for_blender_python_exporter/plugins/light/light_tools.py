# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy

from vray_blender.exporting.tools import getInputSocketByAttr
from vray_blender.nodes.utils import getLightOutputNode


class VRAY_OT_set_light_color_from_temperature(bpy.types.Operator):
    bl_idname = "vray.set_light_color_from_temperature"
    bl_label = "Set as color"
    bl_description = "Set light color from temperature"
    bl_options = {'INTERNAL', 'UNDO'}

    color:       bpy.props.FloatVectorProperty()
    light_name:  bpy.props.StringProperty()
    plugin_type: bpy.props.StringProperty()
    color_attr_name: bpy.props.StringProperty()

    def execute(self, context):
        light = bpy.data.lights.get(self.light_name)
        if not light:
            return {'CANCELLED'}
        
        # The lights can have or not have nodes. This will change starting with Blender 5.1
        # where they are always created with a node tree.
        if (nodeTree := getattr(light, 'node_tree', None)) and nodeTree.nodes and \
            (node := getLightOutputNode(nodeTree)):
                colorSock = getInputSocketByAttr(node, self.color_attr_name)
                colorSock.value = self.color
        elif hasattr(light, "vray"):
            propGroup = getattr(light.vray, self.plugin_type)
            setattr(propGroup, self.color_attr_name, self.color)
            
        return {'FINISHED'}


def register():
    if not hasattr(bpy.types, "VRAY_OT_set_light_color_from_temperature"):
        bpy.utils.register_class(VRAY_OT_set_light_color_from_temperature)


def unregister():
    if hasattr(bpy.types, "VRAY_OT_set_light_color_from_temperature"):
        bpy.utils.unregister_class(VRAY_OT_set_light_color_from_temperature)
