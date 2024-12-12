
from vray_blender.exporting import node_export as commonNodesExport
from vray_blender.exporting.tools import getInputSocketByAttr, getLinkedFromSocket, getVRayBaseSockType, isFloatSocket
from vray_blender.lib.defs import PluginDesc, NodeContext, AttrPlugin
from vray_blender.lib import export_utils, plugin_utils
from vray_blender.lib.names import Names
from vray_blender.plugins import getPluginAttr, getPluginModule

plugin_utils.loadPluginOnModule(globals(), __name__)


def exportTreeNode(nodeCtx: NodeContext):
    
    pluginName = Names.treeNode(nodeCtx)
    pluginDesc = PluginDesc(pluginName, "BRDFVRayMtl")
    pluginDesc.node = nodeCtx.node
    pluginDesc.vrayPropGroup = nodeCtx.node.BRDFVRayMtl

    skippedSockets = []

    # For the moment, we show only the opacity_color socket on the node, although the plugin
    # has also an 'opacity' socket which accepts TEXTURE_FLOAT. 
    opacitySock = getInputSocketByAttr(nodeCtx.node, 'opacity_color')
    if opacitySock.is_linked:
        pluginDesc.setAttributes({
                'opacity': AttrPlugin(),
                'opacity_color': commonNodesExport.exportLinkedSocket(nodeCtx, opacitySock),
                'opacity_source': 1 # Color opacity
            })

        skippedSockets = ['opacity', 'opacity_color', 'opacity_source']

    commonNodesExport.exportNodeTree(nodeCtx, pluginDesc, skippedSockets)
    return commonNodesExport.exportPluginWithStats(nodeCtx, pluginDesc)

def exportCustom(exporterCtx, pluginDesc: PluginDesc):

    fogMult = 1.0

    if propGroup := pluginDesc.vrayPropGroup:
        if (propGroup.fog_mult > 0.0):
            fogMult = 1.0 / propGroup.fog_mult
    else:
        attrDesc = getPluginAttr(getPluginModule(pluginDesc.type), "fog_mult")
        fogMult = attrDesc['default']

    pluginDesc.setAttribute("fog_mult", fogMult)
    return export_utils.exportPluginCommon(exporterCtx, pluginDesc)

