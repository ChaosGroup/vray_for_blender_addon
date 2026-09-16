# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
import math
import os
from pathlib import PurePath

from vray_blender import debug
from vray_blender import features
from vray_blender.features import Feature
from vray_blender.engine.renderer_ipr_viewport import VRayRendererIprViewport
from vray_blender.lib import blender_utils, lib_utils
from vray_blender.lib.path_utils import tryGetRelativePath
from vray_blender.nodes.operators.import_file import importProxyFromMeshFile
from vray_blender.nodes import tree_defaults
from vray_blender.nodes import utils as NodesUtils
from vray_blender.operators import VRAY_OT_render, VRAY_OT_render_interactive
from vray_blender.ui import classes, icons
from vray_blender.menu import VRAY_OT_open_vfb
from vray_blender.vray_tools import vray_proxy
from vray_blender.exporting.tools import GEOMETRY_OBJECT_TYPES, MESH_OBJECT_TYPES
from vray_blender.exporting.update_tracker import UpdateFlags, UpdateTarget, UpdateTracker
from vray_blender.plugins.geometry.VRayDecal import createDecalObject, generateDecalPreviewMesh
from vray_blender.proxy import VRAY_SCENE_FILTER_GLOB, VRAY_PROXY_FILTER_GLOB

from vray_blender.bin import VRayBlenderLib as vray
from vray_blender.lib.mixin import VRayOperatorBase

# The camera operators below all act on the 3D viewport, so they should be greyed out
# instead of throwing when invoked from another editor (e.g. through the F3 search).
def _pollCameraOperator(cls, context: bpy.types.Context):
    return classes.pollEngine(context) and context.space_data and context.space_data.type == 'VIEW_3D'


class VRAY_OT_set_view(VRayOperatorBase):
    bl_idname = "vray.set_view"
    bl_label = "Set View"

    view_type: bpy.props.StringProperty(default='TOP')

    @classmethod
    def poll(cls, context):
        return _pollCameraOperator(cls, context)

    def execute(self, context):
        bpy.ops.view3d.view_axis(type=self.view_type, align_active=False)
        return {'FINISHED'}


class VRAY_OT_set_camera(VRayOperatorBase):
    bl_idname = "vray.set_camera"
    bl_label = "Set Active Camera"
    bl_options = { "UNDO" }

    camera: bpy.props.StringProperty()

    @classmethod
    def poll(cls, context):
        return _pollCameraOperator(cls, context)

    def execute(self, context):
        if not (camera := context.scene.objects.get(self.camera)):
            return {'CANCELLED'}

        context.scene.camera = camera
        if context.area.spaces[0].region_3d.view_perspective not in {'CAMERA'}:
            bpy.ops.view3d.view_camera()
        return {'FINISHED'}


class VRAY_OT_select_camera(VRayOperatorBase):
    bl_idname = "vray.select_camera"
    bl_label = "Select Active Camera"

    @classmethod
    def poll(cls, context):
        return _pollCameraOperator(cls, context)

    def execute(self, context):
        if not context.scene.camera:
            return {'CANCELLED'}

        bpy.ops.object.select_camera()
        return {'FINISHED'}


class VRAY_OT_camera_lock_unlock_view(VRayOperatorBase):
    bl_idname = "vray.camera_lock_unlock_view"
    bl_label = "Lock / Unlock Camera To View"

    @classmethod
    def poll(cls, context):
        return _pollCameraOperator(cls, context)

    def execute(self, context):
        context.space_data.lock_camera = not context.space_data.lock_camera
        return {'FINISHED'}


####### OPERATORS FOR LIGHT CREATION ##########
###############################################

class VRAY_OT_add_object_vray_light(VRayOperatorBase):
    bl_idname = "vray.add_object_vray_light"
    bl_label = "Add V-Ray Light"
    bl_options = { "UNDO" }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.lightType = "BLENDER"
        self.lightName = "VRayLight"

    def _createLightObject(self):
        blenderType = lib_utils.LightVrayTypeToBlender[self.lightType]
        lightData = bpy.data.lights.new(name=self.lightName , type=blenderType)
        lightData.vray.light_type = self.lightType

        if hasattr(self, "_initLight"):
            self._initLight(lightData)

        lightObj = bpy.data.objects.new(name=self.lightName, object_data=lightData)
        lightObj.location = bpy.context.scene.cursor.location
        return lightObj

    def execute(self, context):
        lightObject = self._createLightObject()
        bpy.context.collection.objects.link(lightObject)

        # Deselect all objects and select only the newly created as it is with the default blender objects
        blender_utils.selectObject(lightObject)

        return {'FINISHED'}


class VRAY_OT_add_object_vray_light_ambient(VRAY_OT_add_object_vray_light):
    bl_idname = "vray.add_object_vray_light_ambient"
    bl_label = "V-Ray Ambient Light"
    bl_description = "V-Ray Ambient Light"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.lightType = "AMBIENT"
        self.lightName = "VRayLightAmbient"


class VRAY_OT_add_object_vray_light_direct(VRAY_OT_add_object_vray_light):
    bl_idname = "vray.add_object_vray_light_direct"
    bl_label = "V-Ray Direct Light"
    bl_description = "V-Ray Direct Light"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.lightType = "DIRECT"
        self.lightName = "VRayLightDirect"


class VRAY_OT_add_object_vray_light_ies(VRAY_OT_add_object_vray_light):
    bl_idname = "vray.add_object_vray_light_ies"
    bl_label = "V-Ray IES Light"
    bl_description = "V-Ray IES Light"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.lightType = "IES"
        self.lightName = "VRayIESLight"


class VRAY_OT_add_object_vray_light_mesh(VRAY_OT_add_object_vray_light):
    bl_idname = "vray.add_object_vray_light_mesh"
    bl_label = "V-Ray Mesh Light"
    bl_description = "V-Ray Mesh Light"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.lightType = "MESH"
        self.lightName = "VRayLightMesh"


class VRAY_OT_create_mesh_light(VRAY_OT_add_object_vray_light_mesh):
    """ Create a mesh light for the selected geometry objects. Unlike the plain 'add light'
        operator, it fills in the light's geometry selector with the current selection.
    """
    bl_idname = "vray.create_mesh_light"
    bl_label = "Create Mesh Light"
    bl_description = "Create a V-Ray Mesh Light from the selected geometry objects"

    def execute(self, context):
        # Any object V-Ray exports as geometry can light up a mesh light, not just meshes.
        # This is also what the light's own geometry selector accepts (filters.filterGeometries).
        geometryObjects = [obj for obj in context.selected_objects if obj.type in GEOMETRY_OBJECT_TYPES]

        if not geometryObjects:
            self.report({'WARNING'}, "No object selected, please select geometry to create a mesh light")
            return {'CANCELLED'}

        lightObject = self._createLightObject()
        context.collection.objects.link(lightObject)

        # The light has no node tree yet, so set the selection on the light data property group.
        # It is copied to the node's property group when the tree is created (addLightNodeTree).
        for obj in geometryObjects:
            lightObject.data.vray.LightMesh.object_selector.addListItem(context, obj)

        blender_utils.selectObject(lightObject)

        return {'FINISHED'}


class VRAY_OT_add_object_vray_light_luminaire(VRAY_OT_add_object_vray_light):
    bl_idname = "vray.add_object_vray_light_luminaire"
    bl_label = "V-Ray Luminaire Light"
    bl_description = "V-Ray Luminaire Light. Emits the light of a whole fixture baked into a " \
                     "luminaire cache file, as shipped with Chaos Cosmos light assets"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.lightType = "LUMINAIRE"
        self.lightName = "VRayLuminaireLight"


class VRAY_OT_add_object_vray_light_omni(VRAY_OT_add_object_vray_light):
    bl_idname = "vray.add_object_vray_light_omni"
    bl_label = "V-Ray Omni Light"
    bl_description = "V-Ray Omni Light"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.lightType = "OMNI"
        self.lightName = "VRayOmniLight"


class VRAY_OT_add_object_vray_light_sphere(VRAY_OT_add_object_vray_light):
    bl_idname = "vray.add_object_vray_light_sphere"
    bl_label = "V-Ray Sphere Light"
    bl_description = "V-Ray Sphere Light"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.lightType = "SPHERE"
        self.lightName = "VRaySphereLight"


class VRAY_OT_add_object_vray_light_spot(VRAY_OT_add_object_vray_light):
    bl_idname = "vray.add_object_vray_light_spot"
    bl_label = "V-Ray Spot Light"
    bl_description = "V-Ray Spot Light"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.lightType = "SPOT"
        self.lightName = "VRaySpotLight"


    def _initLight(self, light: bpy.types.Light):
        propGroup = light.vray.LightSpot

        light.spot_size  = propGroup.coneAngle
        light.spot_blend = max(-propGroup.penumbraAngle / propGroup.coneAngle, 0)
        light.show_cone  = propGroup.show_cone


class VRAY_OT_add_object_vray_light_sun_base(VRAY_OT_add_object_vray_light):
    # Base class for sun objects creation.
    # It must be a separate, unregistered operator class,
    # or the derived operators will not be executed.

    def _createSunAndTarget(self, collectionName="VRaySunLight"):
        from math import radians
        # Sun Object
        lightObject = self._createLightObject()
        lightObject.location = (5.0, 5.0, 5.0)

        # Empty target Object
        targetObject = bpy.data.objects.new("VRaySunLightTarget", None)
        targetObject.empty_display_size = 0.5
        targetObject.empty_display_type = 'CIRCLE'
        targetObject.rotation_euler = (radians(90), 0, 0)

        # Creation of collection for lightObject and targetObject
        lightCollection = bpy.data.collections.new(collectionName)
        bpy.context.scene.collection.children.link(lightCollection)
        lightCollection.objects.link(lightObject)
        lightCollection.objects.link(targetObject)

        constraint = lightObject.constraints.new(type='TRACK_TO')
        constraint.target = targetObject

        return targetObject, lightObject


class VRAY_OT_add_object_vray_light_sun(VRAY_OT_add_object_vray_light_sun_base):
    bl_idname = "vray.add_object_vray_light_sun"
    bl_label = "V-Ray Sun Light"
    bl_description = "V-Ray Sun Light"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.lightType = "SUN"
        self.lightName = "VRaySunLight"

    def execute(self, context):
        targetObject, _ = self._createSunAndTarget("VRaySunLight")
        # Deselect all objects and select only the newly created as it is with the default blender objects
        blender_utils.selectObject(targetObject)

        return {'FINISHED'}

class VRAY_OT_add_object_vray_sun_sky(VRAY_OT_add_object_vray_light_sun_base):
    bl_idname = "vray.add_object_vray_sun_sky"
    bl_label = "V-Ray Sun And Sky"
    bl_description = "V-Ray Sun And Sky"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.lightType = "SUN"
        self.lightName = "VRaySunLight"

    def execute(self, context):
        if not context.scene.world:
            context.scene.world = bpy.data.worlds.new('World')
        world = context.scene.world
        
        _, lightObject = self._createSunAndTarget("VRaySunSky")

        if not world.vray.is_vray_class:
            tree_defaults.addWorldNodeTree(world)

        worldTree = world.node_tree
        envNode = NodesUtils.getNodeByType(worldTree, 'VRayNodeEnvironment')

        skyTexNode = worldTree.nodes.new("VRayNodeTexSky")
        skyTexNode.TexSky.sun_select.boundPropObj = lightObject
        skyTexNode.TexSky.sun_select.boundPropObjName = lightObject.name

        if envNode:
            skyTexNode.location.y = envNode.location.y - 20
            skyTexNode.location.x = envNode.location.x - skyTexNode.bl_width_default - 50

            linkedNodes = set()
            for sock in envNode.inputs:
                if sock.name == "Secondary Matte":
                    continue
                if sock.is_linked:
                    linkedNodes.add(sock.links[0].from_node)
                    envNode.id_data.links.remove(sock.links[0])
                worldTree.links.new(skyTexNode.outputs[0], sock)
                sock.use = True

            # Move any previously linked nodes to the left of 'skyTexNode'
            for node in linkedNodes:
                node.location.x = skyTexNode.location.x - node.bl_width_default - 50
                node.select = False

        return {'FINISHED'}


class VRAY_OT_add_object_vray_light_rect(VRAY_OT_add_object_vray_light):
    bl_idname = "vray.add_object_vray_light_rect"
    bl_label = "V-Ray Rect Light"
    bl_description = "V-Ray Rect Light"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.lightType = "RECT"
        self.lightName = "VRayRectLight"

    def _initLight(self, lightData: bpy.types.Light):
        # Make the area light bigger because the default one is too small
        lightData.size = 1
        lightData.size_y = 1
        lightData.shape = 'RECTANGLE'


class VRAY_OT_add_object_vray_light_dome(VRAY_OT_add_object_vray_light):
    bl_idname = "vray.add_object_vray_light_dome"
    bl_label = "V-Ray Dome Light"
    bl_description = "V-Ray Dome Light"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.lightType = "DOME"
        self.lightName = "VRayDomeLight"


def getLightAddOperators():
    """ Every V-Ray light, in V-Ray > Lights menu order; also what gets registered. The Add
        (Shift+A) menu shows only the subset in getMenuLightAddOperators().
    """
    return (
        VRAY_OT_add_object_vray_light_rect,
        VRAY_OT_add_object_vray_light_sphere,
        VRAY_OT_add_object_vray_light_dome,
        VRAY_OT_add_object_vray_light_mesh,
        VRAY_OT_add_object_vray_light_ies,
        VRAY_OT_add_object_vray_light_sun,
        VRAY_OT_add_object_vray_sun_sky,
        VRAY_OT_add_object_vray_light_spot,
        VRAY_OT_add_object_vray_light_omni,
        VRAY_OT_add_object_vray_light_direct,
        VRAY_OT_add_object_vray_light_ambient,
        VRAY_OT_add_object_vray_light_luminaire,
    )

def addVRayLightsToMenu(self, context):
    """ Callback to add all V-Ray lights to a menu. To be used with Menu.append() """

    # List operators after which to draw a separator in the menu
    separatorsAfter  = [VRAY_OT_add_object_vray_sun_sky]

    # Forbid creation of lights in Edit mode (it can produce errors).
    classes.disableLayoutInEditMode(self.layout, context)

    if classes.pollEngine(context):
        for op in getLightAddOperators():
            self.layout.operator(op.bl_idname, text=op.bl_label, icon_value=icons.getUIIcon(op))
            if op in separatorsAfter:
                self.layout.separator()


####### LIGHT ADJUSTMENT FROM THE OBJECT CONTEXT MENU ##########
################################################################

# The 'Adjust ...' items shown for a V-Ray light in the object context menu, per light plugin
# type: (attribute, label, input scale, proportional drag), with None standing for a separator.
# Blender's own items drive native light properties (data.energy, data.shadow_soft_size,
# data.angle, ...) which V-Ray does not read, so they are replaced by these (VBLD-2673). The
# exception is the area light size, which V-Ray does read off the Blender light - see
# light_export._setLightRectLightAttrs.
#
# The item order and grouping follow Blender's own for the same light: the power first, then
# the size / radius, then the spot cone angles in a group of their own.
#
# The intensities use a proportional drag: their magnitude depends on the photometric unit, so
# a single additive step is either unusably coarse (Default) or imperceptible (Lumens).
_LIGHT_ADJUST_ATTRS = {
    'LightAmbient'    : (('intensity',            "Light Intensity",            0.01, True),
                         ('shadowRadius',         "Shadow Radius",              0.01, False)),
    'LightDome'       : (('intensity',            "Light Intensity",            0.01, True),),
    'LightIES'        : (('power',                "Light Power",                10.0, False),
                         ('ies_light_diameter',   "Light Diameter",             0.01, False)),
    'LightMesh'       : (('intensity',            "Light Intensity",            0.01, True),),
    'LightOmni'       : (('intensity',            "Light Intensity",            0.01, True),
                         ('shadowRadius',         "Shadow Radius",              0.01, False)),
    'LightRectangle'  : (('intensity',            "Light Intensity",            0.01, True),),
    'LightSphere'     : (('intensity',            "Light Intensity",            0.01, True),
                         ('radius',               "Light Radius",               0.01, False)),
    'LightSpot'       : (('intensity',            "Light Intensity",            0.01, True),
                         ('shadowRadius',         "Shadow Radius",              0.01, False),
                         None,
                         ('coneAngle',            "Spot Light Beam Angle",      0.01, False),
                         ('penumbraAngle',        "Spot Light Penumbra Angle",  0.01, False)),
    'MayaLightDirect' : (('intensity',            "Light Intensity",            0.01, True),
                         ('beamRadius',           "Beam Radius",                0.01, False),
                         ('shadowRadius',         "Shadow Radius",              0.01, False)),
    'SunLight'        : (('intensity_multiplier', "Light Intensity",            0.01, True),
                         ('size_multiplier',      "Sun Light Size",             0.01, False)),
}


class VRAY_OT_adjust_light_prop(VRayOperatorBase):
    """ Adjust a V-Ray light property with a mouse drag.

        Modelled on wm.context_modal_mouse, but the property is resolved per light through
        getLightPropGroup() instead of a fixed RNA path, so it reaches the values that are
        actually exported for both node and non-node lights.
    """
    bl_idname  = "vray.adjust_light_prop"
    bl_label   = "Adjust V-Ray Light Property"
    bl_options = {'GRAB_CURSOR', 'BLOCKING', 'UNDO', 'INTERNAL'}

    attr_name:   bpy.props.StringProperty(options={'SKIP_SAVE'})
    prop_label:  bpy.props.StringProperty(options={'SKIP_SAVE'})
    input_scale: bpy.props.FloatProperty(default=0.01, options={'SKIP_SAVE'})
    relative:    bpy.props.BoolProperty(default=False, options={'SKIP_SAVE'})
    initial_x:   bpy.props.IntProperty(options={'HIDDEN'})

    def invoke(self, context, event):
        self._targets = []

        for obj in context.selected_editable_objects:
            if (obj.type != 'LIGHT') or not hasattr(obj.data, 'vray'):
                continue

            propGroup = lib_utils.getLightPropGroup(obj.data, lib_utils.getLightPluginType(obj.data))
            if (value := getattr(propGroup, self.attr_name, None)) is None:
                continue

            self._targets.append((obj, propGroup, value))

        if not self._targets:
            self.report({'WARNING'}, f"No selected V-Ray light has a '{self.attr_name}' property")
            return {'CANCELLED'}

        # Angles are stored in radians but shown in degrees, both in the property pages
        # and in the header text below.
        self._isAngle = self._targets[0][1].bl_rna.properties[self.attr_name].subtype == 'ANGLE'

        self.initial_x = event.mouse_x
        self._prevX = event.mouse_x
        self._precisionOffset = 0.0

        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        # Factor for SHIFT precision tweaking, matching wm.context_modal_mouse.
        PRECISION_FAC = 0.05

        match event.type:
            case 'MOUSEMOVE':
                if event.shift:
                    self._precisionOffset += event.mouse_x - self._prevX
                self._prevX = event.mouse_x

                offset = (event.mouse_x - self.initial_x) - self._precisionOffset * (1.0 - PRECISION_FAC)
                delta = offset * self.input_scale

                self._applyDelta(delta)
                self._setHeaderText(context, delta)

            case 'LEFTMOUSE':
                context.area.header_text_set(None)
                return {'FINISHED'}

            case 'RIGHTMOUSE' | 'ESC':
                self._applyDelta(0.0)
                context.area.header_text_set(None)
                return {'CANCELLED'}

        return {'RUNNING_MODAL'}

    def _applyDelta(self, delta: float):
        for obj, propGroup, valueOrig in self._targets:
            if self.relative and (valueOrig != 0.0):
                # Proportional drag, in stops: an input scale of 0.01 doubles the value
                # over 100 pixels regardless of the unit it is expressed in. A zero start
                # value has no proportion to scale, so it falls back to the additive drag.
                value = valueOrig * (2.0 ** delta)
            else:
                value = valueOrig + delta

            setattr(propGroup, self.attr_name, value)

            # The property's own update callback only tags the active object, so tag every
            # light we touch or the rest of a multi-selection is not re-exported during IPR.
            UpdateTracker.tagUpdate(obj.data, UpdateTarget.LIGHT, UpdateFlags.DATA)
            obj.update_tag()

    def _setHeaderText(self, context, delta: float):
        if len(self._targets) > 1:
            context.area.header_text_set(f"{self.prop_label}: {delta:.3f} (delta)")
            return

        value = getattr(self._targets[0][1], self.attr_name)

        if self._isAngle:
            context.area.header_text_set(f"{self.prop_label}: {math.degrees(value):.2f} deg")
        else:
            context.area.header_text_set(f"{self.prop_label}: {value:.3f}")


def _drawNativeLightAdjustItem(layout: bpy.types.UILayout, label: str, dataPath: str):
    """ Draw a stock 'Adjust ...' item driving a native Blender light property. """
    props = layout.operator("wm.context_modal_mouse", text=f"Adjust {label}")
    props.data_path_iter = "selected_editable_objects"
    props.data_path_item = dataPath
    props.header_text = f"{label}: %.3f"


def _drawLightAdjustItems(layout: bpy.types.UILayout, light: bpy.types.Light):
    """ Draw the 'Adjust ...' items for a V-Ray light, in place of Blender's own. """
    pluginType = lib_utils.getLightPluginType(light)
    propGroup  = lib_utils.getLightPropGroup(light, pluginType)

    for item in _LIGHT_ADJUST_ATTRS.get(pluginType, ()):
        if item is None:
            layout.separator()
            continue

        attrName, label, inputScale, relative = item
        if attrName not in propGroup.bl_rna.properties:
            continue

        props = layout.operator(VRAY_OT_adjust_light_prop.bl_idname, text=f"Adjust {label}")
        props.attr_name   = attrName
        props.prop_label  = label
        props.input_scale = inputScale
        props.relative    = relative

    if light.type == 'AREA':
        # A disc light has a single dimension, the other one is derived from it
        # (see light_export._fixBlenderRectLight and LightRectangle.onUpdateWidth).
        if light.shape in ('SQUARE', 'DISK') or getattr(propGroup, 'is_disc', False):
            _drawNativeLightAdjustItem(layout, "Area Light Size", "data.size")
        else:
            _drawNativeLightAdjustItem(layout, "Area Light X Size", "data.size")
            _drawNativeLightAdjustItem(layout, "Area Light Y Size", "data.size_y")

    # Divider from the rest of the menu. Blender's own is dropped together with the items
    # it grouped, see _ObjectContextMenuLayout.
    layout.separator()


class _DiscardedOperatorProps:
    """ Stand-in for the properties object returned by UILayout.operator(), for the menu
        items dropped by _ObjectContextMenuLayout. It absorbs the caller's assignments.
    """
    def __setattr__(self, name, value):
        pass


class _ObjectContextMenuLayout:
    """ Wraps UILayout to replace the native light 'Adjust ...' items of the object context
        menu with the V-Ray ones: the first native item is swapped for the whole V-Ray set
        and the rest are dropped, along with the separators that grouped them - those would
        otherwise all pile up after the replacement. _drawLightAdjustItems draws its own.
    """

    def __init__(self, real, light):
        object.__setattr__(self, "_real", real)
        object.__setattr__(self, "_light", light)
        object.__setattr__(self, "_inLightSection", False)

    def operator(self, idname, *args, **kwargs):
        if idname == "wm.context_modal_mouse":
            if not self._inLightSection:
                object.__setattr__(self, "_inLightSection", True)
                _drawLightAdjustItems(self._real, self._light)

            return _DiscardedOperatorProps()

        # The light section holds nothing but the adjust items, so any other operator marks
        # its end. From there on the separators belong to the rest of the menu.
        object.__setattr__(self, "_inLightSection", False)
        return self._real.operator(idname, *args, **kwargs)

    def separator(self, *args, **kwargs):
        if not self._inLightSection:
            self._real.separator(*args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._real, name)

    def __setattr__(self, name, value):
        # Forward attribute writes (e.g. operator_context) to the wrapped layout.
        setattr(self._real, name, value)


_originalObjectContextMenuDraw = None


def _patchedObjectContextMenuDraw(self, context):
    obj = context.object

    if classes.pollEngine(context) and obj and (obj.type == 'LIGHT') and hasattr(obj.data, 'vray'):
        layout = _ObjectContextMenuLayout(self.layout, obj.data)
        _originalObjectContextMenuDraw(_MenuWithLayout(self, layout), context)
    else:
        _originalObjectContextMenuDraw(self, context)


def register_object_context_menu_draw():
    """ Substitute the patched draw function for Blender's own in the object context menu.

        The substitution is made in the menu's list of draw functions rather than by assigning
        to VIEW3D_MT_object_context_menu.draw, so that it composes with the prepend() call in
        menu.py and with any append() made by another add-on: assigning to 'draw' would discard
        the dispatcher Blender installs for those, and with it their menu items.
    """
    global _originalObjectContextMenuDraw

    menu = bpy.types.VIEW3D_MT_object_context_menu
    drawFuncs = menu._dyn_ui_initialize()

    # Blender's own draw is the one defined alongside the menu class. It is not necessarily
    # the first entry in the list - a prepend() inserts ahead of it.
    index = next(i for i, f in enumerate(drawFuncs) if f.__module__ == menu.__module__)

    _originalObjectContextMenuDraw = drawFuncs[index]
    drawFuncs[index] = _patchedObjectContextMenuDraw


def unregister_object_context_menu_draw():
    global _originalObjectContextMenuDraw

    if _originalObjectContextMenuDraw is not None:
        drawFuncs = bpy.types.VIEW3D_MT_object_context_menu._dyn_ui_initialize()
        drawFuncs[drawFuncs.index(_patchedObjectContextMenuDraw)] = _originalObjectContextMenuDraw
        _originalObjectContextMenuDraw = None


####### OPERATORS FOR CAMERA CREATION #########
###############################################

class VRAY_OT_add_physical_camera(VRayOperatorBase):
    bl_idname = "vray.add_physical_camera"
    bl_label = "Add V-Ray Physical Camera"
    bl_description = "Add V-Ray Physical Camera"
    bl_options = { "UNDO" }

    def execute(self, context):
        import math

        camera = bpy.data.cameras.new(name='Physical Camera')
        camera.vray['use_physical'] = True
        camera.vray.CameraPhysical['use'] = True

        cameraObj = bpy.data.objects.new(name='Physical Camera', object_data=camera)
        cameraObj.location = context.scene.cursor.location
        # Rotate the same as Blender camera
        cameraObj.rotation_euler = (math.radians(60), 0, math.radians(45))
        bpy.context.collection.objects.link(cameraObj)

        # Deselect all objects and select only the newly created as it is with the default blender objects
        blender_utils.selectObject(cameraObj)

        return {'FINISHED'}


####### OPERATORS FOR PROXY CREATION ##########
###############################################

class VRAY_OT_add_object_proxy(VRayOperatorBase):
    """ Show FileSelect dialog for file types supported by GeomMeshFile and import the selected file """

    bl_idname = "vray.add_object_proxy"
    bl_label = "Add V-Ray Proxy"
    bl_description = "Import V-Ray Proxy object."
    bl_options = { "UNDO" }

    filter_glob: bpy.props.StringProperty(
        default=VRAY_PROXY_FILTER_GLOB,
        options={'HIDDEN'}
    )

    filepath: bpy.props.StringProperty(name=f"Filepath ({VRAY_PROXY_FILTER_GLOB})", subtype="FILE_PATH")
    relpath: bpy.props.BoolProperty(name="Use Relative Path", default=True)
    unit_scale: bpy.props.FloatProperty(
        name="Unit scale",
        description="The unit scale to apply during import",
        subtype='DISTANCE',
        default=1.0,
        min=0.0001,
        max=10000.0
    )
    
    def draw(self, context):
        layout = self.layout
        
        # If the scene has not been saved yet, we cannot use relative paths
        if not blender_utils.isDefaultScene():
            layout.prop(self, 'relpath')
        
        layout.prop(self, "unit_scale", text='Unit scale')

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        matPath =  "" if vray_proxy.isAlembicFile(self.filepath) else PurePath(self.filepath).with_suffix(".vrmat").as_posix()

        useRelPath = (not blender_utils.isDefaultScene()) and self.relpath
        with NodesUtils.DisableAutoConnect():
            _, err = importProxyFromMeshFile(context, matPath, self.filepath, useRelPath=useRelPath, scaleUnit=self.unit_scale)

            if err:
                self.report({'ERROR'}, err)
                return {'CANCELLED'} 

        return {'FINISHED'}

class VRAY_OT_add_object_fur(VRayOperatorBase):
    """Add a V-Ray Fur object (Empty with isVRayFur=True)"""

    bl_idname = "vray.add_object_fur"
    bl_label = "Add V-Ray Fur"
    bl_description = "Create an Empty object and set V-Ray Fur property"
    bl_options = {'UNDO'}

    def execute(self, context):
        furData = bpy.data.hair_curves.new('V-Ray Fur')
        furObj = bpy.data.objects.new("V-Ray Fur", furData)
        furObj.vray.isVRayFur = True
        context.collection.objects.link(furObj)

        # Add all selected objects to the fur object.
        for obj in context.selected_objects:
            if obj.type in MESH_OBJECT_TYPES:
                furData.vray.GeomHair.object_selector.addListItem(context, obj)

        # Deselect all, select only the new object
        blender_utils.selectObject(furObj)

        return {'FINISHED'}



class VRAY_OT_add_object_vrayscene(VRayOperatorBase):
    """ Show FileSelect dialog for .vrscene files and import the selected file """

    bl_idname = "vray.add_object_vrayscene"
    bl_label = "Add V-Ray Scene"
    bl_description = "Import V-Ray Scene object. Only available in Solid viewport mode"
    bl_options = { "UNDO" }

    filter_glob: bpy.props.StringProperty(
        default=VRAY_SCENE_FILTER_GLOB,
        options={'HIDDEN'}
    )

    filepath: bpy.props.StringProperty(name=f"Filepath ({VRAY_SCENE_FILTER_GLOB})", subtype="FILE_PATH")
    relpath: bpy.props.BoolProperty(name="Use Relative Path", default=True)

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        if not self.filepath:
            return {'CANCELLED'}

        absFilePath = os.path.normpath(bpy.path.abspath(self.filepath))
        if not os.path.exists(absFilePath):
            self.report({'ERROR'}, "File not found!")
            return {'CANCELLED'}

        fileExt = PurePath(absFilePath).suffix
        if fileExt != '.vrscene' and not fileExt.startswith(".usd"):
           self.report({'ERROR'}, f"File format {fileExt} is not supported by V-Ray Scene")
           return {'CANCELLED'}

        # Create a new mesh object to represent the scene
        name = f"VRayScene@{PurePath(absFilePath).stem}"

        mesh = bpy.data.meshes.new(name)
        ob = bpy.data.objects.new(name, mesh)
        ob.location = context.scene.cursor.location

        context.scene.collection.objects.link(ob)

        useRelPath = (not blender_utils.isDefaultScene()) and self.relpath
        vrayScene = ob.data.vray.VRayScene

        ob.vray.VRayAsset.assetType = blender_utils.VRAY_ASSET_TYPE["Scene"]

        if err := vray_proxy.loadVRayScenePreviewMesh(ob.data.vray.VRayScene, absFilePath):
            debug.report('ERROR', err)
            return {'CANCELLED'}

        # Set the filepath obtained from the FileSelect dialog using the format 
        # corresponding to the 'relative' option.
        filePath = absFilePath
        
        if useRelPath:
            if relFilePath := tryGetRelativePath(absFilePath):
                filePath = relFilePath
            else:
                debug.report('INFO', "Cannot import V-Ray Scene with relative path, using absolute path instead") 

        vrayScene['filepath'] = filePath   
        blender_utils.selectObject(ob)

        return {'FINISHED'}

class VRAY_OT_add_object_decal(VRayOperatorBase):
    """ Add a VRayDecal object to the scene. """

    bl_idname = "vray.add_object_decal"
    bl_label = "Add V-Ray Decal"
    bl_description = "Add V-Ray Decal object"
    bl_options = { 'UNDO' }

    def execute(self, context):
        obj = createDecalObject(context)
        generateDecalPreviewMesh(obj)
        blender_utils.selectObject(obj)

        return {'FINISHED'}


class VRAY_OT_add_object_splat(VRayOperatorBase):
    """ Add a V-Ray Gaussians object: an Empty that renders a Gaussian splat (.ply) file. """

    bl_idname = "vray.add_object_splat"
    bl_label = "Add V-Ray Gaussians"
    bl_description = "Import a Gaussian splat (.ply) file as a V-Ray Gaussians object"
    bl_options = { 'UNDO' }

    filter_glob: bpy.props.StringProperty(default="*.ply", options={'HIDDEN'})
    filepath: bpy.props.StringProperty(name="Filepath (*.ply)", subtype="FILE_PATH")

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        from vray_blender.utils import splat_preview

        obj = bpy.data.objects.new("V-Ray Gaussians", None)
        # Set the file before tagging the object as a splat so the GeomGaussians.file update
        # callback (which also regenerates the preview) is a no-op here - we load the preview
        # explicitly below so we can report a read error to the user, avoiding a double load.
        obj.vray.GeomGaussians.file = self.filepath
        obj.vray.isVRayGaussian = True
        obj.location = context.scene.cursor.location
        context.collection.objects.link(obj)

        # Pre-generate the viewport preview so the first redraw doesn't stall, size the Empty to
        # the model's bounding box, and give immediate feedback if the file cannot be read.
        if err := splat_preview.loadPreview(obj):
            self.report({'WARNING'}, err)

        blender_utils.selectObject(obj)
        return {'FINISHED'}


class VRAY_OT_add_object_infinite_plane(VRayOperatorBase):
    """ Add a V-Ray Infinite Plane object: an Empty that renders as an infinite plane. """

    bl_idname = "vray.add_object_infinite_plane"
    bl_label = "Add V-Ray Infinite Plane"
    bl_description = "Add a V-Ray infinite plane object"
    bl_options = { 'UNDO' }

    def execute(self, context):
        obj = bpy.data.objects.new("V-Ray Infinite Plane", None)
        obj.vray.isVRayInfinitePlane = True
        # The plane spans the object's XY plane, so its only visual cue is the +Z normal. Size it
        # like the other integrations' plane gizmos (V-Ray for Houdini defaults to 2.0) - at the
        # Empty's default of 1.0 the arrow is easy to miss.
        obj.empty_display_type = 'SINGLE_ARROW'
        obj.empty_display_size = 2.0
        obj.location = context.scene.cursor.location
        context.collection.objects.link(obj)

        blender_utils.selectObject(obj)
        return {'FINISHED'}


class VRAY_OT_add_object_perfect_sphere(VRayOperatorBase):
    """ Add a V-Ray Perfect Sphere object: an Empty that renders as an analytic sphere. """

    bl_idname = "vray.add_object_perfect_sphere"
    bl_label = "Add V-Ray Perfect Sphere"
    bl_description = "Add a V-Ray perfect sphere object"
    bl_options = { 'UNDO' }

    def execute(self, context):
        obj = bpy.data.objects.new("V-Ray Perfect Sphere", None)
        obj.vray.isVRayPerfectSphere = True
        obj.empty_display_type = 'SPHERE'
        obj.empty_display_size = obj.vray.GeomPerfectSphere.radius
        obj.location = context.scene.cursor.location
        context.collection.objects.link(obj)

        blender_utils.selectObject(obj)
        return {'FINISHED'}


def getMenuLightAddOperators():
    """ The V-Ray lights shown in the Add (Shift+A) menu, in display order. """
    return (
        VRAY_OT_add_object_vray_light_rect,
        VRAY_OT_add_object_vray_light_sphere,
        VRAY_OT_add_object_vray_light_dome,
        VRAY_OT_add_object_vray_light_mesh,
        VRAY_OT_add_object_vray_light_ies,
        VRAY_OT_add_object_vray_light_sun,
        VRAY_OT_add_object_vray_sun_sky,
    )


def addChaosScatterToMenu(layout: bpy.types.UILayout):
    """ Draw the Chaos Scatter add command. Chaos Scatter is a separate add-on which the user may
        have turned off; its operator is then not registered and drawing it would raise, so key
        off the property group it installs on Object.
    """
    if hasattr(bpy.types.Object, 'chaos_scatter'):
        layout.operator('chaos_scatter.add', text="Chaos Scatter", icon_value=icons.getIcon('CHAOS_SCATTER'))


class VRAY_MT_add(bpy.types.Menu):
    """ V-Ray submenu in the Add (Shift+A) menu, grouped by Camera / Lights / Geometry """
    bl_idname = "VRAY_MT_add"
    bl_label = "V-Ray"

    @classmethod
    def poll(cls, context):
        return classes.pollEngine(context)

    def draw(self, context):
        layout = self.layout

        # Forbid creation of objects in Edit mode (it can produce errors).
        classes.disableLayoutInEditMode(layout, context)

        # --- Camera ---
        layout.label(text="Camera")
        layout.operator(VRAY_OT_add_physical_camera.bl_idname, text="V-Ray Physical Camera", icon_value=icons.getUIIcon(VRAY_OT_add_physical_camera))
        layout.separator()

        # --- Lights ---
        layout.label(text="Lights")
        for op in getMenuLightAddOperators():
            layout.operator(op.bl_idname, text=op.bl_label, icon_value=icons.getUIIcon(op))
        layout.separator()

        # --- Geometry ---
        layout.label(text="Geometry")
        # V-Ray Scene is only available in solid viewport mode (not during IPR viewport render).
        enableVrscene = not VRayRendererIprViewport.isActive()
        vraySceneLayout = layout.column()
        vraySceneLayout.active = enableVrscene
        vraySceneLayout.enabled = enableVrscene
        vraySceneLayout.operator(VRAY_OT_add_object_vrayscene.bl_idname, text="V-Ray Scene", icon_value=icons.getUIIcon(VRAY_OT_add_object_vrayscene))

        layout.operator(VRAY_OT_add_object_proxy.bl_idname, text="V-Ray Proxy", icon_value=icons.getUIIcon(VRAY_OT_add_object_proxy))
        if features.isEnabled(Feature.GAUSSIAN_SPLATS):
            layout.operator(VRAY_OT_add_object_splat.bl_idname, text="V-Ray Gaussians", icon_value=icons.getUIIcon(VRAY_OT_add_object_splat))
        layout.operator(VRAY_OT_add_object_fur.bl_idname, text="V-Ray Fur", icon_value=icons.getUIIcon(VRAY_OT_add_object_fur))
        layout.operator(VRAY_OT_add_object_decal.bl_idname, text="V-Ray Decal", icon_value=icons.getUIIcon(VRAY_OT_add_object_decal))
        layout.operator(VRAY_OT_add_object_infinite_plane.bl_idname, text="V-Ray Infinite Plane", icon_value=icons.getUIIcon(VRAY_OT_add_object_infinite_plane))
        addChaosScatterToMenu(layout)


class _AddMenuLayout:
    """Wraps UILayout.menu() to inject the V-Ray menu just before VIEW3D_MT_mesh_add."""

    def __init__(self, real):
        object.__setattr__(self, "_real", real)

    def menu(self, idname, *args, **kwargs):
        if idname == "VIEW3D_MT_mesh_add":
            self._real.menu(VRAY_MT_add.bl_idname, icon_value=icons.getIcon("VRAY_PLACEHOLDER"))
            self._real.separator()
        self._real.menu(idname, *args, **kwargs)

    def __getattr__(self, name):
        return getattr(self._real, name)

    def __setattr__(self, name, value):
        # Forward attribute writes (e.g. operator_context) to the wrapped layout.
        setattr(self._real, name, value)


class _MenuWithLayout:
    """Wraps a Menu instance to substitute self.layout with a wrapped layout."""

    def __init__(self, realSelf, layout):
        self._real = realSelf
        self.layout = layout

    def __getattr__(self, name):
        return getattr(self._real, name)


_originalAddMenuDraw = None


def _patchedAddMenuDraw(self, context):
    _originalAddMenuDraw(_MenuWithLayout(self, _AddMenuLayout(self.layout)), context)


def register_add_menu_draw():
    global _originalAddMenuDraw
    _originalAddMenuDraw = bpy.types.VIEW3D_MT_add.draw
    bpy.types.VIEW3D_MT_add.draw = _patchedAddMenuDraw


def unregister_add_menu_draw():
    global _originalAddMenuDraw
    if _originalAddMenuDraw is not None:
        bpy.types.VIEW3D_MT_add.draw = _originalAddMenuDraw
        _originalAddMenuDraw = None


original_topbar_render_draw = None

def register_topbar_render_draw():
    global original_topbar_render_draw

    original_topbar_render_draw = bpy.types.TOPBAR_MT_render.draw
    bpy.types.TOPBAR_MT_render.draw = topbar_render_draw

def unregister_topbar_render_draw():
    global original_topbar_render_draw
    bpy.types.TOPBAR_MT_render.draw = original_topbar_render_draw


def topbar_render_draw(self, context):
    global original_topbar_render_draw
    if classes.pollEngine(context):
        layout = self.layout
        layout.enabled = vray.isInitialized()
        layout.operator(VRAY_OT_render.bl_idname, icon_value=icons.getUIIcon(VRAY_OT_render)).forceMode = 'FRAME'
        layout.operator(VRAY_OT_render.bl_idname, icon_value=icons.getUIIcon(VRAY_OT_render), text="Render Animation").forceMode = 'ANIMATION'
        layout.operator(VRAY_OT_render_interactive.bl_idname, icon_value=icons.getUIIcon(VRAY_OT_render_interactive))
        layout.separator()
        layout.operator(VRAY_OT_open_vfb.bl_idname, icon_value=icons.getUIIcon(VRAY_OT_open_vfb))
    else:
        original_topbar_render_draw(self, context)




########  ########  ######   ####  ######  ######## ########     ###    ######## ####  #######  ##    ##
##     ## ##       ##    ##   ##  ##    ##    ##    ##     ##   ## ##      ##     ##  ##     ## ###   ##
##     ## ##       ##         ##  ##          ##    ##     ##  ##   ##     ##     ##  ##     ## ####  ##
########  ######   ##   ####  ##   ######     ##    ########  ##     ##    ##     ##  ##     ## ## ## ##
##   ##   ##       ##    ##   ##        ##    ##    ##   ##   #########    ##     ##  ##     ## ##  ####
##    ##  ##       ##    ##   ##  ##    ##    ##    ##    ##  ##     ##    ##     ##  ##     ## ##   ###
##     ## ########  ######   ####  ######     ##    ##     ## ##     ##    ##    ####  #######  ##    ##

def getRegClasses():
    return (
        VRAY_OT_set_camera,
        VRAY_OT_set_view,
        VRAY_OT_add_object_vrayscene,
        VRAY_OT_add_object_proxy,
        VRAY_OT_add_object_splat,
        VRAY_OT_add_object_fur,
        VRAY_OT_add_object_decal,
        VRAY_OT_add_object_infinite_plane,
        VRAY_OT_add_object_perfect_sphere,
        VRAY_OT_select_camera,
        VRAY_OT_camera_lock_unlock_view,
        VRAY_MT_add,
        VRAY_OT_add_physical_camera,
        VRAY_OT_adjust_light_prop,
        VRAY_OT_create_mesh_light
    ) + getLightAddOperators()


# Classes registered only when their feature flag is enabled.
_GATED_CLASSES = {
    VRAY_OT_add_object_splat: Feature.GAUSSIAN_SPLATS,
}


def register():
    for regClass in getRegClasses():
        if regClass in _GATED_CLASSES and not features.isEnabled(_GATED_CLASSES[regClass]):
            continue
        bpy.utils.register_class(regClass)

    register_add_menu_draw()
    register_object_context_menu_draw()
    register_topbar_render_draw()


def unregister():
    for regClass in getRegClasses():
        if regClass in _GATED_CLASSES and not features.isEnabled(_GATED_CLASSES[regClass]):
            continue
        bpy.utils.unregister_class(regClass)

    unregister_add_menu_draw()
    unregister_object_context_menu_draw()
    unregister_topbar_render_draw()
