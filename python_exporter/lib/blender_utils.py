# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later


from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from vray_blender.lib.defs import ExporterContext
    
import bpy
from bpy_extras import anim_utils
import bmesh
import math
import mathutils
import re

from contextlib import contextmanager

from vray_blender import debug
from vray_blender.lib import lib_utils, path_utils
from vray_blender.ui.preferences import VRayExporterPreferences

VRAY_ASSET_TYPE = {
    "Proxy": "1",
    "Scene": "2"
}

ObjectPrefix = {
    'LIGHT'   : 'LA',
    'CAMERA' : 'CA',
}

NonGeometryTypes = {'LIGHT','LIGHT_PROBE','CAMERA','SPEAKER','ARMATURE','LATTICE','EMPTY'}


def iterExportedMeshAttributes(mesh: bpy.types.Mesh):
    """ Yield bpy.types.Attribute objects from `mesh.attributes` that the exporter
        ships as user attributes. Skips:
          - is_internal attributes (Blender's selection state, internal corner/edge maps, etc.)
          - UV layer names (exported separately via mesh.uv_layers)
          - "position"  - vertex positions, already exported as `vertices`
          - "sharp_face" - per-face shading flag, handled via normals/smoothing
          - layers with empty data (e.g. when the mesh is in edit mode)
        Single source of truth for both the exporter and the node-add menu.
    """
    uvNames = {l.name for l in mesh.uv_layers}
    for attr in mesh.attributes:
        if attr.is_internal:
            continue
        if attr.name in uvNames:
            continue
        if attr.name in {'position', 'sharp_face', 'material_index', 'custom_normal'}:
            continue
        if len(attr.data) == 0:
            continue
        yield attr

TypesThatSupportMaterial = {'MESH', 'CURVE', 'CURVES', 'SURFACE', 'FONT', 'META',
                            'GPENCIL', 'VOLUME', 'HAIR', 'POINTCLOUD'}

NC_WORLD    = 0
NC_MATERIAL = 1
NC_LAMP     = 3


def isNonGeometryExportedAsGeometry(ob) -> bool:
    """ True for objects whose type is not a geometry type (so the normal geometry
        iteration skips them) but which V-Ray still exports through the geometry
        pipeline: Empties flagged as Gaussian splats, infinite planes or perfect spheres.
        Add future "non-geometry object exported as geometry" cases here so they flow
        through the main export loop instead of needing a separate pass. """
    vray = ob.vray
    return ob.type == 'EMPTY' and (vray.isVRayGaussian or vray.isVRayInfinitePlane or vray.isVRayPerfectSphere)


def getActiveMaterial(ob):
    """ The active material of an object, or the material pointer of an Empty-backed geometry. """
    if ob is None:
        return None
    return ob.vray.material if isNonGeometryExportedAsGeometry(ob) else ob.active_material


def geometryObjectIt(objects):
    """ Iterates through each geometry object in the list, plus the non-geometry objects
        that V-Ray exports through the geometry pipeline (e.g. Gaussian splats). """
    for ob in objects:
        if ob.type not in NonGeometryTypes or isNonGeometryExportedAsGeometry(ob):
            yield ob


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
            if gr_name in bpy.data.collections:
                object_list.extend(bpy.data.collections[gr_name].objects)

    dupliGroup = []
    for ob in object_list:
        if ob.instance_type == 'COLLECTION' and ob.instance_collection:
            dupliGroup.extend(ob.instance_collection.objects)
    object_list.extend(dupliGroup)

    return object_list


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


def getSceneCamera(exporterCtx):
    """ Returns the active scene camera. """
    scene: bpy.types.Scene = bpy.context.scene if exporterCtx.preview else exporterCtx.dg.scene
    return scene.camera


def getFirstAvailableView3D():
    """ Returns SpaceView3D of the first available VIEW_3D area in the currently active screen"""
    if not (activeScreen := bpy.context.window.screen):
        return None

    for area in (a for a in activeScreen.areas if a and a.type == 'VIEW_3D'):
        # The currently active space is guaranteed to be the first one on the list
        if view3d := next((space for space in area.spaces if space.type == 'VIEW_3D'), None):
            return view3d
        
    return None


def getSpaceView3D(context : bpy.types.Context):
    """ If the context is 'VIEW_3D' returns its SpaceView3D """
    return context.space_data if context.area and context.area.type == 'VIEW_3D' else None


def isViewportRenderMode():
    """ Return True if any of the currently visible View3D spaces is
        in interactive render mode.
    """
    if not (activeScreen := bpy.context.window.screen):
        return False

    for area in (a for a in activeScreen.areas if a and a.type == 'VIEW_3D'):
        # The currently active space is guaranteed to be the first one on the list
        if any(space for space in area.spaces if space.type == 'VIEW_3D' and space.shading.type in ('RENDERED', 'MATERIAL')):
            return True
    
    return False


def generateVfbTheme(filepath):
    import mathutils

    def rgbToHex(color):
        return '#%02X%02X%02X' % (int(color[0] * 255), int(color[1] * 255), int(color[2] * 255))

    currentTheme = bpy.context.preferences.themes[0]

    themeUI   = currentTheme.user_interface
    themeProp = currentTheme.properties

    back   = rgbToHex(themeProp.space.back)
    text   = rgbToHex(themeProp.space.text)

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
    from xml.etree.ElementTree import Element, SubElement

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


def replaceObjectMesh(mesh: bpy.types.Mesh, newMesh: bpy.types.Mesh):
    """ Replace mesh geometry without changing any other properties """
    bm = bmesh.new()
    bm.from_mesh(newMesh)
    bm.to_mesh(mesh)
    mesh.update()
    bm.free()

def markPreferencesDirty(context: bpy.types.Context):
    """ Used to mark the preferences as dirty so auto save will work. This should be called
        on update by any preferences properties that are nested in some way. See compute_devices
        where the device enable state isn't auto-saved and VRayRenderNode where the name and
        address don't work with auto save.
    """
    context.preferences.is_dirty = True

def getVRayPreferences(context: bpy.types.Context = None) -> VRayExporterPreferences:
    if context is None:
        context = bpy.context
    return context.preferences.addons[VRayExporterPreferences.bl_idname].preferences

def showVRayPreferences():
    bpy.ops.preferences.addon_show(module=VRayExporterPreferences.bl_idname)

# Shadowing is used to determine whether the value of an attribute has changed between
# successive exports. The so called 'shadow' attribute is a dupicate of the original attribute
# with a different (well-known) name which is used to store the original value from before
# the change. Shadow attributes are automatically created if options.shadowed is set to 'true'
# in the custom plugin description of the attribute.

def getShadowAttrName(attrName: str):
    """ Return the name of an attribute's shadow attribute.

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


def getObjectOfData(data):
    """ Return the first object whose .data is `data`, or None.

        Lets a property update callback recover the owning object from its propgroup's id_data
        instead of relying on context.active_object - which is absent or points at the wrong
        object when the property is edited from the V-Ray Scene Lister's Preferences window.
    """
    if data is None:
        return None
    return next((o for o in bpy.data.objects if o.data is data), None)


def hasShadowedAttrChanged(data, attrName):
    """ Return True if the value of the attribute is not the same as the value of its shadow """
    return getShadowAttr(data, attrName) != getattr(data, attrName)


def updateShadowAttr(data, attrName):
    """ Update the value of a shadow attribute with the value of the main attribute """
    setattr(data, getShadowAttrName(attrName), getattr(data, attrName))


def printDepsgraphUpdates(dg: bpy.types.Depsgraph):
    """ Pretty-print the depsgraph update list.

        NOTE: This function is for debugging purposes only, do not call in production code.
    """

    print(f"======= DEPSGRAPH UPDATE START ======")
    try:
        for u in dg.updates:
            print(f"== ID: {u.id.name:<20} [{u.id.rna_type.bl_rna.name:<20}] Shading: {u.is_updated_shading:<5} Transform: {u.is_updated_transform:<5} Geo: {u.is_updated_geometry}")
    finally:
        pass

    print(f"======= DEPSGRAPH UPDATE END ======\n")


def tagUsersForUpdate(data: bpy.types.ID):
    """ Tags the V-Ray-related users of a data for update. """
    from vray_blender.nodes.tree import VRayNodeTreeObjectBase
    if not hasattr(bpy.data, 'user_map'):
        return
    users = bpy.data.user_map(subset={data})

    for user in users.get(data, []):
        if isinstance(user, bpy.types.Texture) or \
            isinstance(user, bpy.types.Light) or \
            isinstance(user, VRayNodeTreeObjectBase):
            # 1) V-Ray nodes uses the image only through a texture (check if that texture has a V-Ray user).
            # 2) Light data update tag doesn't initiate a light export, for this reason its parent object is tagged for update.
            # 3) "update_tag()" doesn't call "VRayNodeTreeObjectBase.update()", so we need to tag its users manually.
            tagUsersForUpdate(user)
        elif isinstance(user, bpy.types.Object) and (objUser := bpy.data.objects.get(user.name, None)):
            # Very often bpy.data.user_map points an invalid object, so we get it from the data directly just to be safe.
            objUser.update_tag()
        elif isinstance(user, bpy.types.Material):
            user.update_tag()
        # Note: No need to tag the world node tree for update, as it is always exported on depsgraph update.

def setFloatFrame(frameController: bpy.types.RenderEngine | bpy.types.Scene, frame: float):
    """ Set the frame of a render engine or scene to a float value. """
    from math import modf
    frac, whole = modf(frame)
    frameController.frame_set(int(whole), subframe=frac)

@contextmanager
def preserveInitialFrame(scene: bpy.types.Scene):
    """ Restore the scene's initial (scene.frame_current) when the block is exited.
    """
    savedFrame = scene.frame_current
    try:
        yield
    finally:
        setFloatFrame(scene, savedFrame)

def getFCurves(obj, ensure: bool = False):
    """ Return the fcurves collection on obj.animation_data.action across Blender versions.

        In 5.0+ fcurves live on a Channelbag reached via Slot -> Layer ->
        KeyframeStrip. When `ensure` is True, the slot/channelbag are created
        if missing (write side). When False, returns [] if any link in the
        chain is absent (read side).
    """
    anim = obj.animation_data
    if not anim or not anim.action:
        return []
    action = anim.action
    if bpy.app.version < (5, 0, 0):
        return action.fcurves

    slot = anim.action_slot
    if slot is None:
        if not ensure:
            return []
        idType = obj.id_type
        slot = anim_utils.action_get_first_suitable_slot(action, idType) \
               or action.slots.new(idType, "Slot")
        anim.action_slot = slot

    if ensure:
        channelbag = anim_utils.action_ensure_channelbag_for_slot(action, slot)
    else:
        channelbag = anim_utils.action_get_channelbag_for_slot(action, slot)

    return channelbag.fcurves if channelbag else []

def getViewLayerUseFCurve(viewLayerName: str):
    """ Returns the keyframes of the view layer enabled state """
    
    for fcurve in getFCurves(bpy.context.scene):
        if fcurve.data_path == f'view_layers["{viewLayerName}"].use':
            return fcurve

    return None

def isDefaultScene():
    return not bpy.data.filepath


def deleteOperatorHasBeenCalled():
    """ Checks if the delete operator has been called since the last depsgraph update"""

    deleteOperators = ('OUTLINER_OT_delete', 'OBJECT_OT_delete')
    return any(op.bl_idname in deleteOperators for op in bpy.context.window_manager.operators)


def getPropertyDefaultValue(propGroup, attrName:str):
    """ Returns the default value of a property in a property group """
    propDef = type(propGroup).__annotations__.get(attrName)
    return propDef.keywords['default']


def makeShadowCatcher(obj: bpy.types.Object, ensureMatteNode = True):
    """ Apply the shadow-catcher preset to an object's V-Ray object properties.

        This is the single definition of the preset. It matches V-Ray for Cinema 4D
        (VRayObjectProperties::makeShadowCatcher) and V-Ray for Maya
        (vrayCreateShadowCatcherProperties), which set these four properties and nothing else.

        'matte_surface' is not a stored value but a proxy for "a Matte Properties node is
        connected", backed by mattePropsSetter, which creates and links a new node on every
        assignment. Hence the guard, and hence ensureMatteNode=False for callers that are
        themselves such a node and so must not add a second one (VBLD-2432).
    """
    props = obj.vray.VRayObjectProperties

    if ensureMatteNode and not props.matte_surface:
        props.matte_surface = True

    props.shadows            = True
    props.affect_alpha       = True
    props.alpha_contribution = -1.0


def isShadowCatcher(obj: bpy.types.Object) -> bool:
    """ Check whether an object's V-Ray object properties currently match the
        shadow-catcher preset applied by makeShadowCatcher().
    """
    props = obj.vray.VRayObjectProperties
    return props.matte_surface and props.shadows and props.affect_alpha and props.alpha_contribution == -1.0


def revertShadowCatcher(obj: bpy.types.Object):
    """ Undo the shadow-catcher preset applied by makeShadowCatcher(), restoring
        the four properties it sets back to their plugin defaults.
    """
    props = obj.vray.VRayObjectProperties

    if props.matte_surface:
        props.matte_surface = False

    props.shadows            = False
    props.affect_alpha       = False
    props.alpha_contribution = 1.0


class TestBreak:
    """ Abort a render job in V-Ray when the user aborts it from the UI. """
    class Exception(Exception):
        pass

    @staticmethod
    def check(exporterCtx: ExporterContext):
        if exporterCtx.production:
            from vray_blender.engine.renderer_prod import VRayRendererProd
            VRayRendererProd.testBreak(exporterCtx.engine)
    

def isMaterialAssignedToObject(materialName: str, obj: bpy.types.Object):
    """ Return True if the object has the specified material assigned """
    
    if not materialName:
        return False

    for slot in obj.material_slots:
        if slot.material and slot.material.name == materialName:
            return True

    return False


def checkAndReportVersionIncompatibility():
    sceneMajor, sceneMinor = bpy.data.version[:2]
    appMajor   = bpy.app.version[0]

    if sceneMajor >= 5 and appMajor < 5:
        debug.printError(f"Scene was saved with a newer version of Blender ({sceneMajor}, {sceneMinor})" +
                         " which is not backward compatible. Expect loss of data.")
        

def resolveNodeFromPath(fullPythonPath: str):
    """ Resolves a full Python string path back into a Blender Node object.
        
        Args:
            fullPythonPath(str) : e.g. bpy.data.materials['Material'].node_tree.nodes['Image Texture']
    """
    try:
        # Pattern looks for: bpy.data.{collection}['{name}']
        match = re.search(r"bpy\.data\.([a-z_]+)\[['\"](.+?)['\"]\]", fullPythonPath)
        
        if not match:
            return None
            
        collectionName = match.group(1)
        itemName = match.group(2)         
        
        if not hasattr(bpy.data, collectionName):
            debug.printError(f"Collection '{collectionName}' not found in bpy.data.")
            return None
            
        dataCollection = getattr(bpy.data, collectionName)
        
        if itemName not in dataCollection:
            return None
            
        idBlock = dataCollection[itemName]
        
        # Get the relative path inside the ID block
        # bpy.data.materials['Material'].node_tree... -> becomes 'node_tree...'
        pathStartIdx = fullPythonPath.find("]", match.end(0) - 1) + 2
        relativePath = fullPythonPath[pathStartIdx:]
        
        try:
            return idBlock.path_resolve(relativePath)
        except ValueError:
            return None

    except Exception as e:
        debug.printExceptionInfo(e, f"blender_utils.resolveNodeFromPath('{fullPythonPath}')")
        return None
    

def getFullPathToNode(node: bpy.types.Node):
    """ Returns the full Python path to a node in a node tree """

    tree = node.id_data
    relPath = node.path_from_id()
    
    # Check if the tree is a standalone NodeGroup
    if tree.library or tree in bpy.data.node_groups.values():
        return f"bpy.data.node_groups['{tree.name}'].{relPath}"
    
    # Check if it belongs to a material
    for mat in bpy.data.materials:
        if mat.node_tree == tree:
            return f"bpy.data.materials['{mat.name}'].node_tree.{relPath}"
            
    # Check if it belongs to a world
    for world in bpy.data.worlds:
        if world.node_tree == tree:
            return f"bpy.data.worlds['{world.name}'].node_tree.{relPath}"
        
    # Check if it belongs to a light
    for light in bpy.data.lights:
        if light.node_tree == tree:
            return f"bpy.data.lights['{light.name}'].node_tree.{relPath}"

    return None


def getPinnedDataFromEditorContext(context: bpy.types.Context, fallback):
    """ Returns the data-block whose node editor is currently pinned, or 'fallback'
        if the editor is not pinned. The pinned data-block is the one whose node tree
        is shown in the editor regardless of the active selection.
    """
    snode = context.space_data
    if isinstance(snode, bpy.types.SpaceNodeEditor) and snode.pin and snode.id_from:
        return snode.id_from
    return fallback