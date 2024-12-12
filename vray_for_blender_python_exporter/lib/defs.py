from __future__ import annotations
from dataclasses import dataclass
from typing import Mapping, TypeVar
import bpy
from numpy import ndarray
from vray_blender.exporting.tools import isObjectVisible, TimeStats, FakeTimeStats
from vray_blender.exporting.plugin_tracker import getObjTrackId, ObjTracker, FakeObjTracker, ScopedNodeTracker, FakeScopedNodeTracker
from vray_blender.lib.blender_utils import getGeomAnimationRange, geometryObjectIt
from vray_blender.lib.motion_blur import MotionBlurBuilder

class RendererMode:
    Invalid     = 0
    Interactive = 1
    Production  = 2
    Preview     = 3
    Bake        = 4
    Viewport    = 5


class ExporterType:
    IPR_VIEWPORT = 0
    IPR_VFB = 1
    PROD = 2
    PREVIEW = 3
    ANIMATION = 4

class ProdRenderMode:
    EXPORT_VRSCENE = 0
    RENDER = 1
    CLOUD_SUBMIT = 2



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


    def __init__(self, exporterCtx: ExporterContext = None):
        from vray_blender.lib.common_settings import CommonSettings
        
        if exporterCtx:
            self._copyConstuct(exporterCtx)
            return
        
        # A raw pointer to the VRayBlenderLib's exporter(renderer) object associated with the current export
        self.renderer = None                  
        self.rendererMode: RendererMode  = RendererMode.Invalid 
        
        # If true, export everything regardless of the depsgraph update status. Otherwise, 
        # only export changes. This flag has meaning for interactive renderer mode only. 
        # For all other modes, the value should always be True
        self.fullExport: bool = True 

        # Settings values collected from the user interface
        self.commonSettings: CommonSettings = None

        # Track VRay plugins associated with scene objects. The same plugins may be referenced
        # by multiple objects. 
        self.objTrackers: Mapping[str, ObjTracker|FakeObjTracker]  = {} 
        
        # Track VRay plugins assosiated with nodes in a nodetree.
        self.nodeTrackers: Mapping[str, ScopedNodeTracker|FakeScopedNodeTracker]  = {}
        
        # A snapshot of scene objects' visibility. Blender does not provide updates when a visibility
        # of an object changes, so we need to track it ourselves.  
        self.visibleObjects = set() # set of objTrackId

        # A snapshot of the active instancer objects in the scene
        self.activeInstancers = set() # set of objTrackId
        
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

        # LightMesh:gizmo pairs active during the previous update cycle
        self.cachedMeshLightsInfo = set() # set[LightMesh.ActiveMeshLightInfo]

         # A snapshot of the active environment fog gizmo objects in the scene
        self.activeGizmos = set() # set of objTrackId

        # Any stats that exporters wish to publish, e.g. number of entities processed
        self.stats: list[str] = []

        # Materials may be shared between objects. Cache the exported materials
        # and hand out the ones from the cache on multiple rewuest for export of
        # the same material. 
        self.exportedMtls = {}  # blender_material: attrPlugin
        
        # There are some default plugins which may be referenced by multiple other plugins, 
        # e.g. mapping etc. We only need one copy of those.
        self.defaultPlugins = {}  # plugin_type: attrPlugin

        # [Prod only] Animation ranges for mesh data edits. Will be used to choose for which frames 
        # of the animation to export values. The format is {object: mathutils.Vector(X, Y)}
        self.geomAnimationRanges = {} # {Object(not ID!): Vector(start, end)

          # A list of all temp objects that have been craeted to support edit mode exports
        self.tempObjects = []

        # A stack of the objects that are being currently exported.
        self.objectContext = __class__._CurrentObjectContext() 

        
        self.motionBlurBuilder = MotionBlurBuilder()


    def _copyConstuct(self, other: ExporterContext):
        self.renderer           = other.renderer
        self.rendererMode       = other.rendererMode
        self.objTrackers        = other.objTrackers
        self.nodeTrackers       = other.nodeTrackers
        self.visibleObjects     = other.visibleObjects
        self.activeInstancers   = other.activeInstancers
        self.commonSettings     = other.commonSettings
        self.ctx                = other.ctx
        self.dg                 = other.dg
        self.ts                 = other.ts
        self.updatedMeshLightsInfo = other.updatedMeshLightsInfo
        self.cachedMeshLightsInfo = other.cachedMeshLightsInfo
        self.activeMeshLightsInfo = other.activeMeshLightsInfo
        self.activeGizmos       = other.activeGizmos
        self.fullExport         = other.fullExport
        self.stats              = other.stats
        self.exportedMtls       = other.exportedMtls
        self.defaultPlugins     = other.defaultPlugins
        self.geomAnimationRanges = other.geomAnimationRanges
        self.tempObjects        = other.tempObjects
        self.objectContext     = other.objectContext
        self.motionBlurBuilder = other.motionBlurBuilder

    @property
    def interactive(self):
        return self.rendererMode in (RendererMode.Viewport, RendererMode.Interactive)
    
    @property
    def viewport(self):
        return self.rendererMode == RendererMode.Viewport

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
    def currentFrame(self):
        return self.commonSettings.animation.frameCurrent
    
    @property
    def sceneObjects(self):
        return self.ctx.scene.objects
    
    def calculateObjectVisibility(self):
        """ Fill the visibility and actiuve instancers info into ExporterContext """ 

        if not self.interactive:
            # In interactive mode, visibility is calculated elsewhere
            self.visibleObjects = set([getObjTrackId(o) for o in self.dg.scene.objects if isObjectVisible(self, o)])
            
        if self.interactive:
            self.activeInstancers = set([getObjTrackId(o) for o in self.dg.scene.objects if o.is_instancer and o.visible_get()])
        else:
            self.activeInstancers = set([getObjTrackId(o) for o in self.dg.scene.objects if o.is_instancer and not o.hide_render])


    def calculateObjectAnimationRanges(self):
        """ Calculate the effective animation ranges for all objects in the scene. 

            These are the ranges between the first and the last keyframe for an object. 
        """
        self.geomAnimationRanges = {o.data.session_uid: getGeomAnimationRange(o) for o in geometryObjectIt(self.dg.scene.objects)}

   
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

    def __init__(self, name = "", output = OUTPUT_UNDEFINED, forceUpdate = False):
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
    """ Context for node tree traversal
    """
    def __init__(self, exporterCtx: ExporterContext, dataObj = None, scene = None, renderer = None):
        self.exporterCtx    = exporterCtx
        self.rootObj        = dataObj # bpy.types.ID to which the node tree is attached
        self.scene          = scene
        self.renderer       = renderer
        
        self.material       = None
        self.sceneObj       = None # [optional] The bpy.types.Object which holds dataObj    
        self.exportedPlugins = {}
        self.nodeTracker: ScopedNodeTracker = None

        self.stats = SceneStats()

        self.object_context = None
        
        # Store all nodes that have been exported - those are the reachable nodes. 
        # We can compare them to all the nodes to figure out which nodes should be 
        # removed from VRay
        self.exportedNodes = set()

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
        

    @property
    def node(self):
        assert self.nodes, "Nodes stack is empty"
        return self.nodes[-1]
    
    def _pushNode(self, node: bpy.types.Node):
        self.nodes.append(node)

    def _popNode(self):
        assert self.nodes, "Nodes stack is empty"
        self.nodes.pop()

    def push(self, node: bpy.types.Node):
        self._pushNode(node)
        return self
    
    def __enter__(self):
        # Node has already been pushed in the push() method
        pass

    def __exit__(self, *args):
        self._popNode()


    def getTreeType(self):
        return self.ntree.vray.tree_type


@dataclass
class LinkInfo:
    """ This class represents the information defined in the options:link_info field 
        of the plugin parameter description.
    """
    OBJECTS     = 'OBJECTS'
    OBJECT_DATA = 'OBJECT_DATA'

    linkType: str = OBJECTS
    fnFilter: function = lambda obj: True