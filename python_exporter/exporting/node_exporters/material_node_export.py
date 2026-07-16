# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import mathutils


from vray_blender import debug
from vray_blender.exporting import node_export as commonNodesExport
from vray_blender.exporting.tools import *
from vray_blender.lib.defs import *
from vray_blender.lib.names import Names

from vray_blender.bin import VRayBlenderLib as vray

def exportVRayNodeBRDFBump(nodeCtx: NodeContext):
    node = nodeCtx.node

    pluginName = Names.treeNode(nodeCtx)
    pluginDesc = PluginDesc(pluginName, 'BRDFBump')
    
    commonNodesExport.exportNodeTree(nodeCtx, pluginDesc)

    sockBump = getInputSocketByName(node, "bump_tex_float")
    sockNormal = getInputSocketByName(node, "bump_tex_color")
    if sockBump and sockBump.hasActiveFarLink():
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

    if node.mode == "INTERNAL":
        scriptPath = saveShaderScript(node.script)
    else:
        scriptPath = bpy.path.abspath(node.filepath)

    pluginDesc.setAttribute("input_parameters", _buildShaderScriptArgumentList(nodeCtx, scriptPath))
    pluginDesc.setAttribute("shader_file", scriptPath)

    if node.bl_idname == "VRayNodeMtlOSL":
        pluginDesc.setAttribute("output_closure", outputClosure)
    else:
        pluginDesc.setAttribute("output_color", outputClosure)
                
    commonNodesExport.exportNodeTree(nodeCtx, pluginDesc)
    return commonNodesExport.exportPluginWithStats(nodeCtx, pluginDesc)

