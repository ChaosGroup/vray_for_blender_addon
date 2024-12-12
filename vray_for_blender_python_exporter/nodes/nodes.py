

import bpy
import nodeitems_utils


from vray_blender import debug
from vray_blender.nodes.customRenderChannelNodes import customRenderChannelNodesDesc
from vray_blender.nodes import utils as NodeUtils, specials, mixin
from vray_blender.plugins import PLUGINS, getPluginModule
from vray_blender.exporting.update_tracker import UpdateFlags, UpdateTracker, UpdateTarget
from vray_blender.lib import class_utils, draw_utils, blender_utils
from vray_blender.lib.names import syncObjectUniqueName
from vray_blender.ui import classes
from vray_blender.plugins.skipped_plugins import MANUALLY_CREATED_PLUGINS, SKIPPED_PLUGINS



VRayNodeTypes = {
    'BRDF'          : [],
    'EFFECT'        : [],
    'GEOMETRY'      : [],
    'LIGHT'         : [],
    'MATERIAL'      : [],
    'TEXTURE'       : [],
    'UVWGEN'        : [],
    'RENDERCHANNEL' : [],
    'MISC'          : [],
}

VRayNodeTypeIcon = {
    'BRDF'          : 'SHADING_TEXTURE',
    'EFFECT'        : 'GHOST_ENABLED',
    'GEOMETRY'      : 'MESH_DATA',
    'LIGHT'         : 'LIGHT',
    'MATERIAL'      : 'MATERIAL',
    'TEXTURE'       : 'TEXTURE',
    'UVWGEN'        : 'GROUP_UVS',
    'RENDERCHANNEL' : 'SCENE_DATA',
}



##     ## ######## ##    ## ##     ##
###   ### ##       ###   ## ##     ##
#### #### ##       ####  ## ##     ##
## ### ## ######   ## ## ## ##     ##
##     ## ##       ##  #### ##     ##
##     ## ##       ##   ### ##     ##
##     ## ######## ##    ##  #######


def _pollObjectNodeTreeSelected(context):
    return context.scene.vray.ActiveNodeEditorType == "OBJECT"

def _pollMaterialNodeTreeSelected(context):
    return context.object and \
        context.object.type in blender_utils.TypesThatSupportMaterial and \
        context.scene.vray.ActiveNodeEditorType == "SHADER"

def _pollWorldNodeTreeSelected(context):
    return context.scene.vray.ActiveNodeEditorType == "WORLD"

class VRayNodeCategory(nodeitems_utils.NodeCategory):
    @classmethod
    def poll(cls, context):
        return classes.pollTreeType(cls, context)

class VRayMaterialNodeCategory(nodeitems_utils.NodeCategory):
    @classmethod
    def poll(cls, context):
        return classes.pollTreeType(cls, context) and _pollMaterialNodeTreeSelected(context)

class VRayObjectNodeCategory(nodeitems_utils.NodeCategory):
    @classmethod
    def poll(cls, context):
        return classes.pollTreeType(cls, context) and _pollObjectNodeTreeSelected(context)

class VRayWorldNodeCategory(nodeitems_utils.NodeCategory):
    @classmethod
    def poll(cls, context):
        return classes.pollTreeType(cls, context) and _pollWorldNodeTreeSelected(context)


class VRayRenderChannelMenu(bpy.types.Menu):
    @classmethod
    def poll(cls, context):
        return classes.pollTreeType(cls, context) and _pollWorldNodeTreeSelected(context)
       
    def draw(self, context):
        layout = self.layout
        col = layout.column(align=True)
        for item in self._nodesList:
            item.draw(item, col, context) 


_channelMenuClasses = []
# Generates Node Menu classes for the render nodes
def _getChannelMenuClasses():
    global _channelMenuClasses
    #If the channel menu
    if not _channelMenuClasses:
        from vray_blender.plugins.channel.RenderChannelsPanel import VRayChannelNodeSubtypes
        for channelSubtype in VRayChannelNodeSubtypes:
            channelSubtypeName = channelSubtype.title()
            def initFunction(self):
                self._nodesList = buildItemsList('RENDERCHANNEL', self.menu_type)

            vRayRenderChannelType = type(
                f"VRay{channelSubtypeName}ChannelsMenu",
                (VRayRenderChannelMenu,),
                {
                    "bl_label": f"{channelSubtypeName}",
                    "bl_idname": f"NODE_MT_VRAY_{channelSubtypeName}_Channels_Menu",
                    "menu_type": channelSubtype,
                    "__init__": initFunction 
                }
            )
            _channelMenuClasses.append(vRayRenderChannelType)
        

    return _channelMenuClasses


class VRayNodeRenderChannelCategory(nodeitems_utils.NodeItem):
    def draw(self, _item, layout, _context):
        for channelMenuClass in _getChannelMenuClasses():
            layout.menu(channelMenuClass.bl_idname)

    @classmethod
    def poll(cls, context):
        return classes.pollTreeType(cls, context)



def buildItemsList(nodeType, subType=None):
    def _hidePlugin(pluginName):

        if pluginName in SKIPPED_PLUGINS:
            return True

        # App specific
        _name_filter = (
            'Maya',
            'TexMaya',
            'MtlMaya',
            'TexModo',
            'TexXSI',
            'texXSI',
            'volumeXSI',
        )
        if pluginName.startswith(_name_filter):
            return True
        if pluginName.find('ASGVIS') >= 0:
            return True
        if pluginName.find('C4D') >= 0:
            return True
        if pluginName.find('Modo') >= 0:
            return True
        return False

    menuItems = []
    for t in VRayNodeTypes[nodeType]:
        pluginName = t.bl_rna.identifier.replace("VRayNode", "")

        if _hidePlugin(pluginName):
            continue

        pluginSubtype = getattr(t, "vray_menu_subtype","")
        if subType is None:
            # Add only data without SUBTYPE
            if pluginSubtype:
                continue
        else:
            # Check subtype
            if subType != pluginSubtype:
                continue

        # Make sure all V-Ray nodes' labels are prefixed with 'V-Ray'
        nodeLabel = t.bl_label 
        if not nodeLabel.startswith('V-Ray'):
            nodeLabel = f"V-Ray {nodeLabel}" 

        menuItems.append(nodeitems_utils.NodeItem(t.bl_rna.identifier, label=nodeLabel))

    return menuItems

def getMaterialItemsList():
    """ Returns list of material node items in specific order """

    def _getMtlByTypeAndLabel(type, label):
        for t in VRayNodeTypes[type]:
            if t.bl_label == label:
                return nodeitems_utils.NodeItem(t.bl_rna.identifier, label=t.bl_label)
        return None

    return (
        _getMtlByTypeAndLabel("BRDF", "V-Ray Mtl"),
        _getMtlByTypeAndLabel("BRDF", "V-Ray AL Surface Mtl"),
        _getMtlByTypeAndLabel("BRDF", "V-Ray Fast SSS2"),
        _getMtlByTypeAndLabel("BRDF", "V-Ray Blend Mtl"),
        _getMtlByTypeAndLabel("BRDF", "V-Ray Bump Mtl"),
        _getMtlByTypeAndLabel("BRDF", "V-Ray Light Mtl"),
        _getMtlByTypeAndLabel("BRDF", "V-Ray Hair Next Mtl"),
        _getMtlByTypeAndLabel("BRDF", "V-Ray Car Paint 2 Mtl"),
        _getMtlByTypeAndLabel("BRDF", "V-Ray Flakes 2 Mtl"),
        _getMtlByTypeAndLabel("BRDF", "V-Ray Stochastic Flakes Mtl"),
        _getMtlByTypeAndLabel("MATERIAL", "V-Ray Switch Mtl"),
        _getMtlByTypeAndLabel("MATERIAL", "V-Ray Mtl 2Sided"),
        _getMtlByTypeAndLabel("MATERIAL", "V-Ray Mtl Material ID"),
        _getMtlByTypeAndLabel("MATERIAL", "V-Ray Mtl Override"),
        _getMtlByTypeAndLabel("MATERIAL", "V-Ray Mtl Render Stats"),
        _getMtlByTypeAndLabel("MATERIAL", "V-Ray Mtl Round Edges"),
        _getMtlByTypeAndLabel("MATERIAL", "V-Ray Mtl Wrapper"),
        _getMtlByTypeAndLabel("MATERIAL", "V-Ray Mtl GLSL"),
        _getMtlByTypeAndLabel("MATERIAL", "V-Ray Mtl OSL"),
        _getMtlByTypeAndLabel("MATERIAL", "V-Ray VRmat Mtl")
    )


def getCategories():
    return [
        VRayMaterialNodeCategory(
            'VRAY_MATERIAL',
            "Material",
            items = getMaterialItemsList()
        ),
        VRayNodeCategory(
            'VRAY_TEXTURE',
            "Textures",
            items = [
                nodeitems_utils.NodeItem("VRayNodeMetaImageTexture"),
            ] + buildItemsList('TEXTURE')
        ),
        VRayNodeCategory(
            'VRAY_TEXTURE_UTILITIES',
            "Texture Utilities",
            items = buildItemsList('TEXTURE', 'UTILITY')
        ),
        VRayNodeCategory(
            'VRAY_UVWGEN',
            "Mapping",
            items = [
                nodeitems_utils.NodeItem("VRayNodeUVWMapping"),
                nodeitems_utils.NodeItem("VRayNodeUVWGenRandomizer"),
            ]
        ),
        VRayObjectNodeCategory(
            'VRAY_GEOMETRY',
            "Geometry",
            items = [
                nodeitems_utils.NodeItem("VRayNodeDisplacement"),
                nodeitems_utils.NodeItem("VRayNodeGeomStaticSmoothedMesh"),
            ]
        ),
        VRayObjectNodeCategory(
            'VRAY_OBJECT_PROPERTIES',
            "Object Properties",
            items = [
                nodeitems_utils.NodeItem("VRayNodeObjectMatteProps"),
                nodeitems_utils.NodeItem("VRayNodeObjectSurfaceProps"),
                nodeitems_utils.NodeItem("VRayNodeObjectVisibilityProps"),
            ]
        ),
        VRayNodeCategory(
            "VRAY_MATH",
            "Math",
            items = [
                nodeitems_utils.NodeItem("VRayNodeTransform"),
                nodeitems_utils.NodeItem("VRayNodeMatrix"),
                nodeitems_utils.NodeItem("VRayNodeVector"),
            ],
        ),
        VRayNodeCategory(
            'VRAY_OUTPUTS',
            "Output",
            items = [
                nodeitems_utils.NodeItem("VRayNodeOutputMaterial", poll=_pollMaterialNodeTreeSelected),
                nodeitems_utils.NodeItem("VRayNodeWorldOutput", poll=_pollWorldNodeTreeSelected),
                nodeitems_utils.NodeItem("VRayNodeObjectOutput", poll=_pollObjectNodeTreeSelected),
            ],
        ),
        VRayNodeCategory(
            'VRAY_SELECTORS',
            "Selectors",
            items = [
                nodeitems_utils.NodeItem("VRayNodeSelectObject"),
                nodeitems_utils.NodeItem("VRayNodeMultiSelect"),
            ],
        ),
        VRayWorldNodeCategory(
            'VRAY_ENVIRONMENT',
            "Environment",
            items = [
                nodeitems_utils.NodeItem("VRayNodeEnvironment"),

            ],
        ),
        VRayWorldNodeCategory(
            "VRAY_EFFECT",
            "Effects",
            items = [
                nodeitems_utils.NodeItem("VRayNodeEffectsHolder"),
            ] + buildItemsList('EFFECT'),
        ),
        VRayWorldNodeCategory(
            'VRAY_RENDERCHANNEL',
            "Render Channels",
            items = [
                nodeitems_utils.NodeItem("VRayNodeRenderChannels", label="V-Ray Channels Container"),
                VRayNodeRenderChannelCategory("VRAY_RENDERCHANNEL")
            ]
        ),
        VRayNodeCategory(
            "VRAY_LAYOUT",
            "Layout",
            items = [
                nodeitems_utils.NodeItem("VRayPluginListHolder"),
                nodeitems_utils.NodeItem("VRayNodeDebugSwitch"),
                nodeitems_utils.NodeItem("NodeFrame"),
                nodeitems_utils.NodeItem("NodeReroute"),
            ],
        ),
    ]


 ######  ##          ###     ######   ######     ##     ## ######## ######## ##     ##  #######  ########   ######
##    ## ##         ## ##   ##    ## ##    ##    ###   ### ##          ##    ##     ## ##     ## ##     ## ##    ##
##       ##        ##   ##  ##       ##          #### #### ##          ##    ##     ## ##     ## ##     ## ##
##       ##       ##     ##  ######   ######     ## ### ## ######      ##    ######### ##     ## ##     ##  ######
##       ##       #########       ##       ##    ##     ## ##          ##    ##     ## ##     ## ##     ##       ##
##    ## ##       ##     ## ##    ## ##    ##    ##     ## ##          ##    ##     ## ##     ## ##     ## ##    ##
 ######  ######## ##     ##  ######   ######     ##     ## ########    ##    ##     ##  #######  ########   ######


def vrayNodeDraw(self, context, layout):
    """ Draw widgets on a node. These widgets are positioned after the output sockets
        and before the input sockets.
    """
    if not hasattr(self, 'vray_type') or not hasattr(self, 'vray_plugin'):
        return

    vrayPlugin = PLUGINS[self.vray_type][self.vray_plugin]
    propGroup  = getattr(self, self.vray_plugin)

    # Draw node properties using 'nodeDraw'
    #
    if hasattr(vrayPlugin, 'nodeDraw'):
        vrayPlugin.nodeDraw(context, layout, self)
        
    elif hasattr(vrayPlugin, 'Node') and 'widgets' in vrayPlugin.Node:
        uiPainter = draw_utils.UIPainter(context, vrayPlugin, propGroup, node = self)
        uiPainter.renderWidgets(layout, vrayPlugin.Node['widgets'])
        


def vrayNodeDrawSide(self, context, layout):
    """ Draw widgets in the node's sidebar """
    if not hasattr(self, 'vray_type') or not hasattr(self, 'vray_plugin'):
        return

    # TODO: This commented code is intentionally retained so that we remembered
    # to check that the functionality is not broken after converting the 
    # corresponding plugin descriptions. Remove afterwards. 
    #if self.vray_type == 'LIGHT' and self.vray_plugin not in {'LightMesh'}:
    #    # We only need sockets from 'LIGHT' nodes.
    #    # Params will be taken from lamp propGroup
    #    #
    #    return

    vrayPlugin = PLUGINS[self.vray_type][self.vray_plugin]

    classes.drawPluginUI(
        context,
        layout,
        getattr(self, self.vray_plugin), # PropertyGroup
        vrayPlugin,                      # Plugin module
        self                             # node
    )


def _updateRenderChannelsState(node, enabled):
    """ Update render channels indicators in the property panel"""
    VRayRenderChannels = bpy.context.scene.world.vray.VRayRenderChannels
    if renderChannels := getattr(VRayRenderChannels, node.bl_idname):
        renderChannels.nodeUpdatesEnabled = False
        renderChannels.nodesCount += 1 if enabled else -1
        renderChannels.enabled = renderChannels.nodesCount >= 1


def _initTemplates(node, pluginModule):
    if not (propGroup := NodeUtils.getVrayPropGroup(node)):
        return

    for attrDesc in [a for a in pluginModule.Parameters if a['type'] == 'TEMPLATE']:
        attrName = attrDesc['attr']
        templateInst = getattr(propGroup, attrName)
        if hasattr(templateInst, 'init'):
            templateInst.init(pluginModule, attrDesc)

def vrayNodeInit(self, context):
    """ This is the init() function for automatically created VRay nodes """
    if not hasattr(self, 'vray_type') or self.vray_type == 'NONE':
        return
    if not hasattr(self, 'vray_plugin') or self.vray_plugin == 'NONE':
        return

    try:
        pluginModule = PLUGINS[self.vray_type][self.vray_plugin]
        
        # Set the unique ID of the node to the propety group. It will be
        # used to reach the node from the property group in callbacks which 
        # do not supply node info. PointerProperty type cannot be used for the 
        # node reference because it only supports types derived from bpy.types.ID
        # This function will also set the parent_node_id on the node's property group.
        syncObjectUniqueName(self, reset=True)
        
        NodeUtils.addInputsOutputs(self, pluginModule)

        if self.vray_type == 'RENDERCHANNEL':
            _updateRenderChannelsState(self, True)
        
        if hasattr(pluginModule, "nodeInit"):
            # Give the node a chance to initialize plugin-specific state
            pluginModule.nodeInit(self)

        _initTemplates(self, pluginModule)
    except Exception as ex:
        debug.printExceptionInfo(ex, f"Failed to init node: {self.vray_plugin}")
        raise ex


def vrayNodeCopy(self, node):
    """ Copy 'node' to 'self' """

    # NOTE: Do not try to access the values of the 'node's sockets in this function. 
    # The reference from the socket to the node has not yet been set and any custom 
    # setter/getter function will cause an access violation. 

    # Create a new unique ID for the node and set the link to it in the property group
    syncObjectUniqueName(self, reset=True)

    propGroupCopy = getattr(self, self.vray_plugin)
    propGroupCopy.parent_node_id = self.unique_id
    
    pluginModule = PLUGINS[self.vray_type][self.vray_plugin]
    if hasattr(pluginModule, "nodeCopy"):
        # Give the node a chance to de-initialize plugin-specific state
        pluginModule.nodeCopy(self, node)

   

def vrayNodeFree(self: bpy.types.Node):
    if self.vray_type == 'RENDERCHANNEL':
        _updateRenderChannelsState(self, False)

    pluginModule = PLUGINS[self.vray_type][self.vray_plugin]
    if hasattr(pluginModule, "nodeFree"):
        # Give the node a chance to de-initialize plugin-specific state
        pluginModule.nodeFree(self)


def vrayNodeDrawLabel(self):
    return self.name


@classmethod
def vrayNodePoll(cls: bpy.types.Node, nodetree: bpy.types.NodeTree):
    return cls.bl_idname.startswith('VRayNode') and bpy.context.engine == 'VRAY_RENDER_RT'


def vrayNodeUpdate(node: bpy.types.Node):
    # This callback is invoked when the topology of a nodetree changes. For VRay custom nodes,
    # node tree updates are not generated by Blender for some reason (it seems that they are only 
    # generated if any of the node's sockets is a stock Blender socket). This is why we need
    # to manually tag the nodetree as having been updated
    parentNodeTree = node.id_data

    # For V-Ray nodes, update the object whose nodetree has changed
    if hasattr(parentNodeTree, 'vray'):
        match parentNodeTree.vray.tree_type:
            case 'MATERIAL':
                if mtl := NodeUtils.findDataObjFromNode(bpy.data.materials, node):
                    UpdateTracker.tagMtlTopology(bpy.context, mtl)
            case 'OBJECT':
                if obj := NodeUtils.findDataObjFromNode(bpy.data.objects, node, isObjTreeNode=True):
                    obj.update_tag()
            case 'LIGHT':
                if light := NodeUtils.findDataObjFromNode(bpy.data.lights, node):
                    UpdateTracker.tagUpdate(light, UpdateTarget.LIGHT, UpdateFlags.TOPOLOGY)
            case 'WORLD':
                if node.vray_type == 'RENDERCHANNEL':
                    # Suppress updates for render channel nodes.
                    # They should be exported only once during the first "full" export
                    pass

    parentNodeTree.update_tag()





########  ##    ## ##    ##    ###    ##     ## ####  ######     ##    ##  #######  ########  ########  ######
##     ##  ##  ##  ###   ##   ## ##   ###   ###  ##  ##    ##    ###   ## ##     ## ##     ## ##       ##    ##
##     ##   ####   ####  ##  ##   ##  #### ####  ##  ##          ####  ## ##     ## ##     ## ##       ##
##     ##    ##    ## ## ## ##     ## ## ### ##  ##  ##          ## ## ## ##     ## ##     ## ######    ######
##     ##    ##    ##  #### ######### ##     ##  ##  ##          ##  #### ##     ## ##     ## ##             ##
##     ##    ##    ##   ### ##     ## ##     ##  ##  ##    ##    ##   ### ##     ## ##     ## ##       ##    ##
########     ##    ##    ## ##     ## ##     ## ####  ######     ##    ##  #######  ########  ########  ######

# This list is used for registration/unregistration of the dynamic classes
DynamicClasses = []


def createDynamicNodeClass(pluginType, pluginCategory, className, vrayPlugin, menuType = None, nodeLabel = "", pluginSubtype = ""):
    """ Creates the class for a custom node representing a VRay plugin. The class
        is created with only boilerplate code. Plugin-specific data and functions 
        are added by the caller.
    """
    if not nodeLabel:
        nodeLabel = vrayPlugin.NAME

    DynNodeClassAttrs = {
        'bl_idname' : className,
        'bl_label'  : nodeLabel,
        #'bl_icon'   : VRayNodeTypeIcon.get(pluginCategory, 'VRAY_LOGO_MONO'),
        #'bl_menu'   : textureMenuType,

        'vray_menu_subtype' : pluginSubtype,
        
        'init'             : vrayNodeInit,
        'copy'             : vrayNodeCopy,
        'free'             : vrayNodeFree,
        'draw_buttons'     : vrayNodeDraw,
        'draw_buttons_ext' : vrayNodeDrawSide,
        'draw_label'       : vrayNodeDrawLabel,
        'poll'             : vrayNodePoll,
        'update'           : vrayNodeUpdate,

        '__annotations__' : {
            'vray_type'   : bpy.props.StringProperty(default=pluginCategory),
            'vray_plugin' : bpy.props.StringProperty(default=pluginType),
        }
    }

    # Allow each plugin to add its own functions to the registered class attributes.
    # This is necessary in order to use the node in scenarios where only registered 
    # functions are allowed, e.g. as callbacks passed in operator context. In order to 
    # use this functionality, the plugin must define a 'nodeRegister' function which 
    # should return a dictionary of functionName => function
    pluginModule = getPluginModule(pluginType)
    if fnRegister := getattr(pluginModule, 'nodeRegister', None):
        DynNodeClassAttrs.update(fnRegister())
                                 
    if menuType:
        DynNodeClassAttrs['bl_menu'] = menuType

    return type(
        className,                # Name
        (mixin.VRayNodeBase,),    # Inheritance
        DynNodeClassAttrs         # Attributes
    )

def _createAdditionalRenderChannelNodes():
    """ Register Node classes for the render channels with non-default 'alias' property set
        (channel variants).

        The nodes for the render channels with the default 'alias' value have been already
        created from the respective plugin descriptions, registered and added to the
        VRayNodeTypes["RENDERCHANNEL"] list.
    """
    import copy
    channels = VRayNodeTypes["RENDERCHANNEL"]

    for customNode in customRenderChannelNodesDesc:  
        # Make a copy of the original (default) plugin module and set the properties required
        # by the particular override
        pluginType = customNode["base_plugin_type"]
        vrayPlugin  = copy.deepcopy(PLUGINS["RENDERCHANNEL"][pluginType])
        
        # Set the overriding properties for the variant
        for param in vrayPlugin.Parameters:
            if param["attr"] in customNode["params"].keys():
                param["default"] = customNode["params"][param["attr"]]

        # Create the boilerplate for the node class
        nodeLabel = customNode['params']['name']
        variantClassName = f"VRayNodeRenderChannel{customNode['params']['name'].replace(' ', '').replace('-', '')}"
        
        # Make sure the names of all channels in the list are unique. Re-registering a class results in a
        # very hard to debug error. 
        if any((c for c in channels if c.bl_idname ==  variantClassName)):
            debug.printError(f"Failed to register render channel variant {variantClassName} because it has" \
                              " the same class name as an already created channel")
        
        nodeClass = createDynamicNodeClass(pluginType, "RENDERCHANNEL", variantClassName, vrayPlugin,
                                            nodeLabel = nodeLabel, pluginSubtype = customNode["Subtype"])

        # Register the variant render channel class
        class_utils.registerPluginPropertyGroup(nodeClass, vrayPlugin, overridePropGroup = True)
        bpy.utils.register_class(nodeClass)

        channels.append(nodeClass)
    


def _loadDynamicNodes():
    """ Dynamic nodes are the ones that are created based only on their JSON descriptions.
        In contrast, there are a number of custom nodes for which node classes are defined explicitly
        in the ./nodes/specials/*.py files
    """
    global DynamicClasses
    global VRayNodeTypes

    DynamicClasses = []

    # Runtime Node classes generation
    #
    for pluginCategory in VRayNodeTypes:
        VRayNodeTypes[pluginCategory] = []

        for pluginType in sorted(PLUGINS[pluginCategory]):
            # Skip manually created nodes
            if pluginType in MANUALLY_CREATED_PLUGINS:
                continue

            if pluginType in  SKIPPED_PLUGINS:
                continue


            vrayPlugin  = PLUGINS[pluginCategory][pluginType]
            textureMenuType = getattr(vrayPlugin, 'MENU', None)

            DynNodeClassName = f"VRayNode{pluginType}"


            # Create the boilerplate for the node class
            DynNodeClass = createDynamicNodeClass(pluginType, pluginCategory, DynNodeClassName,
                                                  vrayPlugin, textureMenuType,
                                                  pluginSubtype=getattr(vrayPlugin, 'SUBTYPE'))

            if pluginType in  {'TexGradRamp', 'BitmapBuffer'}:
                NodeUtils.createFakeTextureAttribute(DynNodeClass, updateFunc=NodeUtils.selectedObjectTagUpdate)

            if pluginType == 'TexSoftbox':
                NodeUtils.createFakeTextureAttribute(DynNodeClass, 'ramp_grad_vert')
                NodeUtils.createFakeTextureAttribute(DynNodeClass, 'ramp_grad_horiz')
                NodeUtils.createFakeTextureAttribute(DynNodeClass, 'ramp_grad_rad')
                NodeUtils.createFakeTextureAttribute(DynNodeClass, 'ramp_frame')

            # Add a property group with all plugin's properties
            class_utils.registerPluginPropertyGroup(DynNodeClass, vrayPlugin)
            
            bpy.utils.register_class(DynNodeClass)
            VRayNodeTypes[pluginCategory].append(DynNodeClass)

            DynamicClasses.append(DynNodeClass)

    # Add manually defined classes
    VRayNodeTypes['BRDF'].append(specials.brdf.VRayNodeBRDFLayered)
    
    VRayNodeTypes['MATERIAL'].append(specials.material.VRayNodeMtlMulti)
    VRayNodeTypes['MATERIAL'].append(specials.material.VRayNodeMtlOSL)

    VRayNodeTypes['TEXTURE'].append(specials.texture.VRayNodeTexLayeredMax)
    VRayNodeTypes['TEXTURE'].append(specials.material.VRayNodeTexOSL)

    # Sort texture list alphabetically. This will be the order the Texture nodes
    # will show on the Node Add list.
    VRayNodeTypes['TEXTURE'].sort(key=lambda e: e.bl_label)

    _createAdditionalRenderChannelNodes()

########  ########  ######   ####  ######  ######## ########     ###    ######## ####  #######  ##    ##
##     ## ##       ##    ##   ##  ##    ##    ##    ##     ##   ## ##      ##     ##  ##     ## ###   ##
##     ## ##       ##         ##  ##          ##    ##     ##  ##   ##     ##     ##  ##     ## ####  ##
########  ######   ##   ####  ##   ######     ##    ########  ##     ##    ##     ##  ##     ## ## ## ##
##   ##   ##       ##    ##   ##        ##    ##    ##   ##   #########    ##     ##  ##     ## ##  ####
##    ##  ##       ##    ##   ##  ##    ##    ##    ##    ##  ##     ##    ##     ##  ##     ## ##   ###
##     ## ########  ######   ####  ######     ##    ##     ## ##     ##    ##    ####  #######  ##    ##

class NodeSidePropertiesDraw:
    """ Hook for the sidebar node properties panel draw function.
        When V-Ray is the active render engine, the panel will not show the 
        'Inputs:' section.
    """  
    original = None   # The original 'draw' function of NODE_PT_active_node_properties
    
    @staticmethod
    def hook():
        from bl_ui.space_node import NODE_PT_active_node_properties
        NodeSidePropertiesDraw.original =  NODE_PT_active_node_properties.draw
        NODE_PT_active_node_properties.draw = NodeSidePropertiesDraw._drawSidebarProps
        
    @staticmethod
    def unhook():
        from bl_ui.space_node import NODE_PT_active_node_properties
        NODE_PT_active_node_properties.draw = NodeSidePropertiesDraw.original

    @staticmethod
    def _drawSidebarProps(self, context):
        if context.engine == 'VRAY_RENDER_RT':
            layout = self.layout
            node = context.active_node
            # set "node" context pointer for the panel layout
            layout.context_pointer_set("node", node)

            if hasattr(node, "draw_buttons_ext"):
                node.draw_buttons_ext(context, layout)
            elif hasattr(node, "draw_buttons"):
                node.draw_buttons(context, layout)
        else:
            NodeSidePropertiesDraw.original(self, context)
        

def register():
    _loadDynamicNodes()
    nodeitems_utils.register_node_categories('VRAY_NODES', getCategories())
    NodeSidePropertiesDraw.hook()

    for menuClass in _getChannelMenuClasses():
        bpy.utils.register_class(menuClass)

def unregister():
    for menuClass in _getChannelMenuClasses():
        bpy.utils.unregister_class(menuClass)

    NodeSidePropertiesDraw.unhook()
    nodeitems_utils.unregister_node_categories('VRAY_NODES')

    for regClass in DynamicClasses:
        bpy.utils.unregister_class(regClass)
