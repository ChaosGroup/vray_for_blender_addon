
from vray_blender.exporting import node_export as commonNodesExport
from vray_blender.exporting.tools import getInputSocketByAttr
from vray_blender.lib.defs import PluginDesc, NodeContext, AttrPlugin
from vray_blender.lib import export_utils, plugin_utils
from vray_blender.lib.names import Names
from vray_blender.nodes.utils import getNodeOfPropGroup

plugin_utils.loadPluginOnModule(globals(), __name__)


def onUseRoughnessUpdate(brdfVrayMtl, context, attrName):
    node = getNodeOfPropGroup(brdfVrayMtl)
    
    sockConfig = {
        'reflect_glossiness': ["Roughness", "Reflection Glossiness"],
        'coat_glossiness': ["Coat Roughness", "Coat Glossiness"],
        'sheen_glossiness': ["Sheen Roughness", "Sheen Glossiness"],
    }

    for sockName in sockConfig:
        sock  = getInputSocketByAttr(node, sockName)
        sock.name = sockConfig[sockName][0 if brdfVrayMtl.option_use_roughness else 1]


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

    # Register the material as emissive if self illumination is active.
    node = nodeCtx.node

    if node.BRDFVRayMtl.self_illumination != (0.0, 0.0, 0.0):
        nodeCtx.exporterCtx.emissiveMaterials.append((pluginName, 'channels', node.name))
        
    commonNodesExport.exportNodeTree(nodeCtx, pluginDesc, skippedSockets)
    return commonNodesExport.exportPluginWithStats(nodeCtx, pluginDesc)


def exportCustom(exporterCtx, pluginDesc: PluginDesc):

    if propGroup := pluginDesc.vrayPropGroup:
        fogMult = 0.0
        
        if (propGroup.fog_mult > 0.0):
            fogMult = 1.0 / propGroup.fog_mult
        
        pluginDesc.setAttribute("fog_mult", fogMult)
    else:
        # This case is valid for plugins without an underlying node, e.g. as part of
        # the default material for which we do not have a node tree. All of their properties
        # which are significant should have already been set.
        pass

    return export_utils.exportPluginCommon(exporterCtx, pluginDesc)

