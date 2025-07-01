import bpy

from vray_blender.exporting.tools import removeOutputSocketLinks, getOutputSocketByAttr
from vray_blender.lib import plugin_utils


plugin_utils.loadPluginOnModule(globals(), __name__)


class VRAY_OT_node_texsampler_sockets(bpy.types.Operator):
    bl_idname      = 'vray.node_texsampler_sockets'
    bl_label       = "Add/Remove Sampler"
    bl_description = "Adds/removes the selected sampler"
    bl_options     = {'INTERNAL'}
    
    isAdd: bpy.props.BoolProperty()

    @classmethod
    def poll(cls, context):
        return True
    
    def execute(self, context):
        node = context.node
        selectedSampler = node.TexSampler.samplers

        outSock = getOutputSocketByAttr(node, selectedSampler)
        outSock.hide = not self.isAdd
        outSock.enabled = self.isAdd

        if not self.isAdd:
            removeOutputSocketLinks(outSock)

        return {'FINISHED'}


def nodeDraw(context, layout, node):
    propGroup = node.TexSampler
    selectedSampler = node.TexSampler.samplers
    outSock = getOutputSocketByAttr(node, selectedSampler)
    
    row = layout.row()
    row.prop(propGroup, 'samplers', text="Samplers")

    if outSock.enabled:
        row.operator('vray.node_texsampler_sockets', icon="REMOVE", text="").isAdd = False
    else:
        row.operator('vray.node_texsampler_sockets', icon="ADD", text="").isAdd = True
    


def widgetDrawUVSetName(context: bpy.types.Context, layout: bpy.types.UILayout, propGroup: bpy.types.PropertyGroup, widgetAttr):
    attrName = widgetAttr['name']
    
    if obj := context.active_object:
        layout.prop_search(propGroup, 'uv_set_name', obj.data, 'uv_layers', text=widgetAttr.get('label', attrName))


def getRegClasses():
    return (
        VRAY_OT_node_texsampler_sockets,
    )


def register():
    for regClass in getRegClasses():
        bpy.utils.register_class(regClass)


def unregister():
    for regClass in getRegClasses():
        bpy.utils.unregister_class(regClass)