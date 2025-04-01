
import bpy
import hashlib
import mathutils
import sys

from vray_blender import debug
from vray_blender.exporting.node_exporters.uvw_node_export import exportDefaultUVWGenChannel
from vray_blender.exporting.tools import COLOR_SOCK_TYPES, FLOAT_SOCK_TYPES, getVRayBaseSockType, removeInputSocketLinks
from vray_blender.plugins import PLUGIN_MODULES, getPluginAttr, getPluginInputNodeDesc, getPluginModule
from vray_blender.lib import attribute_types, attribute_utils
from vray_blender.lib.attribute_utils import toAColor
from vray_blender.lib.defs import AColor, AttrPlugin, NodeContext, PluginDesc
from vray_blender.nodes.tools import isVrayNode
from vray_blender.nodes.utils import selectedObjectTagUpdate


DYNAMIC_SOCKET_OVERRIDES = {}
DYNAMIC_SOCKET_CLASSES = set()
DYNAMIC_SOCKET_CLASS_NAMES = set()

# Socket links colors defaults
HIDDEN_SOCKET_COLOR       = (1.0, 1.0, 1.0, 1.0)

CHANNEL_SOCKET_COLOR      = (0.0, 0.83, 0.63, 1.0)
COLOR_SOCKET_COLOR        = (1.000, 0.819, 0.119, 1.0)
COORDS_SOCKET_COLOR       = (0.250, 0.273, 0.750, 1.0)
EFFECT_SOCKET_COLOR       = (0.92, 0.45, 0.5, 1.0)
GEOMETRY_SOCKET_COLOR     = (0.15, 0.15, 0.15, 1.0)
NUMBER_SOCKET_COLOR       = (0.1, 0.4, 0.4, 1.0)
MATERIAL_SOCKET_COLOR     = (0.156, 0.750, 0.304, 1.0)
PLUGIN_SOCKET_COLOR       = (1.0, 1.0, 1.0, 1.0)
OBJ_PROP_SOCKET_COLOR     = (0.8, 0.2, 0.5, 1.0)
TRANSFORM_SOCKET_COLOR    = (0.075, 0.619, 1.0, 1.0)
VECTOR_SOCKET_COLOR       = (0.388, 0.388, 0.78, 1.0)



def getDynamicSocketClassName(pluginType, socketTypeName, attrName):
    """ Construct name for a dynamic socket class.

        Dynamic socket classes are created for the properties read from plugin descriptions. 
        They are based on custom vray socket classes
    """
    def abbreviateTitle(str):
        return ''.join(filter(lambda c: c >= 'A' and c <= 'Z', str))

    # make the typeName unique per type, node and attribute
    suffix = '%s_%s' % (abbreviateTitle(pluginType), attrName)
    typeName = '%s_%s' % (socketTypeName, suffix)
    # bpy has obscene limitation of 64 symbols for class name!
    if len(typeName) >= 64:
        hashed = hashlib.sha1(typeName.encode('utf-8')).hexdigest()
        hashLen = 64 - 1 - (len(socketTypeName) + 1)
        typeName = "%s_%s" % (socketTypeName, hashed[0:hashLen])
    return typeName


def registerDynamicSocketClass(pluginType, socketTypeName, attrName):
    """ Construct and register a dynamic socket class.

        'Dynamic' socket classes are created for the properties read from plugin descriptions. 
        The reason why 'static' socket classes cannot be used for them is because the socket 
        parameters should be registered Blender properties whose attributes' values (e.g. min, max, 
        default etc) are read-only after registration. Even if two sockets are of the same underlying 
        type, different classes should be registered for them if their attributes differ. It inconvenient
        to keep track of socket types that are shared between different plugin parameters, so a dedicated 
        class is registered for each plugin parameter. 
    """
    import copy

    global DYNAMIC_SOCKET_CLASSES
    global DYNAMIC_SOCKET_CLASS_NAMES

    if not attrName:
        debug.printError(f"Could not register dynamic socket type for {pluginType}::{socketTypeName}")
        return

    if socketTypeName not in DYNAMIC_SOCKET_OVERRIDES:
        return
    
    pluginModule = getPluginModule(pluginType)
    dynamicTypeName = getDynamicSocketClassName(pluginType, socketTypeName, attrName)

    if dynamicTypeName not in DYNAMIC_SOCKET_CLASS_NAMES:
        socketTypeAttributes = {
            'bl_idname': dynamicTypeName,
            '__annotations__': {}
        }
        
        # Dynamic sockets may list one property whose attributes will be overridden 
        # with the info specified in the plugin description.
        if typeInfo := DYNAMIC_SOCKET_OVERRIDES[socketTypeName]:
            overrideName = typeInfo[0]
            overrideType = typeInfo[1]
            # Failing to copy here will result in all sockets of this base type sharing the same data
            overrideParams = copy.deepcopy(typeInfo[2]) 
            attrDesc = attribute_utils.getAttrDesc(pluginModule, attrName)

            # Although the socket is just a proxy to the data in the property group, we need
            # to set the non-data attributes of its value member explicitly. If we don't do this
            # the UX of the socket will be different from that of the property in the property group.
            overrideParams['name'] = attribute_utils.getAttrDisplayName(attrDesc)
            attribute_utils.setAttrDescription(overrideParams, attrDesc)
            attribute_utils.setAttrSubtype(overrideParams, attrDesc)
            attribute_utils.setAttrPrecision(overrideParams, attrDesc)
            attribute_utils.setAttrLimits(overrideParams, attrDesc)
            attribute_utils.setAttrOptions(overrideParams, attrDesc, pluginModule)

            socketTypeAttributes['__annotations__'][overrideName] = overrideType(**overrideParams)

        socketBaseClass = getattr(sys.modules[__name__], socketTypeName)
        
        # Some sockets require custom registration info for their properties (not the default set in 
        # the DYNAMIC_SOCKET_OVERRIDES list)
        if regFunction := getattr(socketBaseClass, 'getPropertyRegistrationInfo', None):
            attrDesc = getPluginAttr(pluginModule, attrName)
            registrationInfo = regFunction(attrDesc)

            for propInfo in registrationInfo:
                propName = propInfo[0]
                propRegValue = propInfo[1]

                socketTypeAttributes['__annotations__'][propName] = propRegValue

        newType = type(
            dynamicTypeName,
            (socketBaseClass,),
            socketTypeAttributes
        )

        bpy.utils.register_class(newType)

        DYNAMIC_SOCKET_CLASSES.add(newType)
        DYNAMIC_SOCKET_CLASS_NAMES.add(dynamicTypeName)



def addInput(node, socketType, socketName, attrName='', pluginType='', visible=True, isMultiInput=False):
    """ Add an input socket to a node.

        @param node - the parent node for the socket. For dynamic sockets, its vray_plugin field should be set. 
        @param socketType - the name of the socket class (e.g. VRaySocketInt)
        @param socketName - the label to show for the socket
        @param attrName - plugin property attribute holding the data to show in the socket
        @param visible - True to draw the socket in both the node and the property pages. False to only
                    draw it in the property pages (some sockets are added to the node so that their
                    values could be animated. Blender has a limitation of being able to animate only
                    direct children of a node, and not e.g. the values in the attached property group) 
    """

    baseType = socketType
    
    # Input sockets may or may not be backed by a plugin property. For every socket of the first kind, 
    # a dedicated class is registered during the VRay addon initialization. The second kind is bound to 
    # data that is not directly exported to VRay but is part of node operation logic.

    if attrName and (socketType in DYNAMIC_SOCKET_OVERRIDES):
        dynamicType = getDynamicSocketClassName(pluginType, socketType, attrName)
        if dynamicType not in DYNAMIC_SOCKET_CLASS_NAMES:
            raise Exception(f"Dynamic socket class not registered for {node.vray_plugin}::{socketType}::{socketName}: {dynamicType}")
        
        socketType = dynamicType
        
    createdSocket: bpy.types.NodeSocket = node.inputs.new(socketType, socketName, use_multi_input=isMultiInput)
    
    if not hasattr(createdSocket, 'vray_attr'):
        debug.printError(f"Created a non-VRay socket of type {socketType}. Socket class must descend from VRaySocket.")
    
    if createdSocket.name != socketName:
        # Blender might change the name if a socket with the same name already exists.
        debug.printError(f"Socket name was changed during creation, probably due to a name conflict: {socketType}::{socketName}")

    createdSocket.hide = not visible
    createdSocket.show_expanded = True
    createdSocket.vray_socket_base_type = baseType
    createdSocket.vray_attr = attrName
    createdSocket.vray_plugin = pluginType

    return createdSocket


def addOutput(node, socketType, socketName, attrName=None):
    if socketName in node.outputs:
        return

    createdSocket = node.outputs.new(socketType, socketName)

    createdSocket.vray_socket_base_type = socketType
    if attrName is not None:
        createdSocket.vray_attr = attrName


def removeInputs(node, sockNames:list[str], removeLinked=False):
    """ Removes several input sockets in a transactional manner """
    
    sockets = [s for s in node.inputs if (s.name in sockNames) and (removeLinked or not s.is_linked)]

    # Only remove the sockets if all of them can be removed
    if len(sockets) == len(sockNames):
        for s in sockets:
            # Remove links before deleting the socket,
            # as nodes connected to it will also be affected
            removeInputSocketLinks(s)

            node.inputs.remove(s)

        return True
    
    return False


def _isConnectionMuted(inputSocket):
    def getConnectedNode(nodeSocket):
        for l in nodeSocket.links:
            if l.from_node:
                return l.from_node
        return None

    muted = False
    conNode = getConnectedNode(inputSocket)
    if conNode:
        muted = conNode.mute
    return muted



def _wrapperSocketSetItem(self, value):
    propGroup = getattr(self.node, self.vray_plugin)
    setattr(propGroup, self.vray_attr, value)


def _wrapperSocketSetItemWithConv(self, value, convFunc):
    propGroup = getattr(self.node, self.vray_plugin)
    setattr(propGroup, self.vray_attr, convFunc(value))


def _wrapperSocketGetItem(self):
    propGroup = getattr(self.node, self.vray_plugin)
    return getattr(propGroup, self.vray_attr)


def _wrapperSocketGetItemWithConv(self, convFunc):
    propGroup = getattr(self.node, self.vray_plugin)
    return convFunc(getattr(propGroup, self.vray_attr))


class VRaySocket(bpy.types.NodeSocket):
    # The type of the plugin which holds the attribute specified in vray_attr. 
    # vray_plugin and vray_attr should both be either set or unset.
    vray_attr: bpy.props.StringProperty(
        name = "V-Ray Attribute",
        description = "V-Ray plugin's attribute name",
        options = {'HIDDEN'},
        default = ""
    )

    # The type of the pugin which holds the attribute specified in vray_attr. 
    # vray_plugin and vray_attr should both be either set or unset.
    vray_plugin: bpy.props.StringProperty(
        name = "V-Ray Plugin",
        description = "Type of the V-Ray plugin containing vray_attr",
        options = {'HIDDEN'},
        default = ""
    )
    
    # Dynamic socket types derive from a 'base' (or 'template') socket type. The name of the 
    # template type is used to query for socket properties.
    vray_socket_base_type: bpy.props.StringProperty(
        name = "V-Ray Socket Base Type",
        description = "V-Ray socket template type",
        options = {'HIDDEN'},
        default = ""
    )

    # Checks if a node is linked to a valid V-Ray node, even through a 'NodeReroute' chain.
    def _isNodeChainLinked(self, node):
        if (node.bl_idname != "NodeReroute"):
            if not isVrayNode(node):
                NodeContext.registerError(f"Skipped export of non V-Ray node: '{node.name}'.")
                return False
            return True
        elif node.inputs[0].links:
            return self._isNodeChainLinked(node.inputs[0].links[0].from_node)
        return False

    def shouldExportLink(self):
        if self.is_linked:
            return self._isNodeChainLinked(self.links[0].from_node)

        return False

    def exportLinked(self, pluginDesc, attrDesc, linkValue):
        pluginDesc.setAttribute(attrDesc['attr'], linkValue)

    def exportUnlinked(self, nodeCtx: NodeContext, pluginDesc, attrDesc):
        pluginDesc.resetAttribute(attrDesc['attr'])


class VRayValueSocket(VRaySocket):
    def setValue(self, value):
        """ Currently, this method is just syntactic sugar. 
            TODO: Add conversions for the target data types
        """
        self.value = value

    def exportUnlinked(self, nodeCtx: NodeContext, pluginDesc, attrDesc):
        # Only export the attribute if custom export code has not set a value for it yet.
        # This might happen if the attributes are exported out-of-order, e.g. a meta
        # property which exports one or more real attributes.
        pluginDesc.setAttribute(attrDesc['attr'], self.value, overwriteExisting=False)


    def copy(self, dest):
        dest.value = self.value

class VRaySocketMult(VRayValueSocket):
    multiplier: bpy.props.FloatProperty(
        name        = "Multiplier",
        description = "Blend factor between the texture and the value",
        subtype     = 'PERCENTAGE',
        precision   = 0,
        min         = 0.0,
        soft_max    = 100.0,
        default     = 100.0,
        update      = selectedObjectTagUpdate
    )

    _USE_MULTIPLICATION_SOCKET_TYPES = COLOR_SOCK_TYPES + FLOAT_SOCK_TYPES

    def computeLinkMultiplier(self, fromSock):
        """ Compute the multuplier that has to be used on the link with fromSocket.

            The presence of this function indicates whether multiplication converter plugins
            should be exported for a given socket type.

            @return the multiplier value, if multiplication is needed, else None.
        """
        # Epsilon is deliberately not used for the multiplier comparison. We want to skip multiplication only if 
        # the value is not a result of a computation.
        if (self.multiplier == 100.0) or (getVRayBaseSockType(fromSock) not in self._USE_MULTIPLICATION_SOCKET_TYPES):
            # No multiplication needed
            return None
        
        return self.multiplier / 100.0
        

    def draw_property(self, context, layout, node, text, expand=False, slider=True):
        """ Draw as property in a property page, not as a socket.
            This method is used to show socket values on property pages.
        """
        layout.prop(self, 'value', text=text, expand=expand, slider=slider)


    def draw(self, context, layout, node, text):
        if self.is_output:
            layout.label(text=text)
        elif self.is_linked and not _isConnectionMuted(self):
            split = layout.split(factor=0.4)
            split.prop(self, 'value', text="")
            split.prop(self, 'multiplier', text=text)
        elif type(self.value) is mathutils.Color:
            split = layout.split(factor=0.4)
            split.prop(self, 'value', text="")
            split.label(text=text)
        else:
            layout.prop(self, 'value', text=text)

    def copy(self, dest):
        dest.multiplier = self.multiplier
        super().copy(dest)



class VRaySocketUse(VRayValueSocket):
    use: bpy.props.BoolProperty(
        name        = "Use",
        description = "Use socket",
        default     = True,
        update = selectedObjectTagUpdate
    )


    def shouldExportLink(self):
        return VRaySocket.shouldExportLink(self) and self.use
    

    def copy(self, dest):
        dest.use = self.use
        super().copy(dest)

 ######   ########  #######  ##     ## ######## ######## ########  ##    ##
##    ##  ##       ##     ## ###   ### ##          ##    ##     ##  ##  ##
##        ##       ##     ## #### #### ##          ##    ##     ##   ####
##   #### ######   ##     ## ## ### ## ######      ##    ########     ##
##    ##  ##       ##     ## ##     ## ##          ##    ##   ##      ##
##    ##  ##       ##     ## ##     ## ##          ##    ##    ##     ##
 ######   ########  #######  ##     ## ########    ##    ##     ##    ##

class VRaySocketGeom(VRayValueSocket):
    bl_idname = 'VRaySocketGeom'
    bl_label  = 'Geomtery socket'

    value: bpy.props.StringProperty(
        name = "Geometry",
        description = "Geometry",
        default = ""
    )

    def draw(self, context, layout, node, text):
        layout.label(text=text)

    def draw_color(self, context, node):
        return GEOMETRY_SOCKET_COLOR


 #######  ########        ## ########  ######  ########
##     ## ##     ##       ## ##       ##    ##    ##
##     ## ##     ##       ## ##       ##          ##
##     ## ########        ## ######   ##          ##
##     ## ##     ## ##    ## ##       ##          ##
##     ## ##     ## ##    ## ##       ##    ##    ##
 #######  ########   ######  ########  ######     ##

class VRaySocketObject(VRayValueSocket):
    """ Used for properties of type PLUGIN """
    bl_idname = 'VRaySocketObject'
    bl_label  = 'Object socket'

    value: bpy.props.StringProperty(
        name = "Object",
        description = "Object",
        default = "",
        update = selectedObjectTagUpdate
    )

    def draw(self, context, layout, node, text):
        layout.label(text=text)

    def draw_color(self, context, node):
        return PLUGIN_SOCKET_COLOR
    
    def exportUnlinked(self, nodeCtx: NodeContext, pluginDesc, attrDesc):
        attrValue = self.value
        
        if not self.value:
            # This will explicitly reset the property value in V-Ray
            attrValue = AttrPlugin()
            
        pluginDesc.setAttribute(attrDesc['attr'], attrValue)
        

class VRaySocketObjectList(VRaySocket):
    """ A list of plugins.

        Accepts either a single plugin or a list of plugins which are exported as
        list[AttrPlugun].
    """
    bl_idname = 'VRaySocketObjectList'
    bl_label  = 'Object list'

    def draw(self, context, layout, node, text):
        layout.label(text=text)

    def draw_color(self, context, node):
        return PLUGIN_SOCKET_COLOR


    def exportLinked(self, pluginDesc, attrDesc, linkValue):
        # Allow plugging in sockets with single-plugin output
        if not isinstance(linkValue, list):
            assert type(linkValue) is AttrPlugin
            linkValue = [] if linkValue.isEmpty() else [linkValue]

        # Export the object list
        super().exportLinked(pluginDesc, attrDesc, linkValue)
        

    def exportUnlinked(self, pluginDesc, attrDesc, linkValue):
        # The property will be exported by the template. This method definition
        # is intended to suppress the one in the base class.
        pass


class VRaySocketIncludeExcludeList(VRaySocketObjectList):
    """ Include/exclude object list. 

        This socket combines two plugin properties: a list and a boolean telling whether
        the list is exclusion or inclusion one.
    """
    bl_idname = 'VRaySocketInlcudeExcludeList'
    bl_label  = 'Include/exclude list'

    def setItem(self, value, attrName):
        propGroup = getattr(self.node, self.vray_plugin)
        setattr(propGroup, attrName, value)


    def getItem(self, attrName):
        propGroup = getattr(self.node, self.vray_plugin)
        return getattr(propGroup, attrName)    
    

    @staticmethod
    def getPropertyRegistrationInfo(attrDesc):
        if not (linkedAttrName := attrDesc.get('inclusion_mode_prop')):
            return []
        
        inclusionMode = bpy.props.EnumProperty(
            items = [
                ('0', 'Exclude', 'Exclude selected items'),
                ('1', 'Include', 'Include only selected items')
            ],
            set = lambda s, v: VRaySocketIncludeExcludeList.setItem(s, v, linkedAttrName),
            get = lambda s: VRaySocketIncludeExcludeList.getItem(s, linkedAttrName),
            name = 'Mode',
            default = '0'
        )
        return [('inclusionMode', inclusionMode)]
    

    def draw(self, context, layout, node, text):
        row = layout.row()
        row.label(text=text)

        if self.is_linked:
            row.prop(self, 'inclusionMode', text='Type', expand=True)


    def draw_color(self, context, node):
        return PLUGIN_SOCKET_COLOR


    def exportLinked(self, pluginDesc, attrDesc, linkValue):
        # Export the object list
        super().exportLinked(pluginDesc, attrDesc, linkValue)
        
        if propInclude := attrDesc.get('inclusion_mode_prop'):
            pluginDesc.setAttribute(propInclude, self.inclusionMode == '1')


#### ##    ## ########
 ##  ###   ##    ##
 ##  ####  ##    ##
 ##  ## ## ##    ##
 ##  ##  ####    ##
 ##  ##   ###    ##
#### ##    ##    ##

class VRaySocketInt(VRayValueSocket):
    bl_idname = 'VRaySocketInt'
    bl_label  = 'Integer socket'

    value: bpy.props.IntProperty(
        name = "Value",
        description = "Value",
        min = -1024,
        max =  1024,
        soft_min = -100,
        soft_max =  100,
        default = 1,
        update = selectedObjectTagUpdate
    )

    def draw(self, context, layout, node, text):
        if self.is_linked or self.is_output:
            layout.label(text=text)
        else:
            layout.prop(self, 'value', text=text)

    def draw_color(self, context, node):
        return NUMBER_SOCKET_COLOR
    


class VRaySocketIntNoValue(VRaySocket):
    bl_idname = 'VRaySocketIntNoValue'
    bl_label  = 'Integer socket'

    def draw(self, context, layout, node, text):
        layout.label(text=text)

    def draw_color(self, context, node):
        return NUMBER_SOCKET_COLOR


class VRaySocketBool(VRayValueSocket):
    bl_idname = 'VRaySocketBool'
    bl_label  = 'Boolean socket'

    value: bpy.props.BoolProperty(
        name = "Value",
        description = "Value",
        default = False,
        update = selectedObjectTagUpdate
    )

    def draw(self, context, layout, node, text):
        if self.is_linked or self.is_output:
            layout.label(text=text)
        else:
            layout.prop(self, 'value', text=text)

    def draw_color(self, context, node):
        return NUMBER_SOCKET_COLOR
    

class VRaySocketEnum(VRayValueSocket):
    bl_idname = 'VRaySocketEnum'
    bl_label  = 'Enum socket'

    def setItem(self, selectedIndex):
        # Blender uses Int for enum values, but they are strings in the property group.
        # Convert the incoming index to the enum value's identifier sstring.

        pluginModule = getPluginModule(self.node.vray_plugin)
        attrDesc = getPluginAttr(pluginModule, self.vray_attr)
        newValue = attrDesc['items'][selectedIndex][0]    
        
        propGroup = getattr(self.node, self.node.vray_plugin)
        setattr(propGroup, self.vray_attr, newValue)
    

    def getItem(self):
        # Blender uses Int for enum values, but they are strings in the property group.
        # Return the index of the currently selected item.
        propGroup = getattr(self.node, self.node.vray_plugin)
        selectedItem = getattr(propGroup, self.vray_attr)
        pluginModule = getPluginModule(self.node.vray_plugin)
        attrDesc = getPluginAttr(pluginModule, self.vray_attr)

        for i, item in enumerate(attrDesc['items']):
            if item[0] == selectedItem:
                return i
        return -1
    
    @staticmethod
    def getPropertyRegistrationInfo(attrDesc):
        prop = bpy.props.EnumProperty(
                name = "Value",
                description = attrDesc.get('desc', 'Value'),
                items = tuple(tuple(sub) for sub in attrDesc['items']),
                set = VRaySocketEnum.setItem,
                get = VRaySocketEnum.getItem
            )
        return [('value', prop)]

    def draw_property(self, context, layout, node, text, expand=False, slider=True):
        layout.prop(self, 'value', text=text, expand=expand, slider=slider)

    def draw(self, context, layout, node, text):
        layout.label(text=text)

    def draw_color(self, context, node):
        return HIDDEN_SOCKET_COLOR


######## ##        #######     ###    ########
##       ##       ##     ##   ## ##      ##
##       ##       ##     ##  ##   ##     ##
######   ##       ##     ## ##     ##    ##
##       ##       ##     ## #########    ##
##       ##       ##     ## ##     ##    ##
##       ########  #######  ##     ##    ##

class VRaySocketFloat(VRaySocketMult):
    bl_idname = 'VRaySocketFloat'
    bl_label  = 'Float socket'

    value: bpy.props.FloatProperty(
        name = "Value",
        description = "Value",
        precision = 3,
        soft_min = -100.0,
        soft_max =  100.0,
        default = 0.5,
        update = selectedObjectTagUpdate
    )
    
    def draw_color(self, context, node):
        return NUMBER_SOCKET_COLOR


class VRaySocketFloatNoValue(VRaySocket):
    bl_idname = 'VRaySocketFloatNoValue'
    bl_label  = 'Float socket'

    def draw(self, context, layout, node, text):
        layout.label(text=text)

    def draw_color(self, context, node):
        return NUMBER_SOCKET_COLOR


class VRaySocketWeight(VRayValueSocket):
    bl_idname = 'VRaySocketWeight'
    bl_label  = 'Weight socket'

    value: bpy.props.FloatProperty(
        name = "Value",
        description = "Weight from 0 to 1",
        precision = 3,
        min = 0.0,
        max = 1.0,
        default = 0.5,
        update = selectedObjectTagUpdate
    )
    
    def draw(self, context, layout, node, text):
        if self.is_linked or self.is_output:
            layout.label(text=text)
        else:
            layout.prop(self, 'value', text=text)

    def draw_color(self, context, node):
        return NUMBER_SOCKET_COLOR
    
######## ##        #######     ###    ########     ######   #######  ##        #######  ########
##       ##       ##     ##   ## ##      ##       ##    ## ##     ## ##       ##     ## ##     ##
##       ##       ##     ##  ##   ##     ##       ##       ##     ## ##       ##     ## ##     ##
######   ##       ##     ## ##     ##    ##       ##       ##     ## ##       ##     ## ########
##       ##       ##     ## #########    ##       ##       ##     ## ##       ##     ## ##   ##
##       ##       ##     ## ##     ##    ##       ##    ## ##     ## ##       ##     ## ##    ##
##       ########  #######  ##     ##    ##        ######   #######  ########  #######  ##     ##

class VRaySocketFloatColor(VRaySocketMult):
    bl_idname = 'VRaySocketFloatColor'
    bl_label  = 'Float color socket'

    value: bpy.props.FloatProperty(
        name = "Value",
        description = "Value",
        precision = 3,
        min = -100000.0,
        max =  100000.0,
        soft_min = -100.0,
        soft_max =  100.0,
        default = 0.5,
        update = selectedObjectTagUpdate
    )

    def draw_color(self, context, node):
        return NUMBER_SOCKET_COLOR


 ######   #######  ##        #######  ########
##    ## ##     ## ##       ##     ## ##     ##
##       ##     ## ##       ##     ## ##     ##
##       ##     ## ##       ##     ## ########
##       ##     ## ##       ##     ## ##   ##
##    ## ##     ## ##       ##     ## ##    ##
 ######   #######  ########  #######  ##     ##

class VRaySocketColor(VRaySocketMult):
    bl_idname = 'VRaySocketColor'
    bl_label  = 'Color socket'
        
    value: bpy.props.FloatVectorProperty(
        name = "Color",
        description = "Color",
        subtype = 'COLOR',
        min = 0.0,
        max = 1.0,
        soft_min = 0.0,
        soft_max = 1.0,
        update = selectedObjectTagUpdate
    )

    def draw_color(self, context, node):
        return COLOR_SOCKET_COLOR
    
# Socket that can be a color and also connected to a texture
class VRaySocketColorTexture(VRaySocketMult):
    """ Meta socket which exports one of 2 bound properties depending on whether it is linked or not. 

        Bound properties:
        - a texture in 'tex_prop' if linked
        - a color in 'color_prop' if not linked
    """
    bl_idname = 'VRaySocketColorTexture'
    bl_label  = 'Color socket'

    value: bpy.props.FloatVectorProperty(
        name = "Color",
        description = "Color",
        subtype = 'COLOR',
        min = 0.0,
        max = 1.0,
        soft_min = 0.0,
        soft_max = 1.0,
        default = mathutils.Color((1.0, 1.0, 1.0)),
        update = selectedObjectTagUpdate
    )

    def exportLinked(self, pluginDesc, attrDesc, linkValue):
        super().exportLinked(pluginDesc, attrDesc, linkValue)
        if propUseTex := attrDesc.get('use_tex_prop'):
            pluginDesc.setAttribute(propUseTex, True)



    def exportUnlinked(self, nodeCtx: NodeContext, pluginDesc, attrDesc):
        pluginDesc.setAttribute(attrDesc['attr'], mathutils.Color(self.value[:]))
        if propUseTex := attrDesc.get('use_tex_prop'):
            pluginDesc.setAttribute(propUseTex, False)



    def draw_color(self, context, node):
        return COLOR_SOCKET_COLOR

    
class VRaySocketAColor(VRaySocketMult):
    bl_idname = 'VRaySocketAColor'
    bl_label  = 'Color with Alpha socket'

    value: bpy.props.FloatVectorProperty(
        name = "Color",
        description = "Color",
        subtype = 'COLOR',
        min = 0.0,
        max = 1.0,
        soft_min = 0.0,
        soft_max = 1.0,
        size = 4,
        update = selectedObjectTagUpdate,
        default = AColor((1.0, 1.0, 1.0, 1.0))
    )

    def exportUnlinked(self, nodeCtx: NodeContext, pluginDesc, attrDesc):
        pluginDesc.setAttribute(attrDesc['attr'], AColor(self.value[:]))


    def draw_color(self, context, node):
        return COLOR_SOCKET_COLOR


class VRaySocketColorNoValue(VRaySocket):
    bl_idname = 'VRaySocketColorNoValue'
    bl_label  = 'Color socket'

    def draw(self, context, layout, node, text):
        layout.label(text=text)

    def draw_color(self, context, node):
        return COLOR_SOCKET_COLOR


class VRaySocketPluginUse(VRaySocketUse):
    """ Meta socket which decides whether to export a linked node based on the value 
        of a separate boolean property. The boolean property is drawn as a checkbox.
        
        Bound properties:
        - a plugin in 'target_prop'. Exported if the property in 'use_prop' is True
        - a boolean property in 'use_prop' 
    """
    bl_idname = 'VRaySocketPluginUse'
    bl_label  = "Plugin socket with a 'use' flag"

    def setItem(self, value, attrName):
        propGroup = getattr(self.node, self.vray_plugin)
        setattr(propGroup, attrName, value)


    def getItem(self, attrName):
        propGroup = getattr(self.node, self.vray_plugin)
        return getattr(propGroup, attrName)    
    

    @staticmethod
    def getPropertyRegistrationInfo(attrDesc):
        linkedAttrName = attrDesc['use_prop']
        useProp = bpy.props.BoolProperty(
                name = "Use",
                description = attrDesc.get('desc', "Use input"),
                set = lambda s, v: VRaySocketPluginUse.setItem(s, v, linkedAttrName),
                get = lambda s: VRaySocketPluginUse.getItem(s, linkedAttrName)
            )
        return [('use', useProp)]
    
    
    def exportLinked(self, pluginDesc, attrDesc, linkValue):
        pluginDesc.setAttribute(attrDesc['target_prop'], linkValue)


    def exportUnlinked(self, nodeCtx: NodeContext, pluginDesc, attrDesc):
        pluginDesc.setAttribute(attrDesc['target_prop'], "")


    def draw_property(self, context, layout, node, text, expand=False, slider=True):
        """ Draw as property in a property page, not as a socket.
            This method is used to show socket values on property pages.
        """
        # Show the description for the 'use' property as a label, not the one for the
        # meta property.
        pluginModule = getPluginModule(node.vray_plugin)
        metaAttr = getPluginAttr(pluginModule, self.vray_attr)
        targetAttr = getPluginAttr(pluginModule, metaAttr['use_prop'])
        attrLabel = targetAttr.get('desc', text)
        
        layout.prop(self, 'use', text=attrLabel, expand=expand, slider=slider)


    def draw(self, context, layout, node, text):
        layout.active = self.use
        split = layout.split()
        row = split.row(align=True)
        row.label(text=text)
        row.prop(self, 'use', text="")
    

    def draw_color(self, context, node):
        return COLOR_SOCKET_COLOR



class VRaySocketColorMult(VRaySocketMult):
    bl_idname = 'VRaySocketColorMult'
    bl_label  = 'Color socket with multiplier'

    value: bpy.props.FloatVectorProperty(
        name = "Color",
        description = "Color",
        subtype = 'COLOR',
        min = 0.0,
        max = 1.0,
        soft_min = 0.0,
        soft_max = 1.0,
        default = mathutils.Color((1.0, 1.0, 1.0)),
        update = selectedObjectTagUpdate
    )

    def draw_color(self, context, node):
        return COLOR_SOCKET_COLOR


##     ## ########  ######  ########  #######  ########
##     ## ##       ##    ##    ##    ##     ## ##     ##
##     ## ##       ##          ##    ##     ## ##     ##
##     ## ######   ##          ##    ##     ## ########
 ##   ##  ##       ##          ##    ##     ## ##   ##
  ## ##   ##       ##    ##    ##    ##     ## ##    ##
   ###    ########  ######     ##     #######  ##     ##

class VRaySocketVector(VRayValueSocket):
    bl_idname = 'VRaySocketVector'
    bl_label  = 'Vector socket'

    value: bpy.props.FloatVectorProperty(
        name = "Vector",
        description = "Vector",
        subtype = 'TRANSLATION',
        soft_min = -1.0,
        soft_max = 1.0,
        default = mathutils.Vector((0.0, 0.0, 0.0)),
        update = selectedObjectTagUpdate
    )

    def draw(self, context, layout, node, text):
        if self.is_linked or self.is_output:
            layout.label(text=text)
        else:
            layout.label(text=text)
            layout.prop(self, 'value', text="")

    def draw_color(self, context, node):
        return VECTOR_SOCKET_COLOR


 ######   #######   #######  ########  ########   ######
##    ## ##     ## ##     ## ##     ## ##     ## ##    ##
##       ##     ## ##     ## ##     ## ##     ## ##
##       ##     ## ##     ## ########  ##     ##  ######
##       ##     ## ##     ## ##   ##   ##     ##       ##
##    ## ##     ## ##     ## ##    ##  ##     ## ##    ##
 ######   #######   #######  ##     ## ########   ######

class VRaySocketCoords(VRayValueSocket):
    bl_idname = 'VRaySocketCoords'
    bl_label  = 'Mapping socket'

    value: bpy.props.StringProperty(
        name        = "UVW Mapping",
        description = "UVW Mapping",
        default     = ""
    )

    def draw(self, context, layout, node, text):
        layout.label(text=text)

    def draw_color(self, context, node):
        return COORDS_SOCKET_COLOR

    def exportUnlinked(self, nodeCtx: NodeContext, pluginDesc: PluginDesc, attrDesc):
        uvwGenPlugin = AttrPlugin()
        
        # Do not implicitly attach mapping nodes to mapping nodes themselves
        if not pluginDesc.type.startswith('UVWGen'):
            uvwGenPlugin = exportDefaultUVWGenChannel(nodeCtx)
        
        pluginDesc.setAttribute(attrDesc['attr'], uvwGenPlugin)


class VRaySocketBRDF(VRayValueSocket):
    bl_idname = 'VRaySocketBRDF'
    bl_label  = 'BRDF socket'

    value: bpy.props.StringProperty(
        name        = "Defautl BRDF",
        description = "Defautl BRDF",
        default     = "BRDFNOBRDFISSET"
    )

    def draw(self, context, layout, node, text):
        layout.label(text=text)

    def draw_color(self, context, node):
        return MATERIAL_SOCKET_COLOR

 ######  ######## ########  #### ##    ##  ######   
##    ##    ##    ##     ##  ##  ###   ## ##    ##  
##          ##    ##     ##  ##  ####  ## ##        
 ######     ##    ########   ##  ## ## ## ##   #### 
      ##    ##    ##   ##    ##  ##  #### ##    ##  
##    ##    ##    ##    ##   ##  ##   ### ##    ##  
 ######     ##    ##     ## #### ##    ##  ######   

class VRaySocketString(VRayValueSocket):
    bl_idname = 'VRaySocketString'
    bl_label  = 'String socket'

    value: bpy.props.StringProperty(
        name        = "String",
        description = "String",
        default     = "",
        update = selectedObjectTagUpdate
    )

    def draw(self, context, layout, node, text):
        layout.prop(self, 'value', text=text)

    def draw_color(self, context, node):
        return HIDDEN_SOCKET_COLOR


##     ##    ###    ######## ######## ########  ####    ###    ##
###   ###   ## ##      ##    ##       ##     ##  ##    ## ##   ##
#### ####  ##   ##     ##    ##       ##     ##  ##   ##   ##  ##
## ### ## ##     ##    ##    ######   ########   ##  ##     ## ##
##     ## #########    ##    ##       ##   ##    ##  ######### ##
##     ## ##     ##    ##    ##       ##    ##   ##  ##     ## ##
##     ## ##     ##    ##    ######## ##     ## #### ##     ## ########

class VRaySocketMtl(VRayValueSocket):
    bl_idname = 'VRaySocketMtl'
    bl_label  = 'Material Socket'

    value: bpy.props.StringProperty(
        name        = "Defautl Material",
        description = "Defautl material",
        default     = "MANOMATERIALISSET"
    )

    def draw(self, context, layout, node, text):
        layout.label(text=text)

    def draw_color(self, context, node):
        return MATERIAL_SOCKET_COLOR


########   ##        ##     ##   ######    ####  ##    ##
##     ##  ##        ##     ##  ##    ##    ##   ###   ##
##     ##  ##        ##     ##  ##          ##   ####  ##
########   ##        ##     ##  ##   ####   ##   ## ## ##
##         ##        ##     ##  ##    ##    ##   ##  ####
##         ##        ##     ##  ##    ##    ##   ##   ###
##         ########   #######    ######    ####  ##    ##

class VRaySocketObjectProps(VRayValueSocket):
    bl_idname = 'VRaySocketObjectProps'
    bl_label  = 'Object Properties Socket'

    def draw(self, context, layout, node, text):
        layout.label(text=text)

    def draw_color(self, context, node):
        return OBJ_PROP_SOCKET_COLOR
        

class VRaySocketPlugin(VRayValueSocket):
    bl_idname = 'VRaySocketPlugin'
    bl_label  = 'Plugin Socket'

    value: bpy.props.StringProperty(
        name        = "Plugin Name",
        description = "Plugin Name",
        default     = ""
    )

    def draw(self, context, layout, node, text):
        layout.label(text=text)

    def draw_color(self, context, node):
        return PLUGIN_SOCKET_COLOR
    

########  ######## ##    ## ########  ######## ########      ######  ##     ##    ###    ##    ## ##    ## ######## ##
##     ## ##       ###   ## ##     ## ##       ##     ##    ##    ## ##     ##   ## ##   ###   ## ###   ## ##       ##
##     ## ##       ####  ## ##     ## ##       ##     ##    ##       ##     ##  ##   ##  ####  ## ####  ## ##       ##
########  ######   ## ## ## ##     ## ######   ########     ##       ######### ##     ## ## ## ## ## ## ## ######   ##
##   ##   ##       ##  #### ##     ## ##       ##   ##      ##       ##     ## ######### ##  #### ##  #### ##       ##
##    ##  ##       ##   ### ##     ## ##       ##    ##     ##    ## ##     ## ##     ## ##   ### ##   ### ##       ##
##     ## ######## ##    ## ########  ######## ##     ##     ######  ##     ## ##     ## ##    ## ##    ## ######## ########

class VRaySocketRenderChannel(VRaySocketUse):
    bl_idname = 'VRaySocketRenderChannel'
    bl_label  = 'Render Channel Socket'

    value: bpy.props.StringProperty(
        name        = "",
        description = "",
        default     = ""
    )

    def draw(self, context, layout, node, text):
        layout.active = self.use
        split = layout.split()
        row = split.row(align=True)
        row.label(text=text)
        row.prop(self, 'use', text="")

    def draw_color(self, context, node):
        return CHANNEL_SOCKET_COLOR


class VRaySocketRenderChannelOutput(VRaySocket):
    bl_idname = 'VRaySocketRenderChannelOutput'
    bl_label  = 'Render Channel Ouput Socket'

    value: bpy.props.StringProperty(
        name        = "",
        description = "",
        default     = ""
    )

    def draw(self, context, layout, node, text):
        layout.label(text=text)

    def draw_color(self, context, node):
        return CHANNEL_SOCKET_COLOR


######## ######## ######## ########  ######  ########  ######
##       ##       ##       ##       ##    ##    ##    ##    ##
##       ##       ##       ##       ##          ##    ##
######   ######   ######   ######   ##          ##     ######
##       ##       ##       ##       ##          ##          ##
##       ##       ##       ##       ##    ##    ##    ##    ##
######## ##       ##       ########  ######     ##     ######

class VRaySocketEffect(VRaySocketUse):
    bl_idname = 'VRaySocketEffect'
    bl_label  = 'Effect Socket'

    value: bpy.props.StringProperty(
        name        = "",
        description = "",
        default     = ""
    )

    def draw(self, context, layout, node, text):
        layout.active = self.use
        split = layout.split()
        row = split.row(align=True)
        row.label(text=text)
        row.prop(self, 'use', text="")

    def draw_color(self, context, node):
        return EFFECT_SOCKET_COLOR


class VRaySocketEffectOutput(VRaySocket):
    bl_idname = 'VRaySocketEffectOutput'
    bl_label  = 'Effect Socket Socket'

    value: bpy.props.StringProperty(
        name        = "",
        description = "",
        default     = ""
    )

    def draw(self, context, layout, node, text):
        layout.label(text=text)

    def draw_color(self, context, node):
        return EFFECT_SOCKET_COLOR


######## ########     ###    ##    ##  ######  ########  #######  ########  ##     ##
   ##    ##     ##   ## ##   ###   ## ##    ## ##       ##     ## ##     ## ###   ###
   ##    ##     ##  ##   ##  ####  ## ##       ##       ##     ## ##     ## #### ####
   ##    ########  ##     ## ## ## ##  ######  ######   ##     ## ########  ## ### ##
   ##    ##   ##   ######### ##  ####       ## ##       ##     ## ##   ##   ##     ##
   ##    ##    ##  ##     ## ##   ### ##    ## ##       ##     ## ##    ##  ##     ##
   ##    ##     ## ##     ## ##    ##  ######  ##        #######  ##     ## ##     ##

class VRaySocketTransform(VRayValueSocket):
    bl_idname = 'VRaySocketTransform'
    bl_label  = 'Transform Socket'

    value: bpy.props.FloatVectorProperty(
        name = "Matrix",
        description = "Matrix",
        size=16,
        subtype='MATRIX',
        default=[b for a in mathutils.Matrix.Identity(4) for b in a],
        update = selectedObjectTagUpdate
    )

    def draw(self, context, layout, node, text):
        layout.label(text=text)

    def draw_color(self, context, node):
        return TRANSFORM_SOCKET_COLOR
    
    def exportUnlinked(self, nodeCtx: NodeContext, pluginDesc, attrDesc):
        value = self.value
        
        # 'MATRIX' and 'MATRIX_TEXTURE' attributes are exported as 3x3 matrix
        if attrDesc["type"] in ('MATRIX', 'MATRIX_TEXTURE'):
            value = self.value.to_3x3()
        
        pluginDesc.setAttribute(attrDesc['attr'], value)

########  ########  ######   ####  ######  ######## ########     ###    ######## ####  #######  ##    ##
##     ## ##       ##    ##   ##  ##    ##    ##    ##     ##   ## ##      ##     ##  ##     ## ###   ##
##     ## ##       ##         ##  ##          ##    ##     ##  ##   ##     ##     ##  ##     ## ####  ##
########  ######   ##   ####  ##   ######     ##    ########  ##     ##    ##     ##  ##     ## ## ## ##
##   ##   ##       ##    ##   ##        ##    ##    ##   ##   #########    ##     ##  ##     ## ##  ####
##    ##  ##       ##    ##   ##  ##    ##    ##    ##    ##  ##     ##    ##     ##  ##     ## ##   ###
##     ## ########  ######   ####  ######     ##    ##     ## ##     ##    ##    ####  #######  ##    ##

def getRegClasses():
    return (
        VRaySocketGeom,
        VRaySocketObject,
        VRaySocketIncludeExcludeList,
        VRaySocketObjectList,
        VRaySocketInt,
        VRaySocketBool,
        VRaySocketString,
        VRaySocketEnum,
        VRaySocketIntNoValue,
        VRaySocketFloat,
        VRaySocketFloatColor,
        VRaySocketFloatNoValue,
        VRaySocketColor,
        VRaySocketColorTexture,
        VRaySocketAColor,
        VRaySocketColorNoValue,
        VRaySocketColorMult,
        VRaySocketVector,
        VRaySocketCoords,
        VRaySocketBRDF,
        VRaySocketMtl,
        VRaySocketRenderChannel,
        VRaySocketRenderChannelOutput,
        VRaySocketEffect,
        VRaySocketEffectOutput,
        VRaySocketTransform,
        VRaySocketPlugin,
        VRaySocketObjectProps,
        VRaySocketPluginUse,
        VRaySocketWeight
    )

# A socket's properties are registered as part of the RNA structure of the socket 
# and are therefore unique per socket class. This means that e.g. in order to have different 
# min/max limits on the socket, a dedicated class must be registered for each combination. 
# This is why, for all dynamically created sockets, we need to register a dedicated class. 
#
# The following list contains the socket classes wich require such dynamic registration.
# Each item should describe a socket class with an optional dynamically registered property.
# In addition to the dynamic properties in the list, sockets may implement a static
# getPropertyRegistrationInfo() function which should return a list of any additional
# properties to be registered.
DYNAMIC_SOCKET_OVERRIDES = {
    'VRaySocketInt': (
        'value', bpy.props.IntProperty, {
            'name':  "Value",
            'description':  "Value",
            'set': _wrapperSocketSetItem,
            'get': _wrapperSocketGetItem
        }
    ),
    'VRaySocketBool': (
        'value', bpy.props.BoolProperty, {
            'name':  "Value",
            'description':  "Value",
            'set': _wrapperSocketSetItem,
            'get': _wrapperSocketGetItem
        }
    ),
    'VRaySocketEnum': (
        'value', bpy.props.EnumProperty, {
            'name':  "Value",
            'description':  "Value"
        }
    ),
    'VRaySocketIntNoValue': (
        'value', bpy.props.IntProperty, {
            'name': "Value",
            'description': "Value",
            'min': -1024,
            'max':  1024,
            'soft_min': -100,
            'soft_max':  100,
            'default': 1
        }
    ),
    'VRaySocketFloat': (
        'value', bpy.props.FloatProperty, {
            'name': "Value",
            'description': "Value",
            'precision': 3,
            'set': _wrapperSocketSetItem,
            'get': _wrapperSocketGetItem
        }
    ),
    'VRaySocketFloatColor': (
        'value', bpy.props.FloatProperty, {
            'name': "Value",
            'description': "Value",
            'precision': 3,
            'set': _wrapperSocketSetItem,
            'get': _wrapperSocketGetItem
        }
    ),
    'VRaySocketColor': (
        'value', bpy.props.FloatVectorProperty, {
            'name': "Color",
            'description': "Color",
            'subtype': 'COLOR',
            'min': 0.0,
            'max': 1.0,
            'soft_min': 0.0,
            'soft_max': 1.0,
            'set': _wrapperSocketSetItem,
            'get': _wrapperSocketGetItem
        }
    ),
    'VRaySocketColorTexture': (
        'value', bpy.props.FloatVectorProperty, {
            'name': "Color",
            'description': "Color",
            'subtype': 'COLOR',
            'min': 0.0,
            'max': 1.0,
            'soft_min': 0.0,
            'soft_max': 1.0,
            'set': _wrapperSocketSetItem,
            'get': _wrapperSocketGetItem
        }
    ),
    'VRaySocketAColor': (
        'value', bpy.props.FloatVectorProperty, {
            'name': "Color",
            'description': "Color",
            'subtype': 'COLOR',
            'min': 0.0,
            'max': 1.0,
            'soft_min': 0.0,
            'soft_max': 1.0,
            'size': 4,
            'set': _wrapperSocketSetItem,
            'get': lambda s: _wrapperSocketGetItemWithConv(s, toAColor)
        }
    ),
    'VRaySocketPluginUse': (
        'use', bpy.props.BoolProperty, {
            'name':  "Use",
            'description':  "Use"
        }
    ),
    'VRaySocketColorMult': (
        'value', bpy.props.FloatVectorProperty, {
            'name': "Color",
            'description': "Color",
            'subtype': 'COLOR',
            'min': 0.0,
            'max': 1.0,
            'soft_min': 0.0,
            'soft_max': 1.0,
            'default': (1.0, 1.0, 1.0)
        }
    ),
    'VRaySocketVector': (
        'value', bpy.props.FloatVectorProperty, {
            'name': "Vector",
            'description': "Vector",
            'subtype': 'TRANSLATION',
            'soft_min': -1.0,
            'soft_max': 1.0,
            'set': _wrapperSocketSetItem,
            'get': _wrapperSocketGetItem
        }
    ),
    'VRaySocketString': (
        'value', bpy.props.StringProperty, {
            'name': "String",
            'description': "String",
            'set': _wrapperSocketSetItem,
            'get': _wrapperSocketGetItem
        }
    ),
    "VRaySocketTransform": (
        'value', bpy.props.FloatVectorProperty, {
            'name': "Matrix",
            'description': "Matrix",
            'size': 16,
            'subtype': 'MATRIX',
            'set': _wrapperSocketSetItem,
            'get': _wrapperSocketGetItem
        }
    ),
    "VRaySocketIncludeExcludeList": (
    )
}


def initDynamicSocketTypes():
    skip_plugins = {"GeomVRayPattern", "Node", "VRayExporter", "Includer",
        "CameraStereoscopic", "VRayRenderChannels"}

    for pluginType in PLUGIN_MODULES:
        if pluginType in skip_plugins:
            continue
        pluginModule = PLUGIN_MODULES[pluginType]

        for param in pluginModule.Parameters:
            attrName = param.get('attr', None)
            paramType = param.get('type', None)
            
            # If there is a custom definition for the node in the plugin description, get the type from there.
            if (node := getPluginInputNodeDesc(pluginModule, attrName)) and (nodeType := node.get('type', None)):
                paramType = nodeType
                
            if paramSocketType := attribute_types.TypeToSocket.get(paramType, None):
                registerDynamicSocketClass(pluginType, paramSocketType, attrName)

def register():
    initDynamicSocketTypes()

    for regClass in getRegClasses():
        bpy.utils.register_class(regClass)



def unregister():
    global DYNAMIC_SOCKET_CLASSES
    global DYNAMIC_SOCKET_CLASS_NAMES

    for regClass in getRegClasses():
        bpy.utils.unregister_class(regClass)

    for regClass in DYNAMIC_SOCKET_CLASSES:
        bpy.utils.unregister_class(regClass)

    DYNAMIC_SOCKET_CLASSES.clear()
    DYNAMIC_SOCKET_CLASS_NAMES.clear()
