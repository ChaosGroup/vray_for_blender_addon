# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later


import bpy

from vray_blender import plugins, osl
from vray_blender.exporting.update_tracker import UpdateTracker
from vray_blender.lib import draw_utils, class_utils
from vray_blender.lib.draw_utils import UIPainter
from vray_blender.lib.mixin import VRayNodeBase, VRayOperatorBase
from vray_blender.nodes.sockets import MATERIAL_SOCKET_COLOR, addInput, addOutput, VRayValueSocket, removeInputs, moveExtendSocketToBottom
from vray_blender.nodes.nodes import vrayNodeInit, vrayNodeDraw, vrayNodeDrawSide
from vray_blender.nodes.utils import selectedObjectTagUpdate, getActiveTreeNode
from vray_blender.nodes.links import getPluginModule, scheduleFixMisdirectedLink, vrayNodeInsertLink, autoConnectNode
from vray_blender.ui import classes


class VRaySocketMtlMulti(VRayValueSocket):
    bl_idname = 'VRaySocketMtlMulti'
    bl_label  = 'MtlMulti Socket'

    value: bpy.props.IntProperty(
        name = "ID",
        description = "This is the value used to loop the texture through the list of materials.",
        min = 0,
        update = selectedObjectTagUpdate
    )

    enabled: bpy.props.BoolProperty(
        default=True,
        update=selectedObjectTagUpdate
    )

    @staticmethod
    def tagMtlTopology(propGroup, context):
        if (ob := context.activeObject) and (mtl := ob.active_material) and mtl.node_tree:
            UpdateTracker.tagMtlTopology(context, mtl)


    def draw(self, context, layout, node, text):
        layout.prop(self, 'value', text="ID")


    def draw_property(self, context, layout, text):
        layout.prop(self, 'value', text="ID", slider=False, expand=False)
        layout.prop(self, 'enabled', text="Enabled")


    @classmethod
    def draw_color_simple(cls):
        return MATERIAL_SOCKET_COLOR


def addMtlMultiExtendSocket(node):
    sockExtend = addInput(node, 'VRaySocketExtend', "")
    sockExtend.add_operator = 'vray.node_mtlmulti_socket_add'
    sockExtend.del_operator = 'vray.node_mtlmulti_socket_del'


def getMaterialSockets(node):
    """ Return the material input sockets (the 'Material X' sockets) of a MtlMulti node,
        in their input-list order.

        Material sockets are located by their socket type instead of by name. The number in
        the 'Material X' name is the material's ID, which may be non-consecutive or not 0-based
        (e.g. after importing a scene with ids_list=[1, 5, 3]) and is also user-editable, so the
        name cannot be relied upon to identify or order the sockets.
    """
    return [s for s in node.inputs if s.bl_idname == 'VRaySocketMtlMulti']


def _getMtlNodeFromOperatorContext(context: bpy.types.Context):
    if hasattr(context, "node"):
        return context.node
    elif context.material and context.material.node_tree:
        return getActiveTreeNode(context.material.node_tree, 'MATERIAL')

class VRAY_OT_node_mtlmulti_socket_add(VRayOperatorBase):
    bl_idname      = 'vray.node_mtlmulti_socket_add'
    bl_label       = "Add MtlMulti Socket"
    bl_description = "Adds MtlMulti sockets"
    bl_options     = {'INTERNAL', 'UNDO'}

    def execute(self, context):
        if not (node := _getMtlNodeFromOperatorContext(context)):
            self.report({'WARNING'}, "Could not add socket to V-Ray Switch Mtl, failed to obtain the active node.")
            return {'CANCELLED'}

        node.addMaterial()
        return {'FINISHED'}


class VRAY_OT_node_mtlmulti_socket_del(VRayOperatorBase):
    bl_idname      = 'vray.node_mtlmulti_socket_del'
    bl_label       = "Remove MtlMulti Socket"
    bl_description = "Removes MtlMulti socket (only not linked sockets will be removed)"
    bl_options     = {'INTERNAL', 'UNDO'}

    def execute(self, context):
        if not (node := _getMtlNodeFromOperatorContext(context)):
            self.report({'WARNING'}, "Could not remove socket from V-Ray Switch Mtl, failed to obtain the active node.")
            return {'CANCELLED'}

        if node.materials < 2:
            # Do not allow the user to remove the last remaining material as
            # the node would cease to be functional.
            self.report({'WARNING'}, f"{node.bl_label} needs at least one material.")
            return {'CANCELLED'}

        # Remove the last material socket, identified by its position rather than by a name
        # rebuilt from the count, since the ID embedded in the name may be non-consecutive.
        lastMtlSock = getMaterialSockets(node)[-1]

        if removeInputs(node, [lastMtlSock.name], removeLinked=False):
            node.materials -= 1
            return {'FINISHED'}

        self.report({'WARNING'}, "Cannot remove linked materials. Unlink and try again.")
        return {'CANCELLED'}


class VRayNodeMtlMulti(VRayNodeBase):
    bl_idname = 'VRayNodeMtlMulti'
    bl_label  = 'V-Ray Switch Mtl'
    bl_icon   = 'MATERIAL'

    vray_type  : bpy.props.StringProperty(default='MATERIAL')
    vray_plugin: bpy.props.StringProperty(default='MtlMulti')

    wrap_id: bpy.props.BoolProperty(
        name        = "Wrap ID",
        description = "Wrap the material ID's to the largest specified ID for the material",
        default     =  False
    )

    materials: bpy.props.IntProperty(default=2, options={'HIDDEN'})

    def copy(self, srcNode):
        while self.materials < srcNode.materials:
            self.addMaterial()
        # Pair the sockets by position; the ID embedded in the name may differ between nodes.
        for dstSock, srcSock in zip(getMaterialSockets(self), getMaterialSockets(srcNode)):
            dstSock.value = srcSock.value
            dstSock.enabled = srcSock.enabled

    def _fixMisdirectedLink(self):
        # When creating a MtlMulti on top of an existing node link between materials it will get
        # connected to the Switch Texture socket. In this case insert_link doesn't get called so
        # we do it here manually.
        if mtlSockets := getMaterialSockets(self):
            scheduleFixMisdirectedLink(self, "Switch Texture", mtlSockets[0].name, {'VRaySocketMtl', 'VRaySocketBRDF'})

    def update(self):
        super().update()
        self._fixMisdirectedLink()

    def init(self, context):
        addInput(self, 'VRaySocketFloatNoValue', "Switch Texture", 'mtlid_gen_float', "MtlMulti")

        for i in range(self.materials):
            texSockName = f"Material {i}"
            mtlSock = addInput(self, 'VRaySocketMtlMulti', texSockName)
            mtlSock.setValue(i)
            mtlSock.enabled = True

        addMtlMultiExtendSocket(self)
        addOutput(self, 'VRaySocketMtl', "Material")
        autoConnectNode(self)

    def addMaterial(self):
        """ Add the inputs for a texture layer """
        # Derive the new material's ID from the existing sockets rather than from the socket
        # count, so it stays unique even when the current IDs are non-consecutive.
        newIndex = max((s.value for s in getMaterialSockets(self)), default=-1) + 1
        sockName = f"Material {newIndex}"
        sockMtl = addInput(self, 'VRaySocketMtlMulti', sockName)
        sockMtl.setValue(newIndex)
        sockMtl.enabled = True
        self.materials += 1
        moveExtendSocketToBottom(self)

    def nodeReset(self):
        """ Re-apply the creation-time values to the existing 'Material N' sockets (count preserved). """
        for i, mtlSock in enumerate(getMaterialSockets(self)):
            mtlSock.setValue(i)
            mtlSock.enabled = True


    def insert_link(self, link: bpy.types.NodeLink):
        def _doInsert(link):
            if link.to_socket.bl_idname == 'VRaySocketExtend':
                from_socket = link.from_socket
                ntree = self.id_data
                self.addMaterial()
                ntree.links.new(from_socket, getMaterialSockets(self)[-1])
                ntree.links.remove(link)

        vrayNodeInsertLink(self, link, _doInsert)

    def draw_buttons(self, context, layout):
        """ Draw node """
        split = layout.split()
        col = split.column()
        painter = UIPainter(context, getPluginModule('MtlMulti'), self.MtlMulti, self)
        painter.drawAttr(col, 'wrap_id', 'Loop Materials') 

        split = layout.split()
        row = split.row(align=True)
        row.operator('vray.node_mtlmulti_socket_add', icon="ADD", text="Add")
        row.operator('vray.node_mtlmulti_socket_del', icon="REMOVE", text="")


    def draw_buttons_ext(self, context, layout):
        """ Draw node property page """

        classes.drawPluginUI(context, layout, self.MtlMulti, plugins.getPluginModule('MtlMulti'), self)
        layout.separator()

        row = layout.row(align=True)
        row.operator('vray.node_mtlmulti_socket_add', icon="ADD", text="Add")
        row.operator('vray.node_mtlmulti_socket_del', icon="REMOVE", text="")

        mtlsPanel = draw_utils.subPanel(layout)

        for sockMtl in getMaterialSockets(self):
            uniqueID = f"{self.as_pointer()}_{sockMtl.identifier}"

            if panelBody := draw_utils.rollout(mtlsPanel, uniqueID, sockMtl.name):
                sockMtl.draw_property(context, draw_utils.subPanel(panelBody), text="")


class VRAY_OT_osl_node_update(VRayOperatorBase):
    bl_idname      = "vray.osl_node_update"
    bl_label       = "Update"
    bl_description = ""
    bl_options     = {'INTERNAL'}

    def execute(self, context):
        osl.update_script_node(context.node)
        return {'FINISHED'}


def osl_node_draw_buttons(self, context, layout):
    row = layout.row()
    row.prop(self, 'mode', expand=True)
    row = layout.row(align=True)
    if self.mode == 'INTERNAL':
        row.prop(self, 'script', text='', icon='NONE')
    else:
        row.prop(self, 'filepath', text='', icon='NONE')
    row.operator("vray.osl_node_update", text='', icon='FILE_REFRESH')
    vrayNodeDraw(self, context, layout)


class VRayNodeTexOSL(VRayNodeBase):
    bl_idname = 'VRayNodeTexOSL'
    bl_label  = 'V-Ray OSL Texture'
    bl_icon   = 'TEXTURE'

    vray_type  : bpy.props.StringProperty(default='TEXTURE')
    vray_plugin: bpy.props.StringProperty(default='TexOSL')

    draw_buttons = osl_node_draw_buttons
    draw_buttons_ex = vrayNodeDrawSide

    def init(self, context):
        vrayNodeInit(self, context)


class VRayNodeMtlOSL(VRayNodeBase):
    bl_idname = 'VRayNodeMtlOSL'
    bl_label  = 'V-Ray Mtl OSL'
    bl_icon   = 'MATERIAL'

    vray_type  : bpy.props.StringProperty(default='MATERIAL')
    vray_plugin: bpy.props.StringProperty(default='MtlOSL')

    draw_buttons = osl_node_draw_buttons
    draw_buttons_ex = vrayNodeDrawSide

    def init(self, context):
        vrayNodeInit(self, context)
        addOutput(self, 'VRaySocketMtl', "Ci")


for cls in [VRayNodeTexOSL, VRayNodeMtlOSL]:
    cls.__annotations__['script'] = bpy.props.PointerProperty(
        name = "Script",
        type = bpy.types.Text,
        description = "Internal shader script to define the shader",
    )

    cls.__annotations__['filepath'] = bpy.props.StringProperty(
        name = 'File Path',
        default = '',
        description = 'Shader script path',
        subtype = 'FILE_PATH',
    )

    cls.__annotations__['mode'] = bpy.props.EnumProperty(
        name = "Script Source",
        items = (
            ('INTERNAL', "Internal", "Use internal text data-block"),
            ('EXTERNAL', "External", "Use external .osl or .oso file"),
        ),
        default = 'INTERNAL',
    )


def getRegClasses():
    return (
        VRaySocketMtlMulti,
        VRAY_OT_node_mtlmulti_socket_add,
        VRAY_OT_node_mtlmulti_socket_del,
        VRayNodeMtlMulti,
        VRayNodeMtlOSL,
        VRayNodeTexOSL,
        VRAY_OT_osl_node_update,
   )


def register():
    class_utils.registerPluginPropertyGroup(VRayNodeMtlOSL, plugins.PLUGINS['MATERIAL']['MtlOSL'])
    class_utils.registerPluginPropertyGroup(VRayNodeTexOSL, plugins.PLUGINS['TEXTURE']['TexOSL'])
    class_utils.registerPluginPropertyGroup(VRayNodeMtlMulti, plugins.PLUGINS['MATERIAL']['MtlMulti'])

    for regClass in getRegClasses():
        bpy.utils.register_class(regClass)


def unregister():
    for regClass in getRegClasses():
        bpy.utils.unregister_class(regClass)