# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later


import bpy
import mathutils
import os

from vray_blender.exporting import node_export as commonNodesExport
from vray_blender.exporting.tools import getFarNodeLink, getInputSocketByAttr, getInputSocketByName
from vray_blender.lib import plugin_utils
from vray_blender.lib.defs import  PluginDesc, NodeContext
from vray_blender.lib.names import Names
from vray_blender.lib.image_utils import getVRayImageFormatFilter, VRAY_IMAGE_FORMATS_LIST
from vray_blender.lib.path_utils import tryGetRelativePath
from vray_blender.nodes.operators.sockets import VRayNodeAddCustomSocket, VRayNodeDelCustomSocket
from vray_blender.nodes.sockets import addInput, RGBA_SOCKET_COLOR, VRaySocketColorMult, moveExtendSocketToBottom
from vray_blender.nodes.links import vrayNodeInsertLink
from vray_blender.nodes.utils import selectedObjectTagUpdate, getUpdateCallbackPropertyContext, getNodeOfPropGroup, createNode, DisableAutoConnect
from vray_blender.nodes.tools import deselectNodes
from vray_blender.plugins.templates.common import VRAY_OT_simple_button
from vray_blender.lib.mixin import VRayOperatorBase

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

    def draw_impl(self, context, layout, node, text):
        split = layout.split(factor=0.6)
        left = split.column()

        texSplit = left.split(factor=0.4)
        texSplit.prop(self, 'value', text='')
        if self.hasActiveFarLink():
            texSplit.prop(self, 'multiplier', text='')
        elif type(self.value) is mathutils.Color:
            texSplit.label(text=self.name)

        row = split.row(align=True)
        row.alignment = 'RIGHT'

        col2 = row.column()
        col2.prop(self, 'id', text='')
        row.separator(factor=1)
        col3 = row.column()
        col3.prop(self, 'use', text='')


    @classmethod
    def draw_color_simple(cls):
        return RGBA_SOCKET_COLOR

    def hasActiveFarLink(self):
        return self.use and super().hasActiveFarLink()

    def getFarLink(self):
        return super().getFarLink() if self.use else None

    def copy(self, dest):
        dest.id = self.id
        dest.use = self.use
        super().copy(dest)


class VRAY_OT_node_texmulti_socket_add(VRayNodeAddCustomSocket, VRayOperatorBase):
    bl_idname      = 'vray.node_texmulti_socket_add'
    bl_label       = "Add Texture Socket"
    bl_description = "Add Texture socket"
    bl_options     = {'INTERNAL', 'UNDO'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.vray_socket_type = 'VRaySocketTexMulti'
        self.vray_socket_name = 'Texture'


    def execute(self, context: bpy.types.Context):
        selectedObjectTagUpdate(self, context)
        node = context.node

        humanIndex = len(_getTexSockets(node)) + 1
        newSock = addInput(node, 'VRaySocketTexMulti', _getTexSockName(humanIndex))
        newSock.id = max([s.id for s in _getTexSockets(node)] + [0]) + 1

        # Move the extend socket to the end
        moveExtendSocketToBottom(node)

        return {'FINISHED'}


class VRAY_OT_node_texmulti_socket_del(VRayNodeDelCustomSocket, VRayOperatorBase):
    bl_idname      = 'vray.node_texmulti_socket_del'
    bl_label       = "Remove Texture Socket"
    bl_description = "Removes Texure socket (only not linked sockets will be removed)"
    bl_options     = {'INTERNAL', 'UNDO'}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.vray_socket_type = 'VRaySocketTexMulti'
        self.vray_socket_name = 'Texture'

    def execute(self, context: bpy.types.Context):
        selectedObjectTagUpdate(self, context)
        VRayNodeDelCustomSocket.execute(self, context)
        return {'FINISHED'}


class VRAY_OT_node_texmulti_add_from_folder(VRayOperatorBase):
    bl_idname      = 'vray.node_texmulti_add_from_folder'
    bl_label       = "Add Textures"
    bl_description = "Add textures from a folder"
    bl_options     = { 'INTERNAL', 'UNDO' }

    directory: bpy.props.StringProperty(subtype='DIR_PATH', options={'SKIP_SAVE', 'HIDDEN'})
    files: bpy.props.CollectionProperty(type=bpy.types.OperatorFileListElement, options={'SKIP_SAVE', 'HIDDEN'})
    filter_glob: bpy.props.StringProperty(default=getVRayImageFormatFilter(), options={'HIDDEN'})
    relative: bpy.props.BoolProperty(name="Relative Path", default=True)

    node_name: bpy.props.StringProperty(options={'SKIP_SAVE', 'HIDDEN'})

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        if not context.space_data or context.space_data.type != 'NODE_EDITOR':
            return {'CANCELLED'}
        ntree = context.space_data.edit_tree
        if not ntree:
            return {'CANCELLED'}

        node = ntree.nodes.get(self.node_name)
        if not node or not node.bl_idname == 'VRayNodeTexMulti':
            return {'CANCELLED'}

        if not os.path.isdir(self.directory):
            return {'CANCELLED'}

        files = [f.name for f in self.files if f.name.lower().endswith(VRAY_IMAGE_FORMATS_LIST)]
        files.sort()

        if not files:
            self.report({'ERROR'}, 'No valid images selected')
            return {'FINISHED'}

        deselectNodes(ntree)

        with DisableAutoConnect():
            # Clear existing non-linked texture sockets
            texSockets = _getTexSockets(node)
            for s in reversed(texSockets):
                if not s.is_linked:
                    node.inputs.remove(s)

            startIndex = len(_getTexSockets(node))

            for i, file in enumerate(files):
                filename = bpy.path.basename(file)
                filepath = os.path.join(self.directory, filename)
                filepath = bpy.path.abspath(filepath)
                if not os.path.exists(filepath):
                    continue

                imageNode = createNode(ntree, "VRayNodeMetaImageTexture")
                imageNode.label = filename

                imageBlockName = bpy.path.display_name_from_filepath(filepath)
                if self.relative:
                    relativePath = tryGetRelativePath(filepath)
                    filepath = relativePath if relativePath is not None else filepath
                image = bpy.data.images.load(filepath, check_existing=True)
                imageNode.texture.image = image
                imageNode.texture.image.name = imageBlockName

                humanIndex = startIndex + i + 1
                sockName = _getTexSockName(humanIndex)
                newSock = addInput(node, 'VRaySocketTexMulti', sockName)
                newSock.id = humanIndex

                ntree.links.new(imageNode.outputs['Color'], newSock)

                imageNode.location.x = node.location.x - 400
                imageNode.location.y = node.location.y - (i * 300)

        selectedObjectTagUpdate(node, context)

        return {'FINISHED'}


def nodeRegister():
    """ Register additional member-functions on the node class """

    return {
        'onRemoveTexture' : onRemoveTexture
    }

def addTexMultiExtendSocket(node):
    sockExtend = addInput(node, 'VRaySocketExtend', "")
    sockExtend.add_operator = 'vray.node_texmulti_socket_add'
    sockExtend.del_operator = 'vray.node_texmulti_socket_del'

def nodeInit(node: bpy.types.Node):
    DEFAULT_SOCKETS = 5

    for i in range(DEFAULT_SOCKETS):
        humanIndex = i + 1
        texSock = addInput(node, 'VRaySocketTexMulti', _getTexSockName(humanIndex))
        texSock.id = humanIndex

        # Set a different shade of grey to each of the pre-created sockets
        clr = 1.0 - (1.0 / DEFAULT_SOCKETS) * i
        texSock.value = mathutils.Color((clr, clr, clr))

    addTexMultiExtendSocket(node)
    getInputSocketByAttr(node, 'id_gen_tex').hide = (node.TexMulti.mode != "30")


def nodeInsertLink(link: bpy.types.NodeLink):
    node = link.to_node
    if link.to_socket.bl_idname == 'VRaySocketExtend':
        from_socket = link.from_socket
        ntree = node.id_data

        humanIndex = len(_getTexSockets(node)) + 1
        newSock = addInput(node, 'VRaySocketTexMulti', _getTexSockName(humanIndex))
        newSock.id = max([s.id for s in _getTexSockets(node)] + [0]) + 1

        # Move the extend socket to the end
        moveExtendSocketToBottom(node)

        # Update the link to use the new socket by creating a new link and removing the old one
        ntree.links.new(from_socket, newSock)
        ntree.links.remove(link)


def nodeDraw(context: bpy.types.Context, layout: bpy.types.UILayout, node: bpy.types.Node):
    split = layout.split()
    row = split.row(align=True)
    row.operator('vray.node_texmulti_socket_add', icon="ADD", text="Add Texture")
    row.operator('vray.node_texmulti_socket_del', icon="REMOVE", text="")
    op = row.operator('vray.node_texmulti_add_from_folder', icon='FILE_FOLDER', text='')
    op.node_name = node.name

    layout.separator()


def widgetDrawTexMap(context, layout, propGroup, widgetAttr):
    node = getNodeOfPropGroup(propGroup)

    row = layout.row(align=True)
    row.operator('vray.node_texmulti_socket_add', icon="ADD", text="Add Texture")
    op = row.operator('vray.node_texmulti_add_from_folder', icon='FILE_FOLDER', text='')
    op.node_name = node.name

    layout.separator()
    panel = layout

    for i, s in enumerate(_getTexSockets(node)):
        panel.label(text=_getTexSockName(i + 1))
        row = panel.row()
        row.prop(s, 'value', text="Color")
        if s.hasActiveFarLink():
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

        if nodeLink := getFarNodeLink(sock):
            texPlugin = commonNodesExport.exportSocketLink(nodeCtx, nodeLink)
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
        'random_mode_scene_name'        : 512
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
        VRAY_OT_node_texmulti_add_from_folder,
    )


def register():
    for regClass in getRegClasses():
        bpy.utils.register_class(regClass)


def unregister():
    for regClass in reversed(getRegClasses()):
        bpy.utils.unregister_class(regClass)




