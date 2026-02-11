# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
import mathutils

from vray_blender.lib.mixin import VRayNodeBase
from vray_blender.nodes.utils import selectedObjectTagUpdate
from vray_blender.nodes.sockets import addInput, addOutput
from vray_blender.exporting.tools import getLinkedFromSocket, getFarNodeLink



def _getVectorSocketValue(node, sockName):
    assert sockName in node.inputs, "Getting value of nonexisting vector socket"

    sock = node.inputs[sockName]
    if (linkedSock := getLinkedFromSocket(sock)) and \
        (linkedSock.node.bl_idname == 'VRayNodeVector'):
        return linkedSock.node.value
    return sock.value


def _drawVectorSock(node, sockName, layout):
    assert sockName in node.inputs, "Drawing nonexisting vector socket"

    vSock = node.inputs[sockName]
    if not vSock.hasActiveFarLink():
        layout.row().column().prop(vSock, 'value')

class VRayNodeTransform(VRayNodeBase):
    """ Helper node for setting a Transform value to a socket"""
    bl_idname = 'VRayNodeTransform'
    bl_label  = 'V-Ray Transform'
    bl_icon   = 'AXIS_TOP'

    vray_type   = 'NONE'
    vray_plugin = 'NONE'

    invert: bpy.props.BoolProperty(
        name        = "Invert",
        description = "Invert transform",
        default     = False,
        update = selectedObjectTagUpdate
    )

    def _getConnectedObject(self, context: bpy.types.Context):
        inputSocket = self.inputs['Object']
        if link := getFarNodeLink(inputSocket):
            connectedNode = link.from_node
            if connectedNode.bl_idname == 'VRayNodeSelectObject':
                 return connectedNode.getSelected(context)
        return None

    def init(self, context):
        addInput(self,  'VRaySocketVectorRotation',    "Rotation")
        addInput(self,  'VRaySocketVectorOffset',    "Offset")
        addInput(self,  'VRaySocketVectorScale',    "Scale")
        addInput(self,  'VRaySocketObject',    "Object")
        addOutput(self, 'VRaySocketTransform', "Transform")

    def draw_buttons(self, context, layout):
        layout.prop(self, 'invert')

    def draw_buttons_ext(self, context, layout):
        inputSocket = self.inputs['Object']
        layout.prop(self, 'invert')
        if not inputSocket.hasActiveFarLink():
            _drawVectorSock(self, 'Rotation', layout)
            _drawVectorSock(self, 'Offset', layout)
            _drawVectorSock(self, 'Scale', layout)

    def getValue(self, context: bpy.types.Context):
        
        if ob := self._getConnectedObject(context):
            tm = ob.matrix_world.inverted()
        else:
            offset = _getVectorSocketValue(self, 'Offset')
            mat_offs = mathutils.Matrix.Translation(offset)


            rotate = _getVectorSocketValue(self, 'Rotation')
            mat_rot  = mathutils.Matrix.Rotation(rotate[0], 4, 'X') @ \
                    mathutils.Matrix.Rotation(rotate[1], 4, 'Y') @ \
                    mathutils.Matrix.Rotation(rotate[2], 4, 'Z')

            scale = _getVectorSocketValue(self, 'Scale')
            mat_sca  = mathutils.Matrix.Scale(scale[0], 4, (1.0, 0.0, 0.0)) @ \
                    mathutils.Matrix.Scale(scale[1], 4, (0.0, 1.0, 0.0)) @ \
                    mathutils.Matrix.Scale(scale[2], 4, (0.0, 0.0, 1.0))

            tm = mat_offs @ mat_rot @ mat_sca

        if self.invert:
            # Matrix.invert_safe() is used to avoid exceptions 
            # on matrices that cannot be inverted, for example when the scale vector has a zero component.
            tm.invert_safe()

        return tm


class VRayNodeMatrix(VRayNodeBase):
    """ Helper node for setting a Matrix value to a socket"""

    bl_idname = 'VRayNodeMatrix'
    bl_label  = 'V-Ray Matrix'
    bl_icon   = 'AXIS_TOP'

    vray_type   = 'NONE'
    vray_plugin = 'NONE'

    invert: bpy.props.BoolProperty(
        name        = "Invert",
        description = "Invert matrix",
        default     = False,
        update = selectedObjectTagUpdate
    )

    def init(self, context):
        addInput(self, 'VRaySocketVectorRotation', "Rotation")
        addInput(self, 'VRaySocketVectorScale', "Scale")
        addOutput(self, 'VRaySocketTransform', "Matrix")

    def draw_buttons(self, context, layout):
        layout.prop(self, 'invert')

    def draw_buttons_ext(self, context, layout):
        layout.prop(self, 'invert')
        _drawVectorSock(self, 'Rotation', layout)
        _drawVectorSock(self, 'Scale', layout)

    def getValue(self):
        rotate = _getVectorSocketValue(self, 'Rotation')
        mat_rot  = mathutils.Matrix.Rotation(rotate[0], 3, 'X') @ \
                   mathutils.Matrix.Rotation(rotate[1], 3, 'Y') @ \
                   mathutils.Matrix.Rotation(rotate[2], 3, 'Z')

        scale = _getVectorSocketValue(self, 'Scale')
        mat_sca  = mathutils.Matrix.Scale(scale[0], 3, (1.0, 0.0, 0.0)) @ \
                   mathutils.Matrix.Scale(scale[1], 3, (0.0, 1.0, 0.0)) @ \
                   mathutils.Matrix.Scale(scale[2], 3, (0.0, 0.0, 1.0))

        tm = mat_rot @ mat_sca

        if self.invert:
            # Matrix.invert_safe() is used to avoid exceptions 
            # on matrices that cannot be inverted, for example when the scale vector has a zero component.
            tm.invert_safe()

        return tm



class VRayNodeVector(VRayNodeBase):
    """ Helper node for setting a Vector value to a socket"""

    bl_idname = 'VRayNodeVector'
    bl_label  = 'V-Ray Vector'
    bl_icon   = 'AXIS_TOP'

    vray_type   = 'NONE'
    vray_plugin = 'NONE'

    useDegrees: bpy.props.BoolProperty(
        name        = "Use Degrees",
        description = "Use Degrees",
        default     = False,
        update = selectedObjectTagUpdate
    )

    value: bpy.props.FloatVectorProperty(
        name        = "Vector",
        description = "Vector",
        size        = 3,
        precision   = 3,
        step        = 1,
        subtype     = 'XYZ',
        default     = (0.0, 0.0, 0.0),
        update = selectedObjectTagUpdate
    )

    # Used to represent the VRayNodeVector.value vector in degrees
    valueDegrees: bpy.props.FloatVectorProperty(
        name        = "Vector Degrees",
        description = "Vector Degrees",
        size        = 3,
        precision   = 3,
        step        = 10,
        subtype     = 'EULER',
        unit        = 'ROTATION',
        get = lambda node: node.value,
        set = lambda node, newValue: setattr(node, "value", newValue),
        update = selectedObjectTagUpdate
    )

    def init(self, context):
        addOutput(self, 'VRaySocketVector', "Vector")

    def _getVectorName(self):
        return self.transform_type.lower()

    def draw_buttons(self, context, layout):
        layout.prop(self, 'useDegrees')
        # layout in column to enable multiple selections for vector properties
        layout.row().column().prop(self, "valueDegrees" if self.useDegrees else "value", text="")

    def getValue(self):
        # Convert self.vector (a bpy_float[3]) to a mathutils.Vector,
        # ensuring it is exported as a vector attribute rather than a list of floats.
        return mathutils.Vector(self.value)


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
