# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later


import bpy
import math
import gpu
from gpu_extras.batch import batch_for_shader
from mathutils import Matrix

from vray_blender.lib import blender_utils, lib_utils
from vray_blender.lib.blender_utils import getSpaceView3D
from vray_blender.engine.renderer_vantage import drawCallbackVantage


handlers = []


_DRAWABLE_LIGHT_TYPES= {'SPHERE', 'DIRECT', 'DOME', 'IES', 'AMBIENT'}# TODO: Add 'RECT' back when we figure out how to disable the built-in Blender gizmo for Area light

# Cached list of (light_type, tm, selected, radius_or_None) for V-Ray lights to gizmo-draw.
# `radius` is set only for SPHERE lights (LightSphere.radius); other types use a
# hardcoded radius inside the drawer and store None.
# `selected` is computed at cache-build time from the source light's selection state.
# Rebuilt lazily by _rebuildLightDrawCache() the first time vrayDrawLightShape
# runs after a depsgraph update; cleared by _onDepsgraphUpdatePost / _onLoadPre.
_lightDrawCache: list[tuple[str, Matrix, bool, float | None]] = []
_lightDrawCacheValid = False


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
        gpu.matrix.multiply_matrix(tm)
        batch.draw(shader)


def vrayDrawLightSphere(col, tm, r):
    vrayDrawShape(circleBatch, r, col, tm)
    vrayDrawShape(circleBatch, r, col, tm @ Matrix.Rotation(math.radians(90.0), 4, 'X'))
    vrayDrawShape(circleBatch, r, col, tm @ Matrix.Rotation(math.radians(90.0), 4, 'Y'))


def vrayDrawLightDirect(color, tm):
    # Draw parallel arrows in the direction of the light
    r = 0.3

    tmArrow1 = tm @ Matrix.Translation((0.0, -r, 0.0)) @ Matrix.Rotation(math.radians(90.0), 4, 'Z')
    tmArrow2 = tm @ Matrix.Translation((0.0,  r, 0.0)) @ Matrix.Rotation(math.radians(90.0), 4, 'Z')
    tmArrow3 = tm @ Matrix.Translation(( r, 0.0, 0.0))
    tmArrow4 = tm @ Matrix.Translation((-r, 0.0, 0.0))

    vrayDrawShape(arrowBatch, 2.0, color, tmArrow1)
    vrayDrawShape(arrowBatch, 2.0, color, tmArrow2)
    vrayDrawShape(arrowBatch, 2.0, color, tmArrow3)
    vrayDrawShape(arrowBatch, 2.0, color, tmArrow4)


def vrayDrawLightDome(col, tm):
    r = 1.0

    tm2 = tm @ Matrix.Rotation(math.radians(90.0), 4, 'Y')
    tm3 = tm2 @ Matrix.Rotation(math.radians(90.0), 4, 'X')

    vrayDrawShape(circleBatch, r, col, tm)
    vrayDrawShape(halfCircleBatch, r, col, tm2)
    vrayDrawShape(halfCircleBatch, r, col, tm3)


def vrayDrawLightIES(color, tm):
    # Photometric light
    r = 1.0

    tmCircle = tm @ Matrix.Translation((0.0, 0.0, -0.5))
    vrayDrawShape(boxBatch, r, color, tm)
    vrayDrawShape(circleBatch, r / 2, color, tmCircle)


def vrayDrawLightRect(color, tm):
    r = 1.0

    vrayDrawShape(crossedRectangleBatch, r, color, tm)
    vrayDrawShape(arrowBatch, r, color, tm)


def vrayDrawLightAmbient(color, tm):
    r = 0.3

    vrayDrawShape(circleBatch, r, color, tm @ Matrix.Translation((0.0, 0.0, 0.02)))
    vrayDrawShape(circleBatch, r, color, tm @ Matrix.Translation((0.0, 0.0, -0.02)))
    vrayDrawShape(circleBatch, r, color, tm @ Matrix.Rotation(math.radians(90.0), 4, 'Y'))
    vrayDrawShape(circleBatch, r, color, tm @ Matrix.Rotation(math.radians(90.0), 4, 'X'))
    

def _rebuildLightDrawCache():
    """ Walk the evaluated depsgraph once and collect everything the gizmo drawers need.

        `depsgraph.object_instances` yields one entry per real scene object
        (`is_instance == False`) and one entry per instance (`is_instance == True`),
        so a single loop covers source lights and instanced lights.
    """
    _lightDrawCache.clear()

    depsgraph = bpy.context.evaluated_depsgraph_get()
    for inst in depsgraph.object_instances:
        ob = inst.object
        if ob is None or ob.type != 'LIGHT':
            continue

        light = ob.data
        lightType = light.vray.light_type
        if lightType not in _DRAWABLE_LIGHT_TYPES:
            continue

        # If the object is not visible in the viewport, skip it (no need to draw the gizmo for invisible lights).
        # No need for visibility check for instances, because depsgraph.object_instances already
        # excludes instances from hidden instancers.
        if not inst.is_instance and not ob.original.visible_get():
            continue

        # Skip lights disabled in V-Ray. The previous code drew them in black, but
        # with no per-entry color in the cache we just omit them entirely.
        vrayLight = lib_utils.getLightPropGroup(light, lib_utils.getLightPluginType(light))
        if not vrayLight.enabled:
            continue

        radius = lib_utils.getLightPropGroup(light, 'LightSphere').radius if lightType == 'SPHERE' else None

        # `inst.matrix_world` is invalidated when the iterator is exhausted, so
        # store a frozen copy (matches the exporter and fur preview convention).
        tm = inst.matrix_world.copy()
        tm.freeze()
        
        selected = inst.parent.original.select_get() if inst.is_instance else ob.original.select_get()
        
        _lightDrawCache.append((lightType, tm, selected, radius))


@bpy.app.handlers.persistent
def _onDepsgraphUpdatePost(scene, depsgraph):
    global _lightDrawCacheValid
    _lightDrawCacheValid = False


@bpy.app.handlers.persistent
def _onLoadPre(e):
    global _lightDrawCacheValid
    _lightDrawCache.clear()
    _lightDrawCacheValid = False


def vrayDrawLightShape():
    global _lightDrawCacheValid

    space3D = getSpaceView3D(bpy.context)
    assert space3D is not None

    if not space3D.overlay.show_overlays or not space3D.overlay.show_extras:
        return

    if not _lightDrawCacheValid:
        _rebuildLightDrawCache()
        _lightDrawCacheValid = True

    if not _lightDrawCache:
        return

    prevGpuState = gpu.state.depth_test_get()
    gpu.state.depth_test_set('LESS_EQUAL')

    uiTheme       = bpy.context.preferences.themes[0]
    colorActive   = uiTheme.view_3d.object_active[:] + (1.0,)  # RGB, add alpha
    colorInactive = uiTheme.view_3d.light

    for lightType, tm, selected, radius in _lightDrawCache:
        color = colorActive if selected else colorInactive
        match lightType:
            case 'SPHERE':  vrayDrawLightSphere(color, tm, radius)
            case 'DIRECT':  vrayDrawLightDirect(color, tm)
            case 'DOME':    vrayDrawLightDome(color, tm)
            case 'IES':     vrayDrawLightIES(color, tm)
            case 'AMBIENT': vrayDrawLightAmbient(color, tm)
            # TODO: Need to figure out how to disable the built-in Blender gizmo for Area light
            #       if we want to show our own rect gizmo
            #case 'RECT':    vrayDrawLightRect(color, tm)

    gpu.state.depth_test_set(prevGpuState)

RegClasses = ()


def register():
    global handlers

    def vrayDrawHandlerAdd(cb, drawType='POST_PIXEL'):
        handlers.append(bpy.types.SpaceView3D.draw_handler_add(cb, (), 'WINDOW', drawType))

    for regClass in RegClasses:
        bpy.utils.register_class(regClass)

    vrayDrawHandlerAdd(vrayDrawLightShape, 'POST_VIEW')
    vrayDrawHandlerAdd(drawCallbackVantage, 'POST_PIXEL')

    blender_utils.addEvent(bpy.app.handlers.depsgraph_update_post, _onDepsgraphUpdatePost)
    blender_utils.addEvent(bpy.app.handlers.load_pre, _onLoadPre)


def unregister():
    global handlers

    blender_utils.delEvent(bpy.app.handlers.depsgraph_update_post, _onDepsgraphUpdatePost)
    blender_utils.delEvent(bpy.app.handlers.load_pre, _onLoadPre)

    for regClass in RegClasses:
        bpy.utils.unregister_class(regClass)

    for handle in handlers:
        bpy.types.SpaceView3D.draw_handler_remove(handle, 'WINDOW')
    handlers = []
