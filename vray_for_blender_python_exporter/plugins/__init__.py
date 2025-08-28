
import json
import os
import sys
from mathutils import Vector

import bpy

from vray_blender import debug
from vray_blender.lib import class_utils
from vray_blender.lib import sys_utils
from vray_blender.lib import attribute_utils, plugin_utils, sys_utils
from vray_blender.lib.mixin import VRayEntity
from vray_blender.nodes.utils import selectedObjectTagUpdate



# Holds the structure of the ./plugins folder
PLUGINS_DIRS = []

# A module contains the data loaded from the JSON plugin description plus all
# global definitions in the corresponding plugins/*.py file, if there is such
# This allows to augment the JSONs with additional properties, to override properties
# or to define custom export functionality. E.g. this is how the _exportCustom()
# mechanism is implemented 
PLUGIN_MODULES = {}

# This struct holds the PLUGIN_MODULES entries sorted by plugin category
PLUGINS = {
    'BRDF':            {},
    'CAMERA':          {},
    'GEOMETRY':        {},
    'LIGHT':           {},
    'MATERIAL':        {},
    'OBJECT':          {},
    'RENDERCHANNEL':   {},
    'SETTINGS':        {},
    'TEXTURE':         {},
    'UVWGEN':          {},
    'SYSTEM':          {},
    'EFFECT':          {},
    'MISC':            {},
}

# Overrides to default plugin properties. Loaded from the corresponding json files in ./settings
DEFAULTS_OVERRIDES = {
    "viewport"   : [],
    "preview"    : [],
    "production" : [],
}


def getPluginModule(pluginType: str):
    """ Return plugin module given the plugin type name. If the plugin does not exist, an exception if thrown """
    pluginModule = PLUGIN_MODULES.get(pluginType)

    if not pluginModule:
        errMsg = f'Plugin "{pluginType}" module not found.'
        debug.printError(errMsg)
        raise Exception(errMsg)
    
    return pluginModule

def findPluginModule(pluginType: str):
    """ Return plugin module given the plugin type name """
    if pluginModule := PLUGIN_MODULES.get(pluginType):
        return pluginModule
    
    return None


def getPluginAttr(pluginModule, attrName):
    """ Return parameter definition or None if not found """
    return next((p for p in pluginModule.Parameters if p['attr'] == attrName), None)


def getPluginInputNodeDesc(pluginModule, attrName):
    """ Return input socket definition from the plugin description or None if not found """
    if not (inputSockets := pluginModule.Node.get('input_sockets')):
        return None
    return next((s for s in inputSockets if s['name'] == attrName), None)


##        #######     ###    ########  #### ##    ##  ######
##       ##     ##   ## ##   ##     ##  ##  ###   ## ##    ##
##       ##     ##  ##   ##  ##     ##  ##  ####  ## ##
##       ##     ## ##     ## ##     ##  ##  ## ## ## ##   ####
##       ##     ## ######### ##     ##  ##  ##  #### ##    ##
##       ##     ## ##     ## ##     ##  ##  ##   ### ##    ##
########  #######  ##     ## ########  #### ##    ##  ######


def addAttributes(pluginModule, pointerProp):
    """ Load settings to the bpy.props.PointerProperty """
    if hasattr(pluginModule, 'add_properties'):
        pluginModule.add_properties(pointerProp)
    if hasattr(pluginModule, 'Parameters'):
        class_utils.registerPluginPropertyGroup(pointerProp, pluginModule)


def _loadPluginAttributes(plugins, pointerProp):
    for pluginName in plugins:
        addAttributes(plugins[pluginName], pointerProp)


def _getPluginsDir():
    return os.path.join(sys_utils.getExporterPath(), "plugins")


def _loadPlugins():
    """ Load JSON descriptions and augment with the corresponding plugins/*.py file's data
    """
    global PLUGINS
    global PLUGIN_MODULES

    pluginsDir = _getPluginsDir()

    if not pluginsDir or not os.path.exists(pluginsDir):
        debug.printError("Plugin directory not found!")
        return

    plugins = []
    for dirName, subdirList, fileList in os.walk(pluginsDir):
        if dirName.endswith("__pycache__"):
            continue

        if not dirName in sys.path:
            PLUGINS_DIRS.append(dirName)
            sys.path.append(dirName)

        for fname in fileList:
            if fname == "__init__.py":
                continue
            if not fname.endswith(".py"):
                continue

            module_name, module_ext = os.path.splitext(fname)

            plugins.append(__import__(module_name))

    for plugin in plugins:
        if not hasattr(plugin, 'ID'):
            continue
        
        try:
            PLUGINS[plugin.TYPE][plugin.ID] = plugin
        except Exception as ex:
            debug.printError(f"Failed to register plugin of type {plugin.ID}, category {plugin.TYPE}: {ex}")
            continue
        
        PLUGIN_MODULES[plugin.ID] = plugin


def _loadAttributeOverrides(mode: str, file: str):
    """ Load overrides to the default property values of plugins 

        mode: viewport, preview, production
        file: path to a json file with overriding values
    """
    global DEFAULTS_OVERRIDES
    
    try:
        overrides = sys_utils.readOverrides(file)
        DEFAULTS_OVERRIDES[mode] = json.loads(overrides) if overrides != "" else None
    except Exception as ex:
        # TODO: use the logging subsys methods when refactored as by
        # https://jira-chaos.atlassian.net/browse/VBLD-464
        debug.printError(f"Failed to load overrides file {file}. Error: {ex}")



 ######     ###    ##     ## ######## ########     ###
##    ##   ## ##   ###   ### ##       ##     ##   ## ##
##        ##   ##  #### #### ##       ##     ##  ##   ##
##       ##     ## ## ### ## ######   ########  ##     ##
##       ######### ##     ## ##       ##   ##   #########
##    ## ##     ## ##     ## ##       ##    ##  ##     ##
 ######  ##     ## ##     ## ######## ##     ## ##     ##

def _toggleCameraUpdate(self, context, isPhysical: bool):
        vrayCamera = context.camera.vray
        physicalCamera = vrayCamera.CameraPhysical
        domeCamera = vrayCamera.CameraDome
        
        # This is a property update callback. Change the properties by using the
        # dictionary access syntax to avoid recursion. 
        if isPhysical:
            physicalCamera['use'] = vrayCamera.use_physical
            domeCamera['use'] = False
            vrayCamera['use_dome'] = False
        else:
            domeCamera['use'] = vrayCamera.use_dome
            physicalCamera['use'] = False
            vrayCamera['use_physical'] = False



# Getter for VRayCamera.focus_distance
def _getFocusDistanceValue(propGroup):
    from vray_blender.lib import camera_utils as ct
    if cameraObj := next((obj for obj in bpy.data.objects if obj.data == propGroup.id_data), None):
        return ct.getCameraDofDistance(cameraObj)
    return 0


class VRayCamera(VRayEntity, bpy.types.PropertyGroup):
    use_camera_loop: bpy.props.BoolProperty(
        name = "Use In \"Camera loop\"",
        description = "Use camera in \"Camera loop\"",
        default = False
    )

    in_update: bpy.props.BoolProperty(
        name = "Updating a property",
        description = "Updating a proeprty",
        default = False
    )

    # Fake focus distance property that shows the distance
    # from the camera to bpy.types.Camera.dof.focus_object.
    focus_distance: bpy.props.FloatProperty(
        name = "Focus Distance",
        description = "Distance to the focus point for depth of field",
        default = 0.0,
        subtype = "DISTANCE",
        get = _getFocusDistanceValue
    )

    # Physical camera
    use_physical: bpy.props.BoolProperty(
        name = "Physical",
        description = "Toggle physical camera",
        default = False,
        update = lambda self, context: _toggleCameraUpdate(self, context, True)
    )

    use_dome: bpy.props.BoolProperty(
        name = "Dome",
        description = "Toggle dome camera",
        default = False,
        update = lambda self, context: _toggleCameraUpdate(self, context, False)
    )

    #
    # Hide From View
    #
    hide_from_view: bpy.props.BoolProperty(
        name = "Hide From View",
        description = "Hide objects from current view",
        default = False
    )

    hf_all: bpy.props.BoolProperty(
        name = "Hide from everything",
        description = "Hide objects completely",
        default = False
    )

    hf_all_auto: bpy.props.BoolProperty(
        name = "Hide from everything (automatic)",
        description = "Create group with name \"hf_<camera-name>\"",
        default = False
    )

    hf_all_objects: bpy.props.StringProperty(
        name = "Objects",
        description = "Objects to hide completely: name{;name;etc}",
        default = ""
    )

    hf_all_groups: bpy.props.StringProperty(
        name = "Groups",
        description = "Groups to hide completely: name{;name;etc}",
        default = ""
    )

for key in {'camera', 'gi', 'reflect', 'refract', 'shadows'}:
    VRayCamera.__annotations__['hf_%s' % key] = bpy.props.BoolProperty(
        name = "Hide from %s" % key,
        description = "Hide objects from %s" % key,
        default = False)

    VRayCamera.__annotations__['hf_%s_auto' % key] = bpy.props.BoolProperty(
        name = "Auto",
        description = "Hide objects automaically from %s" % key,
        default = False)

    VRayCamera.__annotations__['hf_%s_objects' % key] = bpy.props.StringProperty(
        name = "Objects",
        description = "Objects to hide from %s" % key,
        default = "")

    VRayCamera.__annotations__['hf_%s_groups' % key] = bpy.props.StringProperty(
        name = "Groups",
        description = "Groups to hide from %s" % key,
        default = "")

# Add additional pointer properties for the prop search UI
for key in {'all', 'camera', 'gi', 'reflect', 'refract', 'shadows'}:
    # Objects
    origAttr = 'hf_%s_objects' % key
    propSearchAttr = 'hf_%s_object' % key + '_ptr'
    description = "Object to hide from %s" % key
    if key == 'all':
        description = "Object to hide completely"
    VRayCamera.__annotations__[propSearchAttr] = attribute_utils.createPropSearchPointerProp(
        origAttr,
        propSearchAttr,
        bpy.types.Object,
        "Object",
        description
    )

    # Groups
    origAttr = 'hf_%s_groups' % key
    propSearchAttr = 'hf_%s_group' % key + '_ptr'
    description = "Group to hide from %s" % key
    if key == 'all':
        description = "Group to hide completely"
    VRayCamera.__annotations__[propSearchAttr] = attribute_utils.createPropSearchPointerProp(
        origAttr,
        propSearchAttr,
        bpy.types.Collection,
        "Group",
        description
    )

 #######  ########        ## ########  ######  ########
##     ## ##     ##       ## ##       ##    ##    ##
##     ## ##     ##       ## ##       ##          ##
##     ## ########        ## ######   ##          ##
##     ## ##     ## ##    ## ##       ##          ##
##     ## ##     ## ##    ## ##       ##    ##    ##
 #######  ########   ######  ########  ######     ##

# Getter for VRayAsset.filePath
def getFilePathValue(propGroup):
    if "filePath" not in propGroup:
        return ""

    if propGroup.useRelativePath:
        # Converting relative paths to absolute paths, so they could be exported.  
        # Replacing "\\" with "//" helps avoid errors caused by  
        # Windows remote paths being confused with Blender's relative paths.
        return bpy.path.abspath(propGroup["filePath"].replace("\\\\", "//"))
    
    return propGroup["filePath"]

# Setter for VRayAsset.filePath.
# This is necessary because, without a setter, the property becomes read-only if only a getter is defined.
def setFilePathValue(propGroup, path):
    # If bpy.path.abspath("//") returns an empty string, the scene is new and unsaved.  
    # In this case, an absolute path is assigned to ensure the path is valid  
    # and can be properly converted to a relative path when the scene is saved.
    if propGroup.useRelativePath and bpy.path.abspath("//"):
        propGroup["filePath"] = bpy.path.relpath(path)
    else:
        propGroup["filePath"] = path


class VRayAsset(VRayEntity, bpy.types.PropertyGroup):
    assetType: bpy.props.EnumProperty(
        name        = "Type",
        description = "V-Ray asset type",
        items = (
            ('0', "None", ""),
            ('1', "Proxy", ""),
            ('2', "Scene", ""),
        ),
        default = '0'
    )

class VRayCyclesNode(VRayEntity, bpy.types.PropertyGroup):
    pass


class VRayCosmosAsset(VRayEntity, bpy.types.PropertyGroup):
    """ Base class for objects, materials and lights avaiable in Chaos Cosmos """

    cosmos_package_id: bpy.props.StringProperty(
        name = "V-Ray Cosmos Package ID",
        description = "The V-Ray Cosmos asset package id",
        default = ""
    )

    cosmos_revision_id: bpy.props.IntProperty(
        name = "V-Ray Cosmos Revision ID",
        description = "The V-Ray Cosmos asset revision id",
        default = 0
    )


class VRayObject(VRayEntity, bpy.types.PropertyGroup):
    data_updated: bpy.props.IntProperty(
        options = {'HIDDEN', 'SKIP_SAVE'},
        default = False
    )

    overrideWithScene: bpy.props.BoolProperty(
        name        = "Override With VRScene Asset",
        description = "Override with *.vrscene asset",
        default     = False
    )

    ntree: bpy.props.PointerProperty(
        name = "Node Tree",
        type = bpy.types.NodeTree,
        poll = lambda s, p: p.bl_idname == 'VRayEntityObject',
        description = "V-Ray node tree",
    )

    dupliGroupIDOverride: bpy.props.IntProperty(
        name        = "Dupli Group ID Override",
        description = "Override \"Object ID\" for the whole Dupli Group. -1 - means no override",
        min         = -1,
        default     = -1
    )

    dupliShowEmitter: bpy.props.BoolProperty(
        name        = "Show Dupli Emitter",
        description = "Show dupli emitter",
        default     = False
    )

    use_instancer: bpy.props.BoolProperty(
        name = "Use Instancer For Dupli / Particles",
        default = True
    )

    subframes: bpy.props.IntProperty(
        name        = "Subframes",
        description = "Export subframe data",
        min         = 0,
        max         = 64,
        default     = 0
    )


class VRayMesh(VRayCosmosAsset, bpy.types.PropertyGroup):
    __annotations__ = {}


class VRayMetaBall(VRayEntity, bpy.types.PropertyGroup):
    __annotations__ = {}


class VRayMaterial(VRayCosmosAsset, bpy.types.PropertyGroup):
    is_vray_class: bpy.props.BoolProperty(
        name = "V-Ray Class Tag",
        description = "True if this is a V-Ray material.",
        default = False
    )

class VRayNodeTreeSettings(bpy.types.PropertyGroup):
    tree_type: bpy.props.StringProperty(
        name = "VRay Tree Type",
        description = "The type of the VRay tree",
        default = ""
    )


class VRayLight(VRayCosmosAsset, bpy.types.PropertyGroup):
    is_vray_class : bpy.props.BoolProperty(
        name = "V-Ray Class Tag",
        description = "True if this is a V-Ray light.",
        default = True
    )

    color_type: bpy.props.EnumProperty(
        name = "Color type",
        description = "Color type",
        items = (
            ('RGB',    "RGB", ""),
            ('KELVIN', "K",   ""),
        ),
        default = 'RGB'
    )

    temperature: bpy.props.IntProperty(
        name = "Temperature",
        description = "Kelvin temperature",
        min = 1000,
        max = 40000,
        step = 100,
        default = 5000
    )

    include_exclude: bpy.props.EnumProperty(
        name = "Use Include / Exclude",
        items = (
            ('0', "None", "All objects in the scene will be affected by the light"),
            ('1', "Exclude", "The selected objects will not be affected by the light"),
            ('2', "Include", "Only the selected object will be affected by the light"),
        ),
        default = '0',
        update = selectedObjectTagUpdate
    )

    illumination_shadow: bpy.props.EnumProperty(
        name = "Include / Exclude Type",
        items = (
            ('0', "Illumination", ""),
            ('1', "Shadow Casting", ""),
            ('2', "Illumination / Shadow", ""),
        ),
        default = '2',
        update = selectedObjectTagUpdate
    )

    
    from vray_blender.plugins.templates.multi_select import TemplateSelectGeometries
    objectList: bpy.props.PointerProperty(
        type = TemplateSelectGeometries
    )

    light_type: bpy.props.EnumProperty(
        name = "Light type",
        description = "Light type",
        items = (
            ('BLENDER',        "Blender",        ""),
            ('AMBIENT',     'Ambient',        ""),
            ('DIRECT',        'Direct',        ""),
            ('IES',            'IES',            ""),
            ('MESH',        'Mesh',            ""),
            ('OMNI',        'Omni',            ""),
            ('SPHERE',        'Sphere',        ""),
            ('SPOT',        'Spot',            ""),
            ('SUN',            'Sun',            ""),
            ('RECT',        'Rectangle',    ""),
            ('DOME',        'Dome',            ""),
        ),
        default = 'BLENDER'
    )

    # Used only for VRayProxy objects
    initial_proxy_light_pos: bpy.props.FloatVectorProperty(
        name = "Initial Proxy Light Position",
        description = "Initial position of light object attached to a VRayProxy object",
        unit = 'NONE',
        default = Vector((0.0, 0.0, 0.0))
    )

    initial_proxy_light_scale: bpy.props.FloatProperty(
        name = "Initial Proxy Light Scale",
        description = "Initial scale of light object attached to a VRayProxy object",
        default = 1.0
    )




class VRayWorld(VRayEntity, bpy.types.PropertyGroup):
    is_vray_class : bpy.props.BoolProperty(
        name = "V-Ray Class Tag",
        description = "True if this is a V-Ray world object.",
        default = False
    )

    global_light_level: bpy.props.FloatProperty(
        name = "Global Light Level",
        description = "A global light level multiplier for all lights",
        min = 0.0,
        soft_max = 2.0,
        precision = 3,
        default = 1.0,
    )


class VRayTexture(VRayEntity, bpy.types.PropertyGroup):
    __annotations__ = {}


class VRayRenderChannel(VRayEntity, bpy.types.PropertyGroup):
    __annotations__ = {}


class VRayScene(VRayEntity, bpy.types.PropertyGroup):
    ntree: bpy.props.PointerProperty(
        name = "Node Tree",
        type = bpy.types.NodeTree,
        poll = lambda s, p: p.bl_idname == 'VRayEntityScene',
        description = "V-Ray scene node tree",
    )


class VRayWindowManager(bpy.types.PropertyGroup):
    ui_render_context: bpy.props.EnumProperty(
        name = "Render Context Panels",
        description = "Show render panels group",
        items = (
            ('0', "Sampler", ""),
            ('1', "GI", ""),
            ('2', "Globals", ""),
            ('3', "System", ""),
        ),
        default = '0'
    )

    vrayscene_warning_shown: bpy.props.BoolProperty(
        default = False,
        description = "The warning about VRayScene objects in IPR was already shown to the user"
    )

    
class VRayFur(VRayEntity, bpy.types.PropertyGroup):
    width: bpy.props.FloatProperty(
        name        = "Width",
        description = "Hair thickness",
        min         = 0.0,
        max         = 100.0,
        soft_min    = 0.0001,
        soft_max    = 0.01,
        precision   = 5,
        default     = 0.001
    )

    widths_in_pixels: bpy.props.BoolProperty(
        name        = "Width In Pixels",
        description = "If true, the widths parameter is in pixels, otherwise it is in world units",
        default     = False
    )

    make_thinner: bpy.props.BoolProperty(
        name        = "Make Thinner",
        description = "Make hair thiner to the end",
        default     = False
    )


class VRayParticleSettings(VRayEntity, bpy.types.PropertyGroup):
    pass



class VRayRenderNode(VRayEntity, bpy.types.PropertyGroup):
    address: bpy.props.StringProperty(
        name = "IP/Hostname",
        description = "Render node IP or hostname"
    )

    port_override: bpy.props.BoolProperty(
        name = "Port Override",
        description = "Override distributed rendering port for node",
        default = False
    )

    port: bpy.props.IntProperty(
        name = "Port",
        description = "Distributed rendering port",
        min = 0,
        max = 65535,
        default = 20207
    )

    use: bpy.props.BoolProperty(
        name = "Use Node",
        description = "Use render node",
        default = True
    )


class VRayDR(VRayEntity, bpy.types.PropertyGroup):
    on: bpy.props.BoolProperty(
        name = "Distributed Rendering",
        description = "Distributed rendering",
        default = False
    )

    port: bpy.props.IntProperty(
        name = "Distributed Rendering Port",
        description = "Distributed rendering port",
        min = 0,
        max = 65535,
        default = 20207
    )

    shared_dir: bpy.props.StringProperty(
        name = "Shared Directory",
        subtype = 'DIR_PATH',
        description = "Distributed rendering shader directory"
    )

    share_name: bpy.props.StringProperty(
        name = "Share Name",
        default = "VRAYDR",
        description = "Share name"
    )

    assetSharing: bpy.props.EnumProperty(
        name        = "Asset Sharing",
        description = "Asset sharing for distributed rendering",
        items = (
            ('TRANSFER', "V-Ray Transfer",   "V-Ray will transfer assets itself"),
            ('SHARE',    "Shared Directory", "Share assets via shared directory"),
            ('ABSOLUTE', "Absolute Paths",   "Use paths as is"),
        ),
        default = 'TRANSFER'
    )

    networkType: bpy.props.EnumProperty(
        name = "Network Type",
        description = "Distributed rendering network type",
        items = (
            ('WW', "Windows", "Window master & Windows nodes"),
            ('UU', "Unix",    "Unix master & Unix nodes"),
        ),
        default = 'WW'
    )

    nodes: bpy.props.CollectionProperty(
        name = "Render Nodes",
        type =  VRayRenderNode,
        description = "V-Ray render nodes"
    )

    nodes_selected: bpy.props.IntProperty(
        name = "Render Node Index",
        default = -1,
        min = -1,
        max = 100
    )

    renderOnlyOnNodes: bpy.props.BoolProperty(
        name        = "Render Only On Nodes",
        description = "Use distributed rendering excluding the local machine",
        default     = False
    )

    checkAssets: bpy.props.BoolProperty(
        name        = "Check Asset Cache",
        description = "Check for assets in the asset cache folder before transferring them",
        default     = False
    )

    limitHosts: bpy.props.IntProperty(
        name        = "Limit Hosts",
        description = "Limit the number of render hosts used for distributed rendering to the first N idle hosts",
        min         = 0,
        default     = 0
    )


class VRayCollection(VRayEntity, bpy.types.PropertyGroup):
    __annotations__ = {}

########  ########  ######   ####  ######  ######## ########     ###    ######## ####  #######  ##    ##
##     ## ##       ##    ##   ##  ##    ##    ##    ##     ##   ## ##      ##     ##  ##     ## ###   ##
##     ## ##       ##         ##  ##          ##    ##     ##  ##   ##     ##     ##  ##     ## ####  ##
########  ######   ##   ####  ##   ######     ##    ########  ##     ##    ##     ##  ##     ## ## ## ##
##   ##   ##       ##    ##   ##        ##    ##    ##   ##   #########    ##     ##  ##     ## ##  ####
##    ##  ##       ##    ##   ##  ##    ##    ##    ##    ##  ##     ##    ##     ##  ##     ## ##   ###
##     ## ########  ######   ####  ######     ##    ##     ## ##     ##    ##    ####  #######  ##    ##

def _getRegClasses():
    return (
        VRayRenderNode,
        VRayDR,

        VRayAsset,
        VRayCyclesNode,
        VRayCamera,
        VRayCosmosAsset,
        VRayFur,
        VRayLight,
        VRayMaterial,
        VRayMesh,
        VRayMetaBall,
        VRayObject,
        VRayParticleSettings,
        VRayRenderChannel,
        VRayTexture,
        VRayWorld,
        VRayCollection,
        VRayNodeTreeSettings,
    )


def register():
    global PLUGINS
    global PLUGIN_MODULES

    from vray_blender.plugins import templates
    templates.register()

    
    # Load plugin descriptions from the json definition files exported from Vray
    if plugin_utils.loadPluginDescriptions() == 0:
        debug.printError('Failed to load JSON plugin descriptions')
        raise IOError('Failed to load JSON plugin descriptions')
    
    # Load plugins for which per-plugin .py modules exist  
    _loadPlugins()

    # Load the rest of the plugins ( which only have plugin descriptions, but no
    # customized functionality in .py modules )
    for jsonPluginType in plugin_utils.PLUGINS_DESC:
        if jsonPluginType not in PLUGIN_MODULES:
            # Plugin descriptions for These types are not loaded. They use exclusively custom export code
            # TODO: Check each one and determine whether we also need the generic export procedure to be run on them
            if jsonPluginType in {
                'MtlLayeredBRDF',
                'MtlSelectRE',
                'MtlSkp2Sided',
                'MtlWrapperMaya',
                'GeomMeshLoader',
                'GeomMeshLoader1',
                'GeomMeshTest',
                'GeomMetaballSystem',
                'GeomParticleSystem',
                'GeomStaticNURBS',
                'Instancer',
                'Instancer2',
                'PhxShaderSimGeom',
                'PhxShaderSimMesh',
            }:
                continue

            jsonPlugin = plugin_utils.PLUGINS_DESC[jsonPluginType]
            
            #if jsonPlugin['TYPE'] in {'MISC'}:
            #    continue

            DynPluginType = type(
                # Name
                "JSON%s" % jsonPluginType,
                # Inheritance
                (object,),
                # Attributes
                jsonPlugin
            )

            try:
                PLUGINS[jsonPlugin['TYPE']][jsonPlugin['ID']] = DynPluginType
            except Exception as ex:
                debug.printExceptionInfo(ex, f"No plugin module has been loaded for plugin '{jsonPlugin['ID']}' of type '{jsonPlugin['TYPE']}'")
            
            PLUGIN_MODULES[jsonPlugin['ID']] = DynPluginType

    # Register properties
    #
    pluginClasses = {}

    for pluginName in PLUGIN_MODULES:
        plugin = PLUGIN_MODULES[pluginName]
        if hasattr(plugin, 'getRegClasses'):
            for pluginClass in plugin.getRegClasses():
                pluginClasses[pluginClass.__name__] = pluginClass
        if hasattr(plugin, 'register'):
            plugin.register()

    _loadPluginAttributes(PLUGINS['CAMERA'],        VRayCamera)
    _loadPluginAttributes(PLUGINS['BRDF'],          None)
    _loadPluginAttributes(PLUGINS['MATERIAL'],      None)
    _loadPluginAttributes(PLUGINS['GEOMETRY'],      VRayMesh)
    _loadPluginAttributes(PLUGINS['LIGHT'],         VRayLight)
    _loadPluginAttributes(PLUGINS['OBJECT'],        VRayObject)
    _loadPluginAttributes(PLUGINS['RENDERCHANNEL'], VRayRenderChannel)
    _loadPluginAttributes(PLUGINS['EFFECT'],        None)
    _loadPluginAttributes(PLUGINS['SETTINGS'],      VRayScene)
    _loadPluginAttributes(PLUGINS['SYSTEM'],        VRayScene)
    _loadPluginAttributes(PLUGINS['TEXTURE'],       None)
    _loadPluginAttributes(PLUGINS['UVWGEN'],        None)
    _loadPluginAttributes(PLUGINS['MISC'],          None)

    _loadAttributeOverrides("PREVIEW", "preview.json")
    _loadAttributeOverrides("VIEWPORT", "viewport.json")
    _loadAttributeOverrides("PRODUCTION", "production.json")

    # Attach material options plugins to both Object and Material
    VRayObject.__annotations__['VRayObjectProperties'] = bpy.props.PointerProperty(
        type = class_utils.TYPES['VRayVRayObjectProperties'],
    )

    VRayObject.__annotations__['MtlRenderStats'] = bpy.props.PointerProperty(
        type = class_utils.TYPES['VRayMtlRenderStats'],
    )

    VRayObject.__annotations__['MtlRoundEdges'] = bpy.props.PointerProperty(
        type = class_utils.TYPES['VRayMtlRoundEdges'],
    )

    VRayObject.__annotations__['MtlOverride'] = bpy.props.PointerProperty(
        type = class_utils.TYPES['VRayMtlOverride'],
    )


    VRayMaterial.__annotations__['MtlRenderStats'] = bpy.props.PointerProperty(
        type = class_utils.TYPES['VRayMtlRenderStats'],
    )

    VRayMaterial.__annotations__['MtlWrapper'] = bpy.props.PointerProperty(
        type = class_utils.TYPES['VRayMtlWrapper'],
    )

    VRayMaterial.__annotations__['MtlMaterialID'] = bpy.props.PointerProperty(
        type = class_utils.TYPES['VRayMtlMaterialID'],
    )

    VRayMaterial.__annotations__['MtlRoundEdges'] = bpy.props.PointerProperty(
        type = class_utils.TYPES['VRayMtlRoundEdges'],
    )

    for regClass in _getRegClasses():
        bpy.utils.register_class(regClass)

    bpy.types.ParticleSettings.vray = bpy.props.PointerProperty(
        name = "V-Ray Particle Settings",
        type =  VRayParticleSettings,
        description = "V-Ray Particle settings"
    )

    VRayParticleSettings.VRayFur = bpy.props.PointerProperty(
        name = "V-Ray Fur Settings",
        type =  VRayFur,
        description = "V-Ray Fur settings"
    )

    bpy.types.Texture.vray = bpy.props.PointerProperty(
        name = "V-Ray Texture Settings",
        type =  VRayTexture,
        description = "V-Ray texture settings"
    )

    bpy.types.Material.vray = bpy.props.PointerProperty(
        name = "V-Ray Material Settings",
        type =  VRayMaterial,
        description = "V-Ray material settings"
    )

    bpy.types.Mesh.vray = bpy.props.PointerProperty(
        name = "V-Ray Mesh Settings",
        type =  VRayMesh,
        description = "V-Ray geometry settings"
    )

    bpy.types.MetaBall.vray = bpy.props.PointerProperty(
        name = "V-Ray MetaBall Settings",
        type =  VRayMetaBall,
        description = "V-Ray metaball settings"
    )

    bpy.types.Light.vray = bpy.props.PointerProperty(
        name = "V-Ray Light Settings",
        type =  VRayLight,
        description = "V-Ray light settings"
    )

    bpy.types.Curve.vray = bpy.props.PointerProperty(
        name = "V-Ray Curve Settings",
        type =  VRayMesh,
        description = "V-Ray curve settings"
    )

    bpy.types.Curves.vray = bpy.props.PointerProperty(
        name = "V-Ray Curves Settings",
        type =  VRayMesh,
        description = "V-Ray curves settings"
    )

    bpy.types.Camera.vray = bpy.props.PointerProperty(
        name = "V-Ray Camera Settings",
        type =  VRayCamera,
        description = "V-Ray Camera / DoF / Motion Blur settings"
    )

    bpy.types.Object.vray = bpy.props.PointerProperty(
        name = "V-Ray Object Settings",
        type =  VRayObject,
        description = "V-Ray Object Settings"
    )

    bpy.types.Node.vray = bpy.props.PointerProperty(
        name = "V-Ray Entity Settings",
        type = VRayCyclesNode,
        description = "V-Ray Entity Settings"
    )

    bpy.types.World.vray = bpy.props.PointerProperty(
        name = "V-Ray World Settings",
        type =  VRayWorld,
        description = "V-Ray world settings"
    )

    bpy.types.Collection.vray = bpy.props.PointerProperty(
        name = "V-Ray Collection Settings",
        type =  VRayCollection,
        description = "V-Ray collection settings"
    )

    bpy.types.ShaderNodeTree.vray = bpy.props.PointerProperty(
        name = "VRay Node Tree Settings",
        type = VRayNodeTreeSettings,
        description = "VRay Node Tree Settings"
    )

    VRayObject.VRayAsset = bpy.props.PointerProperty(
        name = "VRayAsset",
        type =  VRayAsset,
        description = "VRayAsset settings"
    )

    VRayScene.__annotations__['Exporter'] = bpy.props.PointerProperty(
        name = "Exporter",
        type = pluginClasses['VRayExporter'],
        description = "Global exporting settings"
    )

    VRayScene.__annotations__['VRayDR'] = bpy.props.PointerProperty(
        name = "Distributed rendering",
        type =  VRayDR,
        description = "Distributed rendering settings"
    )

    VRayScene.__annotations__['ActiveNodeEditorType'] = bpy.props.EnumProperty(
        name = "Node Tree Types",
        items = (
            ('SHADER',  "Shader",  "'Material Tree' for geometric objects or 'Light Tree' for lights"),
            ('OBJECT',  "Object",  "Object Geometry Tree"),
            ('WORLD',   "World",   "Effect and Environment Trees"),
        ),
        default = 'SHADER'
    )

    VRayScene.__annotations__['StaticIdCounter'] = bpy.props.IntProperty(
        name = "StaticIdCounter",
        min     = 0,
        default = 0,
        description = "Counter used for generation of VRayEntity.static_id"
    )

    
    bpy.utils.register_class(VRayScene)
    bpy.types.Scene.vray = bpy.props.PointerProperty(
        name = "V-Ray Settings",
        type =  VRayScene,
        description = "V-Ray Renderer settings"
    )


    bpy.utils.register_class(VRayWindowManager)
    bpy.types.WindowManager.vray = bpy.props.PointerProperty(
        name = "V-Ray Settings",
        type =  VRayWindowManager,
        description = "V-Ray window settings"
    )

    

def unregister():
    global PLUGIN_MODULES
    global PLUGINS_DIRS

    for regClass in _getRegClasses():
        bpy.utils.unregister_class(regClass)

    bpy.utils.unregister_class(VRayScene)
    bpy.utils.unregister_class(VRayWindowManager)

    for pluginName in PLUGIN_MODULES:
        plugin = PLUGIN_MODULES[pluginName]
        if hasattr(plugin, 'unregister'):
            plugin.unregister()

    del bpy.types.Camera.vray
    del bpy.types.Light.vray
    del bpy.types.Material.vray
    del bpy.types.Mesh.vray
    del bpy.types.MetaBall.vray
    del bpy.types.Object.vray
    del bpy.types.Scene.vray
    del bpy.types.Texture.vray
    del bpy.types.World.vray

    for plugDir in PLUGINS_DIRS:
        if plugDir in sys.path:
            sys.path.remove(plugDir)
    PLUGINS_DIRS = []

    for plug in PLUGIN_MODULES:
        del plug

    plugin_utils.PLUGINS_DESC.clear()

    from vray_blender.plugins import templates
    templates.unregister()
