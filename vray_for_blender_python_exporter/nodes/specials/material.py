
import bpy


from vray_blender import plugins, osl
from vray_blender.exporting.update_tracker import UpdateTracker
from vray_blender.lib import draw_utils, class_utils
from vray_blender.lib.mixin import VRayNodeBase, VRayOperatorBase
from vray_blender.nodes.sockets import MATERIAL_SOCKET_COLOR, addInput, addOutput, VRayValueSocket, removeInputs
from vray_blender.nodes.operators import sockets as SocketOperators
from vray_blender.nodes.nodes import vrayNodeInit, vrayNodeDraw, vrayNodeDrawSide
from vray_blender.nodes.utils import selectedObjectTagUpdate, getActiveTreeNode
from vray_blender.ui import classes


class VRaySocketMtlMulti(VRayValueSocket):
    bl_idname = 'VRaySocketMtlMulti'
    bl_label  = 'MtlMulti Socket'

    value: bpy.props.IntProperty(
        name = "ID",
        description = "ID",
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


    def draw_property(self, context, layout, node, text):
        layout.prop(self, 'value', text="ID", slider=False, expand=False)
        layout.prop(self, 'enabled', text="Enabled")


    @classmethod
    def draw_color_simple(cls):
        return MATERIAL_SOCKET_COLOR


def _getMtlNodeFromOperatorContext(context: bpy.types.Context):
    if hasattr(context, "node"):
        return context.node
    elif context.material and context.material.node_tree:
        return getActiveTreeNode(context.material.node_tree, 'MATERIAL')

class VRAY_OT_node_mtlmulti_socket_add(VRayOperatorBase):
    bl_idname      = 'vray.node_mtlmulti_socket_add'
    bl_label       = "Add MtlMulti Socket"
    bl_description = "Adds MtlMulti sockets"
    bl_options     = {'INTERNAL'}

    def execute(self, context):
        if not (node := _getMtlNodeFromOperatorContext(context)):
            self.report({'WARNING'}, "Could not add socket to V-Ray Switch Mtl, failed to obtain the active node.")
            return {'CAMCELLED'}

        node.addMaterial()
        return {'FINISHED'}
    

class VRAY_OT_node_mtlmulti_socket_del(VRayOperatorBase):
    bl_idname      = 'vray.node_mtlmulti_socket_del'
    bl_label       = "Remove MtlMulti Socket"
    bl_description = "Removes MtlMulti socket (only not linked sockets will be removed)"
    bl_options     = {'INTERNAL'}

    def execute(self, context):
        if not (node := _getMtlNodeFromOperatorContext(context)):
            self.report({'WARNING'}, "Could not remove socket from V-Ray Switch Mtl, failed to obtain the active node.")
            return {'CAMCELLED'}

        if node.materials < 2:
            # Do not allow the user to remove the last remaining material as
            # the node would cease to be functional.
            self.report({'WARNING'}, f"{node.bl_label} needs at least one material.")
            return {'CANCELLED'}

        humanIndex = node.materials
        sockName = f"Material {humanIndex}"
        
        if removeInputs(node, [sockName], removeLinked=False):
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

    materials: bpy.props.IntProperty(default=2)

    def init(self, context):
        addInput(self, 'VRaySocketFloatNoValue', "Switch Texture", 'mtlid_gen_float', "MtlMulti")

        for i in range(self.materials):
            humanIndex = i + 1
            texSockName = f"Material {humanIndex}"
            mtlSock = addInput(self, 'VRaySocketMtlMulti', texSockName)
            mtlSock.setValue(humanIndex)
            mtlSock.enabled = True

        addOutput(self, 'VRaySocketMtl', "Material")

    def addMaterial(self):
        """ Add the inputs for a texture layer """
        humanIndex = self.materials + 1
        sockName = f"Material {humanIndex}"
        sockMtl = addInput(self, 'VRaySocketMtlMulti', sockName)
        sockMtl.setValue(humanIndex)
        sockMtl.enabled = True
        self.materials += 1 
        

    def draw_buttons(self, context, layout):
        """ Draw node """
        split = layout.split()
        col = split.column()
        col.prop(self, 'wrap_id', text="Loop Materials")

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

        for i in range(self.materials):
            humanIndex = i + 1
            sockLabel = f'Material {humanIndex}'
            uniqueID = f"{self.as_pointer()}_{sockLabel}"
            
            if panelBody := draw_utils.rollout(mtlsPanel, uniqueID, sockLabel):
                sockMtl = self.inputs[sockLabel]
                sockMtl.draw_property(context, draw_utils.subPanel(panelBody), self, text="")


class VRAY_OT_osl_node_update(VRayOperatorBase):
    bl_idname      = "vray.osl_node_update"
    bl_label       = "Update"
    bl_description = ""
    bl_options     = {'INTERNAL'}
    
    def execute(self, context):
        errs = []
        osl.update_script_node(context.node, lambda e, m: errs.append((e, m)))
        for err in errs:
            print("{'%s'}: %s" % (next(iter(err[0])), err[1]))
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
