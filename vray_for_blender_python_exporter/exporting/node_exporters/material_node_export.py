import mathutils


from vray_blender.exporting import node_export as commonNodesExport
from vray_blender.exporting.tools import *
from vray_blender import debug
from vray_blender.lib.names import Names
from vray_blender.lib.defs import *

from vray_blender.bin import VRayBlenderLib as vray

################ CONSTANTS ########################
MAX_LAYERED_BRDFS = 64 # maximum number of brdfs that could be added in BRDFLayered
####################################################


################ SPECIALIZED MTL NODE EXPORTERS ########################

def exportVRayNodeBRDFLayered(nodeCtx: NodeContext):
    
    node = nodeCtx.node
    
    brdfs = []
    weights = []
    for i in range(1, MAX_LAYERED_BRDFS + 1):
        brdfSockName = f"BRDF {i}"
        weigthSockName = f"Weight {i}"

        if not (brdfSock := getInputSocketByName(node, brdfSockName)):
            break
        
        if brdfSock.is_linked:
            brdf = commonNodesExport.exportLinkedSocket(nodeCtx, brdfSock)
            weight = None

            weightSock = getInputSocketByName(node, weigthSockName)
            if weightSock and weightSock.is_linked:
                weight = commonNodesExport.exportLinkedSocket(nodeCtx, weightSock)
            else:
                weightTexPluginName = Names.nextVirtualNode(nodeCtx, f"TexAColor_{i}")
                weigthDesc = PluginDesc(weightTexPluginName, "TexAColor")
                
                weightVal = weightSock.value if weightSock else 1.0
                weigthDesc.setAttribute("texture", mathutils.Color((weightVal, weightVal ,weightVal)))
                
                weight = commonNodesExport.exportPluginWithStats(nodeCtx, weigthDesc)

            brdfs.append(brdf)
            weights.append(weight)
    
    transparency = commonNodesExport.exportSocketByName(nodeCtx, "Transparency")
    
    pluginName = Names.treeNode(nodeCtx)
    pluginDesc = PluginDesc(pluginName, "BRDFLayered")

    pluginDesc.setAttribute("brdfs", brdfs)
    pluginDesc.setAttribute("weights", weights)
    pluginDesc.setAttribute("additive_mode", node.additive_mode)

    if transparency:
        pluginDesc.setAttribute("transparency", transparency)

    return commonNodesExport.exportPluginWithStats(nodeCtx, pluginDesc)


def exportVRayNodeBRDFBump(nodeCtx: NodeContext):
    node = nodeCtx.node

    pluginName = Names.treeNode(nodeCtx)
    pluginDesc = PluginDesc(pluginName, 'BRDFBump')
    
    commonNodesExport.exportNodeTree(nodeCtx, pluginDesc)

    sockBump = getInputSocketByName(node, "bump_tex_float")
    sockNormal = getInputSocketByName(node, "bump_tex_color")
    if sockBump and sockBump.is_linked:
        pluginDesc.removeAttribute("bump_tex_color")
    else:
        pluginDesc.removeAttribute("bump_tex_float")
        uvwgenPlg = commonNodesExport.getTextureUVWGen(nodeCtx, sockNormal)
        if not uvwgenPlg.isEmpty():
            pluginDesc.setAttribute("normal_uvwgen", uvwgenPlg)

    return commonNodesExport.exportPluginWithStats(nodeCtx, pluginDesc)


def _buildShaderScriptArgumentList(nodeCtx: NodeContext, script):
    """ build AttrListValue from parameters of OSL node """
    oslParamList = vray.getOslScriptParameters(script)
    
    scriptArgumentList = AttrListValue()
    for param in oslParamList:

        if not param.isOutputSocket:
            sock = nodeCtx.node.inputs[param.name]
            value = commonNodesExport.exportSocket(nodeCtx, sock)
            scriptArgumentList.append(param.name)
            
            if type(value) is mathutils.Vector:
                value = [value.x, value.y, value.z]
            elif type(value) is mathutils.Color:
                value = [value.r, value.g, value.b]

            scriptArgumentList.append(value)
 
    return scriptArgumentList


def exportVRayNodeShaderScript(nodeCtx: NodeContext):
    node = nodeCtx.node
    
    toSocket = node.outputs["Ci"]
    if not toSocket:
        debug.printError("Invalid output socket in exportVRayNodeShaderScript when parsing OSL material or texture")
        
        return AttrPlugin()

    outputClosure = toSocket.name
    pluginType = "MtlOSL" if node.bl_idname == "VRayNodeMtlOSL" else "TexOSL"
    pluginName = Names.treeNode(nodeCtx)
    pluginDesc = PluginDesc(pluginName, pluginType)

    scriptPath = ""
    if node.mode == "INTERNAL":
        scriptPath = saveShaderScript(node.script)
        
    scriptPath = bpy.path.abspath(node.filepath)

    pluginDesc.setAttribute("input_parameters", _buildShaderScriptArgumentList(nodeCtx, scriptPath))
    pluginDesc.setAttribute("shader_file", scriptPath)

    if node.bl_idname == "VRayNodeMtlOSL":
        pluginDesc.setAttribute("output_closure", outputClosure)
    else:
        pluginDesc.setAttribute("output_color", outputClosure)
                
    commonNodesExport.exportNodeTree(nodeCtx, pluginDesc)
    return commonNodesExport.exportPluginWithStats(nodeCtx, pluginDesc)


def exportVRayNodeMtlMulti(nodeCtx: NodeContext):
    pluginName = Names.treeNode(nodeCtx)
    pluginDesc = PluginDesc(pluginName, "MtlMulti")

    node = nodeCtx.node
    texSock = node.inputs['Switch Texture']
    if texSock.is_linked:
        switchID = commonNodesExport.exportLinkedSocket(nodeCtx, texSock)
    else:
        switchID = node.MtlMulti.switch_id

    mtlSockets = [s for s in node.inputs if s.bl_idname == 'VRaySocketMtlMulti' and s.enabled and s.is_linked]
    mtlIDs = []
    linkedMtls = []
    
    for sock in mtlSockets:
        brdfPlugin = commonNodesExport.exportLinkedSocket(nodeCtx, sock)
        mtlPlugin = exportMtlSingleBrdf(nodeCtx, brdfPlugin)
        linkedMtls.append(mtlPlugin)
        mtlIDs.append(sock.value)

    pluginDesc.setAttributes({
        "ids_list": mtlIDs,
        "mtls_list": linkedMtls,
        "mtlid_gen_float" : switchID
    }) 

    pluginDesc.vrayPropGroup = getattr(nodeCtx.node, node.vray_plugin)
    return commonNodesExport.exportPluginWithStats(nodeCtx, pluginDesc)


def exportMtlSingleBrdf(nodeCtx: NodeContext, brdfPlugin: AttrPlugin):
    """ Export a MtlSingleBRDF plugin for a brdf plugin """
    pluginType = 'MtlSingleBRDF'
    pluginName = Names.nextVirtualNode(nodeCtx, pluginType)
    plDesc = PluginDesc(pluginName, pluginType)
    
    plDesc.setAttributes({
        'brdf'                    : brdfPlugin,
        'scene_name'              : [pluginName],
        'allow_negative_colors'   : True
        })

    return commonNodesExport.exportPluginWithStats(nodeCtx, plDesc)
 