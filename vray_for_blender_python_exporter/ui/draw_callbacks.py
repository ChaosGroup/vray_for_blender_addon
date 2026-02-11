# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later


import bpy
import math
import gpu
from gpu_extras.batch import batch_for_shader
from mathutils import Matrix

from vray_blender.lib import lib_utils
from vray_blender.lib.blender_utils import getSpaceView3D
from vray_blender.engine.renderer_vantage import drawCallbackVantage


handlers = []


# Circle in the horizontal plane
CircleShape = (
    (0.000,1.000,0.000),
    (-0.195,0.981,0.000),
    (-0.383,0.924,0.000),
    (-0.556,0.831,0.000),
    (-0.707,0.707,0.000),
    (-0.831,0.556,0.000),
    (-0.924,0.383,0.000),
    (-0.981,0.195,0.000),
    (-1.000,0.000,0.000),
    (-0.981,-0.195,0.000),
    (-0.924,-0.383,0.000),
    (-0.831,-0.556,0.000),
    (-0.707,-0.707,0.000),
    (-0.556,-0.831,0.000),
    (-0.383,-0.924,0.000),
    (-0.195,-0.981,0.000),
    (0.000,-1.000,0.000),
    (0.195,-0.981,0.000),
    (0.383,-0.924,0.000),
    (0.556,-0.831,0.000),
    (0.707,-0.707,0.000),
    (0.831,-0.556,0.000),
    (0.924,-0.383,0.000),
    (0.981,-0.195,0.000),
    (1.000,0.000,0.000),
    (0.981,0.195,0.000),
    (0.924,0.383,0.000),
    (0.831,0.556,0.000),
    (0.707,0.707,0.000),
    (0.556,0.831,0.000),
    (0.383,0.924,0.000),
    (0.195,0.981,0.000),
)

RectangleShape = (
    ( 1.0,  1.0, 0.0),
    ( 1.0, -1.0, 0.0),
    (-1.0, -1.0, 0.0),
    (-1.0,  1.0, 0.0),
)


crossedRectangleShape = (
    # square
    ( 1.0,  1.0, 0.0),
    ( 1.0, -1.0, 0.0),
    (-1.0, -1.0, 0.0),
    (-1.0,  1.0, 0.0),
    ( 1.0,  1.0, 0.0),
    
    # crossbars
    (-1.0, -1.0, 0.0),
    (-1.0,  1.0, 0.0),
    ( 1.0, -1.0, 0.0),
)


FrustumShape = (
    ( 1.0,  1.0, 0.0),
    ( 1.0,  1.0, 1.0),
    ( 1.0, -1.0, 0.0),
    ( 1.0, -1.0, 1.0),
    (-1.0, -1.0, 0.0),
    (-1.0, -1.0, 1.0),
    (-1.0,  1.0, 0.0),
    (-1.0,  1.0, 1.0),
)

FrustumShape2 = (
    (0.0,  1.0, 0.0),
    (0.0,  1.0, 1.0),
    (0.0, -1.0, 0.0),
    (0.0, -1.0, 1.0),
    ( 1.0, 0.0, 0.0),
    ( 1.0, 0.0, 1.0),
    (-1.0, 0.0, 0.0),
    (-1.0, 0.0, 1.0),
)

downArrowShape = (
    ( 0.0,  0.0,  0.0),
    ( 0.0,  0.0, -1.0),
    ( 0.0,  0.0, -1.0),
    ( 0.02, 0.0, -0.9),
    ( 0.0,  0.0, -1.0),
    (-0.02, 0.0, -0.9),
)


boxShape = [
    # Bottom face
    (-0.5, -0.5, -0.5), (-0.5, -0.5, 0.5),  
    (-0.5, -0.5, -0.5), (0.5, -0.5, -0.5),  
    (0.5, -0.5, -0.5), (0.5, -0.5, 0.5),    
    (-0.5, -0.5, 0.5), (0.5, -0.5, 0.5),    

    # Top face
    (-0.5, 0.5, -0.5), (-0.5, 0.5, 0.5),    
    (-0.5, 0.5, -0.5), (0.5, 0.5, -0.5),    
    (0.5, 0.5, -0.5), (0.5, 0.5, 0.5),      
    (-0.5, 0.5, 0.5), (0.5, 0.5, 0.5),      

    # Vertical edges
    (-0.5, -0.5, -0.5), (-0.5, 0.5, -0.5),  
    (-0.5, -0.5, 0.5), (-0.5, 0.5, 0.5),    
    (0.5, -0.5, -0.5), (0.5, 0.5, -0.5),    
    (0.5, -0.5, 0.5), (0.5, 0.5, 0.5)       
]


shader = gpu.shader.from_builtin('UNIFORM_COLOR')

circleBatch             = batch_for_shader(shader, 'LINE_LOOP', {'pos': CircleShape})
halfCircleBatch         = batch_for_shader(shader, 'LINE_STRIP', {'pos': CircleShape[:len(CircleShape)//2+1]})
rectangleBatch          = batch_for_shader(shader, 'LINE_LOOP', {'pos': RectangleShape})
crossedRectangleBatch   = batch_for_shader(shader, 'LINE_LOOP', {'pos': crossedRectangleShape})
frustumBatch            = batch_for_shader(shader, 'LINES', {'pos': FrustumShape})
frustumBatch2           = batch_for_shader(shader, 'LINES', {'pos': FrustumShape2})
arrowBatch              = batch_for_shader(shader, 'LINES', {'pos': downArrowShape})
boxBatch                = batch_for_shader(shader, 'LINES', {'pos': boxShape})


def vrayDrawShape(batch, mult, color, tm=Matrix.Identity(4)):  
    gpu.state.line_width_set(1.0)
    shader.bind()
    shader.uniform_float('color', color)

    tm = tm @ Matrix.Scale(mult, 4)
    with gpu.matrix.push_pop():
        gpu.matrix.load_matrix(tm)
        gpu.matrix.load_projection_matrix(bpy.context.region_data.perspective_matrix)
        batch.draw(shader)


def vrayDrawLightSphere(ob, col):
    light = ob.data
    propGroup = lib_utils.getLightPropGroup(light, 'LightSphere')
    r = propGroup.radius

    tm = ob.matrix_world
    vrayDrawShape(circleBatch, r, col, tm)
    vrayDrawShape(circleBatch, r, col, tm @ Matrix.Rotation(math.radians(90.0), 4, 'X'))
    vrayDrawShape(circleBatch, r, col, tm @ Matrix.Rotation(math.radians(90.0), 4, 'Y'))


def vrayDrawLightDirect(ob, color):
    # Draw parallel arrows in the direction of the light
    r = 0.3
    
    tmArrow1 = ob.matrix_world @ Matrix.Translation((0.0, -r, 0.0)) @ Matrix.Rotation(math.radians(90.0), 4, 'Z') 
    tmArrow2 = ob.matrix_world @ Matrix.Translation((0.0, r, 0.0)) @ Matrix.Rotation(math.radians(90.0), 4, 'Z')
    tmArrow3 = ob.matrix_world @ Matrix.Translation((r, 0.0, 0.0)) 
    tmArrow4 = ob.matrix_world @ Matrix.Translation((-r, 0.0, 0.0))
    
    vrayDrawShape(arrowBatch, 2.0, color, tmArrow1)
    vrayDrawShape(arrowBatch, 2.0, color, tmArrow2)
    vrayDrawShape(arrowBatch, 2.0, color, tmArrow3)
    vrayDrawShape(arrowBatch, 2.0, color, tmArrow4)
    

def vrayDrawLightDome(ob, col):
    r = 1.0
    tm = ob.matrix_world
    tm2 = tm @ Matrix.Rotation(math.radians(90.0), 4, 'Y')
    tm3 = tm2 @ Matrix.Rotation(math.radians(90.0), 4, 'X')

    vrayDrawShape(circleBatch, r, col, tm)
    vrayDrawShape(halfCircleBatch, r, col, tm2)
    vrayDrawShape(halfCircleBatch, r, col, tm3)


def vrayDrawLightIES(ob, color):
    # Photometric light
    r = 1.0

    tmBox = ob.matrix_world
    tmCircle = ob.matrix_world @ Matrix.Translation((0.0, 0.0, -0.5))
    vrayDrawShape(boxBatch, r, color, tmBox)
    vrayDrawShape(circleBatch, r / 2, color, tmCircle)


def vrayDrawLightRect(ob, color):
    r = 1.0

    tm = ob.matrix_world
    vrayDrawShape(crossedRectangleBatch, r, color, tm)
    vrayDrawShape(arrowBatch, r, color, tm)


def vrayDrawLightAmbient(ob, color):
    r = 0.3
    
    tm = ob.matrix_world
    vrayDrawShape(circleBatch, r, color, tm @ Matrix.Translation((0.0, 0.0, 0.02)))
    vrayDrawShape(circleBatch, r, color, tm @ Matrix.Translation((0.0, 0.0, -0.02)))
    vrayDrawShape(circleBatch, r, color, tm @ Matrix.Rotation(math.radians(90.0), 4, 'Y'))
    vrayDrawShape(circleBatch, r, color, tm @ Matrix.Rotation(math.radians(90.0), 4, 'X'))
    

def vrayDrawLightShape():
    space3D = getSpaceView3D(bpy.context)
    assert space3D is not None
        
    if not space3D.overlay.show_overlays:
        return
    
    uiTheme   = bpy.context.preferences.themes[0]
    colorActive = uiTheme.view_3d.object_active[:] + (1.0,) # This is RGB, add alpha
    colorInactive = uiTheme.view_3d.light
    colorDisabled = (0.0, 0.0, 0.0, 1.0)

    for ob in bpy.context.scene.objects:
        if (ob.type not in {'LIGHT'}) or (not ob.visible_get()):
            continue

        light = ob.data
        vrayLight = lib_utils.getLightPropGroup(light, lib_utils.getLightPluginType(light))

        if vrayLight.enabled:
            color = colorActive if ob == bpy.context.active_object else colorInactive
        else:
            color = colorDisabled

        match light.vray.light_type:
            case 'SPHERE':
                vrayDrawLightSphere(ob, color)
            case 'DIRECT':
                vrayDrawLightDirect(ob, color)
            case 'DOME':
                vrayDrawLightDome(ob, color)
            case 'IES':
                vrayDrawLightIES(ob, color)
            case 'AMBIENT':
                vrayDrawLightAmbient(ob, color)
            # TODO: Need to figure out how to disable the built-in Blender gizmo for Area light
            #       if we want to show our own rect gizmo
            # case 'RECT':
            #     vrayDrawLightRect(ob, color)


RegClasses = ()


def register():
    global handlers

    def vrayDrawHandlerAdd(cb):
        handlers.append(bpy.types.SpaceView3D.draw_handler_add(cb, (), 'WINDOW', 'POST_PIXEL'))

    for regClass in RegClasses:
        bpy.utils.register_class(regClass)

    vrayDrawHandlerAdd(vrayDrawLightShape)
    vrayDrawHandlerAdd(drawCallbackVantage)


def unregister():
    global handlers

    for regClass in RegClasses:
        bpy.utils.unregister_class(regClass)

    for handle in handlers:
        bpy.types.SpaceView3D.draw_handler_remove(handle, 'WINDOW')
    handlers = []
