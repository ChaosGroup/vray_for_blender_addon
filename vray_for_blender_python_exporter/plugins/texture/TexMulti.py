
import bpy 
import mathutils

from vray_blender.exporting import node_export as commonNodesExport
from vray_blender.exporting.tools import getNodeLink, getInputSocketByAttr, getInputSocketByName
from vray_blender.lib import plugin_utils, draw_utils
from vray_blender.lib.defs import  PluginDesc, NodeContext
from vray_blender.lib.names import Names
from vray_blender.nodes.operators.sockets import VRayNodeAddCustomSocket, VRayNodeDelCustomSocket
from vray_blender.nodes.sockets import addInput, RGBA_SOCKET_COLOR, VRaySocketColorMult
from vray_blender.nodes.utils import selectedObjectTagUpdate, getUpdateCallbackPropertyContext, getNodeOfPropGroup
from vray_blender.plugins.templates.common import VRAY_OT_simple_button

plugin_utils.loadPluginOnModule(globals(), __name__)


_PLUGIN_TYPE = 'TexMulti'
_TEX_SOCKET_PREFIX = 'Texture '

class VRaySocketTexMulti(VRaySocketColorMult):
    bl_idname = 'VRaySocketTexMulti'
    bl_label  = 'TexMulti Socket'

    id: bpy.props.IntProperty(
        name = "ID",
        description = "For modes that use IDs, specifies the ID that corresponds to each texture",
        update = selectedObjectTagUpdate
    )

    use: bpy.props.BoolProperty(
        name        = "Use",
        description = "Enables the sub-texture for rendering",
        default     = True,
        update = selectedObjectTagUpdate
    )


    def draw(self, context, layout, node, text):
        split = layout.split(factor=0.9)
        
        col1 = split.column().row(align=True)
        
        super().draw(context, col1, node, text)
        col2 = split.column()
        col2.prop(self, 'use', text='')

    @classmethod
    def draw_color_simple(cls):
        return RGBA_SOCKET_COLOR

    def shouldExportLink(self):
        return self.use and super().shouldExportLink()
    

class VRAY_OT_node_texmulti_socket_add(VRayNodeAddCustomSocket, bpy.types.Operator):
    bl_idname      = 'vray.node_texmulti_socket_add'
    bl_label       = "Add Texture Socket"
    bl_description = "Add Texture socket"
    bl_options     = {'INTERNAL'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.vray_socket_type = 'VRaySocketTexMulti'
        self.vray_socket_name = 'Texture'


    def execute(self, context: bpy.types.Context):
        selectedObjectTagUpdate(self, context)
        VRayNodeAddCustomSocket.execute(self, context)
        context.node.inputs[-1].id = max((s.id for s in _getTexSockets(context.node))) + 1
        return {'FINISHED'}


class VRAY_OT_node_texmulti_socket_del(VRayNodeDelCustomSocket, bpy.types.Operator):
    bl_idname      = 'vray.node_texmulti_socket_del'
    bl_label       = "Remove Texture Socket"
    bl_description = "Removes Texure socket (only not linked sockets will be removed)"
    bl_options     = {'INTERNAL'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.vray_socket_type = 'VRaySocketTexMulti'
        self.vray_socket_name = 'Texture'

    def execute(self, context: bpy.types.Context):
        selectedObjectTagUpdate(self, context)
        VRayNodeDelCustomSocket.execute(self, context)
        return {'FINISHED'}



def nodeRegister():
    """ Register additional member-functions on the node class """

    return {
        'onRemoveTexture' : onRemoveTexture
    }


def nodeInit(node: bpy.types.Node):
    DEFAULT_SOCKETS = 5

    for i in range(DEFAULT_SOCKETS):
        humanIndex = i + 1
        texSock = addInput(node, 'VRaySocketTexMulti', _getTexSockName(humanIndex))
        texSock.id = humanIndex

        # Set a different shade of grey to each of the pre-created sockets
        clr = 1.0 - (1.0 / DEFAULT_SOCKETS) * i
        texSock.value = mathutils.Color((clr, clr, clr))

    getInputSocketByAttr(node, 'id_gen_tex').hide = (node.TexMulti.mode != "30")   


def nodeDraw(context: bpy.types.Context, layout: bpy.types.UILayout, node: bpy.types.Node):
    split = layout.split()
    row = split.row(align=True)
    row.operator('vray.node_texmulti_socket_add', icon="ADD", text="Add Texture")
    row.operator('vray.node_texmulti_socket_del', icon="REMOVE", text="")
    layout.separator()


def widgetDrawTexMap(context, layout, propGroup, widgetAttr):
    node = getNodeOfPropGroup(propGroup)

    layout.operator('vray.node_texmulti_socket_add', icon="ADD", text="Add Texture")
    layout.separator()
    panel = layout

    for i, s in enumerate(_getTexSockets(node)):
        panel.label(text=_getTexSockName(i + 1))
        row = panel.row()
        row.prop(s, 'value', text="Color")
        if s.is_linked:
            row.prop(s, 'multiplier', text='')
        
        row = panel.row()
        row.prop(s, 'id', text="ID")
        row.prop(s, 'use', text="Use")
        panel.separator()
        VRAY_OT_simple_button.create(row, 'Remove', 'Remove the selected texture from the list', node, 'onRemoveTexture', selectorID=str(i+1))
        panel.separator(type='LINE')


def exportTreeNode(nodeCtx: NodeContext):
    node = nodeCtx.node

    pluginDesc = PluginDesc(Names.nextVirtualNode(nodeCtx, _PLUGIN_TYPE), _PLUGIN_TYPE)
    pluginDesc.vrayPropGroup = node.TexMulti

    textures = []
    textureIDs = []

    for sock in [s for s in node.inputs if s.bl_idname == 'VRaySocketTexMulti']:
        if not sock.use:
            continue

        if sock.shouldExportLink():
            nodeLink = getNodeLink(sock)
            assert nodeLink is not None
            
            texPlugin = commonNodesExport.exportLinkedSocket(nodeCtx, nodeLink.to_socket)
            textures.append(texPlugin)
        else:
            # The socket is not connected, export a color wrapped in a TexCombineColor plugin.
            clrPlugin = commonNodesExport._exportCombineTexture(nodeCtx, sock.value, None, 1, asFloat=False)
            textures.append(clrPlugin)

        textureIDs.append(sock.id)

    if textures:
        pluginDesc.setAttribute('textures_list', textures)
        pluginDesc.setAttribute('ids_list', textureIDs)
    else:
        pluginDesc.resetAttribute('textures_list')
        pluginDesc.resetAttribute('ids_list')

    pluginDesc.setAttribute('random_mode', _getRandomMode(node))

    commonNodesExport.exportNodeTree(nodeCtx, pluginDesc, skippedSockets=[])
    return commonNodesExport.exportPluginWithStats(nodeCtx, pluginDesc) 


def onUpdateMode(src, context: bpy.types.Context, attrName: str):
    """ Update handler for the 'mode' property """
    propGroup = src
    propContext = getUpdateCallbackPropertyContext(propGroup, _PLUGIN_TYPE)

    getInputSocketByAttr(propContext.node, 'id_gen_tex').hide = (propGroup.mode != "30")


def onRemoveTexture(node: bpy.types.Node, context: bpy.types.Context, selectorID: str):
    """ Callback for the 'Remove' button in individual texture properties """
    sockID = int(selectorID)

    texSockets = [s for s in node.inputs if s.bl_idname == 'VRaySocketTexMulti']

    assert sockID <= len(texSockets), f"Invalid texture socket ID: {sockID}"

    sock = getInputSocketByName(node, _getTexSockName(sockID))
    node.inputs.remove(sock)

    # Renumber the remaining sockets
    for s in texSockets[sockID:]:
        s.name = _getTexSockName(sockID)
        sockID += 1

    selectedObjectTagUpdate(node, context)
            

def _getRandomMode(node):
    propNames = {
        'random_mode_node_handle'       : 1,
        'random_mode_render_id'         : 2,
        'random_mode_node_name'         : 4,
        'random_mode_particle_id'       : 8,
        'random_mode_instance_id'       : 16,
        'random_mode_polygon_selection' : 32,
        'random_mode_object_id'         : 64,
        'random_mode_mesh_element'      : 128,
        'random_mode_user_attribute'    : 256,
        'random_mode_scene_name'        : 512,
        'random_mode_tile'              : 1024
    }

    result = 0
    for attrName, id in propNames.items():
        result |= id if getattr(node.TexMulti, attrName) else 0

    return result


def _getTexSockets(node: bpy.types.Node):
    return [s for s in node.inputs if s.bl_idname == 'VRaySocketTexMulti']


def _getTexSockName(id: int):
    return f"{_TEX_SOCKET_PREFIX}{id}"


def getRegClasses():
    return (
        VRaySocketTexMulti,
        VRAY_OT_node_texmulti_socket_add,
        VRAY_OT_node_texmulti_socket_del,
    )


def register():
    for regClass in getRegClasses():
        bpy.utils.register_class(regClass)


def unregister():
    for regClass in reversed(getRegClasses()):
        bpy.utils.unregister_class(regClass)




