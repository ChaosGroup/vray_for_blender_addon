
from vray_blender.lib import plugin_utils
plugin_utils.loadPluginOnModule(globals(), __name__)

def nodeDraw(context, layout, node):
    propGroup = node.SphereFade
    split = layout.split()
    col = split.column()
    col.prop(propGroup, 'empty_color', text="")
    col.prop(propGroup, 'falloff')
    col.prop(propGroup, 'affect_alpha')

    layout.separator()

    split = layout.split()
    row = split.row(align=True)

    addOp = row.operator('vray.node_list_socket_add', icon="ADD", text="Add")
    addOp.socketType = 'VRaySocketObject'
    addOp.socketName = 'Gizmo'
    addOp.vray_attr  = "gizmos"

    row.operator('vray.node_list_socket_del', icon="REMOVE", text="")
