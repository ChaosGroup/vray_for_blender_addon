
from vray_blender.lib import plugin_utils

plugin_utils.loadPluginOnModule(globals(), __name__)


# NOTE: Here because of the operator 'vray.proxy_load_preview'
#
def nodeDraw(context, layout, node):
    layout.prop(node.GeomMeshFile, 'file')
    layout.operator('vray.proxy_load_preview', icon='OUTLINER_OB_MESH', text="Load Preview Mesh")
