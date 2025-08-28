
import bpy

from vray_blender.exporting import node_export as commonNodesExport
from vray_blender.exporting.tools import getInputSocketByAttr, getLinkedFromSocket
from vray_blender.lib import export_utils, plugin_utils
from vray_blender.lib.defs import PluginDesc, NodeContext, AttrPlugin
from vray_blender.lib.names import Names
from vray_blender.nodes.tools import isVraySocket, isInputSocketLinked
from vray_blender.nodes.utils import getNodeOfPropGroup

plugin_utils.loadPluginOnModule(globals(), __name__)

# Normal map texture nodes
NORMAL_MAP_TEXTURES = ('VRayNodeTexNormalBump', 'VRayNodeTexBlendBumpNormal', 'VRayNodeTexNormalMapFlip')

def onUseRoughnessUpdate(brdfVrayMtl, context, attrName):
    node = getNodeOfPropGroup(brdfVrayMtl)

    sockConfig = {
        'reflect_glossiness': ["Roughness", "Reflection Glossiness"],
        'coat_glossiness': ["Coat Roughness", "Coat Glossiness"],
        'sheen_glossiness': ["Sheen Roughness", "Sheen Glossiness"]
    }

    isOpenPBR = brdfVrayMtl.option_shading_model=="1"
    
    for sockName in sockConfig:
        sock  = getInputSocketByAttr(node, sockName)
        sock.name = sockConfig[sockName][0 if (brdfVrayMtl.option_use_roughness or isOpenPBR) else 1]

    sock  = getInputSocketByAttr(node, 'panel_refraction')
    sockLabel = 'Transmission' if isOpenPBR else 'Refraction'
    sock.name = Names.panelSocket(sockLabel)


def onBumpTypeUpdate(brdfVrayMtl, context, attrName):
    """ Hides the 'Bump Amount' socket when the bump type is 'Explicit Normal' """

    node = getNodeOfPropGroup(brdfVrayMtl)
    bumpAmountSock = getInputSocketByAttr(node, "bump_amount")
    
    if (hideSock := brdfVrayMtl.bump_type == '6') and bumpAmountSock.is_linked:
        # Disconnect this socket if connected, otherwise it won't be hidden.
        node.id_data.links.remove(bumpAmountSock.links[0])

    bumpAmountSock.hide = hideSock


def onShadingModelUpdate(brdfVrayMtl, context, attrName):
    onUseRoughnessUpdate(brdfVrayMtl, context, attrName)

    node = getNodeOfPropGroup(brdfVrayMtl)

    sockConfig = {
        'self_illumination': ["Emission", "Self-Illumination Color"],
        'refract': ["Transmission", "Refraction Color"],
        'refract_glossiness': ["Transmission Roughness", "Refract Glossiness"],
        'refract_ior': ["Transmission IOR", "Refract IOR"],
    }

    isOpenPBR = brdfVrayMtl.option_shading_model=="1"
    for sockName in sockConfig:
        sock  = getInputSocketByAttr(node, sockName)
        sock.name = sockConfig[sockName][0 if isOpenPBR else 1]


def onTranslucencyUpdate(brdfVrayMtl, context, attrName):
    node = getNodeOfPropGroup(brdfVrayMtl)

    sockConfig = {
        'translucency_color': ["Scatter Color", "SSS Color"],
        'fog_color_colortex': ["Fog Color", "Scatter Radius"]
    }

    isSSS = brdfVrayMtl.translucency == "6"
    for sockName in sockConfig:
        sock  = getInputSocketByAttr(node, sockName)
        sock.name = sockConfig[sockName][1 if isSSS else 0]


def nodeInsertLink(link: bpy.types.NodeLink):
    global NORMAL_MAP_TEXTURES
    attrMap =   {
                    'bump_map':      'bump_type',
                    'coat_bump_map': 'coat_bump_type'
                }

    toSock = link.to_socket
    
    if not isVraySocket(toSock) or (toSock.vray_attr not in attrMap):
        return
    
    if toSock.vray_attr in attrMap:
        if (attrName := attrMap.get(toSock.vray_attr)) is None:
            return
        
        fromNode = getLinkedFromSocket(toSock).node
        toNode = toSock.node
    
        if fromNode.bl_idname in NORMAL_MAP_TEXTURES:
            # Set 'explicit' type to indicate that the connected texture is a normal map
            setattr(toNode.BRDFVRayMtl, attrName, '6')
        elif getattr(toNode.BRDFVRayMtl, attrName) == '6':
            # Set default bump type
           setattr(toNode.BRDFVRayMtl, attrName, '0')

        

def exportTreeNode(nodeCtx: NodeContext):
    pluginName = Names.treeNode(nodeCtx)
    pluginDesc = PluginDesc(pluginName, "BRDFVRayMtl")
    pluginDesc.node = nodeCtx.node
    pluginDesc.vrayPropGroup = nodeCtx.node.BRDFVRayMtl

    skippedSockets = []

    # For the moment, we show only the opacity_color socket on the node, although the plugin
    # has also an 'opacity' socket which accepts TEXTURE_FLOAT. 
    opacitySock = getInputSocketByAttr(nodeCtx.node, 'opacity_color')
    if isInputSocketLinked(opacitySock):
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

    # Always use GGX for OpenPBR.
    if node.BRDFVRayMtl.option_shading_model=="1":
        pluginDesc.setAttribute('brdf_type', 4)
        skippedSockets.append('brdf_type')

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

