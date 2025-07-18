
import bpy
import bmesh
import math
import mathutils
import os
import tempfile

from vray_blender.lib import lib_utils, path_utils
from vray_blender import debug

VRAY_ASSET_TYPE = {
    "Proxy": "1",
    "Scene": "2"
}

ObjectPrefix = {
    'LIGHT'   : 'LA',
    'CAMERA' : 'CA',
}

NonGeometryTypes = {'LIGHT','CAMERA','SPEAKER','ARMATURE','LATTICE','EMPTY'}

TypesThatSupportMaterial = {'MESH', 'CURVE', 'CURVES', 'SURFACE', 'FONT', 'META',
                            'GPENCIL', 'VOLUME', 'HAIR', 'POINTCLOUD'}

NC_WORLD    = 0
NC_MATERIAL = 1
NC_LAMP     = 3


def geometryObjectIt(objects):
    """ Iterates through each geometry object in list """
    for ob in objects:
        if ob.type not in NonGeometryTypes:
            yield ob


def _mergeRanges(range1: mathutils.Vector, range2: mathutils.Vector):
    """ Return a single range encompassing both input ranges """
    return mathutils.Vector((
        min(range1.x, range2.x),
        max(range1.y, range2.y)
    ))


def _getArmatureAnimationRange(obj: bpy.types.Object):
    """ Returns the animation frame range for an object animated by armature.

    Args:
        obj (bpy.types.Object): The object that is potentially animated by one 
        or multuple armatures.

    Returns:
        Vector: Vector(start_frame, end_frame) as integers.
               Returns a (0,0) range if object is not controlled by animated armature.
    """
    frameRange = mathutils.Vector((0,0))
    armatureObj = None
    
    # Find the Armature modifier on the mesh object
    for mod in obj.modifiers:
        if (mod.type == 'ARMATURE') and (mod.object) and (mod.object.type == 'ARMATURE'):
            
            armatureObj = mod.object
            
            # Check if the armature has animation data and an active action
            if armatureObj.animation_data and armatureObj.animation_data.action:
                action = armatureObj.animation_data.action
                frame_start = int(action.frame_range[0])
                frame_end = int(action.frame_range[1])
                return _mergeRanges(frameRange, mathutils.Vector((frame_start, frame_end)))
            
    return frameRange


def frameInRange(frame: float, range: mathutils.Vector):
    return range.x <= frame <= range.y


def getGeomAnimationRange(obj: bpy.types.Object) -> mathutils.Vector:
    """ Get the animation range for a geometry data object.

        Args:
            dataObj: The data object for which to calculate the range

        Returns:
            mathutils.Vector: Vector(start_frame, end_frame) or Vector(0,0) if the object is not animated
    """
    frameRange = mathutils.Vector((1,1))

    # There are three sources of geometry shape changes - directly mesh edits, and modifiers 
    # applied to an object. The direct edits animation data is stored in Object.data.shape_keys.animation_data. 
    # The modifiers's animation data is stored in Objects.mofidiers.node_group.animation_data for
    # geometry nodes and in Object.animation_data for the rest of the modifiers 

    # Check for animated mesh edits
    # The name 'shape_keys' is misleading, the field references a single key
    if key := getattr(obj.data, 'shape_keys', None):
        if key.animation_data and (action := key.animation_data.action):
            frameRange = _mergeRanges(frameRange, action.frame_range)

    # Check for animated modifiers
    if (modifiers := getattr(obj, "modifiers", None)) and (animData := getattr(obj, "animation_data")):
        # Geometry nodes
        if geomNodeTree := next((m.node_group for m in modifiers if hasattr(m, 'node_group')), None):
            if treeAnimData := getattr(geomNodeTree, "animation_data", None): 
                frameRange = _mergeRanges(frameRange, treeAnimData.action.frame_range)
        
        # Non geometry-nodes
        if any(fc for fc in animData.action.fcurves if fc.data_path.startswith('modifiers[')):
            frameRange = _mergeRanges(frameRange, animData.action.frame_range)

    # Check for animated armature
    frameRange = _mergeRanges(frameRange, _getArmatureAnimationRange(obj))
    
    return frameRange

def getGeomCenter(ob: bpy.types.Object) -> mathutils.Vector:
    """ Returns the local-space center of object's mesh. """

    assert ob.type not in NonGeometryTypes, "Object must be a geometry type"

    corners = [mathutils.Vector(c) for c in ob.bound_box]
    
    return sum(corners, mathutils.Vector()) / len(corners)


def getObjectList(object_names_string=None, group_names_string=None):
    object_list = []

    if object_names_string:
        ob_names = object_names_string.split(';')
        for ob_name in ob_names:
            if ob_name in bpy.context.scene.objects:
                object_list.append(bpy.context.scene.objects[ob_name])

    if group_names_string:
        gr_names = group_names_string.split(';')
        for gr_name in gr_names:
            if gr_name in bpy.data.groups:
                object_list.extend(bpy.data.groups[gr_name].objects)

    dupliGroup = []
    for ob in object_list:
        if ob.dupli_type == 'GROUP' and ob.dupli_group:
            dupliGroup.extend(ob.dupli_group.objects)
    object_list.extend(dupliGroup)

    return object_list


def getCameraHideLists(camera):
    VRayCamera = camera.data.vray

    visibility = {
        'all'     : set(),
        'camera'  : set(),
        'gi'      : set(),
        'reflect' : set(),
        'refract' : set(),
        'shadows' : set(),
    }

    if VRayCamera.hide_from_view:
        for hide_type in visibility:
            if getattr(VRayCamera, 'hf_%s' % hide_type):
                if getattr(VRayCamera, 'hf_%s_auto' % hide_type):
                    obList = getObjectList(group_names_string='hf_%s' % camera.name)
                else:
                    obList = getObjectList(getattr(VRayCamera, 'hf_%s_objects' % hide_type),
                                           getattr(VRayCamera, 'hf_%s_groups' % hide_type))
                for o in obList:
                    visibility[hide_type].add(o.as_pointer())

    return visibility


def getEffectsExcludeList(scene):
    # TODO: Rewrite to nodes!
    #
    VRayScene = scene.vray
    exclude_list = []
    VRayEffects  = VRayScene.VRayEffects
    if VRayEffects.use:
        for effect in VRayEffects.effects:
            if effect.use:
                if effect.type == 'FOG':
                    EnvironmentFog = effect.EnvironmentFog
                    fog_objects = getObjectList(EnvironmentFog.objects, EnvironmentFog.groups)
                    for ob in fog_objects:
                        if ob not in exclude_list:
                            exclude_list.append(ob.as_pointer())
    return exclude_list


def filterObjectListByType(objectList, objectType):
    return filter(lambda x: x.type == objectType, objectList)


def getObjectName(ob, prefix=None):
    if prefix is None:
        prefix = ObjectPrefix.get(ob.type, 'OB')
    name = prefix + ob.name
    if ob.library:
        name = 'LI' + path_utils.getFilename(ob.library.filepath) + name
    return lib_utils.cleanString(name)


def getGroupObjects(groupName):
    obList = []
    if groupName in bpy.data.collections:
        obList.extend(bpy.data.collections[groupName].objects)
    return obList


def getGroupObjectsNames(groupName):
    obList = [getObjectName(ob) for ob in getGroupObjects(groupName)]
    return obList


def getSmokeModifier(ob):
    if len(ob.modifiers):
        for md in ob.modifiers:
            if md.type == 'SMOKE' and md.smoke_type == 'DOMAIN':
                return md
    return None


def getSceneObject(scene, objectName):
    if objectName not in scene.objects:
        return None
    return scene.objects[objectName]


def getDistanceObOb(ob1, ob2):
    t1 = ob1.matrix_world.translation
    t2 = ob2.matrix_world.translation
    d = t1 - t2
    return d.length


def getLensShift(ob: bpy.types.Object):
    shift = 0.0
    constraint = None

    if len(ob.constraints) > 0:
        for co in ob.constraints:
            if co.type in {'TRACK_TO', 'DAMPED_TRACK', 'LOCKED_TRACK'}:
                constraint = co
                break

    if constraint:
        constraint_ob = constraint.target
        if constraint_ob:
            z_shift = ob.matrix_world.to_translation()[2] - constraint_ob.matrix_world.to_translation()[2]
            l = getDistanceObOb(ob, constraint_ob)
            shift = -1.0 * z_shift / l
    else:
        rx = ob.rotation_euler[0]
        lsx = rx - math.pi / 2
        if math.fabs(lsx) > 0.0001:
            shift = math.tan(lsx)
        if math.fabs(shift) > math.pi:
            shift = 0.0

    return shift


def getCmToSceneUnitsMultiplier(context):
    """ Returns multiplier for conversion from centimeters to Blender Scene units"""

    lengthUnit = context.scene.unit_settings.length_unit
    match lengthUnit:
        case 'METERS' | 'ADAPTIVE':
            return 1 / 100
        case 'KILOMETERS':
            return 1 / 100000
        case 'MILLIMETERS':
            return 10
        case 'MICROMETERS':
            return 10000
        case 'MILES':
            return 1 / 160900
        case 'FEET':
            return 1 / 30.48
        case 'INCHES':
            return 1 / 2.54
        case 'THOU': # thousandth of an inch
            return 393.7
        case 'CENTIMETERS':
            return 1
        case _:
            debug.printWarning(f"Unsupported Blender '{lengthUnit}' scaling unit")
            return 1

def getMetersToSceneUnitsMultiplier(context):
    """ Returns multiplier for conversion from meters to Blender Scene units"""
    return getCmToSceneUnitsMultiplier(context) * 100


def addEvent(event, func):
    if func not in event:
        event.append(func)


def delEvent(event, func):
    if func in event:
        event.remove(func)


def selectObject(ob):
    # When an active object is in 'EDIT' mode, execution of bpy.ops.object.select_all(action='DESELECT') raises an error.
    # Clearing the active object before such call prevents this problem.
    bpy.context.view_layer.objects.active = None
    
    bpy.ops.object.select_all(action='DESELECT')
    bpy.context.view_layer.objects.active = ob
    ob.select_set(True)


def isPreviewWorld(scene):
    return scene.layers[7]


def getSceneCamera(exporterCtx):
    """ Returns the active scene camera. """ 
    scene: bpy.types.Scene = bpy.context.scene if exporterCtx.preview else exporterCtx.dg.scene
    return scene.camera

def getActiveView3D():
    """ Returns SpaceView3D from the active screen or None if the active screen does not have View3D"""
    if area := next((a for a in bpy.context.screen.areas if a and a.type == 'VIEW_3D'), None):
        return next((space for space in area.spaces if space.type == 'VIEW_3D'), None)

    return None

def getFirstAvailableView3D():
    """ Returns SpaceView3D of the first available VIEW_3D area"""
    area=getActiveView3D()
    if area:
        return area

    # Check other screens
    if area := next((a for screen in bpy.data.screens for a in screen.areas if a.type == 'VIEW_3D'), None):
        return next((space for space in area.spaces if space.type == 'VIEW_3D'), None)

    return None

def getSpaceView3D(context : bpy.types.Context):
    """ If the context is 'VIEW_3D' returns its SpaceView3D """
    return context.space_data if context.area.type == 'VIEW_3D' else None


def getRegion3D(context : bpy.types.Context):
    """ If the context is 'VIEW_3D' returns its Region3D """

    if context.area.type == 'VIEW_3D':
        if not context.space_data.region_quadviews:
            return context.space_data.region_3d
        else:
            # Region3D from region_quadviews corresponding to the current context is returned
            # if quad view is enabled
            quadViewId = -1
            for region in context.area.regions:
                if region.type == 'WINDOW':
                    quadViewId += 1
                    if context.region == region:
                        return context.space_data.region_quadviews[quadViewId]
                   
    return None

def getFirstAvailableRegion3D():
    """ Returns Region3D of the first available VIEW_3D area"""
    if view3dSpace := getFirstAvailableView3D():
        return view3dSpace.region_3d


def generateVfbTheme(filepath):
    import mathutils

    def rgbToHex(color):
        return '#%X%X%X' % (int(color[0] * 255), int(color[1] * 255), int(color[2] * 255))

    currentTheme = bpy.context.preferences.themes[0]

    themeUI   = currentTheme.user_interface
    themeProp = currentTheme.properties

    back   = rgbToHex(themeProp.space.back)
    text   = rgbToHex(themeProp.space.text)
    header = rgbToHex(themeProp.space.header)

    buttonColorTuple = themeUI.wcol_tool.inner
    buttonColor = mathutils.Color((buttonColorTuple[0], buttonColorTuple[1], buttonColorTuple[2]))

    button = rgbToHex(buttonColor)

    buttonColor.v *= 1.35
    buttonHover = rgbToHex(buttonColor)

    shadow = rgbToHex(themeUI.wcol_tool.outline)

    pressed = rgbToHex(themeUI.wcol_radio.inner_sel)
    hover   = rgbToHex(themeUI.wcol_tool.inner_sel)

    rollout = rgbToHex(themeUI.wcol_box.inner)

    import xml.etree.ElementTree
    from xml.etree.ElementTree import Element, SubElement, tostring

    elVfb = Element("VFB")
    elTheme = SubElement(elVfb, "Theme")

    SubElement(elTheme, "style").text = "Standalone"

    # This is the color under the rendered image, just keep it dark.
    SubElement(elTheme, "appWorkspace").text   = "#222222"

    SubElement(elTheme, 'window').text         = back
    SubElement(elTheme, 'windowText').text     = text
    SubElement(elTheme, 'btnFace').text        = button
    SubElement(elTheme, 'btnFacePressed').text = pressed
    SubElement(elTheme, 'btnFaceHover').text   = buttonHover
    SubElement(elTheme, 'hover').text          = hover
    SubElement(elTheme, 'rollout').text        = rollout
    SubElement(elTheme, 'hiLight').text        = shadow
    SubElement(elTheme, 'darkShadow').text     = shadow

    tree = xml.etree.ElementTree.ElementTree(elVfb) 

    with open(filepath, 'wb') as f:
        tree.write(f, encoding='utf-8')

# @return True is the object is a camera
def isCamera(obj):
    return obj and obj.type == 'CAMERA'


def isCollection(obj):
    """ Return True if the passed object is a Blender Collection """
    return obj.bl_rna.identifier == 'Collection'


def getUIMousePos():
    """ Returns the mouse position relative to the current view """
    from vray_blender.operators import VRAY_OT_get_ui_mouse_position # circular import prevention

    bpy.ops.vray.get_ui_mouse_position('INVOKE_DEFAULT')
    return {"x":VRAY_OT_get_ui_mouse_position.pos_x, "y":VRAY_OT_get_ui_mouse_position.pos_y}


def replaceObjectMesh(ob: bpy.types.Object, newMesh: bpy.types.Mesh):
    """ Replace mesh geometry of an object without changing any other properties """
    bm = bmesh.new()
    bm.from_mesh(newMesh)
    bm.to_mesh(ob.data)
    ob.data.update()

    # Remove temp object
    bm.free()



# Shadowing is used to determine whether the value of an attribute has changed between
# successive exports. The so called 'shadow' attribute is a dupicate of the original attribute
# with a different (well-known) name which is used to store the original value from before
# the change. Shadow attributes are automatically created if options.shadowed is set to 'true'
# in the custom plugin description of the attribute.

def getShadowAttrName(attrName: str):
    """ REturn the name of an attribute's shadow attribute.

    Args:
        attrName (str): The name of the main attribute.

    Returns:
        str : The name of the shadow attribute.
    """
    return f"{attrName}_shadow_"

def getShadowAttr(data, attrName: str):
    """ Get the value of a shadow attribute given the main attribute's name """
    return getattr(data, getShadowAttrName(attrName))


def setShadowAttr(data, attrName: str, value):
    """ Set the value of a shadow attribute given the main attribute's name """
    setattr(data, getShadowAttrName(attrName), value)
    
    
def hasShadowedAttrChanged(data, attrName):
    """ Return True if the value of the attribute is not the same as the value of its shadow """
    return getShadowAttr(data, attrName) != getattr(data, attrName)


def updateShadowAttr(data, attrName):
    """ Update the value of a shadow attribute with the value of the main attribute """
    data[getShadowAttrName(attrName)] = getShadowAttr(data, attrName)