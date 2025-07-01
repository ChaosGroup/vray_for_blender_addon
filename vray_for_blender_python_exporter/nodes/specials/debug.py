
import bpy

from vray_blender.nodes.mixin    import VRayNodeBase
from vray_blender.nodes.sockets import addInput, addOutput, VRaySocket


class VRaySocketAny(VRaySocket):
    bl_idname = 'VRaySocketAny'
    bl_label  = 'Any data socket'

    value: bpy.props.StringProperty(
        name = "Data",
        description = "Data",
        default = ""
    )

    def draw(self, context, layout, node, text):
        if node.bl_idname == 'VRayNodeDebugSwitch':
            # NOTE: label ends with digit
            if node.input_index == text[-1]:
                text = '%s *' % text
        layout.label(text=text)

    @classmethod
    def draw_color_simple(cls):
        return (0.3, 0.3, 0.3, 1.0)


class VRayNodeDebugSwitch(VRayNodeBase):
    bl_idname = 'VRayNodeDebugSwitch'
    bl_label  = 'V-Ray Switch'
    bl_icon   = 'NONE'

    vray_type   = 'NONE'
    vray_plugin = 'NONE'

    input_index: bpy.props.EnumProperty(
        name        = "Input",
        description = "Input index",
        items = (
            ('0', "0", ""),
            ('1', "1", ""),
            ('2', "2", ""),
            ('3', "3", ""),
            ('4', "4", ""),
        ),
        default = '0'
    )

    def init(self, context):
        addInput(self, 'VRaySocketAny', "Input 0")
        addInput(self, 'VRaySocketAny', "Input 1")
        addInput(self, 'VRaySocketAny', "Input 2")
        addInput(self, 'VRaySocketAny', "Input 3")
        addInput(self, 'VRaySocketAny', "Input 4")

        addOutput(self, 'VRaySocketAny', "Output")

    def draw_buttons(self, context, layout):
        layout.prop(self, 'input_index', expand=True)


def getRegClasses():
    return (
        VRaySocketAny,
        VRayNodeDebugSwitch,
    )


def register():
    for regClass in getRegClasses():
        bpy.utils.register_class(regClass)


def unregister():
    for regClass in getRegClasses():
        bpy.utils.unregister_class(regClass)
