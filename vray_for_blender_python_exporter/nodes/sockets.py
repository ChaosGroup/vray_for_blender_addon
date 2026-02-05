import bpy
import hashlib
import math
import mathutils
import sys

from vray_blender import debug
from vray_blender.exporting.node_exporters.uvw_node_export import exportDefaultUVWGenChannel
from vray_blender.exporting.tools import getFarNodeLinkImpl, removeSocketLinks
from vray_blender.plugins import PLUGIN_MODULES, getPluginAttr, getInputSocketDesc, getPluginModule
from vray_blender.lib import attribute_types, attribute_utils
from vray_blender.lib.defs import AColor, AttrPlugin, NodeContext, PluginDesc
from vray_blender.nodes.tools import getSocketPanelName, getSocketPanel
from vray_blender.nodes.utils import selectedObjectTagUpdate


DYNAMIC_SOCKET_OVERRIDES = {}
DYNAMIC_SOCKET_CLASSES = set()
DYNAMIC_SOCKET_CLASS_NAMES = set()

STRUCTURAL_SOCKET_CLASSES = {
    # Sockets used for organizing node UI, e.g. rollouts, separators etc
    'ROLLOUT' : 'VRaySocketRollout'
}


# Colors for standard Blender sockets. List copied from .\source\blender\editors\space_node\drawnode.cc
BLENDER_SOCKET_COLORS = {
    'FLOAT' :      (0.63, 0.63, 0.63, 1.0),
    'VECTOR' :     (0.39, 0.39, 0.78, 1.0),
    'RGBA' :       (0.78, 0.78, 0.16, 1.0),
    'SHADER' :     (0.39, 0.78, 0.39, 1.0),
    'BOOLEAN' :    (0.80, 0.65, 0.84, 1.0),
    'UNUSED' :     (0.0, 0.0, 0.0, 0.0),
    'INT' :        (0.35, 0.55, 0.36, 1.0),
    'STRING' :     (0.44, 0.70, 1.00, 1.0),
    'OBJECT' :     (0.93, 0.62, 0.36, 1.0),
    'IMAGE' :      (0.39, 0.22, 0.39, 1.0),
    'GEOMETRY' :   (0.00, 0.84, 0.64, 1.0),
    'COLLECTION' : (0.96, 0.96, 0.96, 1.0),
    'TEXTURE' :    (0.62, 0.31, 0.64, 1.0),
    'MATERIAL' :   (0.92, 0.46, 0.51, 1.0),
    'ROTATION' :   (0.65, 0.39, 0.78, 1.0),
    'MENU' :       (0.40, 0.40, 0.40, 1.0),
    'MATRIX' :     (0.72, 0.20, 0.52, 1.0),
}


# Socket links colors defaults
HIDDEN_SOCKET_COLOR         = (1.0, 1.0, 1.0, 1.0)

RGBA_SOCKET_COLOR           = BLENDER_SOCKET_COLORS['RGBA']
GEOMETRY_SOCKET_COLOR       = BLENDER_SOCKET_COLORS['GEOMETRY']
FLOAT_SOCKET_COLOR          = BLENDER_SOCKET_COLORS['FLOAT']
INT_SOCKET_COLOR            = BLENDER_SOCKET_COLORS['INT']
BOOL_SOCKET_COLOR           = BLENDER_SOCKET_COLORS['BOOLEAN']
MATERIAL_SOCKET_COLOR       = BLENDER_SOCKET_COLORS['SHADER']
OBJECT_SOCKET_COLOR         = BLENDER_SOCKET_COLORS['OBJECT']
COLLECTION_SOCKET_COLOR     = BLENDER_SOCKET_COLORS['COLLECTION']
VECTOR_SOCKET_COLOR         = BLENDER_SOCKET_COLORS['VECTOR']
TRANSFORM_SOCKET_COLOR      = BLENDER_SOCKET_COLORS['MATRIX']
ROLLOUT_SOCKET_COLOR        = BLENDER_SOCKET_COLORS['UNUSED']

MAPPING_SOCKET_COLOR        = (0.250, 0.273, 0.750, 1.0)
CHANNEL_SOCKET_COLOR        = (0.0, 0.83, 0.63, 1.0)
EFFECT_SOCKET_COLOR         = (0.92, 0.45, 0.5, 1.0)
PLUGIN_SOCKET_COLOR         = (1.0, 1.0, 1.0, 1.0)
OBJ_PROP_SOCKET_COLOR       = (0.8, 0.2, 0.5, 1.0)


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


def _setSockDefaultValue(sockParams, socketTypeName, attrDesc):
    """ Sets the default value of a socket, applying necessary conversions based on the socket type.
    """
    if "default" not in attrDesc:
        return

    value = attrDesc["default"]
    match socketTypeName:
        case 'VRaySocketColor':
            sockParams['default'] = attribute_utils.toColor(value)
        case 'VRaySocketAColor':
            sockParams['default'] = attribute_utils.toAColor(value)
        case 'VRaySocketTransform':
            sockParams['default'] = attribute_utils.tupleTo4x4MatrixLayout(value)
        case _:
            sockParams['default'] = value


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
            attribute_utils.setAttrSubtype(overrideParams, attrDesc, pluginModule)
            attribute_utils.setAttrPrecision(overrideParams, attrDesc)
            attribute_utils.setAttrLimits(overrideParams, attrDesc)
            attribute_utils.setAttrOptions(overrideParams, attrDesc, pluginModule)

            _setSockDefaultValue(overrideParams, socketTypeName, attrDesc)

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
        @param socketName - the name of the socket
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

    if attrName:
        if (socketType in DYNAMIC_SOCKET_OVERRIDES):
            dynamicType = getDynamicSocketClassName(pluginType, socketType, attrName)
            if dynamicType not in DYNAMIC_SOCKET_CLASS_NAMES:
                raise Exception(f"Dynamic socket class not registered for {node.vray_plugin}::{socketType}::{socketName}: {dynamicType}")

            socketType = dynamicType
        elif socketType in STRUCTURAL_SOCKET_CLASSES:
            socketType = STRUCTURAL_SOCKET_CLASSES[socketType]

    createdSocket: bpy.types.NodeSocket = node.inputs.new(socketType, socketName, use_multi_input=isMultiInput)

    if not hasattr(createdSocket, 'vray_attr'):
        debug.printError(f"Created a non-VRay socket of type {socketType}. Socket class must descend from VRaySocket.")

    if createdSocket.name != socketName:
        # Blender might change the name if a socket with the same name already exists.
        debug.printError(f"Socket name was changed during creation, probably due to a name conflict: {socketType}::{socketName} => {createdSocket.name}")

    # Socket has to be disabled in order to not participate in the
    # 'Show/Hide Unconnected Sockets' operation. All other functionality is unaffected.
    createdSocket.enabled = visible

    createdSocket.hide = not visible
    createdSocket.show_expanded = True
    createdSocket.vray_socket_base_type = baseType
    createdSocket.vray_attr = attrName
    createdSocket.vray_plugin = pluginType

    _configureInPanel(createdSocket)

    return createdSocket


def _configureInPanel(sock: bpy.types.NodeSocket):
    """ Set socket's initial state if it is placed on a panel or if it is a 'panel' socket """
    assert not sock.is_output, "Only input sockets may be placed onz panels"

    if (pluginName := sock.getPluginName()) != 'NONE':
        pluginModule = getPluginModule(pluginName)

        if sock.bl_idname == 'VRaySocketRollout':
            panelDesc = getSocketPanel(pluginModule, sock.vray_attr)
            sock.is_open = not panelDesc.get('default_closed', True)
        elif panelName := getSocketPanelName(pluginModule, sock.vray_attr):
            # This is a regular socket that is shown in a panel
            panelDesc = getSocketPanel(pluginModule, panelName)
            sock.nest_level = 1
            sock.hide = panelDesc.get('default_closed', True)


def addOutput(node: bpy.types.Node, socketType: str, socketName: str, attrName: str=None, description: str=None):
    if socketName in node.outputs:
        return node.outputs[socketName]

    createdSocket = node.outputs.new(socketType, socketName)

    createdSocket.vray_socket_base_type = socketType

    if attrName is not None:      createdSocket.vray_attr   = attrName
    if description is not None:   createdSocket.description = description

    return createdSocket


def getHiddenInput(node: bpy.types.Node, sockName: str):
    """ Retrieve a hiden input socket from a node. """

    for i in node.inputs:
        if i.name == sockName:
            return i
    return None


def removeInputs(node, sockNames:list[str], removeLinked=False):
    """ Removes several input sockets in a transactional manner """

    sockets = [s for s in node.inputs if (s.name in sockNames) and (removeLinked or not s.is_linked)]

    # Only remove the sockets if all of them can be removed
    if len(sockets) == len(sockNames):
        for s in sockets:
            # Remove links before deleting the socket,
            # as nodes connected to it will also be affected
            removeSocketLinks(s)

            node.inputs.remove(s)

        return True

    return False

# UNUSED
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
    propGroup = getattr(self.node, self.getPluginName())
    setattr(propGroup, self.vray_attr, value)

# UNUSED
def _wrapperSocketSetItemWithConv(self, value, convFunc):
    propGroup = getattr(self.node, self.getPluginName())
    setattr(propGroup, self.vray_attr, convFunc(value))


def _wrapperSocketGetItem(self):
    propGroup = getattr(self.node, self.getPluginName())
    return getattr(propGroup, self.vray_attr)


def _wrapperSocketGetItemWithConv(self, convFunc):
    propGroup = getattr(self.node, self.getPluginName())
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

    # The type of the plugin which holds the attribute specified in vray_attr.
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

    nest_level: bpy.props.IntProperty(
        name = "Nest level",
        description = "The nest level of the socket when part of a rollout",
        default = 0
    )

    def hasActiveFarLink(self):
        return self.getFarLink() is not None

    def getFarLink(self):
        return getFarNodeLinkImpl(self)

    def exportLinked(self, pluginDesc, attrDesc, linkValue):
        pluginDesc.setAttribute(attrDesc['attr'], linkValue)

    def exportUnlinked(self, nodeCtx: NodeContext, pluginDesc, attrDesc):
        # Resetting the attribute will enable the default export procedure to export the value
        pluginDesc.resetAttribute(attrDesc['attr'])

    def getNestedLayout(self, layout: bpy.types.UILayout):
        if self.nest_level == 0:
            col = layout
        else:
            split = layout.split(factor = 0.01 + self.nest_level * 0.02, align = True)
            split.column()
            col = split.column()

        return col
    
    def getPluginName(self):
        """ Get the name of the plugin that the socket is associated with. """

         # This check is for backwards compatibility,
         # all newly created sockets will use VRaySocket.vray_plugin instead of node.vray_plugin
        if self.vray_plugin:
            return self.vray_plugin
        
        if hasattr(self.node, 'vray_plugin'):
            return self.node.vray_plugin
        
        return 'NONE'

    def draw(self, context, layout, node, text):
        """ Draw handler for all vray socket types. It will setup the correct layout
            for the socket. Children may draw in their draw_impl method.
        """
        layout = self.getNestedLayout(layout)
        if fnDraw := getattr(self, 'draw_impl', None):
            fnDraw(context, layout, node, self._sockLabel())
        else:
            layout.label(text=self._sockLabel())


    def _sockLabel(self):
        from vray_blender.lib.condition_processor import evaluateCondition, isCondition
        from vray_blender.lib.attribute_utils import formatAttributeName

        node = self.node

        
        if (pluginName := self.getPluginName()) != 'NONE':
            pluginModule = getPluginModule(pluginName)
            sockDesc = getInputSocketDesc(pluginModule, self.vray_attr)

            if (sockDesc is not None) and ((label := sockDesc.get('label')) is not None):
                if isCondition(label):
                    return evaluateCondition(getattr(node, pluginName), node, label)
                else:
                    return label

        # No label definition was found, draw using the original socket name
        return formatAttributeName(self.name)


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


class VRaySocketRollout(VRaySocket):
    is_open: bpy.props.BoolProperty(
        default = False,
        update = lambda self, value: self.updateIsOpen(value)
    )

    def updateIsOpen(self, context: bpy.types.Context):
        pluginName = self.getPluginName()
        pluginModule = getPluginModule(pluginName)
        panelSockets = pluginModule.SocketPanels.get(self.vray_attr, [])

        for sockAttrName in panelSockets:
            sock = next((s for s in self.node.inputs if s.vray_attr == sockAttrName), None)

            if sock:
                show = self.is_open or sock.is_linked
                sock.hide = not show
            # Do nothing if the socket is missing. This is a normal situation right
            # after the rollout socket is created because its is_open property is
            # set before any of the sokets it groups have been added to the node.


    def exportLinked(self, pluginDesc, attrDesc, linkValue):
        assert False, "Rollouts sockets should not be linked"

    def exportUnlinked(self, nodeCtx: NodeContext, pluginDesc, attrDesc):
        # Rollout sockets have nothing to export.
        pass

    @classmethod
    def draw_color_simple(cls):
        return ROLLOUT_SOCKET_COLOR

    def draw_impl(self, context, layout, node, text):
        layout.alignment = 'LEFT'
        icon = 'DOWNARROW_HLT' if self.is_open else 'RIGHTARROW'
        layout.prop(self, 'is_open', text=text, emboss = False, icon = icon)


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

    def computeLinkMultiplier(self):
        """ Compute the multuplier that has to be used on the link with fromSocket.

            The presence of this function indicates whether multiplication converter plugins
            should be exported for a given socket type.

            @return the multiplier value, if multiplication is needed, else None.
        """
        # Epsilon is deliberately not used for the multiplier comparison. We want to skip multiplication only if
        # the value is not a result of a computation.
        if math.isclose(self.multiplier, 100.0):
            # No multiplication needed
            return None

        return self.multiplier / 100.0

    def draw_property(self, context, layout, text, expand=False, slider=True):
        """ Draw as property in a property page, not as a socket.
            This method is used to show socket values on property pages.
        """
        layout.prop(self, 'value', text=text, expand=expand, slider=slider)


    def draw_impl(self, context, layout, node, text):
        if self.is_output:
            layout.label(text=text)
        elif self.hasActiveFarLink():
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

    def getFarLink(self):
        return super().getFarLink() if self.use else None
    
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
    bl_label  = 'Geometry socket'

    value: bpy.props.StringProperty(
        name = "Geometry",
        description = "Geometry",
        default = ""
    )

    @classmethod
    def draw_color_simple(cls):
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

    @classmethod
    def draw_color_simple(cls):
        return COLLECTION_SOCKET_COLOR

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

    @classmethod
    def draw_color_simple(cls):
        return COLLECTION_SOCKET_COLOR


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
        propGroup = getattr(self.node, self.getPluginName())
        setattr(propGroup, attrName, value)


    def getItem(self, attrName):
        propGroup = getattr(self.node, self.getPluginName())
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

    def draw_impl(self, context, layout, node, text):
        row = layout.row()
        row.label(text=text)

        if self.hasActiveFarLink():
            row.prop(self, 'inclusionMode', text='Type', expand=True)

    @classmethod
    def draw_color_simple(cls):
        return COLLECTION_SOCKET_COLOR

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

    def draw_impl(self, context, layout, node, text):
        if self.hasActiveFarLink():
            layout.label(text=text)
        else:
            layout.prop(self, 'value', text=text)

    @classmethod
    def draw_color_simple(cls):
        return INT_SOCKET_COLOR


class VRaySocketIntNoValue(VRaySocket):
    bl_idname = 'VRaySocketIntNoValue'
    bl_label  = 'Integer socket'

    @classmethod
    def draw_color_simple(cls):
        return INT_SOCKET_COLOR


class VRaySocketBool(VRayValueSocket):
    bl_idname = 'VRaySocketBool'
    bl_label  = 'Boolean socket'

    value: bpy.props.BoolProperty(
        name = "Value",
        description = "Value",
        default = False,
        update = selectedObjectTagUpdate
    )

    def draw_impl(self, context, layout, node, text):
        if self.is_output or self.hasActiveFarLink():
            layout.label(text=text)
        else:
            layout.prop(self, 'value', text=text)

    @classmethod
    def draw_color_simple(cls):
        return BOOL_SOCKET_COLOR


class VRaySocketEnum(VRayValueSocket):
    bl_idname = 'VRaySocketEnum'
    bl_label  = 'Enum socket'

    def setItem(self, selectedIndex):
        # Blender uses Int for enum values, but they are strings in the property group.
        # Convert the incoming index to the enum value's identifier sstring.

        pluginModule = getPluginModule(self.getPluginName())
        attrDesc = getPluginAttr(pluginModule, self.vray_attr)
        newValue = attrDesc['items'][selectedIndex][0]

        propGroup = getattr(self.node, self.getPluginName())
        setattr(propGroup, self.vray_attr, newValue)


    def getItem(self):
        # Blender uses Int for enum values, but they are strings in the property group.
        # Return the index of the currently selected item.
        propGroup = getattr(self.node, self.getPluginName())
        selectedItem = getattr(propGroup, self.vray_attr)
        pluginModule = getPluginModule(self.getPluginName())
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
                get = VRaySocketEnum.getItem,
                options=set()
            )
        return [('value', prop)]

    def draw_property(self, context, layout, text, expand=False, slider=True):
        layout.prop(self, 'value', text=text, expand=expand, slider=slider)

    @classmethod
    def draw_color_simple(cls):
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

    @classmethod
    def draw_color_simple(cls):
        return FLOAT_SOCKET_COLOR


class VRaySocketFloatNoValue(VRaySocket):
    bl_idname = 'VRaySocketFloatNoValue'
    bl_label  = 'Float socket'

    @classmethod
    def draw_color_simple(cls):
        return FLOAT_SOCKET_COLOR


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

    def draw_impl(self, context, layout, node, text):
        if self.is_output or self.hasActiveFarLink():
            layout.label(text=text)
        else:
            layout.prop(self, 'value', text=text)

    @classmethod
    def draw_color_simple(cls):
        return FLOAT_SOCKET_COLOR


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

    @classmethod
    def draw_color_simple(cls):
        return FLOAT_SOCKET_COLOR


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

    @classmethod
    def draw_color_simple(cls):
        return RGBA_SOCKET_COLOR


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

    @classmethod
    def draw_color_simple(cls):
        return RGBA_SOCKET_COLOR


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

    @classmethod
    def draw_color_simple(cls):
        return RGBA_SOCKET_COLOR


class VRaySocketColorNoValue(VRaySocket):
    bl_idname = 'VRaySocketColorNoValue'
    bl_label  = 'Color socket'

    @classmethod
    def draw_color_simple(cls):
        return RGBA_SOCKET_COLOR


class VRaySocketPluginUse(VRaySocketUse):
    """ Meta socket which decides whether to export a linked node based on the value
        of a separate boolean property. The boolean property is drawn as a checkbox.

        NOTE: Ths name of the socket is no longer correct but changing it would require
        a special upgrade procedure. TODO: Create an upgrade path that will allow
        changing socket names.

        Bound properties:
        - a BRDF plugin in 'target_prop'. Exported if the property in 'use_prop' is True
        - a boolean property in 'use_prop'
    """
    bl_idname = 'VRaySocketPluginUse'
    bl_label  = "BRDF socket with a 'use' flag"

    def setItem(self, value, attrName):
        propGroup = getattr(self.node, self.getPluginName())
        setattr(propGroup, attrName, value)

    def getItem(self, attrName):
        propGroup = getattr(self.node, self.getPluginName())
        return getattr(propGroup, attrName)

    def exportLinked(self, pluginDesc, attrDesc, linkValue):
        pluginDesc.setAttribute(attrDesc['bound_props']['use_prop'], self.use)
        pluginDesc.setAttribute(attrDesc['bound_props']['target_prop'], linkValue)

    def exportUnlinked(self, nodeCtx: NodeContext, pluginDesc, attrDesc):
        pluginDesc.setAttribute(attrDesc['bound_props']['use_prop'], False)
        pluginDesc.setAttribute(attrDesc['bound_props']['target_prop'], AttrPlugin())

    def draw_property(self, context, layout, text, expand=False, slider=True):
        """ Draw as property in a property page, not as a socket.
            This method is used to show socket values on property pages.
        """
        # Show the name of the 'use' property as a label, not the one of the
        # meta property.
        pluginModule = getPluginModule(self.getPluginName())
        metaAttr = getPluginAttr(pluginModule, self.vray_attr)
        useAttrName = metaAttr['bound_props']['use_prop']

        if (sockDesc := getInputSocketDesc(pluginModule, self.vray_attr)) is not None and (label := sockDesc.get('label')):
            attrLabel = label
        else:
            useAttrDesc = attribute_utils.getAttrDesc(pluginModule, useAttrName)
            attrLabel = attribute_utils.getAttrDisplayName(useAttrDesc)

        layout.prop(self, 'use', text=attrLabel, expand=expand, slider=slider)

    def draw_impl(self, context, layout, node, text):
        layout.active = self.use
        split = layout.split()
        row = split.row(align=True)
        row.label(text=text)
        row.prop(self, 'use', text="")

    @classmethod
    def draw_color_simple(cls):
        return MATERIAL_SOCKET_COLOR


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

    @classmethod
    def draw_color_simple(cls):
        return RGBA_SOCKET_COLOR


##     ## ########  ######  ########  #######  ########
##     ## ##       ##    ##    ##    ##     ## ##     ##
##     ## ##       ##          ##    ##     ## ##     ##
##     ## ######   ##          ##    ##     ## ########
 ##   ##  ##       ##          ##    ##     ## ##   ##
  ## ##   ##       ##    ##    ##    ##     ## ##    ##
   ###    ########  ######     ##     #######  ##     ##


class VRaySocketVectorBase(VRayValueSocket):
    bl_idname = 'VRaySocketVectorBase'
    bl_label  = 'Vector Socket Base'

    def draw_impl(self, context, layout, node, text):
        if self.is_output or self.hasActiveFarLink():
            layout.label(text=text)
        else:
            layout.row().column().prop(self, 'value', text=text)

    @classmethod
    def draw_color_simple(cls):
        return VECTOR_SOCKET_COLOR


class VRaySocketVector(VRaySocketVectorBase):
    bl_idname = 'VRaySocketVector'
    bl_label  = 'Vector Socket'

    value: bpy.props.FloatVectorProperty(
        name = "Vector",
        description = "Vector",
        unit = 'NONE',
        default = mathutils.Vector((0.0, 0.0, 0.0)),
        update = selectedObjectTagUpdate
    )


class VRaySocketVectorInt(VRaySocketVectorBase):
    bl_idname = 'VRaySocketVectorInt'
    bl_label  = 'Int Vector Socket'

    value: bpy.props.IntVectorProperty(
        name = "Vector",
        description = "Int Vector",
        default = (0, 0, 0),
        update = selectedObjectTagUpdate
    )


# Base socket class for transform vectors (Rotation, Scale, Offset)
class VRaySocketVectorTransformBase(VRaySocketVectorBase):
    def draw_impl(self, context, layout, node, text):
        if (objSock := self.node.inputs.get('Object')) and objSock.hasActiveFarLink():
            layout.label(text=text)
        else:
            super().draw_impl(context, layout, node, text)


class VRaySocketVectorRotation(VRaySocketVectorTransformBase):
    bl_idname = 'VRaySocketVectorRotation'
    bl_label  = 'Vector Socket Rotation'

    value: bpy.props.FloatVectorProperty(
        name = "Rotation",
        description = "Rotation",
        unit = 'ROTATION',
        subtype = 'EULER',
        step = 10,
        size = 3,
        precision = 3,
        update = selectedObjectTagUpdate,
        default = mathutils.Vector((0.0, 0.0, 0.0)),
    )


class VRaySocketVectorScale(VRaySocketVectorTransformBase):
    bl_idname = 'VRaySocketVectorScale'
    bl_label  = 'Vector Socket Scale'

    value: bpy.props.FloatVectorProperty(
        name = "Scale",
        description = "Scale",
        subtype = 'XYZ',
        size = 3,
        precision = 3,
        step = 1,
        update = selectedObjectTagUpdate,
        default = mathutils.Vector((1.0, 1.0, 1.0)),
    )


class VRaySocketVectorOffset(VRaySocketVectorTransformBase):
    bl_idname = 'VRaySocketVectorOffset'
    bl_label  = 'Vector Socket Offset'

    value: bpy.props.FloatVectorProperty(
        name = "Offset",
        description = "Offset",
        unit = 'LENGTH',
        subtype = 'XYZ',
        size = 3,
        precision = 3,
        step = 1,
        update = selectedObjectTagUpdate,
        default = mathutils.Vector((0.0, 0.0, 0.0)),
    )


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

    def draw_impl(self, context, layout, node, text):
        layout = self.getNestedLayout(layout)
        layout.label(text=text)

    @classmethod
    def draw_color_simple(cls):
        return MAPPING_SOCKET_COLOR

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
        name        = "Default BRDF",
        description = "Default BRDF",
        default     = "BRDFNOBRDFISSET"
    )


    @classmethod
    def draw_color_simple(cls):
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

    def draw_impl(self, context, layout, node, text):
        layout.prop(self, 'value', text=text)

    @classmethod
    def draw_color_simple(cls):
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
        name        = "Default Material",
        description = "Default material",
        default     = "MANOMATERIALISSET"
    )

    @classmethod
    def draw_color_simple(cls):
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


    @classmethod
    def draw_color_simple(cls):
        return OBJ_PROP_SOCKET_COLOR


class VRaySocketPlugin(VRayValueSocket):
    bl_idname = 'VRaySocketPlugin'
    bl_label  = 'Plugin Socket'

    value: bpy.props.StringProperty(
        name        = "Plugin Name",
        description = "Plugin Name",
        default     = ""
    )

    @classmethod
    def draw_color_simple(cls):
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

    def draw_impl(self, context, layout, node, text):
        layout.active = self.use
        split = layout.split()
        row = split.row(align=True)
        row.label(text=text)
        row.prop(self, 'use', text="")

    @classmethod
    def draw_color_simple(cls):
        return CHANNEL_SOCKET_COLOR


class VRaySocketRenderChannelOutput(VRaySocket):
    bl_idname = 'VRaySocketRenderChannelOutput'
    bl_label  = 'Render Channel Ouput Socket'

    value: bpy.props.StringProperty(
        name        = "",
        description = "",
        default     = ""
    )

    @classmethod
    def draw_color_simple(cls):
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

    def draw_impl(self, context, layout, node, text):
        layout.active = self.use
        split = layout.split()
        row = split.row(align=True)
        row.label(text=text)
        row.prop(self, 'use', text="")

    @classmethod
    def draw_color_simple(cls):
        return EFFECT_SOCKET_COLOR


class VRaySocketEffectOutput(VRaySocket):
    bl_idname = 'VRaySocketEffectOutput'
    bl_label  = 'Effect Socket Socket'

    value: bpy.props.StringProperty(
        name        = "",
        description = "",
        default     = ""
    )

    @classmethod
    def draw_color_simple(cls):
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

    @classmethod
    def draw_color_simple(cls):
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
        VRaySocketVectorInt,
        VRaySocketVectorRotation,
        VRaySocketVectorScale,
        VRaySocketVectorOffset,
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
        VRaySocketWeight,
        VRaySocketRollout,
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
            'get': lambda s: _wrapperSocketGetItemWithConv(s, attribute_utils.toAColor)
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
            'unit': 'NONE',
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
            paramSubtype = param.get('subtype', None)

            # If there is a custom definition for the node in the plugin description, get the type from there.
            if (node := getInputSocketDesc(pluginModule, attrName)) and (nodeType := node.get('type', None)):
                paramType = nodeType

            if paramSocketType := attribute_types.getSocketType(paramType, paramSubtype):
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
