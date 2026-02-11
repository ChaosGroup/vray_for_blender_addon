# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
from vray_blender import debug
from vray_blender.lib.mixin import VRayNodeBase
from vray_blender.nodes.sockets import addInput
from vray_blender.nodes import utils as NodesUtils
from vray_blender.exporting.tools import getLinkedFromSocket
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
    
    renderChannel = worldTree.nodes.new(nodeName)
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
    for inputSock in channelsNode.inputs:
        if inputSock.is_linked and inputSock.links[0].from_node.bl_idname == nodeName:
            worldTree.nodes.remove(inputSock.links[0].from_node)
            channelsNode.inputs.remove(inputSock)

    channelCnt = 1
    for inputSock in channelsNode.inputs:
        inputSock.name = f"Channel {channelCnt}"
        channelCnt += 1


def _getConnectedChannelsNode(worldTree: bpy.types.NodeTree):
    """ Returns the node connected to the 'Channels' socket of the world output node """
    if outputNode := NodesUtils.getOutputNode(worldTree):
        if (channelsSock := getLinkedFromSocket(outputNode.inputs['Channels'])) and \
            channelsSock.node.bl_idname == 'VRayNodeRenderChannels':

            return channelsSock.node
    
    return None

def _setRenderChannelEnabled(self, useRenderChannel: bool):
    """ Setter for the 'enabled' property of the render channel indicator property group.
        If the render channel is enabled, a new node for the render element is created in the world node tree.
        Otherwise, the existing node for the render element is removed from the tree.
    """
    currentState = _getRenderChannelEnabled(self)
    if currentState != useRenderChannel:
        world = bpy.context.scene.world
        assert (world and world.node_tree), 'Cannot show a render channel checkbox without an active world'

        worldTree = world.node_tree
        if not world.vray.is_vray_class:
            tree_defaults.addWorldNodeTree(world)

        outputNode = NodesUtils.getOutputNode(worldTree) or worldTree.nodes.new('VRayNodeWorldOutput')

        if not (channelsOutputNode := _getConnectedChannelsNode(worldTree)):
            channelsOutputNode = worldTree.nodes.new('VRayNodeRenderChannels')
            channelsOutputNode.location.x = outputNode.location.x - 200
            channelsOutputNode.location.y = outputNode.location.y - 150 
            worldTree.links.new(outputNode.inputs['Channels'], channelsOutputNode.outputs['Channels'])

        if useRenderChannel:
            _createRenderChannel(worldTree, channelsOutputNode, self.nodeName)
        else:
            _removeRenderChannel(worldTree, channelsOutputNode, self.nodeName)


def _getRenderChannelEnabled(self):
    """ Getter for the 'enabled' property of the render channel indicator property group.
        Returns if node for the render element is connected to the 'Channels' socket of the world output node.
    """
    world = bpy.context.scene.world
    if not (world and world.node_tree and world.vray.is_vray_class):
        return False

    if channelsOutputNode := _getConnectedChannelsNode(world.node_tree):
        return any(s.links[0].from_node.bl_idname == self.nodeName for s in channelsOutputNode.inputs if s.is_linked)
    
    return False


_vrayRenderChannelsType = None
_renderChannelIndicatorTypesList = []

# Creates 'RenderChannelIndicator' property group which contains the properties below:
# enabled - indicates if particular render element has been created in the world node tree. 
#   If switched from False to True, new node for the render element is created.
#   Otherwise, existing node instance for the render element is removed from the tree.
# nodeName - name of the node representing the current render element
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
                    set         = _setRenderChannelEnabled,
                    get         = _getRenderChannelEnabled,
                    options     = set() # Explicitly reset as it defaults to {'ANIMATABLE'}
                ),
                # Name of the node representing the current render element
                "nodeName": bpy.props.StringProperty(default=nodeName)
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
        elementsAnnotations[subtypeName] = bpy.props.BoolProperty(name=subtypeName, default=False)

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