
import bpy 

from vray_blender.exporting import node_export as commonNodesExport
from vray_blender.exporting.tools import getNodeLink
from vray_blender.lib import plugin_utils
from vray_blender.lib.defs import  AColor, PluginDesc, NodeContext
from vray_blender.lib.names import Names
from vray_blender.nodes import color_ramp
from vray_blender.nodes.sockets import addInput, removeInputs, VRaySocketAColor
from vray_blender.nodes.tools import isVrayNodeTree
from vray_blender.nodes.utils import selectedObjectTagUpdate, getNodeOfPropGroup
from vray_blender.plugins import getPluginAttr, getPluginModule

plugin_utils.loadPluginOnModule(globals(), __name__)


_TEX_SOCKET_PREFIX = 'Texture '
_PLUGIN_TYPE       = 'TexGradRamp'


class VRaySocketColorRampTexture(VRaySocketAColor):
    bl_idname = 'VRaySocketColorRampTexture'
    bl_label  = 'Color ramp texture'

    def _onUpdate(self: bpy.types.NodeSocket, ctx: bpy.types.Context):
        selectedObjectTagUpdate(self, ctx)
        pluginModule = getPluginModule('TexGradRamp')
        pluginModule.onUpdateColorRampSocket(self, self.node.texture)

    value: bpy.props.FloatVectorProperty(
        name = "Color",
        description = "Color",
        subtype = 'COLOR',
        min = 0.0,
        max = 1.0,
        soft_min = 0.0,
        soft_max = 1.0,
        size = 4,
        update = _onUpdate,
        default = AColor((1.0, 1.0, 1.0, 1.0))
    )


def nodeInit(node: bpy.types.Node):
    color_ramp.createRampTexture(node)
    _createRampSockets(node, node.texture.color_ramp)
    color_ramp.registerColorRamp(node, 'texture', node.texture)


def nodeCopy(nodeCopy: bpy.types.Node, nodeOrig: bpy.types.Node):
    texture = color_ramp.createRampTexture(nodeCopy)
    color_ramp.copyColorRamp(nodeOrig.texture.color_ramp, nodeCopy.texture.color_ramp)
    color_ramp.registerColorRamp(nodeCopy, 'texture', nodeCopy.texture)

    def reattachTexture():
        nodeCopy.texture = texture

    bpy.app.timers.register(reattachTexture)


def nodeFree(node: bpy.types.Node):
    color_ramp.unregisterColorRamp(node, 'texture', node.texture)

    
# TODO: Create a function to automatically draw ramp widget from
# the attribute type
def nodeDraw(context, layout, node):
    if not node.texture:
        # This will be the case right after the node has been copied from another node
        return
    
    layout.template_color_ramp(node.texture, 'color_ramp', expand=True)


def widgetDrawRamp(context, layout, propGroup, widgetAttr):
    node = getNodeOfPropGroup(propGroup)
    if not node.texture:
        # This will be the case right after the node has been copied from another node
        return
    
    layout.template_color_ramp(node.texture, 'color_ramp', expand=True)


def exportTreeNode(nodeCtx: NodeContext):
    node = nodeCtx.node

    pluginDesc = PluginDesc(Names.nextVirtualNode(nodeCtx, _PLUGIN_TYPE), _PLUGIN_TYPE)
    pluginDesc.vrayPropGroup = node.TexGradRamp

    # Export all properties associated with the color ramp.
    rampAttributes = ("texture", "colors", "positions", "interpolation")
    color_ramp.exportRampAttributes(nodeCtx.exporterCtx, pluginDesc, *rampAttributes)
    
    textures = pluginDesc.getAttribute('colors')
    
    for sock in node.inputs:
        if sock.shouldExportLink():
            nodeLink = getNodeLink(sock)
            assert nodeLink is not None
            
            linkedPlugin = commonNodesExport.exportVRayNode(nodeCtx, nodeLink)
            
            if _isRampTextureSocket(sock):
                # Replace the exported colors with textures
                texIndex = _sockNameToTexIndex(sock.name)
                textures[texIndex] = commonNodesExport._exportConverters(nodeCtx, nodeLink.to_socket, linkedPlugin)
            else:
                pluginDesc.setAttribute(sock.vray_attr, linkedPlugin)
        elif sock.vray_attr not in ('', *rampAttributes): 
            # rampAttributes were already exported, dynamic texture sockets have empty vray_attr
            if hasattr(sock, 'exportUnlinked'):
                attrDesc = getPluginAttr(getPluginModule(_PLUGIN_TYPE), sock.vray_attr)
                sock.exportUnlinked(nodeCtx, pluginDesc, attrDesc)
    
    return commonNodesExport.exportPluginWithStats(nodeCtx, pluginDesc) 


def registerColorRamps():
    """ Called from the Load Post event handler"""
    nodeTrees = (
        (bpy.data.materials, 'MATERIAL'), 
        (bpy.data.worlds, 'WORLD'), 
        (bpy.data.lights, 'LIGHT')
    )

    for tree in nodeTrees: 
        for item in [it for it in tree[0] if isVrayNodeTree(it.node_tree, tree[1])]:
            for node in [ n for n in item.node_tree.nodes if hasattr(n, _PLUGIN_TYPE)]:
                color_ramp.registerColorRamp(node, 'texture', node.texture)


def syncColorRamps():
    """ Called from Update Depsgraph Pre event handler """
    for node, _, texture in color_ramp.getColorRampsForPlugin(_PLUGIN_TYPE):
        _createRampSockets(node, texture.color_ramp) 


def onUpdateColorRampSocket(socket, texture):
    # Update ramp colors from socket colors
    ramp = texture.color_ramp
    elemIndex = _sockNameToTexIndex(socket.name)
    if ramp.elements[elemIndex].color[:] != socket.value[:]:
        ramp.elements[elemIndex].color = socket.value[:]
    
    
def _createRampSockets(node:bpy.types.Node, ramp):
    elemCount = len(ramp.elements)
    socketCount = len([s for s in node.inputs if _isRampTextureSocket(s)])

    if socketCount < elemCount:
        # Create sockets for any added ramp elements
        for i in range(socketCount, elemCount):
            addInput(node, 'VRaySocketColorRampTexture', _texIndexToSockName(i))
    elif elemCount < socketCount:
        # Remove the sockets corresponding to removed ramp elements
        sockNames = [_texIndexToSockName(i) for i in range(elemCount, socketCount)]
        removeInputs(node, sockNames, removeLinked=True)

    # Sync the colors between the ramp stops and the node sockets
    for i in range(elemCount):
        sock = node.inputs[_texIndexToSockName(i)]

        if sock.value[:] != ramp.elements[i].color[:]:
            sock.value = ramp.elements[i].color[:]


def _isRampTextureSocket(sock: bpy.types.NodeSocket):
    return (sock.vray_attr == '') and sock.name.startswith(_TEX_SOCKET_PREFIX) 


def _sockNameToTexIndex(sockName: str):
    return int(sockName[len(_TEX_SOCKET_PREFIX):]) - 1


def _texIndexToSockName(i: int):
    return f"{_TEX_SOCKET_PREFIX}{i+1}"



def register():
    bpy.utils.register_class(VRaySocketColorRampTexture)


def unregister():
    bpy.utils.unregister_class(VRaySocketColorRampTexture)
    



