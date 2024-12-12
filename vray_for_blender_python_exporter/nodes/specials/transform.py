
import bpy
import mathutils

from vray_blender.nodes.mixin import VRayNodeBase
from vray_blender.nodes.utils import selectedObjectTagUpdate
from vray_blender.nodes.sockets import addInput, addOutput


class VRayNodeTransform(VRayNodeBase):
    """ Helper node for setting a Transform value to a socket"""
    bl_idname = 'VRayNodeTransform'
    bl_label  = 'V-Ray Transform'
    bl_icon   = 'AXIS_TOP'

    vray_type   = 'NONE'
    vray_plugin = 'NONE'

    rotate: bpy.props.FloatVectorProperty(
        name        = "Rotation",
        description = "Rotation",
        precision   = 3,
        size        = 3,
        unit        = 'ROTATION',
        default     = (0.0, 0.0, 0.0),
        update = selectedObjectTagUpdate
    )

    offset: bpy.props.FloatVectorProperty(
        name        = "Offset",
        description = "Offset",
        size        = 3,
        precision   = 3,
        unit        = 'LENGTH',
        default     = (0.0, 0.0, 0.0),
        update = selectedObjectTagUpdate
    )

    scale: bpy.props.FloatVectorProperty(
        name        = "Scale",
        description = "Scale",
        size        = 3,
        precision   = 3,
        unit        = 'AREA',
        default     = (1.0, 1.0, 1.0),
        update = selectedObjectTagUpdate
    )

    invert: bpy.props.BoolProperty(
        name        = "Invert",
        description = "Invert transform",
        default     = False,
        update = selectedObjectTagUpdate
    )

    def _getConnectedObject(self, context: bpy.types.Context):
        inputSocket = self.inputs['Object']
        if inputSocket.is_linked:
            connectedNode = inputSocket.links[0].from_node
            if connectedNode.bl_idname == 'VRayNodeSelectObject':
                 return connectedNode.getSelected(context)
        return None

    def init(self, context):
        addInput(self,  'VRaySocketObject',    "Object")
        addOutput(self, 'VRaySocketTransform', "Transform")

    def draw_buttons(self, context, layout):
        inputSocket = self.inputs['Object']
        layout.prop(self, 'invert')
        if not inputSocket.is_linked:
            layout.prop(self, 'rotate')
            layout.prop(self, 'offset')
            layout.prop(self, 'scale')

    def getValue(self, context: bpy.types.Context):
        
        if ob := self._getConnectedObject(context):
            tm = ob.matrix_world.inverted()
        else:
            mat_offs = mathutils.Matrix.Translation(self.offset)

            mat_rot  = mathutils.Matrix.Rotation(self.rotate[0], 4, 'X') @ \
                    mathutils.Matrix.Rotation(self.rotate[1], 4, 'Y') @ \
                    mathutils.Matrix.Rotation(self.rotate[2], 4, 'Z')

            mat_sca  = mathutils.Matrix.Scale(self.scale[0], 4, (1.0, 0.0, 0.0)) @ \
                    mathutils.Matrix.Scale(self.scale[1], 4, (0.0, 1.0, 0.0)) @ \
                    mathutils.Matrix.Scale(self.scale[2], 4, (0.0, 0.0, 1.0))

            tm = mat_offs @ mat_rot @ mat_sca

        if self.invert:
            tm.invert()

        return tm


class VRayNodeMatrix(VRayNodeBase):
    """ Helper node for setting a Matrix value to a socket"""

    bl_idname = 'VRayNodeMatrix'
    bl_label  = 'V-Ray Matrix'
    bl_icon   = 'AXIS_TOP'

    vray_type   = 'NONE'
    vray_plugin = 'NONE'

    rotate: bpy.props.FloatVectorProperty(
        name        = "Rotation",
        description = "Rotation",
        size        = 3,
        precision   = 3,
        unit        = 'ROTATION',
        default     = (0.0, 0.0, 0.0),
        update = selectedObjectTagUpdate
    )

    scale: bpy.props.FloatVectorProperty(
        name        = "Scale",
        description = "Scale",
        size        = 3,
        precision   = 3,
        unit        = 'AREA',
        default     = (1.0, 1.0, 1.0),
        update = selectedObjectTagUpdate
    )

    invert: bpy.props.BoolProperty(
        name        = "Invert",
        description = "Invert matrix",
        default     = False,
        update = selectedObjectTagUpdate
    )

    def init(self, context):
        addOutput(self, 'VRaySocketTransform', "Matrix")

    def draw_buttons(self, context, layout):
        layout.prop(self, 'invert')
        layout.prop(self, 'rotate')
        layout.prop(self, 'scale')

    def getValue(self):
        mat_rot  = mathutils.Matrix.Rotation(self.rotate[0], 3, 'X') @ \
                   mathutils.Matrix.Rotation(self.rotate[1], 3, 'Y') @ \
                   mathutils.Matrix.Rotation(self.rotate[2], 3, 'Z')

        mat_sca  = mathutils.Matrix.Scale(self.scale[0], 3, (1.0, 0.0, 0.0)) @ \
                   mathutils.Matrix.Scale(self.scale[1], 3, (0.0, 1.0, 0.0)) @ \
                   mathutils.Matrix.Scale(self.scale[2], 3, (0.0, 0.0, 1.0))

        tm = mat_rot @ mat_sca

        if self.invert:
            tm.invert()

        return tm


class VRayNodeVector(VRayNodeBase):
    """ Helper node for setting a Vector value to a socket"""

    bl_idname = 'VRayNodeVector'
    bl_label  = 'V-Ray Vector'
    bl_icon   = 'AXIS_TOP'

    vray_type   = 'NONE'
    vray_plugin = 'NONE'

    vector: bpy.props.FloatVectorProperty(
        name        = "Vector",
        description = "Vector",
        size        = 3,
        default     = (0.0, 0.0, 0.0),
        update = selectedObjectTagUpdate
    )

    def init(self, context):
        addOutput(self, 'VRaySocketVector', "Vector")

    def draw_buttons(self, context, layout):
        layout.prop(self, 'vector')

    def getValue(self):
        return self.vector


########  ########  ######   ####  ######  ######## ########     ###    ######## ####  #######  ##    ##
##     ## ##       ##    ##   ##  ##    ##    ##    ##     ##   ## ##      ##     ##  ##     ## ###   ##
##     ## ##       ##         ##  ##          ##    ##     ##  ##   ##     ##     ##  ##     ## ####  ##
########  ######   ##   ####  ##   ######     ##    ########  ##     ##    ##     ##  ##     ## ## ## ##
##   ##   ##       ##    ##   ##        ##    ##    ##   ##   #########    ##     ##  ##     ## ##  ####
##    ##  ##       ##    ##   ##  ##    ##    ##    ##    ##  ##     ##    ##     ##  ##     ## ##   ###
##     ## ########  ######   ####  ######     ##    ##     ## ##     ##    ##    ####  #######  ##    ##

def getRegClasses():
    return (
        VRayNodeTransform,
        VRayNodeMatrix,
        VRayNodeVector,
    )


def register():
    for regClass in getRegClasses():
        bpy.utils.register_class(regClass)


def unregister():
    for regClass in getRegClasses():
        bpy.utils.unregister_class(regClass)
