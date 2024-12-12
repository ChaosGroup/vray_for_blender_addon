
import bpy

from vray_blender.lib.names import Names
from vray_blender.lib.defs import NodeContext, PluginDesc
from vray_blender.exporting import node_export as commonNodesExport

from vray_blender.lib import plugin_utils
plugin_utils.loadPluginOnModule(globals(), __name__)


def nodeDraw(context, layout, node: bpy.types.Node):
    layout.prop_search(node.SphereFadeGizmo,   'object',
                       bpy.context.scene, 'objects',
                       text="")
    layout.prop(node.SphereFadeGizmo, 'radius')
    layout.prop(node.SphereFadeGizmo, 'invert')



def exportTreeNode(nodeCtx: NodeContext):
    node = nodeCtx.node
    pluginName = Names.treeNode(nodeCtx)
    pluginDesc = PluginDesc(pluginName, "SphereFadeGizmo")

    # Gets the name of selected object
    pluginDesc.vrayPropGroup = getattr(node, "SphereFadeGizmo")
    ob = nodeCtx.scene.objects.get(node.SphereFadeGizmo.object)
    
    # If there is selected object use its transformation
    if ob: 
        pluginDesc.setAttribute("transform", ob.matrix_world)
    else:
        commonNodesExport.exportNodeTree(nodeCtx, pluginDesc)

    return commonNodesExport.exportPluginWithStats(nodeCtx, pluginDesc)