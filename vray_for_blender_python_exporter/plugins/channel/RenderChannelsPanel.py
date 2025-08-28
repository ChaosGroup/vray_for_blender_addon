import bpy
from vray_blender import debug
from vray_blender.lib.mixin import VRayNodeBase
from vray_blender.nodes.sockets import addInput
from vray_blender.nodes import utils as NodesUtils
from vray_blender.exporting.tools import getFarNodeLink, getLinkedFromSocket
from vray_blender.nodes import tree_defaults
from vray_blender.nodes.tools import deselectNodes
from vray_blender.lib import class_utils


TYPE = 'SYSTEM'
ID   = 'VRayRenderChannels'
NAME = 'Render Elements'
DESC = "Render elements menu"

VRayChannelNodeSubtypes = (
    "BEAUTY",
    "ADVANCED",
    "MATTE",
    "GEOMETRY",
    "UTILITY",
    "RAW"
)


def _createRenderChannel(worldTree: bpy.types.NodeTree, channelsNode: bpy.types.Node, nodeName: str):
    
    deselectNodes(worldTree)
    
    renderChannel  = worldTree.nodes.new(nodeName)
    renderChannel.location.x = channelsNode.location.x - 200
    sockPos = 0
    
    for sock in channelsNode.inputs:
        if not sock.is_linked:
            break
        sockPos += 1

    if sockPos == len(channelsNode.inputs):
        addInput(channelsNode, "VRaySocketRenderChannel", f"Channel {sockPos + 1}")

    renderChannel.location.y = channelsNode.location.y - (80 * sockPos)
    renderChannel.location.x = channelsNode.location.x - VRayNodeBase.bl_width_default - 50

    worldTree.links.new(renderChannel.outputs['Channel'], channelsNode.inputs[sockPos])


def _removeRenderChannel(worldTree: bpy.types.NodeTree, channelsNode: bpy.types.Node, nodeName: str):
    nodeForRemoval = NodesUtils.getNodeByType(worldTree, nodeName)
    outputSocket = nodeForRemoval.outputs['Channel']
    
    for nodeLink in outputSocket.links:
        if nodeLink.to_node == channelsNode and len(channelsNode.inputs) > 1:
            channelsNode.inputs.remove(nodeLink.to_socket)
            # Fixing Channel sockets counters in their labels
            channelCnt = 1
            for inputSock in channelsNode.inputs:
                inputSock.name = f"Channel {channelCnt}"
                channelCnt += 1
        elif nodeLink.to_node.bl_idname == 'NodeReroute':
            worldTree.nodes.remove(nodeLink.to_node)

    worldTree.nodes.remove(nodeForRemoval)

# Triggered when RenderChannelIndicator.enabled is switched
def _updateRenderChannel(self: bpy.types.PropertyGroup, context: bpy.types.Context):
    # This property group should not be editable when there isn't world node tree
    if not (context.scene.world and context.scene.world.node_tree):
        debug.printWarning('Cannot create a render channel without an active world')
        return

    if not context.scene.world.vray.is_vray_class:
        tree_defaults.addWorldNodeTree(context.scene.world)

    worldTree = context.scene.world.node_tree
    channelsNode = NodesUtils.getNodeByType(worldTree, 'VRayNodeRenderChannels')
    if channelsNode:
        if self.nodeUpdatesEnabled:
            if self.enabled:
                _createRenderChannel(worldTree, channelsNode, self.nodeName)
            else:
                _removeRenderChannel(worldTree, channelsNode, self.nodeName)
        
        # Resetting Node update trigger flag
        self.nodeUpdatesEnabled = True


_vrayRenderChannelsType = None
_renderChannelIndicatorTypesList = []

# Creates 'RenderChannelIndicator' property group which contains the properties below:
# enabled - indicates if particular render element has been created in the world node tree. 
#   If switched from False to True, new node for the render element is created.
#   Otherwise, existing node instance for the render element is removed from the tree.
# nodeUpdateEnabled - this property is used to explicitly stop the update function
# nodeName - name of the node representing the current render element
# nodesCount - counts how many such nodes are in the world three
def _createRenderChannelIndicatorType(uiName, nodeName):
    return type(
        # Name. Deviate from the convention and use a short prefix because the name length
        # could easily surpass the maximum length supported by Blender (64 chars at this time)
        f"RCI_{nodeName}", 
        (bpy.types.PropertyGroup,), # Inheritance
        {   '__annotations__': # Attributes
            {
                "enabled": bpy.props.BoolProperty(
                    name        = uiName,
                    default     = False,
                    update      = lambda self, context: _updateRenderChannel(self, context),
                    options     = set() # Explicitly reset as it defaults to {'ANIMATABLE'}
                ),
                # This property is used to explicitly stop the update function
                "nodeUpdatesEnabled": bpy.props.BoolProperty(default = True),

                # Name of the node representing the current render element
                "nodeName": bpy.props.StringProperty(default=nodeName),
                
                # Counts how many such nodes are in the world three
                "nodesCount": bpy.props.IntProperty(default=0),
            }
        }
    )

# Loads list of 'RenderChannelIndicator'-s
def _loadRenderChannelAnnotations(plugins):
    from vray_blender.nodes.customRenderChannelNodes import customRenderChannelNodesDesc

    global _renderChannelIndicatorTypesList

    # The custom render channel nodes aren't created yet,
    # for that the information for their corresponding is extracted from
    # plugins.PLUGINS and vray_blender.nodes.customRenderChannelNodeDesc
    renderChannels = plugins.PLUGINS["RENDERCHANNEL"]
    rendElemsDesc = [(f"VRayNode{name}", renderChannels[name].NAME.replace('Render Channel ',''))
                     for name in renderChannels]

    def toNodeNameAndLabel(elem):
        channelName = elem['params']['name']
        nodeName = f"VRayNodeRenderChannel{channelName.replace(' ','').replace('-', '')}"
        return (nodeName , channelName)
    
    rendElemsDesc += [toNodeNameAndLabel(elem) for elem in customRenderChannelNodesDesc]

    elementsAnnotations = {}
    for rendElem in rendElemsDesc:
        nodeName = rendElem[0]
        uiName = rendElem[1]

        RenderChannelIndicatorType = _createRenderChannelIndicatorType(uiName, nodeName)
        bpy.utils.register_class(RenderChannelIndicatorType)
        elementsAnnotations[nodeName] = bpy.props.PointerProperty(
            name = nodeName,
            type = RenderChannelIndicatorType
        )
        _renderChannelIndicatorTypesList.append(RenderChannelIndicatorType)


    
    return elementsAnnotations


def register():

    # circular import protection
    from vray_blender import plugins

    global _vrayRenderChannelsType

    elementsAnnotations = _loadRenderChannelAnnotations(plugins)
    
    # Creates indication if channel nodes subtype rollout menu is enabled
    for subtype in VRayChannelNodeSubtypes:
        subtypeName = subtype.title()
        elementsAnnotations[subtypeName]= bpy.props.BoolProperty(name=subtypeName, default=False)

    _vrayRenderChannelsType = type(
        "VRayRenderChannelsPanel", # Name
        (bpy.types.PropertyGroup,), # Inheritance
        {'__annotations__': elementsAnnotations} # Attributes
    )

    bpy.utils.register_class(_vrayRenderChannelsType)

    plugins.VRayWorld.__annotations__['VRayRenderChannels'] = bpy.props.PointerProperty(
        name = "VRayRenderChannelsPanel",
        type =  _vrayRenderChannelsType,
        description = ""
    )

    class_utils.registerPluginPropertyGroup(plugins.VRayWorld, plugins.PLUGINS['RENDERCHANNEL']['RenderChannelDenoiser'])


def unregister():
    global _vrayRenderChannelsType
    global _renderChannelIndicatorTypesList

    for regClass in [_vrayRenderChannelsType] + _renderChannelIndicatorTypesList:
        bpy.utils.unregister_class(regClass)

    _vrayRenderChannelsType = None
    _renderChannelIndicatorTypesList.clear()