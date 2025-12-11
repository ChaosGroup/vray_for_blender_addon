
import bpy
import os
from pathlib import PurePath

from vray_blender import debug
from vray_blender.engine.renderer_ipr_viewport import VRayRendererIprViewport
from vray_blender.lib import blender_utils, lib_utils
from vray_blender.nodes.operators.import_file import importProxyFromMeshFile
from vray_blender.nodes import tree_defaults
from vray_blender.nodes import utils as NodesUtils
from vray_blender.operators import VRAY_OT_render, VRAY_OT_render_interactive
from vray_blender.ui import classes, icons
from vray_blender.menu import VRAY_OT_open_vfb
from vray_blender.vray_tools import vray_proxy
from vray_blender.exporting.tools import MESH_OBJECT_TYPES

from vray_blender.bin import VRayBlenderLib as vray
from vray_blender.lib.mixin import VRayOperatorBase

class VRAY_OT_set_view(VRayOperatorBase):
    bl_idname = "vray.set_view"
    bl_label = "Set View"

    view_type: bpy.props.StringProperty(default='TOP')

    def execute(self, context):
        bpy.ops.view3d.view_axis(type=self.view_type, align_active=False)
        return {'FINISHED'}


class VRAY_OT_set_camera(VRayOperatorBase):
    bl_idname = "vray.set_camera"
    bl_label = "Set Active Camera"

    camera: bpy.props.StringProperty()

    def execute(self, context):
        if self.camera:
            context.scene.camera = context.scene.objects[self.camera]
            if context.area.spaces[0].region_3d.view_perspective not in {'CAMERA'}:
                bpy.ops.view3d.view_camera()
        return {'FINISHED'}


class VRAY_OT_select_camera(VRayOperatorBase):
    bl_idname = "vray.select_camera"
    bl_label = "Select Active Camera"

    def execute(self, context):
        if context.scene.camera:
            bpy.ops.object.select_camera()
        return {'FINISHED'}


class VRAY_OT_camera_lock_unlock_view(VRayOperatorBase):
    bl_idname = "vray.camera_lock_unlock_view"
    bl_label = "Lock / Unlock Camera To View"

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
            debug.report("ERROR", "Cannot add a V-Ray Sky without an active world")
            return {'CANCELLED'}

        _, lightObject = self._createSunAndTarget("VRaySunSky")

        if not context.scene.world.vray.is_vray_class:
            tree_defaults.addWorldNodeTree(context.scene.world)

        worldTree = context.scene.world.node_tree
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
    )

def addSeparatorToMenu(self, context):
    """ Callback to add a separator to a menu. To be used with Menu.append() """
    self.layout.separator()


def addVRayLightsToMenu(self, context):
    """ Callback to add all V-Ray lights to a menu. To be used with Menu.append() """

    # List operators after which to draw a separator in the menu
    separatorsAfter = [VRAY_OT_add_object_vray_sun_sky]

    # Forbid creation of lights in Edit mode (it can produce errors).
    classes.disableLayoutInEditMode(self.layout, context)

    if classes.pollEngine(context):
        for op in getLightAddOperators():
            self.layout.operator(op.bl_idname, text=op.bl_label, icon_value=icons.getUIIcon(op))
            if op in separatorsAfter:
                self.layout.separator()


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
        default="*.vrmesh;*.abc",
        options={'HIDDEN'}
    )

    filepath: bpy.props.StringProperty(name="Filepath (*.vrmesh, *abc)", subtype="FILE_PATH")
    relpath: bpy.props.BoolProperty(name="Use Relative Path", default=False)
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

        _, err = importProxyFromMeshFile(context, matPath, self.filepath, useRelPath=self.relpath, scaleUnit=self.unit_scale)
        
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
        default="*.vrscene;*.usd;*.usda;*.usdc;*.usdz",
        options={'HIDDEN'}
    )

    filepath: bpy.props.StringProperty(name="Filepath (*.vrscene, *.usd, *.usda, *.usdc, *.usdz)", subtype="FILE_PATH")
    relpath: bpy.props.BoolProperty(name="Use Relative Path", default=True)

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        if not self.filepath:
            return {'CANCELLED'}

        filepath = os.path.normpath(self.filepath)
        if not os.path.exists(filepath):
            self.report({'ERROR'}, "File not found!")
            return {'CANCELLED'}

        fileExt = PurePath(filepath).suffix
        if fileExt != '.vrscene' and not fileExt.startswith(".usd"):
           self.report({'ERROR'}, f"File format {fileExt} is not supported by V-Ray Scene")
           return {'CANCELLED'}

        # Create a new mesh object to represent the scene
        name = f"VRayScene@{PurePath(filepath).stem}"

        mesh = bpy.data.meshes.new(name)
        ob = bpy.data.objects.new(name, mesh)
        ob.location = context.scene.cursor.location

        context.scene.collection.objects.link(ob)

        # Generate preview for the VRScene
        sceneFilepath = bpy.path.relpath(filepath) if self.relpath and bpy.data.filepath else filepath

        vrayScene = ob.data.vray.VRayScene
        vrayScene['filepath'] = filepath    # Set to the value obtained from the FileSelect dialog

        ob.vray.VRayAsset.assetType = blender_utils.VRAY_ASSET_TYPE["Scene"]

        if err := vray_proxy.loadVRayScenePreviewMesh(sceneFilepath, ob):
            debug.report('ERROR', err)
            return {'CANCELLED'}

        blender_utils.selectObject(ob)

        return {'FINISHED'}


class VRAY_MT_Mesh(bpy.types.Menu):
    bl_idname = "VRAY_MT_Mesh"
    bl_label = "V-Ray"

    @classmethod
    def poll(cls, context):
        return classes.pollEngine(context)

    def draw(self, context):
        enableVrscene = not VRayRendererIprViewport.isActive()
        vraySceneLayout = self.layout.column()
        vraySceneLayout.active = enableVrscene
        vraySceneLayout.enabled = enableVrscene

        vraySceneLayout.operator(VRAY_OT_add_object_vrayscene.bl_idname, text="V-Ray Scene", icon_value=icons.getUIIcon(VRAY_OT_add_object_vrayscene))
        self.layout.operator(VRAY_OT_add_object_proxy.bl_idname, text="V-Ray Proxy", icon_value=icons.getUIIcon(VRAY_OT_add_object_proxy))
        self.layout.operator(VRAY_OT_add_object_fur.bl_idname, text="V-Ray Fur", icon_value=icons.getUIIcon(VRAY_OT_add_object_fur))


def addVRayMeshesToMenu(self, context):
    self.layout.menu(VRAY_MT_Mesh.bl_idname, icon_value=icons.getIcon("VRAY_PLACEHOLDER"))


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
        layout.operator(VRAY_OT_render.bl_idname, icon_value=icons.getUIIcon(VRAY_OT_render))
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
        VRAY_OT_add_object_fur,
        VRAY_OT_select_camera,
        VRAY_OT_camera_lock_unlock_view,
        VRAY_MT_Mesh,
        VRAY_OT_add_physical_camera
    ) + getLightAddOperators()


def register():
    for regClass in getRegClasses():
        bpy.utils.register_class(regClass)

    bpy.types.VIEW3D_MT_mesh_add.append(addSeparatorToMenu)
    bpy.types.VIEW3D_MT_mesh_add.append(addVRayMeshesToMenu)

    bpy.types.VIEW3D_MT_light_add.append(addSeparatorToMenu)
    bpy.types.VIEW3D_MT_light_add.append(addVRayLightsToMenu)

    register_topbar_render_draw()


def unregister():
    for regClass in getRegClasses():
        bpy.utils.unregister_class(regClass)

    bpy.types.VIEW3D_MT_mesh_add.remove(addVRayMeshesToMenu)
    bpy.types.VIEW3D_MT_light_add.remove(addVRayLightsToMenu)

    unregister_topbar_render_draw()
