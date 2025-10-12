from __future__ import annotations
from dataclasses import dataclass
from typing import Mapping, Set, TypeVar, Dict
import bpy, mathutils
from numpy import ndarray

from vray_blender import debug
from vray_blender.exporting.tools import TimeStats, FakeTimeStats, isObjectVisible
from vray_blender.exporting.plugin_tracker import getObjTrackId, ObjTracker, FakeObjTracker, ScopedNodeTracker, FakeScopedNodeTracker
from vray_blender.lib.motion_blur import MotionBlurBuilder

from collections import defaultdict
from vray_blender.lib.names import Names

class RendererMode:
    Invalid     = 0
    Interactive = 1
    Production  = 2
    Preview     = 3
    Bake        = 4
    Viewport    = 5
    Vantage     = 6


class ExporterType:
    IPR_VIEWPORT = 0
    IPR_VFB = 1
    PROD = 2
    PREVIEW = 3
    ANIMATION = 4
    VANTAGE_LIVE_LINK = 5

class ProdRenderMode:
    EXPORT_VRSCENE = 0
    RENDER = 1
    CLOUD_SUBMIT = 2



class PersistedState:
    def __init__(self):
        self.visibleObjects   = set()
        self.activeInstancers = set()
        self.activeGizmos     = set()
        self.exportedMtls: Dict[int, AttrPlugin] = {}
        self.activeFurInfo = set()
        self.activeMeshLightsInfo = set()
        
        # Tracks objects that have been processed during any of the export procedures.
        # Its use is to distinguish between objects already handled on previous update cycles and those newly added to the depsgraph.
        self.processedObjects = set()

        # Points Names.object(obj, instance) to geom plugin name.
        # It is used for easy access to the geom plugin name of already exported objects.
        # it is set during the export of node plugins.
        self.objToGeomPluginName: dict[str, str] = {}


class ExporterContext:
    
    class _CurrentObjectContext:
        # This class manages a stack of the currently exported objects so that the exporting
        # code could obtain the last pushed object. This is necessary e.g. when custom code 
        # is exporting V-Ray properties created directly on the object.
        
        def __init__(self):
            self._objectsStack: list[bpy.types.Object] = []

        def get(self):
            return self._objectsStack[-1] if self._objectsStack else None
        
        def push(self, obj: bpy.types.Object):
            self._objectsStack.append(obj)
            return self

        def __enter__(self):
            # Object has already been pushed in the push() method
            pass

        def __exit__(self, *args):
            self._objectsStack.pop()


    ##### Properties accessible in any context #####

    # A list of plugins to remove before the next export.
    # This list is meant to be filled in from contexts outside the export sequence where immediate changes to V-Ray state
    # are not possible or convenient.
    pluginsToRecreate = set()


    def __init__(self, exporterCtx: ExporterContext = None):
        from vray_blender.lib.common_settings import CommonSettings
        
        if exporterCtx:
            self._copyConstruct(exporterCtx)
            return
        
        # A raw pointer to the VRayBlenderLib's exporter(renderer) object associated with the current export
        self.renderer = None                  
        self.rendererMode: RendererMode  = RendererMode.Invalid 
        
        # If true, export everything regardless of the depsgraph update status. Otherwise, 
        # only export changes. This flag has meaning for interactive renderer mode only. 
        # For all other modes, the value should always be True
        self.fullExport: bool = True 

        # True if this is an export-only job. 
        self.exportOnly: bool = False

        # The number of the frame being currently exported. This may be outside the animation 
        # range if motion blur is active.
        self.currentFrame: float = 0.0

        # Settings values collected from the user interface
        self.commonSettings: CommonSettings = None

        # Track VRay plugins associated with scene objects. The same plugins may be referenced
        # by multiple objects. 
        self.objTrackers: Mapping[str, ObjTracker|FakeObjTracker]  = {} 
        
        # Track VRay plugins assosiated with nodes in a nodetree.
        self.nodeTrackers: Mapping[str, ScopedNodeTracker|FakeScopedNodeTracker]  = {}
        
        # A snapshot of scene objects' visibility. Blender does not provide updates when the visibility
        # of an object changes, so we need to track it ourselves.  
        self.visibleObjects = set() # set of objTrackId

        # A snapshot of the active instancer objects in the scene
        self.activeInstancers = set() # set of objTrackId

        # A dictionary of (parentObjTrackId, geomName, dataName) => list of (instancedObj, geomName, dataName)
        # This is used to export fur for instanced objects
        self.instancesOfInstancer: dict[int, list[tuple[bpy.types.Object, str, str]]] = defaultdict(dict)
        
        # All objects in the scene, inlcuding those from linked collections
        self.allObjects = set()

        # State to carry over to the next rendering cycle
        self.persistedState = PersistedState()

        # Depsgraph updates split by type. 
        self.dgUpdates: dict[str, set[int]] = {}    #   dict[update_type, set[objTrackId]]

        self.ctx: bpy.types.Context      = None
        self.dg: bpy.types.Depsgraph     = None 
        self.ts: TimeStats|FakeTimeStats = None
        
        # LightMesh and target meshes are exported by geometry and light exporters respectively.
        # Both need to know about any updates to the other. In updatedMeshLightsInfo, all pairs of
        # LightMesh and Gizmo Object for which either object has been updated will be stored 
        # at the beginning of each update cycle for later reference. 
        self.updatedMeshLightsInfo = set() # set[LightMesh.UpdatedMeshLightInfo]
        
        # Currently active LightMesh:gizmo pairs
        self.activeMeshLightsInfo = set()  # set[LightMesh.ActiveMeshLightInfo]

        # VRayFur:gizmo pairs that have been updated during the current update cycle
        self.updatedFurInfo = set()
        
        # Currently active VRayFur:gizmo pairs
        self.activeFurInfo = set()

         # A snapshot of the active environment fog gizmo objects in the scene
        self.activeGizmos = set() # set of objTrackId

        # Any stats that exporters wish to publish, e.g. number of entities processed
        self.stats: list[str] = []

        # Cache mapping for exported materials:
        # Each key is a session_uid of a Blender material that has been processed for export,
        # and the corresponding value is its AttrPlugin representation.
        # This cache is used to avoid re-exporting materials that have already been exported.
        self.exportedMtls: Dict[int, AttrPlugin] = {}

        # There are some default plugins which may be referenced by multiple other plugins, 
        # e.g. mapping etc. We only need one copy of those.
        self.defaultPlugins = {}  # plugin_type: attrPlugin

        # A list of all objects for which temp meshes have beem created with the to_mesh method. 
        # to_mesh_clear() must be called on these objects after the export is complete. 
        # Currently, temp meshes are only created for objects in edit mode.
        self.objectsWithTempMeshes = []

        self.objectsWithUpdatedVisibility: dict[int, bool] = {}

        # A dictionary of (pluginName, attrName) => list of render channels. It will be filled up during the export
        # procedure. When all other plugins have already been exported, the render channel names will be
        # exported for each plugin that is on the list.
        self.pluginRenderChannels = {}

        # A dictionary of collection => list of lights. For lights outside a collection, the collection
        # name is an empty string. This info is used for exporting LightSelect and LightMix render channels.
        self.lightCollections = {}

        # A list of the emissive materials in the scene. Used to create LightSelects for the emissive materials
        # when 'Separate emissive materials' is active in the LighMix options.
        # The data type is tuple(emissivePluginName, attrName, nodeName)
        self.emissiveMaterials = [] 

        # A stack of the objects that are being currently exported.
        self.objectContext = __class__._CurrentObjectContext() 


        self.motionBlurBuilder = MotionBlurBuilder()

        # The active LightMix node in the scene or None
        self.activeLightMixNode = None


    def _copyConstruct(self, other: ExporterContext):
        self.renderer               = other.renderer
        self.rendererMode           = other.rendererMode
        self.objTrackers            = other.objTrackers
        self.nodeTrackers           = other.nodeTrackers
        self.visibleObjects         = other.visibleObjects
        self.activeInstancers       = other.activeInstancers
        self.instancesOfInstancer   = other.instancesOfInstancer
        self.persistedState         = other.persistedState
        self.allObjects             = other.allObjects
        self.dgUpdates              = other.dgUpdates
        self.objectsWithUpdatedVisibility  = other.objectsWithUpdatedVisibility
        self.commonSettings         = other.commonSettings
        self.ctx                    = other.ctx
        self.dg                     = other.dg
        self.ts                     = other.ts
        self.updatedMeshLightsInfo  = other.updatedMeshLightsInfo
        self.activeMeshLightsInfo   = other.activeMeshLightsInfo
        self.updatedFurInfo         = other.updatedFurInfo
        self.activeFurInfo          = other.activeFurInfo
        self.activeGizmos           = other.activeGizmos
        self.fullExport             = other.fullExport
        self.exportOnly             = other.exportOnly
        self.currentFrame           = other.currentFrame
        self.stats                  = other.stats
        self.exportedMtls           = other.exportedMtls
        self.defaultPlugins         = other.defaultPlugins
        self.objectsWithTempMeshes  = other.objectsWithTempMeshes
        self.pluginRenderChannels   = other.pluginRenderChannels
        self.lightCollections       = other.lightCollections
        self.emissiveMaterials      = other.emissiveMaterials
        self.objectContext          = other.objectContext
        self.motionBlurBuilder      = other.motionBlurBuilder
        self.activeLightMixNode     = other.activeLightMixNode

    @property
    def viewport(self):
        return self.rendererMode == RendererMode.Viewport

    @property
    def iprVFB(self):
        return self.rendererMode == RendererMode.Interactive

    @property
    def vantage(self):
        return self.rendererMode == RendererMode.Vantage

    @property
    def interactive(self):
        return self.viewport or self.iprVFB or self.vantage

    @property
    def production(self):
        return self.rendererMode == RendererMode.Production

    @property
    def preview(self):
        return self.rendererMode == RendererMode.Preview

    @property
    def bake(self):
        return self.rendererMode == RendererMode.Bake

    @property
    def isAnimation(self):
        return self.commonSettings.animation.use

    @property
    def sceneObjects(self):
        return self.ctx.scene.objects


    
    def calculateObjectVisibility(self):
        """ Fill the visibility and active instancers info into ExporterContext """ 

        # Compile a list of the active instancers in the scene. There are two types of instancers:
        #   1. Legacy, set through Data Properties -> Instancing
        #   2. Objects made instancers through e.g. geometry nodes
        #   3. V-Ray Fur Objects (they are also instancers if they have instancers selected)
        # The depsgraph only includes the visible instancers. We need however a list of all instancers 
        # in the scene in order to determine which ones to delete and which to only hide.
        legacyInstancers = set((getObjTrackId(o) for o in self.sceneObjects if o.is_instancer))
        instancers = set((getObjTrackId(i.parent) for i in self.dg.object_instances if i.is_instance and (i.parent is not None)))   
        furInstancers = set((getObjTrackId(o) for o in self.sceneObjects if o.vray.isVRayFur))
            
        self.activeInstancers = instancers.union(legacyInstancers).union(furInstancers)

        
        for inst in self.dg.object_instances:
            if not inst.is_instance:
                continue

            obj = inst.object.original
            objName = Names.object(obj, inst)
            
            parentId = getObjTrackId(inst.parent)
            objId = id(obj.data)

            # Avoid adding duplicates
            if objId not in self.instancesOfInstancer[parentId]:
                # Store the instance object and its geometry/data names,
                # since DepsgraphObjectInstance will be invalidated when accessed later
                self.instancesOfInstancer[parentId][objId] = (obj, objName)

        
        # Obtain the list of visible objects from the scene. The depsgraph does not include collections.
        self.visibleObjects = set([getObjTrackId(o) for o in self.ctx.scene.objects if isObjectVisible(self, o)])

        # Get a list of all objects in the scene, including objects from linked collections. We will
        # need it in order to determine which objects can be deleted from the scene vs only be hidden.
        self.allObjects = set([o for o in self.ctx.scene.objects])
        
        # Add objects from linked collections as they are not included in scene's depsgraph
        linkedCollections = [c for c in bpy.data.collections if c.library is not None]
        for c in linkedCollections:
            self.allObjects.update(c.all_objects)


        self.dgUpdates = {
            'geometry':  set((u.id.original.session_uid for u in self.dg.updates if u.is_updated_geometry)),
            'transform': set((u.id.original.session_uid for u in self.dg.updates if u.is_updated_transform)),
            'shading':   set((u.id.original.session_uid for u in self.dg.updates if u.is_updated_shading))
        }


    def linkPluginToRenderChannel(self, pluginName: str, channelLinkAttr: str, renderChannelPlugin: AttrPlugin):
        """ Store information about a link from a plugin to a render channel, i.e. that the plugin
            output should be visible in a render channel.

        Args:
            pluginName (str): The name of the plugin to show in the render channel
            channelLinkAttr (str): Attribute of type PLUGIN_LIST to which the render channel name should be added.
            renderChannelPlugin (AttrPlugin): the render channel plugin
        """
        channelKey = (pluginName, channelLinkAttr)
        self.pluginRenderChannels.setdefault(channelKey, []).append(renderChannelPlugin)


class ExporterBase(ExporterContext):
    """ This class serves only to skip copying ExporterContext's members in each 
        exporter's constructor. The alternative is to have ExporterContext as a member,
        but this leads to confusion of the names with the Blender's context object and 
        also makes accessing ExporterContext's properties unnecessarily verbose ( one more
        indirection )
    """    
    def __init__(self, exporterCtx: ExporterContext):
        super().__init__(exporterCtx)
        
    

class DataArray:
    def __init__(self, ptr = 0, count = 0, name = ""):
        self.ptr = ptr
        self.count = count
        self.name = name

    @staticmethod
    def fromAttribute(data, attributeName: str):
        """ Creates DataArray from attribute data """
        if attribute := data.attributes.get(attributeName, None):
            return DataArray(attribute.data[0].as_pointer(), len(attribute.data))
        return DataArray(0, 0) # Return an empty DataArray if the attribute is missing

@dataclass
class NdDataArray:
    buffer: ndarray
    name: str = ""


class AColor:
    """ Representation of Color with alpha channel
    """

    def __init__(self, chanelsInput):
        """ Gets a list (chanelsInput) of channels where the firs element in it is R,
            the second G, the third B, and the last is Alpha.
            The argument chanelsInput can take a list with less than 4 elements
        """
        self.channels = [0, 0, 0, 1]
        for i in range(0, min(4, len(chanelsInput))):
            self.channels[i] = chanelsInput[i]

    @property
    def r(self):
        return self.channels[0]
    @r.setter
    def r(self, value):
        self.channels[0] = value

    @property
    def g(self):
        return self.channels[1]
    @g.setter
    def g(self, value):
        self.channels[1] = value

    @property
    def b(self):
        return self.channels[2]
    @b.setter
    def b(self, value):
        self.channels[2] = value

    @property
    def a(self):
        return self.channels[3]
    @a.setter
    def a(self, value):
        self.channels[3] = value

    def __getitem__(self, i):
        return self.channels[i]
    

class PluginDesc:
    """ This is the main carrier of plugin property data. Fill-in the properties
        and pass the object to the export procedure.
    """
    def __init__(self, name, type):
        self.name = name    # unique name of the plugin
        self.type = type    # type of the plugin
        self.attrs = {}
        
        # VRay extension of the Blender's underlying object for a plugin.
        # It will have the state set through the UI 
        self.vrayPropGroup = {} 

        # 'node' should be set if the plugin is being exported as part of
        # a nodetree. It will be used by the final export procedure to determine
        # how to export meta properties.
        self.node: bpy.types.Node = None


    def resetAttribute(self, name):
        """ Reset a single attribute to its default (not set) state.
            This is achieved by setting it to empty Plugin.
        """
        self.attrs[name] = AttrPlugin()

    def setAttribute(self, name, value, overwriteExisting = True):
        """ Add/update a single attribute """
        if overwriteExisting or (name not in self.attrs):
            self.attrs[name] = value

    def appendToListAttribute(self, name, value):
        """ Appends value to list attribute """
        if (name not in self.attrs) or \
                ((attr := self.attrs[name]) and (type(attr) is AttrPlugin) and attr.isEmpty()):
            self.attrs[name] = [value]
        else:
            assert type(self.attrs[name]) is list
            self.attrs[name].append(value)

            

    def setAttributes(self, dict):
        """ Add/update multiple values at once """
        self.attrs.update(dict)

    
    def getAttribute(self, name):
        return self.attrs.get(name)
    

    def removeAttribute(self, name):
        try:
            self.attrs.pop(name)
        except KeyError:
            pass
        
    def isSetAttribute(self, name):
        return name in self.attrs
   

class AttrPlugin:
    OUTPUT_UNDEFINED = None
    OUTPUT_DEFAULT   = ''

    def __init__(self, name = "", output = OUTPUT_UNDEFINED, forceUpdate = False, pluginType = ""):
        self.name = name        # The name of the plugin

        # The name of the output socket to connect. We distinguish between 3 states:
        # 1. None - use only to tell the generic code for exporting sockets that it should
        #           set the value, i.e. to differentiate with the default ('') value.
        # 2. ''   - use plugin's default output (i.e. do not append ::sock_name when exporting)
        # 3. non-empty - use the output name set to this field
        self.output = output

        # 'True' to bypass change tracker cache on the server
        self.forceUpdate = forceUpdate

        # Some exporters need additional data about the exported plugin besides its V-Ray name.
        # The actual plugin exporter may add arbitrary data as auxData to be consumed by its caller.
        self.auxData = {}

        # The plugin type being exported. Used for type conversions.
        self.pluginType = pluginType

    def isEmpty(self):
        return self.name == ''

    def isOutputSet(self):
        return self.output is not __class__.OUTPUT_UNDEFINED

    def useDefaultOutput(self):
        self.output = __class__.OUTPUT_DEFAULT

    
class AttrListValue:
    """ Python representation of VRayBaseTypes::AttrListValue """
    
    def __init__(self):
        self.attrList = [] # members of AttrListValue instance
        self.attrType = "" # string representing the types in self.attrList

    def _getTypeAsChar(self, val):
        """ returns the type of value. If it is a list the next len(list) character are its member types """
        if type(val) is bool or type(val) is int:
            return 'i'
        elif type(val) is float:
            return 'f'
        elif type(val) is str:
            return 's'
        elif type(val) is AttrPlugin:
            return 'p'
        elif type(val) is list:
            chType = 'l'
            for v in val:
                chType += self._getTypeAsChar(v)
            return chType

    def append(self, val):
        if type(val) is AttrPlugin:
            self.attrList.append(val.name)
        elif type(val) is list:
            # Handling of plugin list members
            self.attrList.append([v.name if type(v) is AttrPlugin else v  for v in val])
        else:
            self.attrList.append(val)
        self.attrType += self._getTypeAsChar(val)

    def isEmpty(self):
        return len(self.attrList) == 0
    
    def __eq__(self, other):
        return self.attrList == other.attrList


# This definition lets the class use type hints for its own type
TSceneStats = TypeVar("TSceneStats", bound="SceneStats")


class SceneStats:
    """Statistics for a single scene export"""

    def __init__(self):
        self.uniqueMtls     = set()
        self.uniqueObjs     = set()
        self.uniquePlugins  = set()
        
        self.mtls: int      = 0
        self.objs: int      = 0
        self.plugins: int   = 0
        self.attrs: int     = 0

    def __add__(self, v: TSceneStats):
        result = SceneStats()
        result.uniqueMtls = self.uniqueMtls.union( v.uniqueMtls)
        result.uniquePlugins = self.uniquePlugins.union( v.uniquePlugins)

        result.mtls = self.mtls + v.mtls
        result.objs = self.objs + v.objs
        result.plugins = self.plugins + v.plugins
        result.attrs = self.attrs + v.attrs

        return result


class NodeContext:
    """ Context for a single node tree export """

    class _NodeItem:
        """ Context manager for the export of a single node """
        def __init__(self, ctx: NodeContext, node: bpy.types.Node):
            self.ctx = ctx
            self.node = node

        def __enter__(self):
            self.ctx._pushNode(self.node)

        def __exit__(self, *args):
            self.ctx._popNode()

    # A list of errors that have occurred during the export of the node tree.
    # It is inconvenient to pass the context to all utility functions that might need it just to 
    # be able to log errors. This facility provides a globally accessible place to store error messages.
    # The implementation relies on the fact that all export actions will be carried out sequentiallu.
    # NOTE:The list should be updated through the registerError() function. This is a 
    # provision for a possible future need to store the information per-thread if the export procedure
    # gets parallelized when Python 3.12+ is adopted.
    _errorList = set()

    def __init__(self, exporterCtx: ExporterContext, dataObj = None, scene = None, renderer = None):
        self.exporterCtx    = exporterCtx
        self.rootObj        = dataObj # bpy.types.ID to which the node tree is attached
        self.scene          = scene
        self.renderer       = renderer
        
        self.material       = None
        self.sceneObj       = None # [optional] The bpy.types.Object which holds dataObj
        self.nodeTracker: ScopedNodeTracker = None

        self.stats = SceneStats()

        self.object_context = None

        # Cache of exported (reachable) nodes to prevent duplicate exports.
        self._exportedNodes: Dict[bpy.types.Node, AttrPlugin] = {}

        # The nodetree being exported
        self.ntree: bpy.types.NodeTree

        # A stack of the parent nodes of the node that is being curently exported.
        # The current node is at the top of the stack.
        self.nodes: list[bpy.types.Node] = []

        # Virtual node counter. Used to append a unique suffix to vitual nodes
        self.virtualNodes: int = 0

        # A custom handler to be invoked for each node being exported in addition to the
        # regular export procedure. The handled is invoked right before export_utils.exportPlugin()
        # is exported for the node 
        # The signature of the handler is customHandler(nodeCtx: NodeContext, plDesc: PluginDesc)
        self.customHandler = None

        # Stack of composed transforms. The last transform is the active one (if there is one).
        self.transformStack = []

        # Reset the error list for each node tree that is to be exported
        __class__._errorList = set()

    @property
    def node(self):
        assert self.nodes, "Nodes stack is empty"
        return self.nodes[-1]

    def _pushNode(self, node: bpy.types.Node):
        self.nodes.append(node)

    def _popNode(self):
        assert self.nodes, "Nodes stack is empty"

        # Use a warning instead of "assert self.node in self._exportedNodes"
        # because if an error occurs in the export code, the node plugin won't be cached.
        # This would trigger the assertion, preventing the actual error from being printed.
        if self.node not in self._exportedNodes:
            debug.printWarning(f"Node named '{self.node.name}' is not cached!")

        self.nodes.pop()

    def push(self, node: bpy.types.Node):
        return __class__._NodeItem(self, node)

    def pushUVWTransform(self, matrix: mathutils.Matrix):
        if len(self.transformStack) == 0:
            self.transformStack.append(matrix)
        else:
            self.transformStack.append(matrix @ self.transformStack[-1])

    def popUVWTransform(self):
        if len(self.transformStack)>0:
            self.transformStack.pop()

    def getUVWTransform(self):
        if len(self.transformStack)>0:
            return self.transformStack[-1]
        return mathutils.Matrix()

    def __enter__(self):
        pass

    def __exit__(self, *args):
        assert len(self.nodes) == 0, "Incorrectly unwound nodes stack"
        self._reportErrors()

    def getTreeType(self):
        return self.ntree.vray.tree_type

    def cacheNodePlugin(self, node: bpy.types.Node, attrPlugin: AttrPlugin = AttrPlugin()):
        """ Caches the AttrPlugin of already exported V-Ray node.
            If the node doesn't have corresponding AttrPlugin, an empty one is added.
        """
        self._exportedNodes[node] = attrPlugin

    def getCachedNodePlugin(self, node: bpy.types.Node):
        """ If the given node is cached returns its AttrPlugin, otherwise it returns None
        """
        return self._exportedNodes.get(node, None)

    @staticmethod
    def registerError(msg: str):
        __class__._errorList.add(msg)

    @staticmethod
    def getErrors():
        return __class__._errorList

    def _reportErrors(self):
        for i, msg in enumerate(__class__._errorList):
            if i > 1:
                debug.reportAsync('WARNING', "Multiple node export warnings")
                break
            errMsg = f"{msg} [{self.rootObj.id_type} {self.rootObj.name}]"
            if self.sceneObj is not None:
                errMsg += f" Object: {self.sceneObj.name}"

            if self.exporterCtx.fullExport and self.exporterCtx.interactive:
                # To avoid pestering the user with status messages on each scene change, only
                # report as status during the first export after switching to viewport/IPR.
                debug.reportAsync('WARNING', errMsg)
            elif not self.exporterCtx.preview:
                # In production or subsequnt changes to the scene while viewport render is running,
                # only log the issues to the console. In preview mode, we don't want to print anything
                # at all as it could easily flood the console with messages.
                debug.printWarning(errMsg)

@dataclass
class LinkInfo:
    """ This class represents the information defined in the options:link_info field 
        of the plugin parameter description.
    """
    OBJECTS     = 'OBJECTS'
    OBJECT_DATA = 'OBJECT_DATA'

    linkType: str = OBJECTS
    fnFilter: function = lambda obj: True