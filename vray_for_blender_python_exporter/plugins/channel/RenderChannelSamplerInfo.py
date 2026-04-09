# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import math

from vray_blender.exporting import node_export as commonNodesExport
from vray_blender.exporting.tools import getInputSocketByAttr
from vray_blender.lib import plugin_utils
from vray_blender.lib.defs import AttrPlugin, NodeContext, PluginDesc
from vray_blender.lib.names import Names

plugin_utils.loadPluginOnModule(globals(), __name__)

def exportTreeNode(nodeCtx: NodeContext):
    node = nodeCtx.node
    propGroup = node.RenderChannelSamplerInfo

    samplerInfoType = int(propGroup.type)

    rcPluginType = 'RenderChannelExtraTex'
    if samplerInfoType == 9: # User Float Attribute
        rcPluginType = 'RenderChannelExtraTexFloat'
    elif samplerInfoType in (8, 11): # User Integer ID, Face ID
        rcPluginType = 'RenderChannelExtraTexInt'

    renderChannelName = Names.treeNode(nodeCtx)
    rcPluginDesc = PluginDesc(renderChannelName, rcPluginType)

    rcPluginDesc.setAttribute('name', propGroup.name or 'SamplerInfo')
    rcPluginDesc.setAttribute('enableDeepOutput', propGroup.enableDeepOutput)

    if rcPluginType == 'RenderChannelExtraTex':
        rcPluginDesc.setAttribute('filtering', propGroup.filtering)
        rcPluginDesc.setAttribute('denoise', propGroup.denoise)
        rcPluginDesc.setAttribute('consider_for_aa', propGroup.consider_for_aa)
        rcPluginDesc.setAttribute('force_lossless_compression', propGroup.force_lossless_compression)

    if samplerInfoType in (0, 9): # Point, User Float Attribute
        rcPluginDesc.setAttribute('force_32_bit_output', True)

    extraTexInput = AttrPlugin()
    if samplerInfoType in (6, 7): # Occlusion
        occPluginName = Names.nextVirtualNode(nodeCtx, 'TexMotionOcclusion')
        occPluginDesc = PluginDesc(occPluginName, 'TexMotionOcclusion')

        occBias = propGroup.occlusion_bias
        occPluginDesc.setAttribute('near_threshold', occBias)
        occPluginDesc.setAttribute('far_threshold', occBias * 2.0)

        occPlugin = commonNodesExport.exportPluginWithStats(nodeCtx, occPluginDesc)

        if samplerInfoType == 6: # Backward
            extraTexInput = AttrPlugin(occPlugin.name, 'backward_occlusion')
        else: # Forward
            extraTexInput = AttrPlugin(occPlugin.name, 'forward_occlusion')
    elif samplerInfoType in (8, 9, 10): # User Attributes
        attrName = propGroup.user_attribute

        if samplerInfoType == 8: # User Integer ID
            userPluginName = Names.nextVirtualNode(nodeCtx, 'TexUserInteger')
            userPluginDesc = PluginDesc(userPluginName, 'TexUserInteger')
        elif samplerInfoType == 9: # User Float ID
            userPluginName = Names.nextVirtualNode(nodeCtx, 'TexUserScalar')
            userPluginDesc = PluginDesc(userPluginName, 'TexUserScalar')
        else: # User Color
            userPluginName = Names.nextVirtualNode(nodeCtx, 'TexUserColor')
            userPluginDesc = PluginDesc(userPluginName, 'TexUserColor')

        userPluginDesc.setAttribute('user_attribute', attrName)
        extraTexInput = commonNodesExport.exportPluginWithStats(nodeCtx, userPluginDesc)
    else: # Use TexSampler
        texSamplerPluginName = Names.nextVirtualNode(nodeCtx, 'TexSampler')
        texSamplerPluginDesc = PluginDesc(texSamplerPluginName, 'TexSampler')

        outputParam = ''
        hasCoordinates = False
        hasOutputType = False

        match samplerInfoType:
            case 0: # Point
                outputParam = 'point'
                hasCoordinates = True
            case 1: # Normal
                outputParam = 'normal'
                hasCoordinates = True
                hasOutputType = True
            case 2: # Reflection
                outputParam = 'reflection'
                hasCoordinates = True
                hasOutputType = True
            case 3: # Refraction
                outputParam = 'refraction'
                hasCoordinates = True
                hasOutputType = True
                texSamplerPluginDesc.setAttribute('refraction_ior', propGroup.refraction_ior)
            case 4: # UVW
                outputParam = 'uvCoord'
                uvwMode = int(propGroup.uv_mode)
                if uvwMode == 1: # Clamp
                    outputParam += 'Clamp'
                elif uvwMode == 2: # Tile
                    outputParam += "Tile"

                texSamplerPluginDesc.setAttribute('uv_set_name', propGroup.uv_set_name)
                texSamplerPluginDesc.setAttribute('uv_index', propGroup.uv_index)
            case 5: # Bump Normal
                outputParam = 'bumpNormal'
                hasCoordinates = True
                hasOutputType = True
            case 11: # Face ID
                outputParam = 'face_index'
                hasOutputType = True
            case 12: # Barycentric
                outputParam = 'barycentric_coords'
                hasOutputType = True
            case 13: # Tangent
                outputParam = 'tangentUObject'
                texSamplerPluginDesc.setAttribute('uv_index', propGroup.uv_index)
                hasOutputType = True
            case 14: # Bitangent
                outputParam = 'tangentVObject'
                texSamplerPluginDesc.setAttribute('uv_index', propGroup.uv_index)
                hasOutputType = True

        if hasCoordinates:
            coordSys = int(propGroup.coordinate_system)
            if coordSys == 1: # Object
                outputParam += 'Object'
            elif coordSys == 2: # Camera
                outputParam += 'Camera'
            elif coordSys == 3: # Relative
                relNodeSocket = getInputSocketByAttr(node, 'relative_node')
                relNodeTransform = commonNodesExport.exportSocket(nodeCtx, relNodeSocket)
                texSamplerPluginDesc.setAttribute('transform', relNodeTransform)
                outputParam += 'Relative'

        texSamplerPlugin = commonNodesExport.exportPluginWithStats(nodeCtx, texSamplerPluginDesc)
        extraTexInput = AttrPlugin(texSamplerPlugin.name, outputParam)

        # Output conversion (Vector -> Color)
        if hasOutputType and propGroup.output == '1':
            texVecOpPluginName = Names.nextVirtualNode(nodeCtx, 'TexVectorOp')
            texVecOpDesc = PluginDesc(texVecOpPluginName, 'TexVectorOp')
            texVecOpDesc.setAttribute('vector_a', extraTexInput)
            texVecOpDesc.setAttribute('vector_b', [0.5, 0.5, 0.5])
            texVecOpDesc.setAttribute('mult_a', 0.5)
            texVecOp = commonNodesExport.exportPluginWithStats(nodeCtx, texVecOpDesc)
            extraTexInput = AttrPlugin(texVecOp.name, 'sum')

        # Point multiplier
        if samplerInfoType == 0: # Point
            ptMult = propGroup.point_multiplier
            if not math.isclose(ptMult, 1.0, abs_tol=0.0001):
                texVecOpPluginName = Names.nextVirtualNode(nodeCtx, 'TexVectorOp')
                texVecOpDesc = PluginDesc(texVecOpPluginName, 'TexVectorOp')
                texVecOpDesc.setAttribute('vector_a', extraTexInput)
                texVecOpDesc.setAttribute('mult_a', ptMult)
                voPlugin = commonNodesExport.exportPluginWithStats(nodeCtx, texVecOpDesc)
                extraTexInput = AttrPlugin(voPlugin.name, 'result_a')

        # Non-Color types need TexVectorToColor if used with RenderChannelExtraTex
        if samplerInfoType not in (8, 9, 10, 11, 6, 7):
            if rcPluginType == 'RenderChannelExtraTex':
                vecToColorPluginName = Names.nextVirtualNode(nodeCtx, 'TexVectorToColor')
                vecToColorDesc = PluginDesc(vecToColorPluginName, 'TexVectorToColor')
                vecToColorDesc.setAttribute('input', extraTexInput)
                vecToColorPlugin = commonNodesExport.exportPluginWithStats(nodeCtx, vecToColorDesc)
                extraTexInput = AttrPlugin(vecToColorPlugin.name, 'color')

    rcPluginDesc.setAttribute('texmap', extraTexInput)

    return commonNodesExport.exportPluginWithStats(nodeCtx, rcPluginDesc)
