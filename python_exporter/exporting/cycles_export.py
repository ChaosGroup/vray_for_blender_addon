# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

from vray_blender.bin import VRayBlenderLib as vray
from vray_blender.exporting.mtl_export import MtlExporter
from vray_blender.exporting.node_export import exportLinkedSocket
from vray_blender.exporting.node_exporters.uvw_node_export import exportDefaultUVWGenChannel
from vray_blender.exporting.tools import FarNodeLink, getFarNodeLink, resolveNodeSocket, _getActiveNearLinks, socketHasActiveNearLinks
from vray_blender.lib.attribute_utils import toColor
from vray_blender.lib.defs import AColor, AttrPlugin, NodeContext, PluginDesc
from vray_blender.lib.export_utils import wrapAsTexture
from vray_blender.lib.names import Names
from vray_blender.lib.plugin_utils import updateValue, createPlugin
from vray_blender.lib import image_utils
from vray_blender.nodes.curves_node import fillSplineData
from mathutils import Color, Euler, Matrix, Vector

import bpy, math
import numpy as np

# There are a few things to keep in mind when adding support for a new node.
# 1. Add the node type to the list of supported nodes in attribute_types.py, add
# the node to the switch in node_export.py and add the corresponding export here.
# 2. Match V-Ray and Cycles output types i.e. float output socket->V-Ray
# plugin with float output, cycles Vectors should be exported as colors/uvwgens.
# 3. Avoid using any export functions which aren't currently used in here since they
# often depend on plugin descriptions/attributes and others V-Ray only things. Mainly
# note that you should always use _exportCyclesPluginWithStats(...) instead of
# exportPluginWithStats(...).

INVALID_COLOR = Color((1.0, 0.0, 1.0))
WHITE_COLOR = Color((1.0, 1.0, 1.0))
BLACK_COLOR = Color((0.0, 0.0, 0.0))

def _exportCyclesLinkedSocket(nodeCtx: NodeContext, socket: bpy.types.NodeSocket):
    value = exportLinkedSocket(nodeCtx, socket)
    if value is not None:
        return value
    return AttrPlugin()

def _exportCyclesPluginWithStats(nodeCtx: NodeContext, pluginDesc: PluginDesc, allowTypeChanges = False) -> AttrPlugin:
    # Use a custom plugin export function(not explort)
    nodeCtx.nodeTracker.trackPlugin(pluginDesc.name)

    nodeCtx.stats.uniquePlugins.add(pluginDesc.name)
    nodeCtx.stats.plugins += 1
    nodeCtx.stats.attrs += len(pluginDesc.attrs)

    if nodeCtx.customHandler:
        return nodeCtx.customHandler(nodeCtx, pluginDesc)

    # This path writes the attributes itself instead of going through export_utils, so it has to
    # report them itself too - counted from the loop rather than from len(attrs), so the number is
    # what was actually written.
    createPlugin(nodeCtx.exporterCtx, pluginDesc.name, pluginDesc.type, allowTypeChanges)

    exportedAttrs = 0
    for name, value in pluginDesc.attrs.items():
        updateValue(nodeCtx.exporterCtx.renderer, pluginDesc.name, name, value)
        exportedAttrs += 1

    nodeCtx.exporterCtx.sceneStats.addAttrs(exportedAttrs)

    return AttrPlugin(pluginDesc.name, forceUpdate=allowTypeChanges, pluginType=pluginDesc.type)

from enum import Enum
class SocketValueType(Enum):
    Float = "float"
    Color = "color"

def _getResolvedSocketValue(socket: bpy.types.NodeSocket, type: SocketValueType) -> Color | float:
    """ Returns the (constant)value of an already resolved socket and does any necessary conversions.
        When going through group noodes it's entirely possible to have a mismatch in the socket types
        while the parameter is still a constant value e.g. float<->color so here we need to manually
        check if a conversion is necessary.
    """
    value = socket.default_value
    if type == SocketValueType.Float:
        if socket.type in ('VALUE', 'INT', 'BOOLEAN'):
            return float(value)
        elif socket.type == 'RGBA':
            return value[0] * 0.2126 + value[1] * 0.7152 + value[2] * 0.0722
        elif socket.type == 'VECTOR' or socket.type == 'ROTATION':
            return (value[0] + value[1] + value[2]) / 3.0
        else:
            assert False, "Unsupported socket type"
    elif type == SocketValueType.Color:
        if socket.type == 'RGBA' or socket.type == 'ROTATION' or socket.type == "VECTOR":
            return toColor(value)
        elif socket.type in ('VALUE', 'INT', 'BOOLEAN'):
            return Color((float(value), float(value), float(value)))
        else:
            assert False, "Unsupported socket type"
    return toColor(value)


def _getSocketValue(socket: bpy.types.NodeSocket, type: SocketValueType) -> Color | float:
   
    if socket.is_output or not socketHasActiveNearLinks(socket):
        return _getResolvedSocketValue(socket, type)
    
    if resolvedSocket := resolveNodeSocket(socket):
        return _getResolvedSocketValue(resolvedSocket, type)
    
    return _getResolvedSocketValue(socket, type)


def _isSocketConnected(socket: bpy.types.NodeSocket):
    # The check the two basic cases - if we have an unmuted connection. Then check if the connection
    # is meaningful i.e. going to an actual node not just re-routed to a node group.
    if not socketHasActiveNearLinks(socket):
        return False
    
    resolvedSocket = resolveNodeSocket(socket)
    if not resolvedSocket or not socketHasActiveNearLinks(resolvedSocket):
        return False
    return True


def _wrapClampPlugin(nodeCtx: NodeContext, input: AttrPlugin, min: float | AttrPlugin, max: float | AttrPlugin):
    # Currently we have no way to clamp floats so wrap the plugin similar to the
    # way the _clamp(...) function below works i.e. max(min(value, max_value), min_value).
    minTexName = Names.nextVirtualNode(nodeCtx, "TexFloatOp")
    minTexDesc = PluginDesc(minTexName, "TexFloatOp")
    minTexDesc.setAttribute("float_a", input)
    minTexDesc.setAttribute("float_b", max)
    minTexDesc.setAttribute("mode", 7) # min
    minTex = _exportCyclesPluginWithStats(nodeCtx, minTexDesc)

    maxTexName = Names.nextVirtualNode(nodeCtx, "TexFloatOp")
    maxTexDesc = PluginDesc(maxTexName, "TexFloatOp")
    maxTexDesc.setAttribute("float_a", minTex)
    maxTexDesc.setAttribute("float_b", min)
    maxTexDesc.setAttribute("mode", 8) # max
    return _exportCyclesPluginWithStats(nodeCtx, maxTexDesc)

def _wrapClampPlugin01(nodeCtx: NodeContext, input: float | AttrPlugin):
    if isinstance(input, float):
        return _clamp01(input)
    else:
        return _wrapClampPlugin(nodeCtx, input, 0.0, 1.0)

def _wrapFloatToColor(nodeCtx: NodeContext, input: AttrPlugin):
    floatToColorName = Names.nextVirtualNode(nodeCtx, "TexFloatToColor")
    floatToColorDesc = PluginDesc(floatToColorName, "TexFloatToColor")
    floatToColorDesc.setAttribute("input", input)
    return _exportCyclesPluginWithStats(nodeCtx, floatToColorDesc)

def _clamp(val, minv, maxv):
    return max(min(val, maxv), minv)

def _clamp01(val):
    return _clamp(val, 0.0, 1.0)

def _exportCyclesColorAttribute(nodeCtx: NodeContext, pluginDesc: PluginDesc, attr: str|bpy.types.NodeSocketFloat, *plgParamNames: str):
    attribute = nodeCtx.node.inputs[attr] if isinstance(attr, str) else attr
    if _isSocketConnected(attribute):
        if sockValue := _exportCyclesLinkedSocket(nodeCtx, attribute):
            for param in plgParamNames:
                pluginDesc.setAttribute(param, sockValue)
    else:
        for param in plgParamNames:
            pluginDesc.setAttribute(param, _getSocketValue(attribute, SocketValueType.Color))

def _exportCyclesFloatAttribute(nodeCtx: NodeContext, pluginDesc: PluginDesc, attr: str|bpy.types.NodeSocketFloat, *plgParamNames: str):
    attribute = nodeCtx.node.inputs[attr] if isinstance(attr, str) else attr
    if _isSocketConnected(attribute):
        if sockValue := _exportCyclesLinkedSocket(nodeCtx, attribute):
            for param in plgParamNames:
                pluginDesc.setAttribute(param, sockValue)
    else:
        for param in plgParamNames:
            pluginDesc.setAttribute(param, _getSocketValue(attribute, SocketValueType.Float))

def _exportCyclesMixedAttribute(nodeCtx: NodeContext, pluginDesc: PluginDesc, colorAttrName: str, strengthAttrName: str, plgParamName: str):
    strengthSocket = nodeCtx.node.inputs[strengthAttrName]
    # If strength is zero just export black color.
    if _isSocketZero(strengthSocket):
        pluginDesc.setAttribute(plgParamName, BLACK_COLOR)
        return
    # If strength is 1.0 then just export the color parameter without a mix plugin.
    if not _isSocketConnected(strengthSocket):
        if math.isclose(_getSocketValue(strengthSocket, SocketValueType.Float), 1.0):
            _exportCyclesColorAttribute(nodeCtx, pluginDesc, colorAttrName, plgParamName)
            return
        else:
            colorSocket = nodeCtx.node.inputs[colorAttrName]
            if not _isSocketConnected(colorSocket):
                value = _getSocketValue(colorSocket, SocketValueType.Color) * _getSocketValue(strengthSocket, SocketValueType.Float)
                pluginDesc.setAttribute(plgParamName, value)
                return

    pluginName = Names.nextVirtualNode(nodeCtx, "TexAColorOp")
    texAColorOpDesc = PluginDesc(pluginName, "TexAColorOp")

    _exportCyclesColorAttribute(nodeCtx, texAColorOpDesc, colorAttrName, "color_a")
    _exportCyclesFloatAttribute(nodeCtx, texAColorOpDesc, strengthAttrName, "mult_a")

    texAColorOp = _exportCyclesPluginWithStats(nodeCtx, texAColorOpDesc)
    texAColorOp.output = "result_a"
    pluginDesc.setAttribute(plgParamName, texAColorOp)

def _cyclesSpecularFresnelIOR(ior: float, level: float):
    # The IOR Cycles uses for the dielectric specular Fresnel term once "Specular IOR Level" has
    # rescaled F0 around its 0.5 neutral point (svm/closure.h "Apply IOR adjustment").
    if math.isclose(level, 0.5):
        return ior
    f0 = ((ior - 1.0) / (ior + 1.0)) ** 2 * 2.0 * level
    sqrtF0 = math.sqrt(_clamp(f0, 0.0, 0.99)) # Cycles clamps F0 in ior_from_F0
    eta = (1.0 + sqrtF0) / (1.0 - sqrtF0)
    return (1.0 / eta) if ior < 1.0 else eta

def _cyclesSpecularFresnelIORTex(nodeCtx: NodeContext, ior: float, level: AttrPlugin):
    # _cyclesSpecularFresnelIOR as a texture graph, for a linked "Specular IOR Level":
    # eta = (1 + s) / (1 - s) with s = sqrt(clamp(F0(ior) * 2 * level, 0, 0.99)).
    def floatOp(mode: int, a, b):
        desc = PluginDesc(Names.nextVirtualNode(nodeCtx, "TexFloatOp"), "TexFloatOp")
        desc.setAttribute("float_a", a)
        desc.setAttribute("float_b", b)
        desc.setAttribute("mode", mode)
        return _exportCyclesPluginWithStats(nodeCtx, desc)

    f0 = floatOp(0, level, ((ior - 1.0) / (ior + 1.0)) ** 2 * 2.0) # product
    s = floatOp(4, _wrapClampPlugin(nodeCtx, f0, 0.0, 0.99), 0.5)  # power, i.e. sqrt
    num = floatOp(2, s, 1.0)                                       # sum
    den = floatOp(3, 1.0, s)                                       # difference
    # ior < 1 means Cycles takes the reciprocal of eta, which is just the inverted ratio.
    return floatOp(1, num, den) if ior >= 1.0 else floatOp(1, den, num)

def _exportCyclesSpecular(nodeCtx: NodeContext, mtlDesc: PluginDesc):
    # Cycles treats "Specular IOR Level" 0.5 as neutral: it scales the dielectric F0 by 2*level and
    # re-derives the Fresnel IOR from the result, so reflect = tint * level would render at half the
    # reflectance. With a constant IOR and level, reproduce the adjustment on fresnel_ior exactly as
    # Cycles does - that matches the whole Fresnel curve and does not saturate above level 0.5.
    levelSocket = nodeCtx.node.inputs["Specular IOR Level"]
    iorSocket = nodeCtx.node.inputs["IOR"]

    # Write both on every path, else a relinked socket leaves a stale fresnel_ior plugin reference.
    mtlDesc.setAttribute("fresnel_ior_lock", True)
    if (refractIOR := mtlDesc.getAttribute("refract_ior")) is not None:
        mtlDesc.setAttribute("fresnel_ior", refractIOR)

    if _isSocketZero(levelSocket):
        mtlDesc.setAttribute("reflect", BLACK_COLOR)
        return

    if not _isSocketConnected(iorSocket):
        ior = _getSocketValue(iorSocket, SocketValueType.Float)
        _exportCyclesColorAttribute(nodeCtx, mtlDesc, "Specular Tint", "reflect")
        if _isSocketConnected(levelSocket):
            level = _exportCyclesLinkedSocket(nodeCtx, levelSocket)
            fresnelIOR = _cyclesSpecularFresnelIORTex(nodeCtx, ior, level)
        else:
            fresnelIOR = _cyclesSpecularFresnelIOR(ior, _getSocketValue(levelSocket, SocketValueType.Float))
        mtlDesc.setAttribute("fresnel_ior", fresnelIOR)
        mtlDesc.setAttribute("fresnel_ior_lock", False)
        return

    # Textured IOR: F0 is no longer a constant, so keep the Fresnel term on the material IOR and
    # carry the 2x on the reflection colour. Exact at level 0.5; the core clamps reflect above it.
    _exportCyclesMixedAttribute(nodeCtx, mtlDesc, "Specular Tint", "Specular IOR Level", "reflect")
    reflect = mtlDesc.getAttribute("reflect")
    if isinstance(reflect, AttrPlugin):
        texAColorOpDesc = PluginDesc(Names.nextVirtualNode(nodeCtx, "TexAColorOp"), "TexAColorOp")
        texAColorOpDesc.setAttribute("color_a", reflect)
        texAColorOpDesc.setAttribute("mult_a", 2.0)
        texAColorOp = _exportCyclesPluginWithStats(nodeCtx, texAColorOpDesc)
        texAColorOp.output = "result_a"
        mtlDesc.setAttribute("reflect", texAColorOp)
    else:
        mtlDesc.setAttribute("reflect", reflect * 2.0)

def _exportCyclesWrappedAttribute(nodeCtx: NodeContext, attributeName: str):
    # Export a color and optionally wraps it in a TexAColor if necessary.
    colorAttr = nodeCtx.node.inputs[attributeName]
    if _isSocketConnected(colorAttr):
        return _exportCyclesLinkedSocket(nodeCtx, colorAttr)
    else:
        return wrapAsTexture(nodeCtx, _getSocketValue(colorAttr, SocketValueType.Color))

def _exportCyclesVectorAttribute(nodeCtx: NodeContext, pluginDesc: PluginDesc, vectorAttrName: str, additionalMatrix: Matrix = Matrix()):
    vectorSocket = nodeCtx.node.inputs[vectorAttrName]
    if _isSocketConnected(vectorSocket):
        uvwgen = exportLinkedSocket(nodeCtx, vectorSocket)
        # Blender uses Vector type for UVWGen so it's possible to sometimes end up here with a color as UVWGen.
        if uvwgen is not None and (not isinstance(uvwgen, AttrPlugin) or 'UVWGen' not in uvwgen.pluginType):
            # Wrap plugins that are not UVW generators
            uvwgenExplicitName = Names.nextVirtualNode(nodeCtx, "UVWGenExplicit")
            uvwgenExplicitDesc = PluginDesc(uvwgenExplicitName, "UVWGenExplicit")
            uvwgenExplicitDesc.setAttribute("uvw", uvwgen)
            uvwgen = _exportCyclesPluginWithStats(nodeCtx, uvwgenExplicitDesc)
        pluginDesc.setAttribute("uvwgen", uvwgen)
        return

    if len(nodeCtx.transformStack) == 0:
        uvwgen = exportDefaultUVWGenChannel(nodeCtx)
    else:
        uvwChannelName = Names.nextVirtualNode(nodeCtx, "UVWGenChannel")
        uvwChanneDesc = PluginDesc(uvwChannelName, "UVWGenChannel")
        uvwChanneDesc.setAttribute("uvw_channel", -1)
        uvwChanneDesc.setAttribute("tex_transform", nodeCtx.getUVWTransform())
        uvwgen =_exportCyclesPluginWithStats(nodeCtx, uvwChanneDesc)

    pluginDesc.setAttribute("uvwgen", uvwgen)

def _wrapVectorToColor(nodeCtx: NodeContext, inputPlugin: AttrPlugin):
    texVectorToColorName = Names.nextVirtualNode(nodeCtx, "TexVectorToColor")
    texVectorToColorDesc = PluginDesc(texVectorToColorName, "TexVectorToColor")
    texVectorToColorDesc.setAttribute("input", inputPlugin)
    return _exportCyclesPluginWithStats(nodeCtx, texVectorToColorDesc)

def _exportIntensityOutput(nodeCtx: NodeContext, plugin: AttrPlugin):
    texColorToFloatName = Names.nextVirtualNode(nodeCtx, "TexColorToFloat")
    texColorToFloatDesc = PluginDesc(texColorToFloatName, "TexColorToFloat")
    texColorToFloatDesc.setAttribute("input", plugin)
    return _exportCyclesPluginWithStats(nodeCtx, texColorToFloatDesc)

def _outputScopedName(nodeCtx: NodeContext, output: str):
    """ Name for a plugin whose type or attributes differ per output socket. A Cycles node is
        exported once per used output, so a shared name lets the second pass overwrite the first.
    """
    return f"{Names.treeNode(nodeCtx)}_{output}"

def _convertBlenderHSVToVRay(nodeCtx: NodeContext, hueSocket: bpy.types.NodeSocket) -> float | AttrPlugin:
    # V-Ray expects [0-360] hue, blender uses values in the range [0-1] so we multiply by 360
    if _isSocketConnected(hueSocket):
        texFloatOpName = Names.nextVirtualNode(nodeCtx, "TexFloatOp")
        texFloatOpDesc = PluginDesc(texFloatOpName, "TexFloatOp")
        _exportCyclesFloatAttribute(nodeCtx, texFloatOpDesc, hueSocket, "float_a")
        texFloatOpDesc.setAttribute("float_b", 360.0)
        texFloatOp = _exportCyclesPluginWithStats(nodeCtx, texFloatOpDesc)
        texFloatOp.output = "product"
        return texFloatOp
    else:
        return _getSocketValue(hueSocket, SocketValueType.Float) * 360.0

def _wrapColorFactorSocket(nodeCtx: NodeContext, factorSocket: bpy.types.NodeSocket, color1: AttrPlugin, color2: AttrPlugin):
    # Export a TexMix plugin that will blend color1 and color2 based on the value of factorSocket.
    if not _isSocketConnected(factorSocket):
        if math.isclose(_getSocketValue(factorSocket, SocketValueType.Float), 0.0):
            return color1
        if math.isclose(_getSocketValue(factorSocket, SocketValueType.Float), 1.0):
            return color2
    texMixName = Names.nextVirtualNode(nodeCtx, "TexMix")
    texMixDesc = PluginDesc(texMixName, "TexMix")
    texMixDesc.setAttribute("color1", color1)
    texMixDesc.setAttribute("color2", color2)
    if _isSocketConnected(factorSocket):
        factor = _exportCyclesLinkedSocket(nodeCtx, factorSocket)
        factor = _wrapFloatToColor(nodeCtx, factor)
        texMixDesc.setAttribute("mix_map", factor)
    else:
        texMixDesc.setAttribute("mix_map", _getSocketValue(factorSocket, SocketValueType.Color))
    return _exportCyclesPluginWithStats(nodeCtx, texMixDesc)

def _exportUVWToColor(nodeCtx: NodeContext, input: AttrPlugin):
    texUVWName = Names.nextVirtualNode(nodeCtx, "TexUVW")
    texUVWDesc = PluginDesc(texUVWName, "TexUVW")
    texUVWDesc.setAttribute("uvwgen", input)
    return _exportCyclesPluginWithStats(nodeCtx, texUVWDesc)

def _isSocketZero(socket):
    return not _isSocketNonZero(socket)

def _isSocketNonZero(socket):
    return _isSocketConnected(socket) or not math.isclose(_getSocketValue(socket, SocketValueType.Float), 0.0)

def exportCyclesNode(nodeCtx: NodeContext, nodeLink: bpy.types.NodeLink):
    match nodeCtx.node.bl_idname:
        case "ShaderNodeBsdfPrincipled":
            return _exportCyclesBsdfPrincipled(nodeCtx)
        case "ShaderNodeBsdfDiffuse":
            return _exportCyclesDiffuseBsdf(nodeCtx)
        case "ShaderNodeEmission":
            return _exportCyclesEmissionBsdf(nodeCtx)
        case "ShaderNodeBsdfRefraction":
            return _exportCyclesRefractiveBsdf(nodeCtx, isGlass=False)
        case "ShaderNodeBsdfGlass":
            return _exportCyclesRefractiveBsdf(nodeCtx, isGlass=True)
        case "ShaderNodeBsdfSheen":
            return _exportCyclesSheenBsdf(nodeCtx)
        case "ShaderNodeBsdfAnisotropic":
            return _exportCyclesGlossyBsdf(nodeCtx)
        case "ShaderNodeBsdfTranslucent":
            return _exportCyclesTranslucentBsdf(nodeCtx)
        case "ShaderNodeBsdfTransparent":
            return _exportCyclesTransparentBsdf(nodeCtx)
        case "ShaderNodeLightPath":
            return _exportCyclesLightPathNode(nodeCtx, nodeLink)
        case "ShaderNodeAddShader":
            return _exportCyclesBlendShaderNode(nodeCtx, isMix=False)
        case "ShaderNodeMixShader":
            return _exportCyclesBlendShaderNode(nodeCtx, isMix=True)
        case "ShaderNodeTexChecker":
            return _exportCyclesCheckerTexture(nodeCtx, nodeLink)
        case "ShaderNodeNormalMap":
            return _exportCyclesNormalMap(nodeCtx)
        case "ShaderNodeMath":
            return _exportCyclesMathNode(nodeCtx)
        case "ShaderNodeVectorMath":
            return _exportCyclesVectorMathNode(nodeCtx)
        case "ShaderNodeMapRange":
            return _exportCyclesMapRangeNode(nodeCtx)
        case "ShaderNodeFloatCurve":
            return _exportCyclesFloatCurveNode(nodeCtx)
        case "ShaderNodeRGB":
            return _exportCyclesRGBNode(nodeCtx)
        case "ShaderNodeInvert":
            return _exportCyclesInvertNode(nodeCtx)
        case "ShaderNodeRGBToBW":
            return _exportCyclesRGBToBwNode(nodeCtx)
        case "ShaderNodeTexImage":
            return _exportCyclesImageNode(nodeCtx, nodeLink, isEnvironment=False)
        case "ShaderNodeTexEnvironment":
            return _exportCyclesImageNode(nodeCtx, nodeLink, isEnvironment=True)
        case "ShaderNodeUVMap":
            return _exportCyclesUVWMapNode(nodeCtx)
        case "ShaderNodeCombineColor":
            return _exportCyclesCombineColorNode(nodeCtx)
        case "ShaderNodeCombineXYZ":
            return _exportCyclesCombineVectorNode(nodeCtx)
        case "ShaderNodeValToRGB":
            return _exportCyclesRampNode(nodeCtx, nodeLink)
        case "ShaderNodeTexGradient":
            return _exportCyclesGradientNode(nodeCtx, nodeLink)
        case "ShaderNodeBlackbody":
            return _exportCyclesBlackbodyNode(nodeCtx)
        case "ShaderNodeWireframe":
            return _exportCyclesWireframeNode(nodeCtx)
        case "ShaderNodeRGBCurve":
            return _exportCyclesCurvesNode(nodeCtx, isColor=True)
        case "ShaderNodeVectorCurve":
            return _exportCyclesCurvesNode(nodeCtx, isColor=False)
        case "ShaderNodeValue":
            return _exportCyclesValueNode(nodeCtx)
        case "ShaderNodeTexCoord":
            return _exportCyclesTextureCoordinatesNode(nodeCtx, nodeLink)
        case "ShaderNodeMapping":
            return _exportCyclesMappingNode(nodeCtx)
        case "ShaderNodeAttribute":
            return _exportCyclesAttributeNode(nodeCtx, nodeLink)
        case "ShaderNodeVertexColor":
            return _exportCyclesColorAttributeNode(nodeCtx, nodeLink)
        case "ShaderNodeNormal":
            return _exportCyclesNormalNode(nodeCtx, nodeLink)
        case "ShaderNodeCameraData":
            return _exportCyclesCameraDataNode(nodeCtx, nodeLink)
        case "ShaderNodeBevel":
            return _exportCyclesBevelNode(nodeCtx)
        case "ShaderNodeAmbientOcclusion":
            return _exportCyclesAmbientOcclusionNode(nodeCtx, nodeLink)
        case "ShaderNodeBump":
            return _exportCyclesBumpNode(nodeCtx)
        case "ShaderNodeObjectInfo":
            return _exportCyclesObjectInfoNode(nodeCtx, nodeLink)
        case "ShaderNodeNewGeometry":
            return _exportCyclesGeometryNode(nodeCtx, nodeLink)
        case "ShaderNodeSeparateColor":
            return _exportCyclesSeperateColorNode(nodeCtx, nodeLink)
        case "ShaderNodeSeparateXYZ":
            return _exportCyclesSeperateXYZNode(nodeCtx, nodeLink)
        case "ShaderNodeFresnel":
            return _exportCyclesFresnelNode(nodeCtx)
        case "ShaderNodeLayerWeight":
            return _exportCyclesLayerWeightNode(nodeCtx, nodeLink)
        case "ShaderNodeMix":
            return _exportCyclesMixNode(nodeCtx)
        case "ShaderNodeMixRGB":
            return _exportCyclesColorMixNode(nodeCtx, isColorMix=True, isLegacy=True)
        case "ShaderNodeClamp":
            return _exportCyclesClampNode(nodeCtx)
        case "ShaderNodeGamma":
            return _exportCyclesGammaNode(nodeCtx)
        case "ShaderNodeHueSaturation":
            return _exportCyclesHSVNode(nodeCtx)
        case "ShaderNodeBrightContrast":
            return _exportCyclesBrightnessNode(nodeCtx)
        case "ShaderNodeTexNoise":
            return _exportCyclesNoiseNode(nodeCtx, nodeLink)
        case "ShaderNodeTexBrick":
            return _exportCyclesFlatProcedural(nodeCtx, nodeLink, "Brick Texture", ("Color1", "Color2"))
        case "ShaderNodeTexWave":
            return _exportCyclesFlatProcedural(nodeCtx, nodeLink, "Wave Texture", ())
        case "ShaderNodeTexSky":
            return _exportCyclesSkyTexture(nodeCtx)
        case "ShaderNodeBsdfHairPrincipled":
            return _exportCyclesHairPrincipled(nodeCtx)
        case "ShaderNodeHairInfo":
            return _exportCyclesHairInfoNode(nodeCtx, nodeLink)

# --- Cycles Principled subsurface/transmission helpers -----------------------
# V-Ray random-walk SSS effective mean free path (cgrepo mtlbrdf.h
# initVolumetricTranslucency + randomwalkvolume.h:655): scatterRadius =
# fog_color_tex * fog_depth * scatterRadius_const, with
# scatterRadius_const = 1/max(0.01*metersScale, fog_mult*metersScale*100).
# We pin scatterRadius_const to 1 via fog_mult, put the normalized radius ratio in
# fog_color_tex (dodges the core's [0,1] fog-color clamp), and carry the magnitude in
# fog_depth. _SSS_MFP_SCALE mirrors Cycles' 0.25/pi radius rescale but also absorbs
# V-Ray's (0.574893+1.289*reflectance)*2pi factor, so it is calibrated against Cycles
# reference renders rather than derived exactly.
_SSS_MFP_SCALE = 0.0796  # ~0.25/pi; empirically tuned


def _sceneMetersScale() -> float:
    us = bpy.context.scene.unit_settings
    return us.scale_length if us.system != 'NONE' else 1.0


def _exportSSSScatterParams(nodeCtx: NodeContext, mtlDesc: PluginDesc):
    """ Configure translucency=6 (random-walk SSS) scatter distance from Cycles'
        Subsurface Radius/Scale so the effective mean free path matches Cycles. """
    radiusSock = nodeCtx.node.inputs["Subsurface Radius"]
    scaleSock  = nodeCtx.node.inputs["Subsurface Scale"]
    metersScale = _sceneMetersScale()

    # Pin scatterRadius_const = 1 (fog_unit_scale_on multiplies fog_mult by metersScale*100)
    mtlDesc.setAttribute("fog_mult", 1.0 / (100.0 * max(1e-6, metersScale)))

    if not _isSocketConnected(radiusSock) and not _isSocketConnected(scaleSock):
        r = list(radiusSock.default_value)[:3]
        maxR = max(max(r), 1e-6)
        scale = scaleSock.default_value
        mtlDesc.setAttribute("fog_color_tex", Color((r[0] / maxR, r[1] / maxR, r[2] / maxR)))
        mtlDesc.setAttribute("fog_depth", maxR * scale * _SSS_MFP_SCALE)
    else:
        # textured radius/scale: pass the ratio through, scale the magnitude by the factor
        _exportCyclesColorAttribute(nodeCtx, mtlDesc, "Subsurface Radius", "fog_color_tex")
        depth = PluginDesc(Names.nextVirtualNode(nodeCtx, "TexFloatOp"), "TexFloatOp")
        _exportCyclesFloatAttribute(nodeCtx, depth, "Subsurface Scale", "float_a")
        depth.setAttribute("float_b", _SSS_MFP_SCALE)
        depth.setAttribute("mode", 0)  # product
        mtlDesc.setAttribute("fog_depth", _exportCyclesPluginWithStats(nodeCtx, depth))


# --- Cycles volume shaders -> BRDFVRayMtl 'volume' ---------------------------
# Cycles: Absorption adds sigma_a = Density * (1 - Color), Scatter adds sigma_s = Density * Color.
# VolumeFog's transmittance is color^(color_mult * dist), so color = exp(-sigma), color_mult = 1.
# Not VolumeScatterFog: its albedo is pinned near 1 (scatter rate from -log(max(color))), and water
# rendered 50x too bright.


def _constantSource(socket):
    """ The RGB / Value node feeding 'socket' through any reroutes, or None if it is textured. """
    while socket.is_linked:
        node = socket.links[0].from_node
        if node.bl_idname == 'NodeReroute':
            socket = node.inputs[0]
            continue
        if node.bl_idname in ('ShaderNodeRGB', 'ShaderNodeValue'):
            return node.outputs[0].default_value
        return None
    return None


def _volumeSocketColor(socket, name):
    """ A volume colour. V-Ray's volumetrics take constants, so fold an RGB node and report
        anything genuinely textured rather than silently using the socket's own default. """
    if (const := _constantSource(socket)) is not None:
        return list(const)[:3]
    if _isSocketConnected(socket):
        NodeContext.registerError(f"Volume {name}: a textured value is not supported, " "the socket's own value is used")
    return _getSocketValue(socket, SocketValueType.Color)


def _volumeSocketFloat(socket, name):
    if (const := _constantSource(socket)) is not None:
        return float(const)
    if _isSocketConnected(socket):
        NodeContext.registerError(f"Volume {name}: a textured value is not supported, " "the socket's own value is used")
    return _getSocketValue(socket, SocketValueType.Float)


class _CyclesVolume:
    """ The resolved Cycles medium: absorption, scattering, phase and emission. """
    def __init__(self):
        self.sigmaA = [0.0, 0.0, 0.0]
        self.sigmaS = [0.0, 0.0, 0.0]
        self.emission = [0.0, 0.0, 0.0]
        self.scatters = False

    def isEmpty(self):
        return not (any(self.sigmaA) or any(self.sigmaS) or any(self.emission))


def _accumulateCyclesVolume(socket, weight, vol, seen):
    """ Sum the volume shaders feeding 'socket' into 'vol'. """
    if not socket.is_linked:
        return
    node = socket.links[0].from_node
    if node.name in seen:
        return
    seen.add(node.name)

    match node.bl_idname:
        case 'NodeReroute':
            _accumulateCyclesVolume(node.inputs[0], weight, vol, seen)
        case 'ShaderNodeAddShader':
            for s in node.inputs:
                _accumulateCyclesVolume(s, weight, vol, seen)
        case 'ShaderNodeMixShader':
            fac = _clamp01(_volumeSocketFloat(node.inputs["Fac"], "Mix"))
            _accumulateCyclesVolume(node.inputs[1], weight * (1.0 - fac), vol, seen)
            _accumulateCyclesVolume(node.inputs[2], weight * fac, vol, seen)
        case 'ShaderNodeVolumeAbsorption':
            color = _volumeSocketColor(node.inputs["Color"], "Absorption Color")
            density = _volumeSocketFloat(node.inputs["Density"], "Absorption Density")
            for i in range(3):
                vol.sigmaA[i] += weight * density * max(0.0, 1.0 - color[i])
        case 'ShaderNodeVolumeScatter':
            color = _volumeSocketColor(node.inputs["Color"], "Scatter Color")
            density = _volumeSocketFloat(node.inputs["Density"], "Scatter Density")
            for i in range(3):
                vol.sigmaS[i] += weight * density * max(0.0, color[i])
            vol.scatters = True
        case 'ShaderNodeEmission':
            color = _volumeSocketColor(node.inputs["Color"], "Emission Color")
            strength = _volumeSocketFloat(node.inputs["Strength"], "Emission Strength")
            for i in range(3):
                vol.emission[i] += weight * strength * color[i]
        case _:
            NodeContext.registerError(f"Volume shader '{node.bl_idname}' is not supported by V-Ray")


def _resolveCyclesVolume(nodeCtx: NodeContext):
    """ The medium on the material output's Volume socket, or None. """
    mtl = nodeCtx.material
    if (mtl is None) or (not mtl.node_tree):
        return None

    output = next((n for n in mtl.node_tree.nodes if n.bl_idname == 'ShaderNodeOutputMaterial' and n.is_active_output), None)
    if (output is None) or ((volumeSocket := output.inputs.get("Volume")) is None) \
            or (not volumeSocket.is_linked):
        return None

    vol = _CyclesVolume()
    _accumulateCyclesVolume(volumeSocket, 1.0, vol, set())
    return None if vol.isEmpty() else vol


def _exportCyclesVolume(nodeCtx: NodeContext, mtlDesc: PluginDesc) -> bool:
    """ Put the material's Cycles medium in BRDFVRayMtl's 'volume' slot. """
    if (vol := _resolveCyclesVolume(nodeCtx)) is None:
        return False

    # The fog cannot return scattered light, only take it out. At volume_bounces 0 Cycles drops
    # scattered paths too, so the full extinction matches; above 0 most comes back and sigma_a is
    # closer.
    extinction = list(vol.sigmaA)
    if vol.scatters:
        bounces = getattr(getattr(bpy.context.scene, "cycles", None), "volume_bounces", 0)
        if bounces == 0:
            for i in range(3):
                extinction[i] += vol.sigmaS[i]
        else:
            NodeContext.registerError("Volume Scatter is converted as absorption - V-Ray's "
                                      "volumetric fog cannot scatter with an albedo below 1, so "
                                      "the in-scattered light is lost")

    desc = PluginDesc(Names.nextVirtualNode(nodeCtx, "VolumeFog"), "VolumeFog")
    desc.setAttribute("color", Color(tuple(math.exp(-s) for s in extinction)))
    desc.setAttribute("color_mult", 1.0)
    desc.setAttribute("emission", Color(tuple(vol.emission)))
    desc.setAttribute("bias", 0.0)
    # The surface BRDF owns the refraction; a second IOR here would bend the ray twice.
    desc.setAttribute("ior", 1.0)
    desc.setAttribute("closed_volume", False)

    mtlDesc.setAttribute("volume", _exportCyclesPluginWithStats(nodeCtx, desc))
    return True


def _exportCyclesBsdfPrincipled(nodeCtx: NodeContext):
    node: bpy.types.ShaderNodeBsdfPrincipled = nodeCtx.node
    pluginName = Names.treeNode(nodeCtx)
    mtlDesc = PluginDesc(pluginName, "BRDFVRayMtl")

    baseColorSocket = node.inputs["Base Color"]
    baseColor = _exportCyclesLinkedSocket(nodeCtx, baseColorSocket) if _isSocketConnected(baseColorSocket) else _getSocketValue(baseColorSocket, SocketValueType.Color)
    mtlDesc.setAttribute("translucency_color", baseColor)

    _exportCyclesFloatAttribute(nodeCtx, mtlDesc, "Alpha", "opacity")

    # Diffuse roughness was added in 4.3
    if bpy.app.version >= (4, 3, 0):
        _exportCyclesFloatAttribute(nodeCtx, mtlDesc, "Diffuse Roughness", "roughness")
    _exportCyclesMixedAttribute(nodeCtx, mtlDesc, "Emission Color", "Emission Strength", "self_illumination")

    _exportCyclesFloatAttribute(nodeCtx, mtlDesc, "Metallic", "metalness")

    roughnessSocket = nodeCtx.node.inputs["Roughness"]
    # OpenPBR also changes refract glossines to roughness
    _exportCyclesFloatAttribute(nodeCtx, mtlDesc, roughnessSocket, "reflect_glossiness", "refract_glossiness")
    refrGloss = mtlDesc.getAttribute("refract_glossiness")
    if isinstance(refrGloss, float):
        mtlDesc.setAttribute('refract_glossiness', _clamp(refrGloss, 0, 0.99))

    _exportCyclesFloatAttribute(nodeCtx, mtlDesc, "IOR", "refract_ior")

    _exportCyclesSpecular(nodeCtx, mtlDesc)

    _exportCyclesColorAttribute(nodeCtx, mtlDesc, "Coat Tint", "coat_color")
    _exportCyclesFloatAttribute(nodeCtx, mtlDesc, "Coat Weight", "coat_amount")
    _exportCyclesFloatAttribute(nodeCtx, mtlDesc, "Coat Roughness", "coat_glossiness")
    _exportCyclesFloatAttribute(nodeCtx, mtlDesc, "Coat IOR", "coat_ior")

    _exportCyclesColorAttribute(nodeCtx, mtlDesc, "Sheen Tint", "sheen_color")
    _exportCyclesFloatAttribute(nodeCtx, mtlDesc, "Sheen Weight", "sheen_amount")
    _exportCyclesFloatAttribute(nodeCtx, mtlDesc, "Sheen Roughness", "sheen_glossiness")

    _exportCyclesFloatAttribute(nodeCtx, mtlDesc, "Thin Film IOR", "thin_film_ior")

    thinFilmSocket = nodeCtx.node.inputs["Thin Film Thickness"]
    if _isSocketNonZero(thinFilmSocket):
        # V-Ray blends thin_film_thickness_min..max via the thin_film_thickness tex (0 -> min);
        # Blender exposes a single thickness, so pin both min and max to it.
        _exportCyclesFloatAttribute(nodeCtx, mtlDesc, thinFilmSocket, "thin_film_thickness_min", "thin_film_thickness_max")
        mtlDesc.setAttribute("thin_film_on", True)
    else:
        mtlDesc.setAttribute("thin_film_on", False)

    # Blender 5.2+ Principled "Thin Wall" (bool) -> V-Ray thin-walled refraction. Looked up by
    # name so the shifted socket id on 5.2 is harmless; absent on <=5.1 (guarded).
    if "Thin Wall" in nodeCtx.node.inputs:
        mtlDesc.setAttribute("refract_thin_walled", bool(nodeCtx.node.inputs["Thin Wall"].default_value))

    sssWeightSocket = nodeCtx.node.inputs["Subsurface Weight"]
    transmissionWeight = nodeCtx.node.inputs["Transmission Weight"]
    if _isSocketConnected(transmissionWeight):
        transmission = exportLinkedSocket(nodeCtx, transmissionWeight)
        refract = _wrapFloatToColor(nodeCtx, transmission)
    else:
        refract = _getSocketValue(transmissionWeight, SocketValueType.Color)
    mtlDesc.setAttribute("refract", refract)

    # Cycles splits the base between three lobes:
    #   refraction = transmission
    #   SSS        = subsurface * (1 - transmission)
    #   diffuse    = (1 - transmission) * (1 - subsurface) * base
    # The V-Ray core reproduces this exact partition on its own from refract + translucency_amount
    # (it computes diffuse *= (1-refract)*(1-translucency_amount) and splits refraction vs SSS by
    # translucency_amount - see cgrepo brdf_vraymtl_common.h:582-598 + mtlbrdf.h:2925-2927). So we
    # feed the FULL base color and the raw subsurface weight and let the core conserve energy;
    # hand-dimming the base here would double-count.
    mtlDesc.setAttribute("diffuse", baseColor)
    _exportCyclesFloatAttribute(nodeCtx, mtlDesc, sssWeightSocket, "translucency_amount")

    if node.subsurface_method != 'BURLEY' and "Subsurface Anisotropy" in nodeCtx.node.inputs:
        sssAnisotropySocket = nodeCtx.node.inputs["Subsurface Anisotropy"]
        if _isSocketConnected(sssAnisotropySocket):
            NodeContext.registerError("V-Ray does not support textured Subsurface Anisotropy")
        mtlDesc.setAttribute("translucency_scatter_dir", _getSocketValue(sssAnisotropySocket, SocketValueType.Float))
    else:
        mtlDesc.setAttribute("translucency_scatter_dir", 0.0)

    hasTransmission = _isSocketNonZero(transmissionWeight)
    hasSSS = _isSocketNonZero(sssWeightSocket)
    if hasSSS:
        # Cycles random-walk subsurface -> V-Ray translucency mode 6 (random-walk SSS).
        # translucency_color (= base color, set above) is the SSS reflectance; the scatter
        # distance comes from Subsurface Radius/Scale via _exportSSSScatterParams. When
        # transmission is also present, the core partitions refraction vs SSS from
        # refract + translucency_amount (set above).
        mtlDesc.setAttribute("translucency", 6)  # SSS (random-walk)
        _exportSSSScatterParams(nodeCtx, mtlDesc)
    elif hasTransmission:
        # Clear/colored transmission with no volume absorption (Cycles Principled has none):
        # fog_mult=0 disables the Beer-Lambert volume and, in OpenPBR, tints refraction by the
        # fog color per interface. A solid object crosses two interfaces, so V-Ray applies the
        # tint twice (fog_color^2); Cycles tints by sqrt(base) per interface (net = base). To
        # match, feed sqrt(base) so V-Ray's two crossings also net to base.
        if isinstance(baseColor, AttrPlugin):
            mtlDesc.setAttribute("fog_color", WHITE_COLOR)
            mtlDesc.setAttribute("fog_color_tex", baseColor)
        else:
            mtlDesc.setAttribute("fog_color", Color((baseColor.r**0.5, baseColor.g**0.5, baseColor.b**0.5)))
            mtlDesc.setAttribute("fog_color_tex", AttrPlugin())
        mtlDesc.setAttribute("fog_depth", 1.0)
        mtlDesc.setAttribute("fog_mult", 0.0)
        mtlDesc.setAttribute("translucency", 0)  # none
    else:
        mtlDesc.setAttribute("translucency", 0)  # none
        mtlDesc.setAttribute("fog_mult", 0.0)
        # Reset the whole fog group, not just fog_mult.
        mtlDesc.setAttribute("fog_color", WHITE_COLOR)
        mtlDesc.setAttribute("fog_color_tex", AttrPlugin())
        mtlDesc.setAttribute("fog_depth", 1.0)

    # The formula here is purely based on observation. The anisotropy in Cycles seems to be clamped
    # between [0-1] and seems to somewhat match V-Ray's when rescaled in the range [0.0, 0.6].
    anisotropySocket = node.inputs["Anisotropic"]
    if not _isSocketConnected(anisotropySocket):
        anisotropy = _clamp01(_getSocketValue(anisotropySocket, SocketValueType.Float))*0.6
    else:
        anisotropy = _exportCyclesLinkedSocket(nodeCtx, anisotropySocket)
        anisotropy = _wrapClampPlugin01(nodeCtx, anisotropy)
        texFloatOpName = Names.nextVirtualNode(nodeCtx, "TexFloatOp")
        texFloatOpDesc = PluginDesc(texFloatOpName, "TexFloatOp")
        texFloatOpDesc.setAttribute("float_a", anisotropy)
        texFloatOpDesc.setAttribute("float_b", 0.6)
        texFloatOpDesc.setAttribute("mode", 0) # product
        anisotropy = _exportCyclesPluginWithStats(nodeCtx, texFloatOpDesc)
    mtlDesc.setAttribute("anisotropy", anisotropy)
    mtlDesc.setAttribute("coat_anisotropy", anisotropy)

    _exportCyclesFloatAttribute(nodeCtx, mtlDesc, "Anisotropic Rotation", "anisotropy_rotation", "coat_anisotropy_rotation")

    _exportCyclesColorAttribute(nodeCtx, mtlDesc, "Normal", "bump_map")

    ansiotropyUVWGen = exportDefaultUVWGenChannel(nodeCtx)
    mtlDesc.setAttribute("anisotropy_uvwgen", ansiotropyUVWGen)
    mtlDesc.setAttribute("refract_affect_shadows", False)
    mtlDesc.setAttribute("bump_type", 6) # explicit normal
    mtlDesc.setAttribute("coat_bump_type", 6) # explicit normal
    mtlDesc.setAttribute("roughness_model", 1) # oren-nayar
    mtlDesc.setAttribute("option_use_roughness", True)
    # fresnel_ior_lock is set by _exportCyclesSpecular - do not write it here, last write wins
    mtlDesc.setAttribute("opacity_source", 0) # gray scale alpha
    mtlDesc.setAttribute("option_shading_model", 1) # openpbr for the fuzz sheen
    mtlDesc.setAttribute("fog_unit_scale_on", True)
    mtlDesc.setAttribute("anisotropy_derivation", 0) # local axis
    mtlDesc.setAttribute("anisotropy_axis", 2) # Z axis
    mtlDesc.setAttribute("brdf_type", 4) # ggx
    # fog_mult is set per-branch above (0 for transmission/none; SSS derives it from meters_scale)
    mtlDesc.setAttribute("fresnel", True) # enable fresnel reflections
    mtlDesc.setAttribute("translucency_scatter_coeff", 1.0) # full scattering
    mtlDesc.setAttribute("self_illumination_gi", True)
    # Cycles' "Multiscatter GGX" (the node default) adds the multiple-scattering energy back; plain
    # "GGX" is single-scatter and darkens at high roughness. V-Ray's compensation restores the full
    # single-scatter loss, which is what Cycles does for a metal or a transmissive lobe - but for an
    # opaque *dielectric* Cycles weights the correction by the Fresnel albedo
    # (bsdf_microfacet_setup_fresnel_generalized_schlick, the is_zero(transmission_tint) branch), so
    # a weak specular lobe barely changes and V-Ray's full compensation is ~2.4x too bright at
    # roughness 1. Compensate everywhere except that case.
    metallicSocket = node.inputs["Metallic"]
    isMetallic = _isSocketConnected(metallicSocket) or \
                 _getSocketValue(metallicSocket, SocketValueType.Float) > 0.5
    compensate = node.distribution == 'MULTI_GGX' and (isMetallic or hasTransmission)
    mtlDesc.setAttribute("gtr_energy_compensation", 2 if compensate else 0)
    mtlDesc.setAttribute("option_reflect_on_back", True)
    # TODO: Export from cycles render engine->Light Paths, these are default-ish
    mtlDesc.setAttribute("reflect_depth", 4)
    mtlDesc.setAttribute("refract_depth", 12)

    if _isSocketConnected(nodeCtx.node.inputs["Tangent"]):
        NodeContext.registerError("V-Ray does not support tangent textures")

    # 'volume' makes the core ignore the fog and translucency parameters written above, and is only
    # traced through refraction. It also drops the Base Color tint the fog carried; moving that to
    # 'refract' measured worse everywhere, so it is reported as lost.
    if _exportCyclesVolume(nodeCtx, mtlDesc) and not hasTransmission:
        NodeContext.registerError("A Volume shader only renders through a refractive surface "
                                  "in V-Ray; this material has no Transmission")

    return _exportCyclesPluginWithStats(nodeCtx, mtlDesc)


# --- Cycles Principled Hair -> V-Ray BRDFHair4 -------------------------------
# BRDFHair4 is the same Chiang et al. 2016 model as the node's 'Chiang' option, with identical
# variance and logistic formulas. Only the parametrization differs, plus a few BRDFHair4 extras
# (glints, a coat boost) that Chiang has no counterpart for and that default to on.

# Chiang's longitudinal variance v(beta): brdf_hair4_impl.h getLongitudalVariance() ==
# bsdf_principled_hair_chiang.h:168.
def _chiangVariance(beta: float):
    return (0.726 * beta + 0.812 * (beta ** 2) + 3.7 * (beta ** 20)) ** 2

# Cycles divides ln(color) by a roughness dependent scale (bsdf_util.h
# bsdf_principled_hair_albedo_roughness_scale), BRDFHair4 always by 6. They agree near beta 0.3
# (5.888) and drift apart towards beta 1 (3.375).
VRAY_HAIR_ABSORPTION_SCALE = 6.0

def _cyclesHairAbsorptionScale(beta: float):
    b = _clamp01(beta)
    return ((((0.245 * b + 5.574) * b - 10.73) * b + 2.532) * b - 0.215) * b + 5.969

# Cycles pigment absorption per unit concentration (bsdf_util.h).
CYCLES_EUMELANIN_SIGMA   = (0.506, 0.841, 1.653)
CYCLES_PHEOMELANIN_SIGMA = (0.343, 0.733, 1.924)


def _hairDyeColorForSigma(sigma) -> Color:
    """ Invert BRDFHair4's sigma_a = (ln(dye_color)/6)^2 so an arbitrary Cycles absorption
        coefficient comes out exactly through 'dye_color'. """
    return Color(tuple(math.exp(-VRAY_HAIR_ABSORPTION_SCALE * math.sqrt(max(c, 0.0))) for c in sigma))


def _getConstSocketFloat(socket: bpy.types.NodeSocket):
    """ Clamped value of a float socket, or None if it is textured. """
    if _isSocketConnected(socket):
        return None
    return _clamp01(_getSocketValue(socket, SocketValueType.Float))


def _exportCyclesHairGlossiness(nodeCtx: NodeContext, mtlDesc: PluginDesc, socket: bpy.types.NodeSocket):
    """ Roughness -> glossiness. BRDFHair4's beta_m is (1 - glossiness)^2 and Cycles' is Roughness,
        so glossiness = 1 - sqrt(Roughness). """
    if not _isSocketConnected(socket):
        mtlDesc.setAttribute("glossiness", _clamp01(1.0 - math.sqrt(_clamp01(_getSocketValue(socket, SocketValueType.Float)))))
        return

    sqrtDesc = PluginDesc(Names.nextVirtualNode(nodeCtx, "TexFloatOp"), "TexFloatOp")
    _exportCyclesFloatAttribute(nodeCtx, sqrtDesc, socket, "float_a")
    sqrtDesc.setAttribute("mode", 15)  # sqrt

    invDesc = PluginDesc(Names.nextVirtualNode(nodeCtx, "TexFloatOp"), "TexFloatOp")
    invDesc.setAttribute("float_a", 1.0)
    invDesc.setAttribute("float_b", _exportCyclesPluginWithStats(nodeCtx, sqrtDesc))
    invDesc.setAttribute("mode", 3)  # difference
    mtlDesc.setAttribute("glossiness", _exportCyclesPluginWithStats(nodeCtx, invDesc))


def _exportCyclesHairAbsorption(nodeCtx: NodeContext, mtlDesc: PluginDesc, azimuthalRoughness: float | None):
    """ Cycles hair absorption -> BRDFHair4 'dye_color'. All three parametrizations reduce to one
        absorption coefficient which dye_color can encode exactly, so melanin/pheomelanin are
        switched off - their defaults are non-zero and the two terms are additive. """
    node = nodeCtx.node
    mtlDesc.setAttribute("melanin", 0.0)
    mtlDesc.setAttribute("pheomelanin", 0.0)

    parametrization = getattr(node, "parametrization", "COLOR")

    if parametrization == "ABSORPTION":
        socket = node.inputs["Absorption Coefficient"]
        if _isSocketConnected(socket):
            NodeContext.registerError("Principled Hair: a textured 'Absorption Coefficient' is not supported by V-Ray")
        mtlDesc.setAttribute("dye_color", _hairDyeColorForSigma(_getSocketValue(socket, SocketValueType.Color)))
        return

    if parametrization == "MELANIN":
        melaninSocket = node.inputs["Melanin"]
        rednessSocket = node.inputs["Melanin Redness"]
        tintSocket = node.inputs["Tint"]

        if (randomColor := _getConstSocketFloat(node.inputs["Random Color"])) is None or randomColor > 0.0:
            NodeContext.registerError("Principled Hair: 'Random Color' is not supported by V-Ray")

        textured = any(_isSocketConnected(s) for s in (melaninSocket, rednessSocket, tintSocket))
        if not textured and azimuthalRoughness is not None:
            # Melanin 0..1 becomes a concentration of -ln(1 - Melanin), split between the two
            # pigments, plus the Tint reflectance (svm/closure.h:885).
            concentration = -math.log(max(1.0 - _clamp01(_getSocketValue(melaninSocket, SocketValueType.Float)), 1e-4))
            redness = _clamp01(_getSocketValue(rednessSocket, SocketValueType.Float))
            tint = _getSocketValue(tintSocket, SocketValueType.Color)
            scale = _cyclesHairAbsorptionScale(azimuthalRoughness)
            sigma = [concentration * (eu * (1.0 - redness) + pheo * redness) + (math.log(max(t, 1e-6)) / scale) ** 2
                     for eu, pheo, t in zip(CYCLES_EUMELANIN_SIGMA, CYCLES_PHEOMELANIN_SIGMA, tint)]
            mtlDesc.setAttribute("dye_color", _hairDyeColorForSigma(sigma))
            return

        # Textured pigment: fall back to BRDFHair4's melanin model. Different constants and
        # concentration curve, so this branch only matches approximately.
        _exportCyclesFloatAttribute(nodeCtx, mtlDesc, melaninSocket, "melanin")
        _exportCyclesFloatAttribute(nodeCtx, mtlDesc, rednessSocket, "pheomelanin")
        _exportCyclesColorAttribute(nodeCtx, mtlDesc, tintSocket, "dye_color")
        return

    # 'COLOR' (direct coloring): sigma_a = (ln(Color)/scale)^2 on the Cycles side and
    # (ln(dye_color)/6)^2 on the V-Ray one, so dye_color = Color^(6/scale).
    colorSocket = node.inputs["Color"]
    exponent = 1.0 if azimuthalRoughness is None else \
        VRAY_HAIR_ABSORPTION_SCALE / _cyclesHairAbsorptionScale(azimuthalRoughness)

    if not _isSocketConnected(colorSocket):
        color = _getSocketValue(colorSocket, SocketValueType.Color)
        mtlDesc.setAttribute("dye_color", Color(tuple(max(c, 0.0) ** exponent for c in color)))
    elif math.isclose(exponent, 1.0, abs_tol=1e-3):
        _exportCyclesColorAttribute(nodeCtx, mtlDesc, colorSocket, "dye_color")
    else:
        # MayaGamma outputs input^(1/gamma).
        gammaDesc = PluginDesc(Names.nextVirtualNode(nodeCtx, "MayaGamma"), "MayaGamma")
        _exportCyclesColorAttribute(nodeCtx, gammaDesc, colorSocket, "input")
        gammaDesc.setAttribute("gamma", Color((1.0 / exponent,) * 3))
        mtlDesc.setAttribute("dye_color", _exportCyclesPluginWithStats(nodeCtx, gammaDesc))


def _exportCyclesHairHighlightShift(nodeCtx: NodeContext, mtlDesc: PluginDesc, socket: bpy.types.NodeSocket):
    """ Cuticle tilt: Offset (radians) -> highlight_shift (degrees). Both sides derive the same
        per-lobe shifts (-2a for R, +a for TT, +4a for TRT). """
    if not _isSocketConnected(socket):
        mtlDesc.setAttribute("highlight_shift", math.degrees(_getSocketValue(socket, SocketValueType.Float)))
        return

    shiftDesc = PluginDesc(Names.nextVirtualNode(nodeCtx, "TexFloatOp"), "TexFloatOp")
    _exportCyclesFloatAttribute(nodeCtx, shiftDesc, socket, "float_a")
    shiftDesc.setAttribute("float_b", 180.0 / math.pi)
    shiftDesc.setAttribute("mode", 0)  # product
    mtlDesc.setAttribute("highlight_shift", _exportCyclesPluginWithStats(nodeCtx, shiftDesc))


def _hairPrimaryGlossinessBoost(nodeCtx: NodeContext, roughness: float | None):
    """ 'Coat' -> 'primary_glossiness_boost'. Coat sharpens only the primary reflection: Cycles
        takes the R lobe variance from (1 - Coat) * Roughness, BRDFHair4 scales that lobe's
        variance by (1 - primary_glossiness_boost). """
    coatSocket = nodeCtx.node.inputs["Coat"]
    coat = _getConstSocketFloat(coatSocket)
    if coat is None:
        NodeContext.registerError("Principled Hair: a textured 'Coat' is not supported by V-Ray")
        return 0.0

    if (coat == 0.0) or (roughness is None) or (roughness == 0.0):
        return 0.0
    return _clamp01(1.0 - _chiangVariance((1.0 - coat) * roughness) / _chiangVariance(roughness))


def _exportCyclesHairHuangLobes(nodeCtx: NodeContext, mtlDesc: PluginDesc):
    """ Huang's per-lobe weights map onto BRDFHair4's per-lobe tints - they multiply the same
        three attenuation terms. """
    node = nodeCtx.node
    for socketName, tint in (("Reflection", "primary_tint"),
                             ("Transmission", "transmission_tint"),
                             ("Secondary Reflection", "secondary_tint")):
        weight = _getConstSocketFloat(node.inputs[socketName])
        if weight is None:
            _exportCyclesColorAttribute(nodeCtx, mtlDesc, node.inputs[socketName], tint)
        else:
            mtlDesc.setAttribute(tint, Color((weight,) * 3))

    # Measured against Cycles, the Chiang lobes come out 60% bright at Roughness 0.1 and 130% at
    # 0.8 - the two models normalize differently and no parameter mapping closes that.
    NodeContext.registerError("Principled Hair: V-Ray has no near-field hair model, so 'Huang' is "
                              "approximated with Chiang lobes and renders brighter")

    if (aspect := _getConstSocketFloat(node.inputs["Aspect Ratio"])) is None or not math.isclose(aspect, 1.0):
        NodeContext.registerError("Principled Hair: the Huang model's 'Aspect Ratio' "
                                  "(elliptical cross-section) is not supported by V-Ray")


def _exportCyclesHairPrincipled(nodeCtx: NodeContext):
    """ Cycles 'Principled Hair BSDF' -> V-Ray BRDFHair4. The mapping is exact wherever both sides
        take a constant; the near-field 'Huang' model has no V-Ray counterpart and is approximated
        with the Chiang lobes. ND_chiang_hair_bsdf is not an instantiable plugin, so BRDFHair4 is
        the only target. """
    node: bpy.types.ShaderNodeBsdfHairPrincipled = nodeCtx.node
    mtlDesc = PluginDesc(Names.treeNode(nodeCtx), "BRDFHair4")

    isHuang = getattr(node, "model", "CHIANG") == "HUANG"

    roughnessSocket = node.inputs["Roughness"]
    roughness = _getConstSocketFloat(roughnessSocket)

    # Huang has no separate azimuthal roughness - Cycles feeds it the longitudinal one, and its
    # 'Radial Roughness' socket is disabled and must not be read.
    radialSocket = roughnessSocket if isHuang else node.inputs["Radial Roughness"]
    radial = roughness if isHuang else _getConstSocketFloat(radialSocket)

    _exportCyclesHairGlossiness(nodeCtx, mtlDesc, roughnessSocket)
    # 'softness' is the azimuthal roughness fed directly - same logistic scale on both sides.
    _exportCyclesFloatAttribute(nodeCtx, mtlDesc, radialSocket, "softness")

    _exportCyclesHairAbsorption(nodeCtx, mtlDesc, radial)
    _exportCyclesFloatAttribute(nodeCtx, mtlDesc, node.inputs["IOR"], "ior")
    _exportCyclesHairHighlightShift(nodeCtx, mtlDesc, node.inputs["Offset"])

    # The Huang model exposes no coat, so its R lobe keeps the base roughness.
    mtlDesc.setAttribute("primary_glossiness_boost",
                         0.0 if isHuang else _hairPrimaryGlossinessBoost(nodeCtx, roughness))

    # Cycles multiplies both roughnesses by 1 + 2*(random - 0.5)*Random Roughness, a uniform
    # +/- roughness*RR offset on beta; BRDFHair4 offsets beta by an absolute +/- random_glossiness.
    # Scaling by the base roughness makes the two distributions coincide.
    # 0 = plugin default (no randomisation); written on every path.
    mtlDesc.setAttribute("random_glossiness", 0.0)
    mtlDesc.setAttribute("random_softness", 0.0)
    if (randomRoughness := _getConstSocketFloat(node.inputs["Random Roughness"])) is not None:
        if roughness is not None:
            mtlDesc.setAttribute("random_glossiness", roughness * randomRoughness)
        if radial is not None:
            mtlDesc.setAttribute("random_softness", radial * randomRoughness)

    # Chiang has no diffuse or glint/glitter lobes; BRDFHair4 enables all of them by default.
    # glint_variation also forces a floor under the random_* amounts (brdf_hair4_impl.h:701), so it
    # has to be zeroed even with the glint lobes off.
    mtlDesc.setAttribute("diffuse_amount", 0.0)
    mtlDesc.setAttribute("glint_strength", 0.0)
    mtlDesc.setAttribute("glint_variation", 0.0)
    mtlDesc.setAttribute("glitter_strength", 0.0)

    if isHuang:
        _exportCyclesHairHuangLobes(nodeCtx, mtlDesc)

    return _exportCyclesPluginWithStats(nodeCtx, mtlDesc)


def _exportCyclesDiffuseBsdf(nodeCtx: NodeContext):
    pluginName = Names.treeNode(nodeCtx)
    mtlDesc = PluginDesc(pluginName, "BRDFVRayMtl")

    _exportCyclesColorAttribute(nodeCtx, mtlDesc, "Color", "diffuse")
    _exportCyclesFloatAttribute(nodeCtx, mtlDesc, "Roughness", "roughness")
    _exportCyclesColorAttribute(nodeCtx, mtlDesc, "Normal", "bump_map")
    mtlDesc.setAttribute("roughness_model", 1) # oren-nayar
    mtlDesc.setAttribute("bump_type", 6) # explicit normal
    mtlDesc.setAttribute("gtr_energy_compensation", 2)

    return _exportCyclesPluginWithStats(nodeCtx, mtlDesc)

def _exportCyclesEmissionBsdf(nodeCtx: NodeContext):
    pluginName = Names.treeNode(nodeCtx)
    mtlDesc = PluginDesc(pluginName, "BRDFLight")

    _exportCyclesColorAttribute(nodeCtx, mtlDesc, "Color", "color")
    _exportCyclesFloatAttribute(nodeCtx, mtlDesc, "Strength", "colorMultiplier")

    return _exportCyclesPluginWithStats(nodeCtx, mtlDesc)

def _exportCyclesSheenBsdf(nodeCtx: NodeContext):
    node: bpy.types.ShaderNodeBsdfSheen = nodeCtx.node
    pluginName = Names.treeNode(nodeCtx)
    mtlDesc = PluginDesc(pluginName, "BRDFVRayMtl")

    _exportCyclesColorAttribute(nodeCtx, mtlDesc, "Color", "sheen_color")
    _exportCyclesFloatAttribute(nodeCtx, mtlDesc, "Roughness", "sheen_glossiness")
    _exportCyclesColorAttribute(nodeCtx, mtlDesc, "Normal", "bump_map")

    if node.distribution=='ASHIKHMIN':
        mtlDesc.setAttribute("option_shading_model", 0) # vray
    else:
        mtlDesc.setAttribute("option_shading_model", 1) # openpbr

    mtlDesc.setAttribute("roughness_model", 1) # oren-nayar
    mtlDesc.setAttribute("option_use_roughness", True)
    mtlDesc.setAttribute("bump_type", 6) # explicit normal
    mtlDesc.setAttribute("diffuse", BLACK_COLOR)
    mtlDesc.setAttribute("gtr_energy_compensation", 2)

    return _exportCyclesPluginWithStats(nodeCtx, mtlDesc)

def _exportCyclesGlossyBsdf(nodeCtx: NodeContext):
    pluginName = Names.treeNode(nodeCtx)
    mtlDesc = PluginDesc(pluginName, "BRDFVRayMtl")

    _exportCyclesColorAttribute(nodeCtx, mtlDesc, "Color", "diffuse")
    _exportCyclesFloatAttribute(nodeCtx, mtlDesc, "Roughness", "reflect_glossiness")
    _exportCyclesColorAttribute(nodeCtx, mtlDesc, "Normal", "bump_map")

    # The anisotropy in the Glossy BSDF matches V-Ray almost perfectly. We just have to clamp
    # the anisotropy because V-Ray does not handle -1.0 and 1.0 and we add 0.25 to the rotation
    # because the highlight is rotated 90degrees by default.
    anisotropySocket = nodeCtx.node.inputs["Anisotropy"]
    if not _isSocketConnected(anisotropySocket):
        anisotropy = _clamp(_getSocketValue(anisotropySocket, SocketValueType.Float), -0.99, 0.99)
    else:
        anisotropy = _exportCyclesLinkedSocket(nodeCtx, anisotropySocket)
        anisotropy = _wrapClampPlugin(nodeCtx, anisotropy, -0.99, 0.99)
    mtlDesc.setAttribute("anisotropy", anisotropy)
    mtlDesc.setAttribute("coat_anisotropy", anisotropy)
    rotationSocket = nodeCtx.node.inputs["Rotation"]
    if not _isSocketConnected(rotationSocket):
        mtlDesc.setAttribute("anisotropy_rotation", _getSocketValue(rotationSocket, SocketValueType.Float) + 0.25)
    else:
        texFloatOpName = Names.nextVirtualNode(nodeCtx, "TexFloatOp")
        texFloatOpDesc = PluginDesc(texFloatOpName, "TexFloatOp")
        texFloatOpDesc.setAttribute("float_a", _exportCyclesLinkedSocket(nodeCtx, rotationSocket))
        texFloatOpDesc.setAttribute("float_b", 0.25)
        texFloatOpDesc.setAttribute("mode", 2) # sum
        anisotropyRotation = _exportCyclesPluginWithStats(nodeCtx, texFloatOpDesc)
        mtlDesc.setAttribute("anisotropy_rotation", anisotropyRotation)

    mtlDesc.setAttribute("metalness", 1.0)
    mtlDesc.setAttribute("anisotropy_derivation", 0) # local axis
    mtlDesc.setAttribute("anisotropy_axis", 2) # Z axis
    mtlDesc.setAttribute("reflect", WHITE_COLOR)
    mtlDesc.setAttribute("fresnel", True)
    mtlDesc.setAttribute("roughness_model", 1) # oren-nayar
    mtlDesc.setAttribute("option_use_roughness", True)
    mtlDesc.setAttribute("bump_type", 6) # explicit normal
    mtlDesc.setAttribute("gtr_energy_compensation", 2)

    return _exportCyclesPluginWithStats(nodeCtx, mtlDesc)

def _exportCyclesFogColor(nodeCtx: NodeContext, mtlDesc: PluginDesc, socket: bpy.types.NodeSocket):
    """ Write a colour socket to BRDFVRayMtl's fog. 'fog_color' takes only a constant colour;
        a textured one has to go to 'fog_color_tex', which overrides it. """
    if _isSocketConnected(socket):
        mtlDesc.setAttribute("fog_color", WHITE_COLOR)
        mtlDesc.setAttribute("fog_color_tex", _exportCyclesLinkedSocket(nodeCtx, socket))
    else:
        mtlDesc.setAttribute("fog_color", _getSocketValue(socket, SocketValueType.Color))
        mtlDesc.setAttribute("fog_color_tex", AttrPlugin())


def _exportCyclesFlatProcedural(nodeCtx: NodeContext, nodeLink: FarNodeLink, label: str, colorInputs: tuple):
    """ Stand-in for a Cycles procedural V-Ray has no plugin for. Dropping the link instead leaves
        the consumer on V-Ray's own default - a 0.8 grey diffuse, which read 17x too bright for a
        dark brick wall - so emit the pattern's mean colour and say so. """
    NodeContext.registerError(f"{label} has no V-Ray equivalent; it is converted to its average " "colour and the pattern is lost")

    if colorInputs:
        mean = [sum(_getSocketValue(nodeCtx.node.inputs[n], SocketValueType.Color)[i]
                    for n in colorInputs) / len(colorInputs) for i in range(3)]
    else:
        mean = [0.5, 0.5, 0.5]   # a Fac/Color pattern averages to mid grey

    desc = PluginDesc(Names.treeNode(nodeCtx), "TexAColor")
    desc.setAttribute("texture", AColor((mean[0], mean[1], mean[2], 1.0)))
    plugin = _exportCyclesPluginWithStats(nodeCtx, desc)
    if nodeLink.from_socket.name == "Fac":
        return _exportIntensityOutput(nodeCtx, plugin)
    return plugin


def _exportCyclesTransparentBsdf(nodeCtx: NodeContext):
    """ Cycles Transparent BSDF: the ray continues in the same direction, tinted by Color. That
        is a straight-through refraction - IOR 1 so nothing bends, no Fresnel, no reflection. """
    mtlDesc = PluginDesc(Names.treeNode(nodeCtx), "BRDFVRayMtl")

    _exportCyclesColorAttribute(nodeCtx, mtlDesc, "Color", "refract")
    mtlDesc.setAttribute("diffuse", BLACK_COLOR)
    mtlDesc.setAttribute("reflect", BLACK_COLOR)
    mtlDesc.setAttribute("fresnel", False)
    mtlDesc.setAttribute("refract_ior", 1.0)
    mtlDesc.setAttribute("refract_glossiness", 0.0)
    mtlDesc.setAttribute("fog_mult", 0.0)
    mtlDesc.setAttribute("option_shading_model", 1) # openpbr, to read glossiness as roughness
    mtlDesc.setAttribute("refract_affect_shadows", True)
    mtlDesc.setAttribute("refract_depth", 12)

    return _exportCyclesPluginWithStats(nodeCtx, mtlDesc)


# Light Path -> TexRaySwitch: each 'Is ... Ray' reads 1 in its own slot and 0 elsewhere.
# 'default' covers camera rays plus anything V-Ray does not classify.
_RAY_SWITCH_SLOTS = ("default_texture", "shadow_ray_texture", "gi_ray_texture", "reflect_ray_texture", "refract_ray_texture")

_LIGHT_PATH_SLOTS = {
    "Is Camera Ray":       "default_texture",
    "Is Shadow Ray":       "shadow_ray_texture",
    "Is Diffuse Ray":      "gi_ray_texture",
    "Is Glossy Ray":       "reflect_ray_texture",
    "Is Reflection Ray":   "reflect_ray_texture",
    "Is Transmission Ray": "refract_ray_texture",
}


# The counters come from TexSampler. 'path_length' is accumulated where Cycles' 'Ray Length' is
# the current segment, so they only agree on a camera ray.
_LIGHT_PATH_SAMPLER_OUTPUTS = {
    "Ray Length":        "path_length",
    "Ray Depth":         "ray_depth",
    "Transparent Depth": "transparency_level",
}


def _exportCyclesLightPathNode(nodeCtx: NodeContext, nodeLink: FarNodeLink):
    name = nodeLink.from_socket.name

    if (output := _LIGHT_PATH_SAMPLER_OUTPUTS.get(name)) is not None:
        sampler = _exportCyclesPluginWithStats( nodeCtx, PluginDesc(Names.treeNode(nodeCtx), "TexSampler"))
        sampler.output = output
        return sampler

    if (slot := _LIGHT_PATH_SLOTS.get(name)) is None:
        NodeContext.registerError(f"Light Path '{name}' output is not supported by V-Ray")
        return None

    # Scoped by slot so the outputs sharing 'reflect_ray_texture' still share one plugin.
    # '_texture' dropped to keep the name short - node names truncate at 63 chars.
    texDesc = PluginDesc(_outputScopedName(nodeCtx, slot.removesuffix("_texture")), "TexRaySwitch")
    for s in _RAY_SWITCH_SLOTS:
        texDesc.setAttribute(s, WHITE_COLOR if s == slot else BLACK_COLOR)

    return _exportIntensityOutput(nodeCtx, _exportCyclesPluginWithStats(nodeCtx, texDesc, True))


def _exportCyclesTranslucentBsdf(nodeCtx: NodeContext):
    pluginName = Names.treeNode(nodeCtx)
    mtlDesc = PluginDesc(pluginName, "BRDFVRayMtl")

    # Cycles Translucent BSDF = pure diffuse transmission (light enters, scatters, exits the
    # far side). Map to a thin-walled, fully-rough refraction with no Fresnel reflection so all
    # the energy is transmitted and diffused rather than split off into a glossy reflection.
    # 'fog_color' is a plain colour; a textured one must go through 'fog_color_tex'.
    _exportCyclesFogColor(nodeCtx, mtlDesc, nodeCtx.node.inputs["Color"])
    _exportCyclesColorAttribute(nodeCtx, mtlDesc, "Normal", "bump_map")

    mtlDesc.setAttribute("diffuse", BLACK_COLOR)
    mtlDesc.setAttribute("refract", WHITE_COLOR)
    mtlDesc.setAttribute("reflect", BLACK_COLOR)
    mtlDesc.setAttribute("fresnel", False)
    mtlDesc.setAttribute("refract_thin_walled", True)
    mtlDesc.setAttribute("fog_mult", 0.0)
    mtlDesc.setAttribute("refract_glossiness", 0.99)
    mtlDesc.setAttribute("roughness_model", 1) # oren-nayar
    mtlDesc.setAttribute("bump_type", 6) # explicit normal
    mtlDesc.setAttribute("gtr_energy_compensation", 2)
    mtlDesc.setAttribute("option_shading_model", 1)
    mtlDesc.setAttribute("fog_unit_scale_on", True)
    mtlDesc.setAttribute("refract_affect_shadows", False)

    return _exportCyclesPluginWithStats(nodeCtx, mtlDesc)

def _exportCyclesRefractiveBsdf(nodeCtx: NodeContext, isGlass: bool):
    if not isGlass:
        NodeContext.registerError("V-Ray Refractive BSDF will render different")
    pluginName = Names.treeNode(nodeCtx)
    mtlDesc = PluginDesc(pluginName, "BRDFVRayMtl")

    colorSocket = nodeCtx.node.inputs["Color"]
    # fog_color_tex overrides fog_color, so clear it when the socket is unlinked.
    if _isSocketConnected(colorSocket):
        mtlDesc.setAttribute("fog_color", WHITE_COLOR)
        mtlDesc.setAttribute("fog_color_tex", _exportCyclesLinkedSocket(nodeCtx, colorSocket))
    else:
        mtlDesc.setAttribute("fog_color", _getSocketValue(colorSocket, SocketValueType.Color))
        mtlDesc.setAttribute("fog_color_tex", AttrPlugin())
    _exportCyclesFloatAttribute(nodeCtx, mtlDesc, "Roughness", "refract_glossiness")
    _exportCyclesFloatAttribute(nodeCtx, mtlDesc, "IOR", "refract_ior")
    _exportCyclesColorAttribute(nodeCtx, mtlDesc, "Normal", "bump_map")

    # Thin film was added to the Glass BSDF in Blender 5.0 (absent on <=4.5), so guard on the
    # socket's presence. V-Ray blends thin_film_thickness_min..max via the thin_film_thickness
    # tex (0 -> min); Blender has a single thickness, so pin both to it. Mirrors the Principled path.
    if "Thin Film Thickness" in nodeCtx.node.inputs:
        _exportCyclesFloatAttribute(nodeCtx, mtlDesc, "Thin Film IOR", "thin_film_ior")
        thinFilmSocket = nodeCtx.node.inputs["Thin Film Thickness"]
        if _isSocketNonZero(thinFilmSocket):
            _exportCyclesFloatAttribute(nodeCtx, mtlDesc, thinFilmSocket, "thin_film_thickness_min", "thin_film_thickness_max")
            mtlDesc.setAttribute("thin_film_on", True)
        else:
            mtlDesc.setAttribute("thin_film_on", False)

    mtlDesc.setAttribute("fog_mult", 0.0)
    if isGlass:
        mtlDesc.setAttribute("reflect", WHITE_COLOR)
        mtlDesc.setAttribute("fresnel", True)
    else:
        mtlDesc.setAttribute("reflect", BLACK_COLOR)
        mtlDesc.setAttribute("fresnel", False)
    mtlDesc.setAttribute("refract", WHITE_COLOR)
    mtlDesc.setAttribute("reflect_glossiness", 0.0)
    mtlDesc.setAttribute("option_shading_model", 1) # openpbr(just to invert refract glossiness to roughness)
    mtlDesc.setAttribute("refract_affect_shadows", False)
    mtlDesc.setAttribute("roughness_model", 1) # oren-nayar
    mtlDesc.setAttribute("bump_type", 6) # explicit normal
    mtlDesc.setAttribute("diffuse", BLACK_COLOR)
    mtlDesc.setAttribute("gtr_energy_compensation", 2)
    mtlDesc.setAttribute("option_reflect_on_back", True)

    return _exportCyclesPluginWithStats(nodeCtx, mtlDesc)

def _findLayerAlpha(nodeCtx: NodeContext, sockets):
    """ The Principled layer whose Cycles 'Alpha' the BRDFLayered has to carry, or None.

        In Cycles, Alpha mixes that node's closure with a Transparent one, so an alpha cut-out
        stays a cut-out however the shader is combined. A BRDF's own 'opacity' does not do that
        inside BRDFLayered - the stack takes its transparency from 'transparency'/'transparency_tex'
        alone, so an opaque sibling layer (a Translucent leaf card, say) makes the whole card
        opaque and the cut-out renders black instead of showing the background.
    """
    for socket in sockets:
        if not (link := getFarNodeLink(socket)):
            continue
        node = link.from_node
        if node.bl_idname != 'ShaderNodeBsdfPrincipled':
            continue
        alphaSocket = node.inputs["Alpha"]
        if _isSocketConnected(alphaSocket) or \
                _getSocketValue(alphaSocket, SocketValueType.Float) < 1.0:
            return node, alphaSocket
    return None


def _exportCyclesLayerTransparency(nodeCtx: NodeContext, mtlDesc: PluginDesc, node, alphaSocket):
    """ Give the BRDFLayered stack the layer's Cycles Alpha as its transparency.

        The exact composite is alpha*shader + (1 - alpha)*straight-through, which needs a per-layer
        opacity texture; BRDFLayered's 'opacities' is a constant float list, so the stack-wide
        'transparency' is the closest available. Measured 0.98-1.00 of Cycles on alpha cut-outs
        (against 0.44-0.52 with the alpha left on the BRDF alone), the residual being that the
        layer's own opacity still applies inside the opaque fraction.
    """
    if not _isSocketConnected(alphaSocket):
        mtlDesc.setAttribute("transparency", Color((1.0 - _getSocketValue(alphaSocket, SocketValueType.Float),) * 3))
        return

    with nodeCtx.push(node):
        alpha = _exportCyclesLinkedSocket(nodeCtx, alphaSocket)
    if not isinstance(alpha, AttrPlugin):
        return

    invDesc = PluginDesc(Names.nextVirtualNode(nodeCtx, "TexFloatToColor"), "TexFloatToColor")
    invDesc.setAttribute("input", alpha)
    invDesc.setAttribute("invert", True)
    mtlDesc.setAttribute("transparency_tex", _exportCyclesPluginWithStats(nodeCtx, invDesc))


def _exportCyclesBlendShaderNode(nodeCtx: NodeContext, isMix: bool):
    pluginName = Names.treeNode(nodeCtx)
    mtlDesc = PluginDesc(pluginName, "BRDFLayered")

    layerAlpha = _findLayerAlpha(nodeCtx, (nodeCtx.node.inputs["Shader"], nodeCtx.node.inputs["Shader_001"]))
    return _exportCyclesBlendShaderNodeImpl(nodeCtx, isMix, mtlDesc, layerAlpha)


def _exportCyclesBlendShaderNodeImpl(nodeCtx: NodeContext, isMix: bool, mtlDesc: PluginDesc, layerAlpha):
    brdfs, weights = [], []

    baseLayer = nodeCtx.node.inputs["Shader"]
    if _isSocketConnected(baseLayer):
        material = exportLinkedSocket(nodeCtx, baseLayer)
        if not material or not isinstance(material, AttrPlugin):
            material = MtlExporter.exportDefaultMaterial(nodeCtx.exporterCtx)
        brdfs.append(material)
        weight = wrapAsTexture(nodeCtx, WHITE_COLOR)
        weights.append(weight)

    layer = nodeCtx.node.inputs["Shader_001"]
    if _isSocketConnected(layer):
        material = exportLinkedSocket(nodeCtx, layer)
        if not material or not isinstance(material, AttrPlugin):
            material = MtlExporter.exportDefaultMaterial(nodeCtx.exporterCtx)
        brdfs.append(material)
        if isMix:
            factorSocket = nodeCtx.node.inputs["Fac"]
            if _isSocketConnected(factorSocket):
                factorPlugin = _exportCyclesLinkedSocket(nodeCtx, factorSocket)
                texFloatToColor = _wrapFloatToColor(nodeCtx, factorPlugin)
                weights.append(texFloatToColor)
            else:
                weight = wrapAsTexture(nodeCtx, _getSocketValue(factorSocket, SocketValueType.Color))
                weights.append(weight)
        else:
            weights.append(wrapAsTexture(nodeCtx, WHITE_COLOR))

    mtlDesc.setAttribute("brdfs", list(reversed(brdfs)))
    mtlDesc.setAttribute("weights", list(reversed(weights)))
    mtlDesc.setAttribute("additive_mode", 1 if not isMix else 0)

    if layerAlpha:
        _exportCyclesLayerTransparency(nodeCtx, mtlDesc, *layerAlpha)

    return _exportCyclesPluginWithStats(nodeCtx, mtlDesc)

def _exportCyclesCheckerTexture(nodeCtx: NodeContext, nodeLink: FarNodeLink):
    # The blender checker node is very very very weird...
    # 'Color' and 'Fac' fill the colour slots differently.
    pluginName = _outputScopedName(nodeCtx, nodeLink.from_socket.identifier)
    texDesc = PluginDesc(pluginName, "TexChecker")

    if nodeLink.from_socket.identifier == "Color":
        _exportCyclesColorAttribute(nodeCtx, texDesc, "Color1", "black_color")
        _exportCyclesColorAttribute(nodeCtx, texDesc, "Color2", "white_color")
    elif nodeLink.from_socket.identifier == "Fac":
        texDesc.setAttribute("white_color", BLACK_COLOR)
        texDesc.setAttribute("black_color", WHITE_COLOR)

    scaleSocket = nodeCtx.node.inputs['Scale']
    if _isSocketConnected(scaleSocket):
        nodeCtx.registerError("V-Ray does not support textured scale of Checker texture")

    scale = _getSocketValue(scaleSocket, SocketValueType.Float) / 2.0
    smat = Matrix.Scale(scale, 4, Vector((1, 0, 0))) @ Matrix.Scale(scale, 4, Vector((0, 1, 0))) @ Matrix.Scale(scale, 4, Vector((0, 0, 1)))
    vectorSocket = nodeCtx.node.inputs["Vector"]
    if _isSocketConnected(vectorSocket):
        nodeCtx.pushUVWTransform(smat)
        _exportCyclesVectorAttribute(nodeCtx, texDesc, "Vector")
        nodeCtx.popUVWTransform()
    else:
        uvwgenProjectionName = Names.nextVirtualNode(nodeCtx, "UVWGenProjection")
        uvwgenProjectionDesc = PluginDesc(uvwgenProjectionName, "UVWGenProjection")
        uvwgenProjectionDesc.setAttribute("uvw_transform", smat)
        uvwgenProjectionDesc.setAttribute("type", 6) # triplanar
        uvwgen = _exportCyclesPluginWithStats(nodeCtx, uvwgenProjectionDesc)
        texDesc.setAttribute("uvwgen", uvwgen)

    checkerPlugin = _exportCyclesPluginWithStats(nodeCtx, texDesc)
    if nodeLink.from_socket.identifier == "Fac":
        checkerPlugin.output = "out_intensity"
    return checkerPlugin

def _exportCyclesNormalMap(nodeCtx: NodeContext):
    node: bpy.types.ShaderNodeNormalMap = nodeCtx.node
    pluginName = Names.treeNode(nodeCtx)
    texDesc = PluginDesc(pluginName, "TexNormalBump")

    match (node.space):
        case 'TANGENT':
            texDesc.setAttribute("map_type", 1) # normal in tangent space
        case 'OBJECT' | 'BLENDER_OBJECT':
            texDesc.setAttribute("map_type", 2) # normal in object space
        case 'WORLD' | 'BLENDER_WORLD':
            texDesc.setAttribute("map_type", 4) # normal in world space

    colorValue = _exportCyclesWrappedAttribute(nodeCtx, "Color")
    texDesc.setAttribute("bump_tex_color", colorValue)
    # texDesc.setAttribute("normal_uvwgen_auto", True)
    texDesc.setAttribute("blue2Z_mapping_method", 1)

    # TODO: need to wrap this so it works on GPU?
    _exportCyclesFloatAttribute(nodeCtx, texDesc, "Strength", "bump_tex_mult_tex")

    return _exportCyclesPluginWithStats(nodeCtx, texDesc)

def _exportCyclesMathNode(nodeCtx: NodeContext):
    node: bpy.types.ShaderNodeMath = nodeCtx.node
    op = node.operation
    if op in ['SIGN', 'SMOOTH_MIN', 'SMOOTH_MAX', 'FLOORED_MODULO', 'WRAP', 'SNAP', 'PINGPONG', 'SINH', 'COSH', 'TANH']:
        NodeContext.registerError(f"Math node operation {op} is not supported by V-Ray")
        return None

    pluginName = Names.treeNode(nodeCtx)
    if op in ['GREATER_THAN', 'LESS_THAN']:
        texDesc = PluginDesc(pluginName, "TexCondition2")
        _exportCyclesFloatAttribute(nodeCtx, texDesc, "Value", "first_term")
        _exportCyclesFloatAttribute(nodeCtx, texDesc, "Value_001", "second_term")
        texop = 2 if op == 'GREATER_THAN' else 4
        texDesc.setAttribute("operation", texop)
        texDesc.setAttribute("color_if_true", WHITE_COLOR)
        texDesc.setAttribute("color_if_false", BLACK_COLOR)

        attrPlugin = _exportCyclesPluginWithStats(nodeCtx, texDesc, True)
        attrPlugin = _exportIntensityOutput(nodeCtx, attrPlugin)
    elif op == 'LOGARITHM':
        # V-Ray's log output is log(float_a) and drops the base; Cycles is log(a)/log(base).
        logA = PluginDesc(Names.nextVirtualNode(nodeCtx, "TexFloatOp"), "TexFloatOp")
        _exportCyclesFloatAttribute(nodeCtx, logA, "Value", "float_a")
        logA.setAttribute("mode", 13)
        logB = PluginDesc(Names.nextVirtualNode(nodeCtx, "TexFloatOp"), "TexFloatOp")
        _exportCyclesFloatAttribute(nodeCtx, logB, "Value_001", "float_a")
        logB.setAttribute("mode", 13)

        texDesc = PluginDesc(pluginName, "TexFloatOp")
        texDesc.setAttribute("float_a", _exportCyclesPluginWithStats(nodeCtx, logA))
        texDesc.setAttribute("float_b", _exportCyclesPluginWithStats(nodeCtx, logB))
        texDesc.setAttribute("mode", 1)  # ratio
        attrPlugin = _exportCyclesPluginWithStats(nodeCtx, texDesc, True)
    elif op == 'COMPARE':
        # 1 if abs(A-B)<C else 0
        texDifferenceName = Names.nextVirtualNode(nodeCtx, "TexFloatOp")
        texDifferenceDesc = PluginDesc(texDifferenceName, "TexFloatOp")
        texDifferenceDesc.setAttribute("mode", 3) # difference
        _exportCyclesFloatAttribute(nodeCtx, texDifferenceDesc, "Value", "float_a")
        _exportCyclesFloatAttribute(nodeCtx, texDifferenceDesc, "Value_001", "float_b")
        texDifference = _exportCyclesPluginWithStats(nodeCtx, texDifferenceDesc)
        texAbsName = Names.nextVirtualNode(nodeCtx, "TexFloatOp")
        texAbsDesc = PluginDesc(texAbsName, "TexFloatOp")
        texAbsDesc.setAttribute("mode", 9) # abs
        texAbsDesc.setAttribute("float_a", texDifference)
        texAbs = _exportCyclesPluginWithStats(nodeCtx, texAbsDesc)

        texDesc = PluginDesc(pluginName, "TexCondition2")
        _exportCyclesFloatAttribute(nodeCtx, texDesc, "Value_002", "second_term")
        texDesc.setAttribute("first_term", texAbs)
        texDesc.setAttribute("operation", 5) # less than or equals
        texDesc.setAttribute("color_if_true", WHITE_COLOR)
        texDesc.setAttribute("color_if_false", BLACK_COLOR)
        attrPlugin = _exportCyclesPluginWithStats(nodeCtx, texDesc, True)
        attrPlugin = _exportIntensityOutput(nodeCtx, attrPlugin)
    elif op == 'MULTIPLY_ADD':
        # (A*B)+C
        texABName = Names.nextVirtualNode(nodeCtx, "TexFloatOp")
        texABDesc = PluginDesc(texABName, "TexFloatOp")
        _exportCyclesFloatAttribute(nodeCtx, texABDesc, "Value", "float_a")
        _exportCyclesFloatAttribute(nodeCtx, texABDesc, "Value_001", "float_b")
        texABDesc.setAttribute("mode", 0) # product
        texAB = _exportCyclesPluginWithStats(nodeCtx, texABDesc)
        texDesc = PluginDesc(pluginName, "TexFloatOp")
        texDesc.setAttribute("mode", 2) # sum
        texDesc.setAttribute("float_a", texAB)
        _exportCyclesFloatAttribute(nodeCtx, texDesc, "Value_002", "float_b")

        attrPlugin = _exportCyclesPluginWithStats(nodeCtx, texDesc, True)
    else:
        texDesc = PluginDesc(pluginName, "TexFloatOp")

        _exportCyclesFloatAttribute(nodeCtx, texDesc, "Value", "float_a")
        if "Value_001" in nodeCtx.node.inputs:
            _exportCyclesFloatAttribute(nodeCtx, texDesc, "Value_001", "float_b")

        # Note that using the outpus is slightly faster than using the parameter but it's currently not trivial to do so.
        mode = 0
        match op:
            case 'ADD' | 'MULTIPLY_ADD': mode = 2
            case 'SUBTRACT': mode = 3
            case 'MULTIPLY': mode = 0
            case 'DIVIDE': mode = 1
            case 'POWER': mode = 4
            case 'SQRT': mode = 15
            case 'INVERSE_SQRT':
                # a^-0.5; TexInvertFloat is the complement, not the reciprocal.
                mode = 4
                texDesc.setAttribute("float_b", -0.5)
            case 'ABSOLUTE': mode = 9
            case 'EXPONENT': mode = 11
            case 'MINIMUM': mode = 7
            case 'MAXIMUM': mode = 8
            case 'FLOOR': mode = 12
            case 'CEIL': mode = 10
            case 'ROUND':
                # Mode 10 is ceil; Cycles' round is floor(a + 0.5).
                mode = 12
                offsetDesc = PluginDesc(Names.nextVirtualNode(nodeCtx, "TexFloatOp"), "TexFloatOp")
                _exportCyclesFloatAttribute(nodeCtx, offsetDesc, "Value", "float_a")
                offsetDesc.setAttribute("float_b", 0.5)
                offsetDesc.setAttribute("mode", 2)  # sum
                texDesc.setAttribute("float_a", _exportCyclesPluginWithStats(nodeCtx, offsetDesc))
            case 'TRUNC':
                # No truncate mode; a - fmod(a, 1) rounds towards zero for both signs, floor does not.
                mode = 3  # difference
                modDesc = PluginDesc(Names.nextVirtualNode(nodeCtx, "TexFloatOp"), "TexFloatOp")
                _exportCyclesFloatAttribute(nodeCtx, modDesc, "Value", "float_a")
                modDesc.setAttribute("float_b", 1.0)
                modDesc.setAttribute("mode", 16)  # fmod
                texDesc.setAttribute("float_b", _exportCyclesPluginWithStats(nodeCtx, modDesc))
            # These five are f(float_a * float_b) against Cycles' single operand. Unset is not
            # the same as 1: the CPU substitutes 1.0 for a NULL operand, the GPU reads 0.
            case 'SINE' | 'COSINE' | 'ARCSINE' | 'ARCCOSINE' | 'ARCTANGENT':
                mode = {'SINE': 5, 'COSINE': 6, 'ARCSINE': 19,
                        'ARCCOSINE': 20, 'ARCTANGENT': 21}[op]
                texDesc.setAttribute("float_b", 1.0)
            case 'TANGENT': mode = 18
            case 'ARCTAN2':
                # V-Ray's is atan2(float_b, float_a); Cycles' is atan2(a, b).
                mode = 22
                _exportCyclesFloatAttribute(nodeCtx, texDesc, "Value", "float_b")
                _exportCyclesFloatAttribute(nodeCtx, texDesc, "Value_001", "float_a")
            case 'MODULO': mode = 16

            case 'FRACT':
                # Cycles' fraction is a - floor(a); fmod(a, 1) keeps the sign of a.
                mode = 3  # difference
                floorDesc = PluginDesc(Names.nextVirtualNode(nodeCtx, "TexFloatOp"), "TexFloatOp")
                _exportCyclesFloatAttribute(nodeCtx, floorDesc, "Value", "float_a")
                floorDesc.setAttribute("mode", 12)  # floor
                texDesc.setAttribute("float_b", _exportCyclesPluginWithStats(nodeCtx, floorDesc))
            case 'RADIANS':
                mode = 0
                DEG_TO_RAD = math.pi / 180.0
                texDesc.setAttribute("float_b", DEG_TO_RAD)
            case 'DEGREES':
                mode = 0
                RAD_TO_DEG = 180.0 / math.pi
                texDesc.setAttribute("float_b", RAD_TO_DEG)

        texDesc.setAttribute("mode", mode)

        attrPlugin = _exportCyclesPluginWithStats(nodeCtx, texDesc, True)

    if node.use_clamp:
        return _wrapClampPlugin01(nodeCtx, attrPlugin)

    return attrPlugin

def _exportCyclesRGBNode(nodeCtx: NodeContext):
    # Export a texture instead of returning the color directly to preserve material graph
    pluginName = Names.treeNode(nodeCtx)
    texDesc = PluginDesc(pluginName, "TexAColor")

    texDesc.setAttribute("texture", _getSocketValue(nodeCtx.node.outputs[0], SocketValueType.Color))

    return _exportCyclesPluginWithStats(nodeCtx, texDesc)

def _exportCyclesRGBToBwNode(nodeCtx: NodeContext):
    pluginName = Names.treeNode(nodeCtx)
    # V-Ray TexLuminance is a bit different than the one in blender
    texDesc = PluginDesc(pluginName, "TexLuminance")

    _exportCyclesColorAttribute(nodeCtx, texDesc, "Color", "input")

    return _exportCyclesPluginWithStats(nodeCtx, texDesc)

def _exportCyclesInvertNode(nodeCtx: NodeContext):
    pluginName = Names.treeNode(nodeCtx)
    texDesc = PluginDesc(pluginName, "TexInvert")

    colorSocket = nodeCtx.node.inputs["Color"]
    if _isSocketConnected(colorSocket):
        inputColor = _exportCyclesLinkedSocket(nodeCtx, colorSocket)
    else:
        inputColor = _getSocketValue(colorSocket, SocketValueType.Color)
    texDesc.setAttribute("texture", inputColor)
    texInvert = _exportCyclesPluginWithStats(nodeCtx, texDesc)
    return _wrapColorFactorSocket(nodeCtx, nodeCtx.node.inputs["Fac"], inputColor, texInvert)

# From shader_nodes.cpp(TextureMapping::compute_transform(...))
def _computeMappingMatrix(translation, rotation, scale, type):
    smat = Matrix.Scale(scale.x, 4, Vector((1, 0, 0))) @ \
           Matrix.Scale(scale.y, 4, Vector((0, 1, 0))) @ \
           Matrix.Scale(scale.z, 4, Vector((0, 0, 1)))
    rmat = Euler(rotation).to_matrix().to_4x4()
    tmat = Matrix.Translation(translation)

    if type == 'TEXTURE':
        # Inverted point(moving the texture itself instead of moving the placement of the texture on the object)
        mat = (tmat @ rmat @ smat).inverted()
    elif type == 'POINT':
        # This is a basic uvw transformation
        mat = tmat @ rmat @ smat
    elif type == 'VECTOR':
        # Used for direction transforming vectors, point transformation without the translation
        mat = rmat @ smat
    elif type == 'NORMAL':
        # TODO: This seems a bit different...
        # Used for transforming normals, keeps things orthogonal
        mat = (rmat @ smat).inverted().transposed()
    else:
        mat = Matrix.Identity(4)

    return mat

def _computeImageMappingTransform(mapping: bpy.types.TexMapping):
    mmat = Matrix.Scale(0.0, 4)

    type = mapping.vector_type

    def textureMappingAxisToRow(mapping):
        match mapping:
            case 'X': return 0
            case 'Y': return 1
            case 'Z': return 2

    if mapping.mapping_x != 'NONE':
        mmat[0][textureMappingAxisToRow(mapping.mapping_x)] = 1.0
    if mapping.mapping_y != 'NONE':
        mmat[1][textureMappingAxisToRow(mapping.mapping_y)] = 1.0
    if mapping.mapping_z != 'NONE':
        mmat[2][textureMappingAxisToRow(mapping.mapping_z)] = 1.0

    scale_clamped = mapping.scale.copy()

    # Make sure the matrix is invertible
    if type in ('TEXTURE', 'NORMAL'):
        if abs(scale_clamped.x) < 1e-5:
            scale_clamped.x = np.sign(scale_clamped.x) * 1e-5
        if abs(scale_clamped.y) < 1e-5:
            scale_clamped.y = np.sign(scale_clamped.y) * 1e-5
        if abs(scale_clamped.z) < 1e-5:
            scale_clamped.z = np.sign(scale_clamped.z) * 1e-5

    mat = _computeMappingMatrix(mapping.translation, mapping.rotation, scale_clamped, type)

    return mat @ mmat

# Cycles' equirectangular u = (pi - atan2(y,x))/2pi against UVWGenEnvironment's
# u = 1 - atan2(y,x)/2pi leaves a half turn about Z. The poles already agree.
ENV_SPHERICAL_FIXUP = Matrix.Rotation(math.pi, 4, 'Z')


def _envMappingTransform(nodeCtx: NodeContext):
    """ Transform of a Mapping node on an Environment Texture's Vector input. UVWGenEnvironment
        builds its coordinates from the ray direction, so the Mapping is collapsed into the
        environment transform instead of being walked into as a UVW generator. """
    vectorSocket = nodeCtx.node.inputs["Vector"]
    if not _isSocketConnected(vectorSocket):
        return Matrix()

    farLink = getFarNodeLink(vectorSocket)
    mappingNode = farLink.from_node if farLink else None
    if mappingNode is None or mappingNode.bl_idname == 'ShaderNodeTexCoord':
        # A bare Texture Coordinate is the lookup the environment already uses.
        return Matrix()
    if mappingNode.bl_idname != 'ShaderNodeMapping':
        NodeContext.registerError("Environment Texture: only a Mapping node is supported on the Vector input")
        return Matrix()

    location = mappingNode.inputs["Location"] if "Location" in mappingNode.inputs else None
    rotation = mappingNode.inputs["Rotation"]
    scale = mappingNode.inputs["Scale"]
    if (location is not None and _isSocketConnected(location)) \
            or _isSocketConnected(rotation) or _isSocketConnected(scale):
        NodeContext.registerError("V-Ray does not support linked Mapping node sockets")

    return _computeMappingMatrix(
        Vector(_getSocketValue(location, SocketValueType.Color)) if location else Vector(),
        Vector(_getSocketValue(rotation, SocketValueType.Color)),
        Vector(_getSocketValue(scale, SocketValueType.Color)),
        mappingNode.vector_type)


def _exportCyclesImageNode(nodeCtx: NodeContext, nodeLink: FarNodeLink, isEnvironment = False):
    node: bpy.types.ShaderNodeTexImage = nodeCtx.node
    pluginName = Names.treeNode(nodeCtx)
    texBitmapDesc = PluginDesc(pluginName, "TexBitmap")
    bitmapBufferName = Names.nextVirtualNode(nodeCtx, "BitmapBuffer")
    bitmapBufferDesc = PluginDesc(bitmapBufferName, "BitmapBuffer")
    # TODO: Images in scenes?
    image: bpy.types.Image = node.image
    if image is not None:
        imagePath = image_utils.getTrackedImagePath(image)

        colorSpace = image.colorspace_settings.name
        match colorSpace:
            case 'ACEScg':
                vrayTransferFunc = 3 # auto
                vrayColorSpace = 'acescg'
            case 'sRGB':
                vrayTransferFunc = 2 # srgb
                vrayColorSpace = 'lin_srgb'
            case 'Non-Color':
                vrayTransferFunc = 0 # linear
                vrayColorSpace = 'raw'
            case 'Linear Rec.709':
                vrayTransferFunc = 0 # linear
                vrayColorSpace = 'lin_srgb'
            case _:
                vrayTransferFunc = 2 # srgb
                vrayColorSpace = 'lin_srgb'
                NodeContext.registerError(f"Image color space {colorSpace} is not supported. Default sRGB will be used.")
        if image.colorspace_settings.is_data:
            vrayTransferFunc = 0 # linear
            vrayColorSpace = 'raw'
            bitmapBufferDesc.setAttribute("allow_negative_colors", True)
        else:
            bitmapBufferDesc.setAttribute("allow_negative_colors", False)
        bitmapBufferDesc.setAttribute("transfer_function", vrayTransferFunc)
        bitmapBufferDesc.setAttribute("rgb_color_space", vrayColorSpace)

        if image.alpha_mode == "NONE":
            texBitmapDesc.setAttribute("alpha_from_intensity", 2) # opaque
        elif image.alpha_mode == "STRAIGHT":
            texBitmapDesc.setAttribute("alpha_from_intensity", 0) # bitmap alpha
        else:
            texBitmapDesc.setAttribute("alpha_from_intensity", 0) # bitmap alpha
            NodeContext.registerError(f"Bitmap alpha mode {image.alpha_mode} is not fully supported")
    else:
        imagePath = ""

    if imagePath and image_utils.imageUpdated(image):
        # Reset the file attribute to force reload of the image file.
        # Note: Does not work in GPU mode.
        # [GPU_BROKEN_MODIFIED_IMAGES_RELOAD_IPR]
        updateValue(nodeCtx.exporterCtx.renderer, bitmapBufferName, 'file', AttrPlugin(forceUpdate=True))
    
    bitmapBufferDesc.setAttribute("file", imagePath)

    # A sequence pins a per-frame frame_number; register the material so it re-exports each frame.
    if image is not None and image_utils.applySequenceFrameAttrs(image, node.image_user, nodeCtx.exporterCtx.currentFrame, bitmapBufferDesc):
        nodeCtx.exporterCtx.registerAnimatedBitmapMaterial(nodeCtx.material)

    match node.interpolation:
        case 'Linear':
            vrayInterpolation = 0 # bilinear
            vrayFilterType = 1 # mip map filtering
        case 'Closest':
            vrayInterpolation = 0 # mip map filtering
            vrayFilterType = -1 # nearest
        case 'Cubic':
            vrayInterpolation = 1 # cubic
            vrayFilterType = 1 # mip map filtering
        case 'Smart':
            # Here blender will use bicubic when "zooming in" on the image and bilinear otherwise.
            # Just use biqadratic which will use 9 samples.
            vrayInterpolation = 2 # biquadratic
            vrayFilterType = 1 # mip map filtering
        case _:
            vrayInterpolation = 0 # bilinear
            vrayFilterType = 1 # mip map filtering

    bitmapBufferDesc.setAttribute("filter_type", vrayFilterType)
    bitmapBufferDesc.setAttribute("interpolation", vrayInterpolation)
    bitmapBuffer = _exportCyclesPluginWithStats(nodeCtx, bitmapBufferDesc)

    texBitmapDesc.setAttribute("bitmap", bitmapBuffer)
    texBitmapDesc.setAttribute("default_color", INVALID_COLOR)

    uvwMatrix = _computeImageMappingTransform(node.texture_mapping)
    nodeCtx.pushUVWTransform(uvwMatrix)

    # TODO: projection, repeat/mirror
    if isEnvironment:
        uvwgenName = Names.nextVirtualNode(nodeCtx, "UVWGenEnvironment")
        uvwgenDesc = PluginDesc(uvwgenName, "UVWGenEnvironment")
        if node.projection=="EQUIRECTANGULAR":
            uvwgenDesc.setAttribute("mapping_type", "spherical")
        elif node.projection=="MIRROR_BALL":
            uvwgenDesc.setAttribute("mapping_type", "mirror_ball")
        # 'uvw_matrix' rotates the environment (it transforms the incoming direction);
        # 'uvw_transform' would only rotate the texture within the mapping. This plugin has no
        # 'tex_transform' - writing one is silently dropped.
        envMatrix = ENV_SPHERICAL_FIXUP @ _envMappingTransform(nodeCtx) @ nodeCtx.getUVWTransform()
        uvwgenDesc.setAttribute("uvw_matrix", envMatrix.to_3x3())
        uvwgen = _exportCyclesPluginWithStats(nodeCtx, uvwgenDesc)
        texBitmapDesc.setAttribute("uvwgen", uvwgen)
    else:
        _exportCyclesVectorAttribute(nodeCtx, texBitmapDesc, "Vector", uvwMatrix)
        if node.projection != 'FLAT':
            NodeContext.registerError("V-Ray only supports Flat projection when a Vector input is connected")
    nodeCtx.popUVWTransform()

    if not isEnvironment and node.extension != "REPEAT":
        NodeContext.registerError("V-Ray only supports Repeat image extension")

    import copy
    bitmapPlugin = _exportCyclesPluginWithStats(nodeCtx, texBitmapDesc)
    bitmapPluginAlphaOutput = copy.copy(bitmapPlugin)
    bitmapPluginAlphaOutput.output = "out_alpha"

    if not isEnvironment and nodeLink.from_socket.name == "Alpha":
        return bitmapPluginAlphaOutput

    # Only STRAIGHT files need RGB premultiplied here. PREMUL already has alpha
    # baked into RGB on disk; multiplying again would darken every pixel.
    if image is None or image.alpha_mode != 'STRAIGHT' \
            or image.colorspace_settings.is_data:
        return bitmapPlugin

    texAColorOpName = Names.nextVirtualNode(nodeCtx, "TexAColorOp")
    texAColorOpDesc = PluginDesc(texAColorOpName, "TexAColorOp")
    texAColorOpDesc.setAttribute("color_a", bitmapPlugin)
    texAColorOpDesc.setAttribute("mult_a", bitmapPluginAlphaOutput)
    return _exportCyclesPluginWithStats(nodeCtx, texAColorOpDesc)

def _exportCyclesUVWMapNode(nodeCtx: NodeContext):
    node: bpy.types.ShaderNodeNormalMap = nodeCtx.node
    pluginName = Names.treeNode(nodeCtx)
    uvwgenDesc = PluginDesc(pluginName, "UVWGenMayaPlace2dTexture")
    uvwgenDesc.setAttribute("uv_set_name", node.uv_map)

    # This is the only UVWGen that picks a UV set by name, and the only one with no 'uvw_transform',
    # so a scale pushed by the consumer (the Checker's Scale, an image's texture mapping, a Mapping
    # node) had nowhere to go and was dropped. repeat_u/repeat_v tile the UV range, which is the same
    # thing as scaling the coordinates. With nothing pending the transform is identity and these are
    # 1.0, the plugin's own default.
    scale = nodeCtx.getUVWTransform().to_scale()
    uvwgenDesc.setAttribute("repeat_u", scale.x)
    uvwgenDesc.setAttribute("repeat_v", scale.y)

    return _exportCyclesPluginWithStats(nodeCtx, uvwgenDesc)

def _exportCyclesCombineColorNode(nodeCtx: NodeContext):
    node: bpy.types.ShaderNodeCombineColor = nodeCtx.node
    pluginName = Names.treeNode(nodeCtx)
    texDesc = PluginDesc(pluginName, "Float3ToAColor")

    if node.mode == "RGB":
        _exportCyclesFloatAttribute(nodeCtx, texDesc, "Red", "float1")
    elif node.mode == 'HSV':
        redSocket = node.inputs["Red"]
        hue = _convertBlenderHSVToVRay(nodeCtx, redSocket)
        texDesc.setAttribute("float1", hue)
    _exportCyclesFloatAttribute(nodeCtx, texDesc, "Green", "float2")
    _exportCyclesFloatAttribute(nodeCtx, texDesc, "Blue", "float3")

    attrPlugin = _exportCyclesPluginWithStats(nodeCtx, texDesc)
    if node.mode == 'RGB':
        return attrPlugin
    elif node.mode == 'HSV':
        hsvToRgbName = Names.nextVirtualNode(nodeCtx, "TexHSVToRGB")
        hsvToRgbDesc = PluginDesc(hsvToRgbName, "TexHSVToRGB")
        hsvToRgbDesc.setAttribute("inHsv", attrPlugin)
        return _exportCyclesPluginWithStats(nodeCtx, hsvToRgbDesc)
    elif node.mode == 'HSL':
        NodeContext.registerError("V-Ray does not support Combine Color nodes in HSL mode")
    return attrPlugin

def _exportCyclesCombineVectorNode(nodeCtx: NodeContext):
    pluginName = Names.treeNode(nodeCtx)
    texDesc = PluginDesc(pluginName, "Float3ToAColor")

    _exportCyclesFloatAttribute(nodeCtx, texDesc, "X", "float1")
    _exportCyclesFloatAttribute(nodeCtx, texDesc, "Y", "float2")
    _exportCyclesFloatAttribute(nodeCtx, texDesc, "Z", "float3")

    return _exportCyclesPluginWithStats(nodeCtx, texDesc)

_RAMP_RESAMPLE_STOPS = 12


def _exportCyclesRampNode(nodeCtx: NodeContext, nodeLink: FarNodeLink):
    node: bpy.types.ShaderNodeValToRGB = nodeCtx.node
    pluginName = Names.treeNode(nodeCtx)
    texDesc = PluginDesc(pluginName, "TexGradRamp")

    positions, colors = [], []

    ramp: bpy.types.ColorRamp = node.color_ramp
    if ramp.interpolation in ('CARDINAL', 'B_SPLINE'):
        # TexGradRamp has no cardinal spline and 'Smooth' misses it where the spline overshoots,
        # so resample into a dense linear ramp instead.
        interpolation = 1
        for i in range(_RAMP_RESAMPLE_STOPS):
            t = i / (_RAMP_RESAMPLE_STOPS - 1)
            positions.append(t)
            colors.append(wrapAsTexture(nodeCtx, toColor(ramp.evaluate(t))))
    else:
        for e in ramp.elements:
            positions.append(e.position)
            colors.append(wrapAsTexture(nodeCtx, toColor(e.color)))

        interpolation = 0
        match ramp.interpolation:
            case 'LINEAR': interpolation = 1
            case 'EASE': interpolation = 4 # smooth, exact match
            case 'CONSTANT': interpolation = 0 # none, exact match

    # 'position' (12) + 'gradient_position' is equivalent on CPU but is not in V-Ray GPU's
    # procedural whitelist, so GPU bakes the ramp into a UV-indexed texture and any non-UV driver
    # (Hair Info, Fresnel, ...) is silently lost. 'mapped' (5) is whitelisted and reads the
    # coordinate from 'texture_map' as (r+g+b)/3 - a colour slot, so the float driver is wrapped.
    facSocket = nodeCtx.node.inputs["Fac"]
    if _isSocketConnected(facSocket):
        texDesc.setAttribute("texture_map",
                             _wrapFloatToColor(nodeCtx, _exportCyclesLinkedSocket(nodeCtx, facSocket)))
    else:
        fac = _clamp01(_getSocketValue(facSocket, SocketValueType.Float))
        texDesc.setAttribute("texture_map", Color((fac, fac, fac)))

    texDesc.setAttribute("interpolation", interpolation)
    texDesc.setAttribute("positions", positions)
    texDesc.setAttribute("colors", colors) # TODO: check if I need wrapper for colors?
    texDesc.setAttribute("gradient_type", 5) # mapped

    #TODO: what is hsl/hsv ramp???
    rampPlugin = _exportCyclesPluginWithStats(nodeCtx, texDesc)
    if nodeLink.from_socket.name == "Alpha":
        rampPlugin.output = "out_alpha"
    return rampPlugin

def _exportCyclesGradientNode(nodeCtx: NodeContext, nodeLink: FarNodeLink):
    node: bpy.types.ShaderNodeTexGradient = nodeCtx.node
    pluginName = Names.treeNode(nodeCtx)
    texDesc = PluginDesc(pluginName, "TexGradRamp")

    blackTex, whiteTex = wrapAsTexture(nodeCtx, BLACK_COLOR), wrapAsTexture(nodeCtx, WHITE_COLOR)
    positions, colors = [0.0, 1.0], [blackTex, whiteTex]

    texDesc.setAttribute("positions", positions)
    texDesc.setAttribute("colors", colors)

    gradientType = 0
    interpolation = 1 # linear, default
    match node.gradient_type:
        case 'LINEAR': gradientType = 4; interpolation = 4
        case 'EASING': gradientType = 4; interpolation = 4
        case 'RADIAL': gradientType = 8
        case 'DIAGONAL': gradientType = 2
        case _:
            NodeContext.registerError(f"V-Ray does not support '{node.gradient_type} gradients")

    texDesc.setAttribute("gradient_type", gradientType)
    texDesc.setAttribute("interpolation", interpolation)
    uvwMatrix = _computeImageMappingTransform(node.texture_mapping)

    vectorSocket = nodeCtx.node.inputs["Vector"]
    if _isSocketConnected(vectorSocket):
        _exportCyclesVectorAttribute(nodeCtx, texDesc, "Vector", uvwMatrix)
    else:
        uvwgen = _exportCyclesGeneratedCoordsUVWGen(nodeCtx, False)
        texDesc.setAttribute("uvwgen", uvwgen)

    rampTex = _exportCyclesPluginWithStats(nodeCtx, texDesc)
    if nodeLink.from_socket.name == "Color":
        return rampTex
    else:
        return _exportIntensityOutput(nodeCtx, rampTex)

def _exportCyclesBlackbodyNode(nodeCtx: NodeContext):
    pluginName = Names.treeNode(nodeCtx)
    texDesc = PluginDesc(pluginName, "TexTemperature")

    _exportCyclesFloatAttribute(nodeCtx, texDesc, "Temperature", "temperature")
    texDesc.setAttribute("color_mode", 1) # from temperature

    return _exportCyclesPluginWithStats(nodeCtx, texDesc)

def _texSpaceTransform(nodeCtx: NodeContext):
    """ Blender's Generated coordinates are the object-space point normalized over the mesh's
        texture space: 0.5*(P - C)/S + 0.5. C and S are per-mesh constants, so the whole
        normalization collapses into a single matrix on the object-space point. """
    obj = nodeCtx.exporterCtx.objectContext.get()
    mesh = obj.data if obj else None
    if (mesh is None) or (not hasattr(mesh, "texspace_location")):
        return Matrix()

    C = mesh.texspace_location
    # Blender keeps texspace_size away from zero and preserves its sign, so no guard is needed.
    S = mesh.texspace_size
    k = (0.5 / S[0], 0.5 / S[1], 0.5 / S[2])

    m = Matrix.Diagonal((k[0], k[1], k[2], 1.0))
    m.translation = Vector((0.5 - k[0] * C[0], 0.5 - k[1] * C[1], 0.5 - k[2] * C[2]))
    return m


def _exportCyclesGeneratedCoordsUVWGen(nodeCtx, fromMappingNode=True, pluginName=None):
    if pluginName is None:
        pluginName = Names.treeNode(nodeCtx) if fromMappingNode else Names.nextVirtualNode(nodeCtx, "UVWGenProjection")
    uvwgenDesc = PluginDesc(pluginName, "UVWGenProjection")
    # 'object' mapping passes the transformed point through unchanged; combined with object_space
    # this yields the object-space point, which uvw_transform normalizes to Generated coordinates.
    uvwgenDesc.setAttribute("type", 15)
    uvwgenDesc.setAttribute("object_space", 1)
    uvwgenDesc.setAttribute("uvw_transform", _texSpaceTransform(nodeCtx))
    uvwgenDesc.setAttribute("tex_transform", nodeCtx.getUVWTransform())
    return _exportCyclesPluginWithStats(nodeCtx, uvwgenDesc, fromMappingNode)

def _exportCyclesTextureCoordinatesNode(nodeCtx: NodeContext, nodeLink: FarNodeLink):
    node: bpy.types.ShaderNodeTexCoord = nodeCtx.node
    fromSocket = nodeLink.from_socket
    # Each branch builds a different UVWGen type.
    if fromSocket.name == "Generated":
        return _exportCyclesGeneratedCoordsUVWGen(nodeCtx, pluginName=_outputScopedName(nodeCtx, fromSocket.name))
    elif fromSocket.name in ["Normal", "Reflection", "Camera"]:
        uvwgenName = _outputScopedName(nodeCtx, fromSocket.name)
        uvwgneDesc = PluginDesc(uvwgenName, "UVWGenExplicit")
        uvwgneDesc.setAttribute("useSeparateParams", 1) # uvw

        samplerPluginName = Names.nextVirtualNode(nodeCtx, "TexSampler")
        samplerDesc = PluginDesc(samplerPluginName, "TexSampler")
        samplerPlugin = _exportCyclesPluginWithStats(nodeCtx, samplerDesc)
        if fromSocket.name == "Normal":
            samplerPlugin.output = "gnormal" # normal/gnormal?
        elif fromSocket.name == "Reflection":
            samplerPlugin.output = "reflection"
        elif fromSocket.name == "Camera":
            samplerPlugin.output = "pointCamera"
        else:
            assert False, "Unsupported output parameter"

        uvwInput = _wrapVectorToColor(nodeCtx, samplerPlugin)

        if len(nodeCtx.transformStack) > 0:
            texName = Names.nextVirtualNode(nodeCtx, "TexVectorProduct")
            texDesc = PluginDesc(texName, "TexVectorProduct")
            texDesc.setAttribute("input1", uvwInput)
            texDesc.setAttribute("transform", nodeCtx.getUVWTransform())
            texDesc.setAttribute("operation", 4) # point matrix prod
            uvwInput =  _exportCyclesPluginWithStats(nodeCtx, texDesc)

        uvwgneDesc.setAttribute("uvw", uvwInput)

        return _exportCyclesPluginWithStats(nodeCtx, uvwgneDesc, True)
    elif fromSocket.name == "UV":
        uvwgenName = _outputScopedName(nodeCtx, fromSocket.name)
        uvwgenDesc = PluginDesc(uvwgenName, "UVWGenChannel")
        uvwgenDesc.setAttribute("uvw_transform", nodeCtx.getUVWTransform())
        uvwgenDesc.setAttribute("uvw_channel", -1)
        return _exportCyclesPluginWithStats(nodeCtx, uvwgenDesc, True)
    elif fromSocket.name in [ "Object", "Window" ]:
        uvwgenName = _outputScopedName(nodeCtx, fromSocket.name)
        uvwgenDesc = PluginDesc(uvwgenName, "UVWGenProjection")
        if fromSocket.name == "Window":
            uvwgenDesc.setAttribute("type", 8) # cam
        elif fromSocket.name == "Object":
            uvwgenDesc.setAttribute("type", 15) # object
        else:
            assert False, "Unsupported output parameter"
        obj = node.object
        if obj is not None:
            uvwgenDesc.setAttribute("object_space", False)
            uvwgenDesc.setAttribute("tex_transform", obj.matrix_world.inverted() @ nodeCtx.getUVWTransform())
        else:
            uvwgenDesc.setAttribute("object_space", True)
            uvwgenDesc.setAttribute("tex_transform", nodeCtx.getUVWTransform())
        return _exportCyclesPluginWithStats(nodeCtx, uvwgenDesc, True)
    else:
        NodeContext.registerError(f"Texture coordinates '{fromSocket.name}' are not supported")
    return None

def _exportCyclesWireframeNode(nodeCtx: NodeContext):
    node: bpy.types.ShaderNodeWireframe = nodeCtx.node
    pluginName = Names.treeNode(nodeCtx)
    texDesc = PluginDesc(pluginName, "TexEdges")

    sizeSocket = node.inputs["Size"]

    def _exportWidthTexture(widthParam, multiplier):
        if _isSocketConnected(sizeSocket):
            texFloatOpDesc = PluginDesc(Names.nextVirtualNode(nodeCtx, "TexFloatOp"), "TexFloatOp")
            _exportCyclesFloatAttribute(nodeCtx, texFloatOpDesc, sizeSocket, "float_a")
            texFloatOpDesc.setAttribute("float_b", multiplier)
            texFloatOp = _exportCyclesPluginWithStats(nodeCtx, texFloatOpDesc)
            texFloatOp.output = "product"
            texDesc.setAttribute(widthParam, texFloatOp)
        else:
            texDesc.setAttribute(widthParam, _getSocketValue(sizeSocket, SocketValueType.Float) * multiplier)

    if node.use_pixel_size:
        # It seems we need to double the width in pixels mode, however, blender's node seems to
        # work directly in screen space while V-Ray does not.
        texDesc.setAttribute("width_type", 1) # pixels
        _exportWidthTexture("pixel_width", 2)
    else:
        # And it seems world width does the opposite to pixel width and we need half width....
        texDesc.setAttribute("width_type", 0) # world
        _exportWidthTexture("world_width", 0.5)
    texDesc.setAttribute("show_subtriangles", False)
    texDesc.setAttribute("show_hidden_edges", True)

    plugin = _exportCyclesPluginWithStats(nodeCtx, texDesc)
    return _exportIntensityOutput(nodeCtx, plugin)


def _detectSimpleSpline(curve: bpy.types.CurveMap):
    """ Return True if the spline has only 1 points - at (0,0) and at (1,1) """
    points = curve.points
    def vector2Approx(point: Vector, v: float):
        return math.isclose(point.x, v) and math.isclose(point.y, v)

    if len(points) != 2:
        return False
    if vector2Approx(points[0].location, 0.0) and vector2Approx(points[1].location, 1.0):
        return True
    return False


_CURVE_RESAMPLE_STEPS = 12


def _splineData(curveMapping: bpy.types.CurveMapping, curve: bpy.types.CurveMap):
    """ Sample a Blender curve into a dense linear TexRemap spline.

        Handing V-Ray the control points instead makes it re-solve the spline with its own
        handles, which does not reproduce Blender's auto-clamped bezier (measured 8% off on a
        single lifted point). 12 steps measured the same as 32; the two samples outside [0, 1] carry
        the mapping's 'extend' behaviour, which evaluate() already applies.
    """
    try:
        positions, values = [], []
        for i in range(-1, _CURVE_RESAMPLE_STEPS + 2):
            t = (-1.0 if i < 0 else 2.0 if i > _CURVE_RESAMPLE_STEPS else i / _CURVE_RESAMPLE_STEPS)
            positions.append(t)
            values.append(curveMapping.evaluate(curve, t))
        return positions, values, [1] * len(positions)   # 1 = linear
    except (RuntimeError, AttributeError):
        # evaluate() needs an up-to-date curve mapping; calling update() here would tag a
        # depsgraph change mid-export, so fall back to the raw control points.
        return fillSplineData(curve, curveMapping.extend)


def _exportSplineColorData(seperateChannelSplineDesc: PluginDesc, curveMapping: bpy.types.CurveMapping):
    rPoints, rValues, rTypes = _splineData(curveMapping, curveMapping.curves[0])
    seperateChannelSplineDesc.setAttribute("red_positions", rPoints)
    seperateChannelSplineDesc.setAttribute("red_values", rValues)
    seperateChannelSplineDesc.setAttribute("red_types", rTypes)

    gPoints, gValues, gTypes = _splineData(curveMapping, curveMapping.curves[1])
    seperateChannelSplineDesc.setAttribute("green_positions", gPoints)
    seperateChannelSplineDesc.setAttribute("green_values", gValues)
    seperateChannelSplineDesc.setAttribute("green_types", gTypes)

    bPoints, bValues, bTypes = _splineData(curveMapping, curveMapping.curves[2])
    seperateChannelSplineDesc.setAttribute("blue_positions", bPoints)
    seperateChannelSplineDesc.setAttribute("blue_values", bValues)
    seperateChannelSplineDesc.setAttribute("blue_types", bTypes)

def _exportCyclesCurvesNode(nodeCtx: NodeContext, isColor: bool):
    node: bpy.types.ShaderNodeRGBCurve = nodeCtx.node
    # Here we might export 2 TexRemaps - one for the full Color spline and one for the seperate channels ramps
    curveMapping: bpy.types.CurveMapping = node.mapping
    colorSocket = node.inputs["Color"] if isColor else nodeCtx.node.inputs["Vector"]
    input = None
    if _isSocketConnected(colorSocket):
        input: AttrPlugin = _exportCyclesLinkedSocket(nodeCtx, colorSocket)
    else:
        input = _getSocketValue(colorSocket, SocketValueType.Color)

    if isColor:
        rgbCurve = curveMapping.curves[3]
        if not _detectSimpleSpline(rgbCurve):
            rgbSplinePluginName = Names.treeNode(nodeCtx)
            rgbSplineDesc = PluginDesc(rgbSplinePluginName, "TexRemap")

            rgbSplineDesc.setAttribute("input_color", input)
            rgbSplineDesc.setAttribute("type", 1) # remap color

            positions, values, types = _splineData(curveMapping, rgbCurve)

            for param in ["red_positions", "blue_positions", "green_positions"]:
                rgbSplineDesc.setAttribute(param, positions)
            for param in ["red_values", "blue_values", "green_values"]:
                rgbSplineDesc.setAttribute(param, values)
            for param in ["red_types", "blue_types", "green_types"]:
                rgbSplineDesc.setAttribute(param, types)

            input = _exportCyclesPluginWithStats(nodeCtx, rgbSplineDesc, True)
    else:
        if isinstance(input, AttrPlugin) and input.pluginType.startswith("UVWGen"):
            input = _exportUVWToColor(nodeCtx, input)

    simpleSpline = _detectSimpleSpline(curveMapping.curves[0]) and _detectSimpleSpline(curveMapping.curves[1]) and _detectSimpleSpline(curveMapping.curves[2])
    if simpleSpline:
        return input

    seperateChannelSplineName = Names.nextVirtualNode(nodeCtx, "TexRemap") if isColor else Names.treeNode(nodeCtx)
    seperateChannelSplineDesc = PluginDesc(seperateChannelSplineName, "TexRemap")

    _exportSplineColorData(seperateChannelSplineDesc, curveMapping)

    seperateChannelSplineDesc.setAttribute("input_color", input)
    seperateChannelSplineDesc.setAttribute("type", 1) # remap color

    return _exportCyclesPluginWithStats(nodeCtx, seperateChannelSplineDesc, True)

def _exportCyclesFloatCurveNode(nodeCtx: NodeContext):
    node: bpy.types.ShaderNodeFloatCurve = nodeCtx.node
    curveMapping: bpy.types.CurveMapping = node.mapping
    curve = curveMapping.curves[0]

    valueSocket = node.inputs["Value"]
    value = _exportCyclesLinkedSocket(nodeCtx, valueSocket) if _isSocketConnected(valueSocket) else _getSocketValue(valueSocket, SocketValueType.Float)

    if _detectSimpleSpline(curve):
        return value

    remapDesc = PluginDesc(Names.treeNode(nodeCtx), "TexRemap")
    remapDesc.setAttribute("type", 0) # remap value
    remapDesc.setAttribute("input_value", value)

    positions, values, types = _splineData(curveMapping, curve)
    remapDesc.setAttribute("float_positions", positions)
    remapDesc.setAttribute("float_values", values)
    remapDesc.setAttribute("float_types", types)

    remapped = _exportCyclesPluginWithStats(nodeCtx, remapDesc, True)
    remapped.output = "out_value"

    factorSocket = node.inputs["Factor"]
    if not _isSocketConnected(factorSocket) and math.isclose(_getSocketValue(factorSocket, SocketValueType.Float), 1.0):
        return remapped

    # result = value + Factor * (curve(value) - value)
    factor = _exportCyclesLinkedSocket(nodeCtx, factorSocket) if _isSocketConnected(factorSocket) else _getSocketValue(factorSocket, SocketValueType.Float)
    delta = _texFloatOp(nodeCtx, 3, remapped, value)
    return _texFloatOp(nodeCtx, 2, value, _texFloatOp(nodeCtx, 0, factor, delta))


def _exportCyclesValueNode(nodeCtx: NodeContext):
    pluginName = Names.treeNode(nodeCtx)
    texDesc = PluginDesc(pluginName, "FloatToTex")

    texDesc.setAttribute("input", _getSocketValue(nodeCtx.node.outputs[0], SocketValueType.Float))

    return _exportCyclesPluginWithStats(nodeCtx, texDesc)

def _exportCyclesMappingNode(nodeCtx: NodeContext):
    node: bpy.types.ShaderNodeMapping = nodeCtx.node

    location = node.inputs["Location"] if "Location" in node.inputs else None
    rotation = node.inputs["Rotation"]
    scale = node.inputs["Scale"]
    if (location is not None and _isSocketConnected(location)) or _isSocketConnected(rotation) or _isSocketConnected(scale):
        NodeContext.registerError("V-Ray does not support linked Mapping node sockets")

    transform = _computeMappingMatrix(
        Vector(_getSocketValue(location, SocketValueType.Color)) if location else Vector(),
        Vector(_getSocketValue(rotation, SocketValueType.Color)),
        Vector(_getSocketValue(scale, SocketValueType.Color)),
        node.vector_type
    )

    vectorSocket: bpy.types.NodeSocketVector = node.inputs["Vector"]
    if _isSocketConnected(vectorSocket):
        nodeCtx.pushUVWTransform(transform)
        sockValue = _exportCyclesLinkedSocket(nodeCtx, vectorSocket)
        nodeCtx.popUVWTransform()
        if isinstance(sockValue, AttrPlugin) and sockValue.pluginType.startswith("UVWGen"):
            return sockValue
    else:
        return Vector(_getSocketValue(vectorSocket, SocketValueType.Color)) @ transform

    # This only does the math for colors here, no uvwgens
    texName = Names.treeNode(nodeCtx)
    texDesc = PluginDesc(texName, "TexVectorProduct")
    texDesc.setAttribute("input1", sockValue)
    texDesc.setAttribute("transform", transform)
    texDesc.setAttribute("operation", 4) # point matrix prod
    return _exportCyclesPluginWithStats(nodeCtx, texDesc)

def _exportCyclesAttributeNodeHelper(nodeCtx: NodeContext, nodeLink: FarNodeLink, attribute: str, preferUserAttribute=False):
    fromSocket = nodeLink.from_socket
    pluginName = Names.treeNode(nodeCtx)
    texDesc = PluginDesc(pluginName, "TexUserColor")

    texDesc.setAttribute("user_attribute", attribute)
    if preferUserAttribute:
        # Instancer/object attributes are exported as V-Ray user attributes; don't let a
        # same-named mapping channel shadow them.
        texDesc.setAttribute("attribute_priority", 1)
    texUserColor = _exportCyclesPluginWithStats(nodeCtx, texDesc, True)
    if fromSocket.name != "Alpha":
        return texUserColor
    else:
        texChannelName = Names.nextVirtualNode(nodeCtx, "TexAColorOp")
        texChannelDesc = PluginDesc(texChannelName, "TexAColorOp")
        texChannelDesc.setAttribute("color_a", texUserColor)
        texAColorOp = _exportCyclesPluginWithStats(nodeCtx, texChannelDesc)
        texAColorOp.output = "alpha"
        return texAColorOp

def _exportCyclesAttributeNode(nodeCtx: NodeContext, nodeLink: FarNodeLink):
    node: bpy.types.ShaderNodeAttribute = nodeCtx.node
    fromSocket = nodeLink.from_socket
    preferUserAttribute = node.attribute_type in ('INSTANCER', 'OBJECT')
    if fromSocket.identifier in ("Color", "Vector", "Alpha"):
        return _exportCyclesAttributeNodeHelper(nodeCtx, nodeLink, node.attribute_name, preferUserAttribute)
    elif fromSocket.identifier == "Fac":
        pluginName = Names.treeNode(nodeCtx)
        texDesc = PluginDesc(pluginName, "TexUserColor")

        texDesc.setAttribute("user_attribute", node.attribute_name)
        if preferUserAttribute:
            texDesc.setAttribute("attribute_priority", 1)
        texUserColor = _exportCyclesPluginWithStats(nodeCtx, texDesc, True)

        texChannelName = Names.nextVirtualNode(nodeCtx, "TexAColorOp")
        texChannelDesc = PluginDesc(texChannelName, "TexAColorOp")
        texChannelDesc.setAttribute("color_a", texUserColor)
        texAColorOp = _exportCyclesPluginWithStats(nodeCtx, texChannelDesc)
        texAColorOp.output = "intensity"
        return texAColorOp

def _exportCyclesColorAttributeNode(nodeCtx: NodeContext, nodeLink: FarNodeLink):
    node: bpy.types.ShaderNodeVertexColor = nodeCtx.node
    return _exportCyclesAttributeNodeHelper(nodeCtx, nodeLink, node.layer_name)

def _exportCyclesNormalNode(nodeCtx: NodeContext, nodeLink: FarNodeLink):
    fromSocket = nodeLink.from_socket
    # 'Normal' and 'Dot' use a different 'operation'.
    pluginName = _outputScopedName(nodeCtx, fromSocket.name)
    texDesc = PluginDesc(pluginName, "TexVectorProduct")

    texDesc.setAttribute("input1", _getSocketValue(nodeCtx.node.outputs["Normal"], SocketValueType.Color))
    texDesc.setAttribute("normalize", True)

    if fromSocket.name == "Normal":
        texDesc.setAttribute("operation", 0) # None
    elif fromSocket.name == "Dot":
        _exportCyclesColorAttribute(nodeCtx, texDesc, "Normal", "input2")
        texDesc.setAttribute("operation", 1) # Dot product
        plugin = _exportCyclesPluginWithStats(nodeCtx, texDesc)
        return _exportIntensityOutput(nodeCtx, plugin)
    return _exportCyclesPluginWithStats(nodeCtx, texDesc)

def _exportCyclesCameraDataNode(nodeCtx: NodeContext, nodeLink: FarNodeLink):
    fromSocket = nodeLink.from_socket
    pluginName = Names.treeNode(nodeCtx)
    texDesc = PluginDesc(pluginName, "TexSampler")
    texSampler = _exportCyclesPluginWithStats(nodeCtx, texDesc)
    # This is not exactly right, the Z coordiante here is different
    texSampler.output = "pointCamera"
    viewDir = _wrapVectorToColor(nodeCtx, texSampler)

    # Export TexVectortProduct to normalize the view vector
    texVectorProductName = Names.nextVirtualNode(nodeCtx, "TexVectorProduct")
    texVectorProductDesc = PluginDesc(texVectorProductName, "TexVectorProduct")
    texVectorProductDesc.setAttribute("operation", 0)
    texVectorProductDesc.setAttribute("input1", viewDir)
    texVectorProductDesc.setAttribute("normalize", True)
    texVectorProduct = _exportCyclesPluginWithStats(nodeCtx, texVectorProductDesc)

    if fromSocket.name != "View Vector":
        NodeContext.registerError(f"Camera Data {fromSocket.name} output is not supported")
    return texVectorProduct

def _exportCyclesBevelNode(nodeCtx: NodeContext):
    node: bpy.types.ShaderNodeBevel = nodeCtx.node
    pluginName = Names.treeNode(nodeCtx)
    texEdgesDesc = PluginDesc(pluginName, "TexEdges")

    # Additional bump is only for bump and it works only if the base map is in normal mode.
    # Initially TexEdges is exported as the base bump map but when the normal socket is connected
    # it goes in the additional bump, so that the normal can be exported in its place.
    _exportCyclesFloatAttribute(nodeCtx, texEdgesDesc, "Radius", "world_width")
    texEdges = _exportCyclesPluginWithStats(nodeCtx, texEdgesDesc)

    normalBumpName = Names.nextVirtualNode(nodeCtx, "TexNormalBump")
    normalBumpDesc = PluginDesc(normalBumpName, "TexNormalBump")

    inputNormalSocket = node.inputs["Normal"]
    if _isSocketConnected(inputNormalSocket):
        _exportCyclesColorAttribute(nodeCtx, normalBumpDesc, inputNormalSocket, "bump_tex_color")
        normalBumpDesc.setAttribute("map_type", 6) # explicit

        normalBumpDesc.setAttribute("additional_bump", texEdges)
        normalBumpDesc.setAttribute("additional_bump_type", 5) # bump gradient
    else:
        normalBumpDesc.setAttribute("bump_tex_color", texEdges)
        normalBumpDesc.setAttribute("map_type", 5) # bump gradient

        normalBumpDesc.setAttribute("additional_bump", AttrPlugin())

    normalBumpDesc.setAttribute("blue2Z_mapping_method", 1) # -> Z [-1, 1]
    # normalBumpDesc.setAttribute("normal_uvwgen_auto", True)
    return _exportCyclesPluginWithStats(nodeCtx, normalBumpDesc)

def _exportCyclesAmbientOcclusionNode(nodeCtx: NodeContext, nodeLink: FarNodeLink):
    node: bpy.types.ShaderNodeAmbientOcclusion = nodeCtx.node
    fromSocket = nodeLink.from_socket
    # 'Color' and 'AO' use a different 'white_color'.
    pluginName = _outputScopedName(nodeCtx, fromSocket.name)
    texDirtDesc = PluginDesc(pluginName, "TexDirt")
    # TODO: If distance here is 0.0 blender has some global radius fallback.
    _exportCyclesFloatAttribute(nodeCtx, texDirtDesc, "Distance", "radius")
    if fromSocket.name == "Color":
        _exportCyclesColorAttribute(nodeCtx, texDirtDesc, "Color", "white_color")
    else:
        texDirtDesc.setAttribute("white_color", WHITE_COLOR)

    if node.only_local:
        texDirtDesc.setAttribute("consider_same_object_only", 1) # shading instance
    else:
        texDirtDesc.setAttribute("consider_same_object_only", 0) # off
    if node.inside:
        texDirtDesc.setAttribute("mode", 5) # ao + inner
    else:
        texDirtDesc.setAttribute("mode", 0) # ao

    texDirt = _exportCyclesPluginWithStats(nodeCtx, texDirtDesc)
    if fromSocket.name == "AO":
        return _exportIntensityOutput(nodeCtx, texDirt)
    return texDirt

def _exportCyclesBumpNode(nodeCtx: NodeContext):
    node: bpy.types.ShaderNodeBump = nodeCtx.node
    strengthSocket = node.inputs["Strength"]
    distanceSocket = node.inputs["Distance"]
    heightSocket = node.inputs["Height"]
    normalSocket = node.inputs["Normal"]

    if not _isSocketConnected(heightSocket):
        if _isSocketConnected(normalSocket):
            return _exportCyclesLinkedSocket(nodeCtx, normalSocket)
        else:
            return AttrPlugin()

    pluginName = Names.treeNode(nodeCtx)
    texDesc = PluginDesc(pluginName, "TexNormalBump")
    texDesc.setAttribute("map_type", 0)

    # Similar situation to the export of the Bevel node...
    strength = _getSocketValue(strengthSocket, SocketValueType.Float)
    distance = _getSocketValue(distanceSocket, SocketValueType.Float)
    if node.invert:
        distance = -distance
    bumpMultDefault = strength * distance
    if _isSocketConnected(normalSocket):
        # Strength is technically a blend between the original normal and the bump normal
        # but for now all sockets are just multiplied together.
        if not _isSocketConnected(strengthSocket) and not _isSocketConnected(distanceSocket):
            bump = _exportCyclesLinkedSocket(nodeCtx, heightSocket) if _isSocketConnected(heightSocket) else _getSocketValue(heightSocket, SocketValueType.Float)
            bump = _wrapFloatToColor(nodeCtx, bump)
            texDesc.setAttribute("bump_map_mult", bumpMultDefault)
        else:
            strength = _exportCyclesLinkedSocket(nodeCtx, strengthSocket) if _isSocketConnected(strengthSocket) else _getSocketValue(strengthSocket, SocketValueType.Float)
            strengthDistanceName = Names.nextVirtualNode(nodeCtx, "TexAColorOp")
            strengthDistanceDesc = PluginDesc(strengthDistanceName, "TexAColorOp")
            strengthDistanceDesc.setAttribute("color_a", _wrapFloatToColor(nodeCtx, strength))
            strengthDistanceDesc.setAttribute("mode", 0)
            _exportCyclesFloatAttribute(nodeCtx, strengthDistanceDesc, "Distance", "mult_a")
            if node.invert:
                strengthDistanceDesc.setAttribute("color_b", Color((-1.0, -1.0, -1.0)))
            else:
                strengthDistanceDesc.setAttribute("color_b", WHITE_COLOR)
            _exportCyclesFloatAttribute(nodeCtx, strengthDistanceDesc, "Height", "mult_b")
            bump = _exportCyclesPluginWithStats(nodeCtx, strengthDistanceDesc)

        _exportCyclesColorAttribute(nodeCtx, texDesc, normalSocket, "bump_tex_color")
        texDesc.setAttribute("map_type", 6) # explicit
        texDesc.setAttribute("additional_bump", bump)
    else:
        if not _isSocketConnected(strengthSocket) and not _isSocketConnected(distanceSocket):
            texDesc.setAttribute("bump_tex_mult", bumpMultDefault)
            texDesc.setAttribute("bump_tex_mult_tex", AttrPlugin())
        else:
            strengthDistanceName = Names.nextVirtualNode(nodeCtx, "TexFloatOp")
            strengthDistanceDesc = PluginDesc(strengthDistanceName, "TexFloatOp")
            strengthDistanceDesc.setAttribute("mode", 0)
            _exportCyclesFloatAttribute(nodeCtx, strengthDistanceDesc, strengthSocket, "float_a")
            _exportCyclesFloatAttribute(nodeCtx, strengthDistanceDesc, distanceSocket, "float_b")
            bumpMult = _exportCyclesPluginWithStats(nodeCtx, strengthDistanceDesc)
            texDesc.setAttribute("bump_tex_mult_tex", bumpMult)
            texDesc.setAttribute("bump_tex_mult", 1.0)
        bump = _exportCyclesLinkedSocket(nodeCtx, heightSocket) if _isSocketConnected(heightSocket) else _getSocketValue(heightSocket, SocketValueType.Float)
        bump = _wrapFloatToColor(nodeCtx, bump)
        texDesc.setAttribute("map_type", 5) # bump
        texDesc.setAttribute("bump_tex_color", bump)
    texNormalBump = _exportCyclesPluginWithStats(nodeCtx, texDesc)

    return texNormalBump

def _exportCyclesObjectInfoNode(nodeCtx: NodeContext, nodeLink: FarNodeLink):
    fromSocket = nodeLink.from_socket
    pluginName = Names.treeNode(nodeCtx)
    texDesc = PluginDesc(pluginName, "TexSampler")
    texSampler = _exportCyclesPluginWithStats(nodeCtx, texDesc)
    texSampler.output = "object_id"
    # TODO: Color and alpha will have to be user attributes exported from the object's viewport color.
    if fromSocket.name == "Object Index":
        texSampler.output = "object_id"
    elif fromSocket.name == "Material Index":
        texSampler.output = "material_id"
    elif fromSocket.name == "Random":
        texSampler.output = "random_by_renderID"
    else:
        NodeContext.registerError(f"Object Info {fromSocket.name} output is not supported by V-Ray")
    return texSampler

# Curvature radius as a fraction of the mean edge length.
CURVATURE_SCALE_PER_EDGE = 0.386

def _meanWorldEdgeLength(nodeCtx: NodeContext) -> float:
    """ Geometric mean of the world space edge length over the meshes using this material, 0 if there are none. """
    logTotal, meshes = 0.0, 0
    material = nodeCtx.material.original

    for obj in nodeCtx.exporterCtx.sceneObjects:
        if (obj.type != 'MESH') or all(slot.material is None or slot.material.original is not material for slot in obj.material_slots):
            continue

        mesh = obj.evaluated_get(nodeCtx.exporterCtx.dg).data
        if not (numEdges := len(mesh.edges)):
            continue

        edgeVerts = np.empty(numEdges * 2, dtype=np.int32)
        mesh.edges.foreach_get("vertices", edgeVerts)
        coords = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
        mesh.vertices.foreach_get("co", coords)
        coords = coords.reshape(-1, 3)

        objScale = obj.matrix_world.to_scale()
        meanEdge = np.linalg.norm(coords[edgeVerts[0::2]] - coords[edgeVerts[1::2]], axis=1).mean() * sum(abs(c) for c in objScale) / 3.0
        if meanEdge > 0.0:
            logTotal += math.log(meanEdge)
            meshes += 1

    return math.exp(logTotal / meshes) if meshes else 0.0

def _exportCyclesGeometryNode(nodeCtx: NodeContext, nodeLink: FarNodeLink):
    fromSocket = nodeLink.from_socket

    if fromSocket.name == "Pointiness":
        NodeContext.registerError("Pointiness is converted to a ray traced V-Ray curvature over a radius taken from the "
                                  "mesh. Blender's follows the tessellation, so the two only agree where the geometry "
                                  "is evenly tessellated, and one material shared by meshes of differing density "
                                  "cannot match all of them")
        curvatureDesc = PluginDesc(Names.nextVirtualNode(nodeCtx, "TexCurvature"), "TexCurvature")
        curvatureDesc.setAttribute("mode", 3) # convex in red, concave in green
        meanEdge = _meanWorldEdgeLength(nodeCtx)
        curvatureDesc.setAttribute("scale", CURVATURE_SCALE_PER_EDGE * meanEdge if meanEdge else 0.01)
        curvatureDesc.setAttribute("ignore_bump", True)
        curvatureDesc.setAttribute("out_color_min", 0.0)
        curvatureDesc.setAttribute("out_color_max", 0.5) # each channel carries half of Blender's range
        curvature = _exportCyclesPluginWithStats(nodeCtx, curvatureDesc)

        splitDesc = PluginDesc(Names.nextVirtualNode(nodeCtx, "TexAColorOp"), "TexAColorOp")
        splitDesc.setAttribute("mode", 0) # result_a
        splitDesc.setAttribute("color_a", curvature)
        splitDesc.setAttribute("mult_a", 1.0)
        split = _exportCyclesPluginWithStats(nodeCtx, splitDesc)

        convex = _texFloatOp(nodeCtx, 2, 0.5, AttrPlugin(split.name, "red"))
        return _texFloatOp(nodeCtx, 3, convex, AttrPlugin(split.name, "green"))

    pluginName = Names.treeNode(nodeCtx)
    texDesc = PluginDesc(pluginName, "TexSampler")
    texSampler = _exportCyclesPluginWithStats(nodeCtx, texDesc)

    isVector = False
    if fromSocket.name == "Position":
        texSampler.output = "point"
        isVector = True
    elif fromSocket.name == "Normal":
        texSampler.output = "bumpNormal"
        isVector = True
    elif fromSocket.name == "True Normal": #TODO: Check this too
        texSampler.output = "gnormal"
        isVector = True
    elif fromSocket.name == "Random Per Island":
        texSampler.output = "random_by_polyShell" # no gpu
    elif fromSocket.name == "Backfacing":
        texSampler.output = "flipped_normal" # no gpu
    elif fromSocket.name == "Incoming":
        texSampler.output = "view_dir" # TODO: Investigate, it's different
        isVector = True
    else:
        NodeContext.registerError(f"Geometry node {fromSocket.name} output is not supported by V-Ray")
    if isVector:
        return _wrapVectorToColor(nodeCtx, texSampler)
    return texSampler

# Preetham and Hosek exist in both renderers and map onto themselves. Blender's scattering models
# (NISHITA in 4.x) have no V-Ray equivalent; PRG Clear Sky New is the closest and the only one
# taking an observer altitude. TexSky reads different parameters per model (sunsky.cpp:95-108), so
# each branch below writes only what its model uses.
CYCLES_SKY_MODELS = {
    'PREETHAM': 0,
    'HOSEK_WILKIE': 3,
    'SINGLE_SCATTERING': 5,
    'MULTIPLE_SCATTERING': 5,
    'NISHITA': 5,   # Blender 4.x
}

# The two renderers' skies differ in brightness by a per-model factor: the geometric mean of the
# V-Ray/Cycles ratio measured over the sky region at 15/45/75 degrees of sun elevation. The elevation
# drift (~+/-20%) is left over; turbidity does not affect the ratio. Same approach as
# light_convert.py. The two scattering models land on the same V-Ray model but are a factor of two
# apart, so this is keyed by the Blender sky type rather than by 'sky_model'.
VRAY_SKY_INTENSITY_SCALE = {
    'PREETHAM'           : 1.0 / 11.3,
    'HOSEK_WILKIE'       : 1.0 / 56.2,
    'SINGLE_SCATTERING'  : 1.0 / 1.02,
    'MULTIPLE_SCATTERING': 1.0 / 0.50,
    'NISHITA'            : 1.0 / 1.02,   # Blender 4.x, the single scattering implementation
}

# Blender's scattering skies have no turbidity - they model the atmosphere with separate air and
# aerosol densities. PRG Clear Sky takes a turbidity, which it converts to a meteorological
# visibility internally, so bridge the two through visibility using Koschmieder's law
# (V = 3.912 / extinction). The coefficients are Blender's sea-level extinction in the 560nm band
# (sky_multiple_scattering.cpp:62-74); sky_single_scattering.cpp's differ by under 5%.
CYCLES_AEROSOL_EXTINCTION   = 0.02486    # km^-1 per unit of 'aerosol_density'
CYCLES_MOLECULAR_EXTINCTION = 0.01067    # km^-1 per unit of 'air_density'
KOSCHMIEDER_CONSTANT        = 3.912      # ln(1 / 0.02), the 2% contrast threshold

# The turbidity range the PRG dataset actually covers (prgskymodel.h:104). Outside it the model
# silently saturates, so the conversion clamps instead of writing a value that will not be used.
PRG_TURBIDITY_RANGE = (1.81, 4.89)

# V-Ray's altitude range for the model (prgskymodel.h:105). Blender allows up to 100km.
PRG_MAX_ALTITUDE = 15000.0


def _prgTurbidityForVisibility(visibilityKm: float):
    """ Invert V-Ray's turbidity -> visibility fit (prgsunandskymodel_base.h:100-107) by bisection.
        The fit decreases monotonically over the dataset range. """
    lo, hi = PRG_TURBIDITY_RANGE

    for _ in range(24):
        mid = (lo + hi) / 2.0
        if 7487.0 * math.exp(-2.57796 * mid) + 117.1 * math.exp(-0.3604608 * mid) > visibilityKm:
            lo = mid
        else:
            hi = mid

    return (lo + hi) / 2.0


def _exportCyclesSkyTexture(nodeCtx: NodeContext):
    """ Cycles 'Sky Texture' -> V-Ray TexSky. With no 'sun' plugin attached, TexSky reads the sun
        direction off the Z axis of its 'transform' (sunsky.cpp sunDir = normalize(sunTm.m[2])). """
    node: bpy.types.ShaderNodeTexSky = nodeCtx.node
    skyDesc = PluginDesc(Names.treeNode(nodeCtx), "TexSky")

    skyType = getattr(node, "sky_type", "NISHITA")
    if skyType not in CYCLES_SKY_MODELS:
        skyType = 'HOSEK_WILKIE'   # a sky type we don't know yet, Hosek is the closest common one
    skyModel = CYCLES_SKY_MODELS[skyType]
    skyDesc.setAttribute("sky_model", skyModel)

    if skyModel == 5:
        # Aimed by elevation/rotation rather than a direction vector (svm/sky.h:157). Cycles negates
        # the azimuth before rendering (`rotation = 2pi - rotation` in SkyTextureNode::simplify_settings,
        # shader_nodes.cpp), so without the minus the sky comes out mirrored - invisible at 0 and 180
        # degrees, which is why it went unnoticed.
        elevation, rotation = node.sun_elevation, -node.sun_rotation
        sunDir = Vector((-math.cos(elevation) * math.sin(rotation),
                          math.cos(elevation) * math.cos(rotation),
                          math.sin(elevation)))
        skyDesc.setAttribute("altitude", min(node.altitude, PRG_MAX_ALTITUDE))

        # 'dust_density' was renamed to 'aerosol_density' in Blender 5.0.
        aerosols = node.aerosol_density if hasattr(node, "aerosol_density") else node.dust_density
        extinction = CYCLES_AEROSOL_EXTINCTION * aerosols + CYCLES_MOLECULAR_EXTINCTION * node.air_density
        visibility = KOSCHMIEDER_CONSTANT / max(extinction, 1e-6)
        skyDesc.setAttribute("turbidity", _prgTurbidityForVisibility(visibility))
        # Below the horizon PRG Clear Sky paints 'ground_albedo' * dome illuminance, which the
        # scattering skies have no counterpart for - they fade to black there. At the 0.2 default the
        # band came out 20-50x too bright, so zero it. Blender's own hardcoded 0.3
        # (sky_multiple_scattering.cpp:41) is a different quantity: it sits inside the multiple
        # scattering integral, and using it here overshot by another 5x.
        skyDesc.setAttribute("ground_albedo", BLACK_COLOR)

        unsupported = []
        if node.altitude > PRG_MAX_ALTITUDE:
            unsupported.append(f"Altitude above {PRG_MAX_ALTITUDE:.0f}m")
        if not math.isclose(node.ozone_density, 1.0):
            # PRG Clear Sky reads 'ozone' for the CIE and Hosek models only (sunsky_common.cpp:3607).
            unsupported.append("Ozone Density")
        if node.sun_disc:
            # Blender draws the disc into the sky texture; in V-Ray it comes from a SunLight.
            unsupported.append("Sun Disc")
        if not math.isclose(node.sun_intensity, 1.0):
            unsupported.append("Sun Intensity")
        if unsupported:
            NodeContext.registerError("Sky Texture: V-Ray's PRG Clear Sky has no equivalent for "
                                      + ", ".join(unsupported))
    else:
        sunDir = Vector(node.sun_direction).normalized()
        skyDesc.setAttribute("turbidity", node.turbidity)
        if skyType == 'HOSEK_WILKIE':
            albedo = _clamp01(node.ground_albedo)
            skyDesc.setAttribute("ground_albedo", Color((albedo, albedo, albedo)))

    skyDesc.setAttribute("intensity_multiplier", VRAY_SKY_INTENSITY_SCALE[skyType])

    if sunDir.length < 1e-6:
        sunDir = Vector((0.0, 0.0, 1.0))
    skyDesc.setAttribute("transform", sunDir.to_track_quat('Z', 'Y').to_matrix().to_4x4())

    return _exportCyclesPluginWithStats(nodeCtx, skyDesc)


def _exportCyclesHairInfoNode(nodeCtx: NodeContext, nodeLink: FarNodeLink):
    """ Cycles 'Hair Info' -> V-Ray TexHairSampler. Without it the ramps that read Intercept lose
        their input and collapse to flat grey, desaturating the fur. """
    name = nodeLink.from_socket.name

    if name == "Is Strand":
        # Both Curves and particle hair always shade strands.
        return wrapAsTexture(nodeCtx, WHITE_COLOR)

    if name in ("Thickness", "Tangent Normal"):
        # Thickness is the strand width at the shading point in scene units; TexHairSampler only
        # reports the relative position across the strand, which is a different quantity.
        NodeContext.registerError(f"Hair Info '{name}' output is not supported by V-Ray")
        return wrapAsTexture(nodeCtx, BLACK_COLOR)

    sampler = _exportCyclesPluginWithStats(nodeCtx, PluginDesc(Names.treeNode(nodeCtx), "TexHairSampler"))

    if name == "Intercept":                 # 0 at the root -> 1 at the tip
        sampler.output = "distance_along_strand"
        return sampler
    if name == "Random":
        sampler.output = "random_by_strand"
        return sampler

    # 'Length' is the whole strand. TexHairSampler gives the distance to the shading point both as
    # a fraction and in scene units (divided by hair_max_distance, default 1), so their ratio is the
    # strand length. 'distance_along_strand_absolute' is CPU only, so this does not work on GPU.
    lengthDesc = PluginDesc(Names.nextVirtualNode(nodeCtx, "TexFloatOp"), "TexFloatOp")
    lengthDesc.setAttribute("float_a", AttrPlugin(sampler.name, output="distance_along_strand_absolute"))
    lengthDesc.setAttribute("float_b", AttrPlugin(sampler.name, output="distance_along_strand"))
    lengthDesc.setAttribute("mode", 1)  # ratio
    return _exportCyclesPluginWithStats(nodeCtx, lengthDesc)

def _exportCyclesSeperateColorNode(nodeCtx: NodeContext, nodeLink: FarNodeLink):
    node: bpy.types.ShaderNodeSeparateColor = nodeCtx.node
    fromSocket = nodeLink.from_socket
    pluginName = Names.treeNode(nodeCtx)
    texDesc = PluginDesc(pluginName, "TexAColorOp")
    if node.mode == "RGB":
        _exportCyclesColorAttribute(nodeCtx, texDesc, "Color", "color_a")
    elif node.mode == "HSV":
        texRGBToHSVName = Names.nextVirtualNode(nodeCtx, "TexRGBToHSV")
        texRGBToHSVDesc = PluginDesc(texRGBToHSVName, "TexRGBToHSV")
        _exportCyclesColorAttribute(nodeCtx, texRGBToHSVDesc, "Color", "inRgb")
        texRGBToHSV = _exportCyclesPluginWithStats(nodeCtx, texRGBToHSVDesc)
        texDesc.setAttribute("color_a", texRGBToHSV)
    else:
        NodeContext.registerError(f"V-Ray does not support {node.mode} seperate mode")
    texAColorOp = _exportCyclesPluginWithStats(nodeCtx, texDesc)
    # The outputs are named Red/Green/Blue in every mode, so in HSV mode 'Red' is Hue, 'Green'
    # Saturation and 'Blue' Value.
    channel = {"Red": "red", "Green": "green", "Blue": "blue"}[fromSocket.name]
    texAColorOp.output = channel

    if node.mode == "HSV" and channel == "red":
        # Hue only: RGBtoHSV reports it in degrees, Blender's Separate Color in [0, 1].
        hueDesc = PluginDesc(Names.nextVirtualNode(nodeCtx, "TexFloatOp"), "TexFloatOp")
        hueDesc.setAttribute("float_a", texAColorOp)
        hueDesc.setAttribute("float_b", 360.0)
        hueDesc.setAttribute("mode", 1)  # ratio
        hue = _exportCyclesPluginWithStats(nodeCtx, hueDesc)
        hue.output = "ratio"
        return hue

    return texAColorOp

def _exportCyclesSeperateXYZNode(nodeCtx: NodeContext, nodeLink: FarNodeLink):
    fromSocket = nodeLink.from_socket
    pluginName = Names.treeNode(nodeCtx)
    texDesc = PluginDesc(pluginName, "TexAColorOp")
    vectorSocket = nodeCtx.node.inputs["Vector"]
    value = _exportCyclesLinkedSocket(nodeCtx, vectorSocket) if _isSocketConnected(vectorSocket) else _getSocketValue(vectorSocket, SocketValueType.Color)
    if value is not None and isinstance(value, AttrPlugin) and "UVWGen" in value.pluginType:
        value = _exportUVWToColor(nodeCtx, value)
    texDesc.setAttribute("color_a", value)
    texAColorOp = _exportCyclesPluginWithStats(nodeCtx, texDesc)
    texAColorOp.output = "red"
    if fromSocket.name == "X":
        texAColorOp.output = "red"
    elif fromSocket.name == "Y":
        texAColorOp.output = "green"
    elif fromSocket.name == "Z":
        texAColorOp.output = "blue"
    return texAColorOp

def _texFloatOp(nodeCtx: NodeContext, mode: int, floatA, floatB = None, name: str = None, allowTypeChanges = False):
    """ Create a TexFloatOp with the given mode. Omitting 'name' creates a virtual node. """
    texDesc = PluginDesc(name if name else Names.nextVirtualNode(nodeCtx, "TexFloatOp"), "TexFloatOp")
    texDesc.setAttribute("mode", mode)
    texDesc.setAttribute("float_a", floatA)
    if floatB is not None:
        texDesc.setAttribute("float_b", floatB)
    return _exportCyclesPluginWithStats(nodeCtx, texDesc, allowTypeChanges)


def _exportCyclesVectorInput(nodeCtx: NodeContext, socket: bpy.types.NodeSocket):
    """ Resolve a Cycles Vector socket to a color, converting UVW generators on the way. """
    value = _exportCyclesLinkedSocket(nodeCtx, socket) if _isSocketConnected(socket) else _getSocketValue(socket, SocketValueType.Color)
    if isinstance(value, AttrPlugin) and "UVWGen" in value.pluginType:
        value = _exportUVWToColor(nodeCtx, value)
    return value


def _exportCyclesVectorDot(nodeCtx: NodeContext, vectorA, vectorB, name: str, allowTypeChanges = False):
    """ dot(a, b) = (a * b).r + (a * b).g + (a * b).b """
    productDesc = PluginDesc(Names.nextVirtualNode(nodeCtx, "TexAColorOp"), "TexAColorOp")
    productDesc.setAttribute("mode", 2) # product
    productDesc.setAttribute("color_a", vectorA)
    productDesc.setAttribute("color_b", vectorB)
    product = _exportCyclesPluginWithStats(nodeCtx, productDesc)

    # 'red'/'green'/'blue' are channels of color_a*mult_a, not of the operation's result, so they
    # have to be read off a plugin whose color_a IS the product.
    channelsDesc = PluginDesc(Names.nextVirtualNode(nodeCtx, "TexAColorOp"), "TexAColorOp")
    channelsDesc.setAttribute("mode", 0) # result_a
    channelsDesc.setAttribute("color_a", product)
    channels = _exportCyclesPluginWithStats(nodeCtx, channelsDesc)

    redGreen = _texFloatOp(nodeCtx, 2, AttrPlugin(channels.name, "red"), AttrPlugin(channels.name, "green"))
    return _texFloatOp(nodeCtx, 2, redGreen, AttrPlugin(channels.name, "blue"), name, allowTypeChanges)


# Blender op -> TexAColorOp mode.
_VECTOR_MATH_BINARY_MODES = {'ADD': 3, 'SUBTRACT': 4, 'MULTIPLY': 2, 'DIVIDE': 6, 'MINIMUM': 7, 'MAXIMUM': 8,
                             'MODULO': 21}
_VECTOR_MATH_UNARY_MODES  = {'ABSOLUTE': 14, 'FLOOR': 17, 'CEIL': 15, 'SINE': 12, 'COSINE': 13, 'TANGENT': 23,
                             'SQRT': 20}
# sin and cos are f(color_a * color_b) against Cycles' single operand, and an unset color_b reads
# as black on both devices.
_VECTOR_MATH_WHITE_B = {'SINE', 'COSINE'}

def _exportCyclesVectorMathNode(nodeCtx: NodeContext):
    node: bpy.types.ShaderNodeVectorMath = nodeCtx.node
    op = node.operation
    # All three vector inputs are named "Vector", so they can only be addressed by index.
    vectorA, vectorB = node.inputs[0], node.inputs[1]
    pluginName = Names.treeNode(nodeCtx)

    if op in _VECTOR_MATH_BINARY_MODES:
        texDesc = PluginDesc(pluginName, "TexAColorOp")
        texDesc.setAttribute("mode", _VECTOR_MATH_BINARY_MODES[op])
        texDesc.setAttribute("color_a", _exportCyclesVectorInput(nodeCtx, vectorA))
        texDesc.setAttribute("color_b", _exportCyclesVectorInput(nodeCtx, vectorB))
        return _exportCyclesPluginWithStats(nodeCtx, texDesc, True)

    if op in _VECTOR_MATH_UNARY_MODES:
        texDesc = PluginDesc(pluginName, "TexAColorOp")
        texDesc.setAttribute("mode", _VECTOR_MATH_UNARY_MODES[op])
        texDesc.setAttribute("color_a", _exportCyclesVectorInput(nodeCtx, vectorA))
        if op in _VECTOR_MATH_WHITE_B:
            texDesc.setAttribute("color_b", WHITE_COLOR)
        return _exportCyclesPluginWithStats(nodeCtx, texDesc, True)

    match op:
        case 'SCALE':
            texDesc = PluginDesc(pluginName, "TexAColorOp")
            texDesc.setAttribute("mode", 0) # result_a = color_a * mult_a
            texDesc.setAttribute("color_a", _exportCyclesVectorInput(nodeCtx, vectorA))
            _exportCyclesFloatAttribute(nodeCtx, texDesc, node.inputs["Scale"], "mult_a")
            attrPlugin = _exportCyclesPluginWithStats(nodeCtx, texDesc, True)
            attrPlugin.output = "result_a"
            return attrPlugin
        case 'MULTIPLY_ADD':
            productDesc = PluginDesc(Names.nextVirtualNode(nodeCtx, "TexAColorOp"), "TexAColorOp")
            productDesc.setAttribute("mode", 2) # product
            productDesc.setAttribute("color_a", _exportCyclesVectorInput(nodeCtx, vectorA))
            productDesc.setAttribute("color_b", _exportCyclesVectorInput(nodeCtx, vectorB))
            product = _exportCyclesPluginWithStats(nodeCtx, productDesc)

            texDesc = PluginDesc(pluginName, "TexAColorOp")
            texDesc.setAttribute("mode", 3) # sum
            texDesc.setAttribute("color_a", product)
            texDesc.setAttribute("color_b", _exportCyclesVectorInput(nodeCtx, node.inputs[2]))
            return _exportCyclesPluginWithStats(nodeCtx, texDesc, True)
        case 'CROSS_PRODUCT':
            # The plugin computes cross(input1, input2), whatever the parameter names say.
            texDesc = PluginDesc(pluginName, "TexVectorProduct")
            texDesc.setAttribute("operation", 2) # cross product
            texDesc.setAttribute("input1", _exportCyclesVectorInput(nodeCtx, vectorA))
            texDesc.setAttribute("input2", _exportCyclesVectorInput(nodeCtx, vectorB))
            return _exportCyclesPluginWithStats(nodeCtx, texDesc, True)
        case 'DOT_PRODUCT':
            vectorAPlugin = _exportCyclesVectorInput(nodeCtx, vectorA)
            vectorBPlugin = _exportCyclesVectorInput(nodeCtx, vectorB)
            return _exportCyclesVectorDot(nodeCtx, vectorAPlugin, vectorBPlugin, pluginName, True)
        case 'LENGTH':
            vector = _exportCyclesVectorInput(nodeCtx, vectorA)
            lengthSqr = _exportCyclesVectorDot(nodeCtx, vector, vector, None)
            return _texFloatOp(nodeCtx, 15, lengthSqr, None, pluginName, True) # sqrt
        case 'DISTANCE':
            differenceDesc = PluginDesc(Names.nextVirtualNode(nodeCtx, "TexAColorOp"), "TexAColorOp")
            differenceDesc.setAttribute("mode", 4) # difference
            differenceDesc.setAttribute("color_a", _exportCyclesVectorInput(nodeCtx, vectorA))
            differenceDesc.setAttribute("color_b", _exportCyclesVectorInput(nodeCtx, vectorB))
            difference = _exportCyclesPluginWithStats(nodeCtx, differenceDesc)

            lengthSqr = _exportCyclesVectorDot(nodeCtx, difference, difference, None)
            return _texFloatOp(nodeCtx, 15, lengthSqr, None, pluginName, True) # sqrt
        case 'NORMALIZE':
            texDesc = PluginDesc(pluginName, "TexVectorProduct")
            texDesc.setAttribute("operation", 0) # pass input1 through
            texDesc.setAttribute("input1", _exportCyclesVectorInput(nodeCtx, vectorA))
            texDesc.setAttribute("normalize", True)
            return _exportCyclesPluginWithStats(nodeCtx, texDesc, True)

    NodeContext.registerError(f"Vector Math operation {op} is not supported by V-Ray")
    return None


def _exportCyclesMapRangeNode(nodeCtx: NodeContext):
    node: bpy.types.ShaderNodeMapRange = nodeCtx.node

    if node.data_type != 'FLOAT':
        NodeContext.registerError(f"Map Range data type {node.data_type} is not supported by V-Ray")
        return None
    if node.interpolation_type not in ('LINEAR', 'SMOOTHSTEP'):
        NodeContext.registerError(f"Map Range interpolation {node.interpolation_type} is not supported by V-Ray")
        return None

    # The FLOAT and VECTOR variants of the node share socket names, so index access is required.
    valueSocket, fromMinSocket, fromMaxSocket, toMinSocket, toMaxSocket = (node.inputs[i] for i in range(5))

    def operand(socket):
        return _exportCyclesLinkedSocket(nodeCtx, socket) if _isSocketConnected(socket) else _getSocketValue(socket, SocketValueType.Float)

    value, fromMin, fromMax = operand(valueSocket), operand(fromMinSocket), operand(fromMaxSocket)
    toMin, toMax = operand(toMinSocket), operand(toMaxSocket)

    # factor = (value - fromMin) / (fromMax - fromMin)
    factor = _texFloatOp(nodeCtx, 1, _texFloatOp(nodeCtx, 3, value, fromMin), _texFloatOp(nodeCtx, 3, fromMax, fromMin))

    if node.interpolation_type == 'SMOOTHSTEP':
        factor = _wrapClampPlugin01(nodeCtx, factor)
        factorSqr = _texFloatOp(nodeCtx, 0, factor, factor)
        slope = _texFloatOp(nodeCtx, 3, 3.0, _texFloatOp(nodeCtx, 0, factor, 2.0))
        factor = _texFloatOp(nodeCtx, 0, factorSqr, slope)

    # result = toMin + factor * (toMax - toMin)
    scaled = _texFloatOp(nodeCtx, 0, factor, _texFloatOp(nodeCtx, 3, toMax, toMin))
    result = _texFloatOp(nodeCtx, 2, toMin, scaled, Names.treeNode(nodeCtx), True)

    if node.clamp and (node.interpolation_type != 'SMOOTHSTEP'):
        return _wrapClampPlugin(nodeCtx, result, toMin, toMax)
    return result


def _exportCyclesFresnelNode(nodeCtx: NodeContext):
    pluginName = Names.treeNode(nodeCtx)
    texDesc = PluginDesc(pluginName, "TexFresnel")

    _exportCyclesFloatAttribute(nodeCtx, texDesc, "IOR", "fresnel_ior_tex")
    # TexFresnel's white_color is the refraction (front) colour and black_color the reflection
    # (side) one, so it returns the complement of Cycles' Fac. Swapping them makes it exact.
    texDesc.setAttribute("white_color", BLACK_COLOR)
    texDesc.setAttribute("black_color", WHITE_COLOR)
    texFresnel = _exportCyclesPluginWithStats(nodeCtx, texDesc)
    return _exportIntensityOutput(nodeCtx, texFresnel)

def _exportLayerWeightBias(nodeCtx: NodeContext, input: AttrPlugin):
    # When nothing is connected here use the calculation from blender,
    # otherwise use schlick bias which is a bit different.
    blendSocket = nodeCtx.node.inputs["Blend"]
    if _isSocketConnected(blendSocket):
            texFloatOpName = Names.nextVirtualNode(nodeCtx, "TexFloatOp")
            texFloatOpDesc = PluginDesc(texFloatOpName, "TexFloatOp")
            texFloatOpDesc.setAttribute("float_a", input)
            _exportCyclesFloatAttribute(nodeCtx, texFloatOpDesc, blendSocket, "float_b")
            texFloatOp = _exportCyclesPluginWithStats(nodeCtx, texFloatOpDesc)
            texFloatOp.output = "bias_schlick"
            return texFloatOp
    else:
        blend = _getSocketValue(blendSocket, SocketValueType.Float)
        if not math.isclose(blend, 0.5):
            blend = _clamp(blend, 0.0, 1.0 - 1e-5)
            if blend < 0.5:
                blend = 2.0 * blend
            else:
                blend = 0.5 / (1.0 - blend)
        texPowName = Names.nextVirtualNode(nodeCtx, "TexFloatOp")
        texPowDesc = PluginDesc(texPowName, "TexFloatOp")
        texPowDesc.setAttribute("float_a", input)
        texPowDesc.setAttribute("float_b", blend)
        texFloatOp = _exportCyclesPluginWithStats(nodeCtx, texPowDesc)
        texFloatOp.output = "power"

        texInvertName = Names.nextVirtualNode(nodeCtx, "TexInvertFloat")
        texInvertDesc = PluginDesc(texInvertName, "TexInvertFloat")
        texInvertDesc.setAttribute("texture", texFloatOp)
        return _exportCyclesPluginWithStats(nodeCtx, texInvertDesc)

def _exportCyclesLayerWeightNode(nodeCtx: NodeContext, nodeLink: FarNodeLink):
    fromSocket = nodeLink.from_socket
    # 'Facing' and 'Fresnel' build different plugin types.
    pluginName = _outputScopedName(nodeCtx, fromSocket.name)

    if fromSocket.name == "Facing":
        texDesc = PluginDesc(pluginName, "TexSampler")
        texSampler = _exportCyclesPluginWithStats(nodeCtx, texDesc, True)
        texSampler.output = "facing_ratio"

        return _exportLayerWeightBias(nodeCtx, texSampler)
    elif fromSocket.name == "Fresnel":
        texDesc = PluginDesc(pluginName, "TexFresnel")

        texDesc.setAttribute("fresnel_ior", 1.6)
        texFresnel = _exportCyclesPluginWithStats(nodeCtx, texDesc, True)
        fresnelValue = _exportIntensityOutput(nodeCtx, texFresnel)
        return _exportLayerWeightBias(nodeCtx, fresnelValue)

    return 0.0

def _exportCyclesColorMixNode(nodeCtx: NodeContext, isColorMix: bool, isLegacy=False):
    node: bpy.types.ShaderNodeMix = nodeCtx.node
    pluginName = Names.treeNode(nodeCtx)
    # TexLayeredMax with 2 layers as it seems to be the only texture supporting all modes
    texDesc = PluginDesc(pluginName, "TexLayeredMax")

    mode = 0
    if isColorMix:
        match node.blend_type:
            case 'MIX': mode = 0
            case 'DARKEN': mode = 4
            case 'MULTIPLY': mode = 5
            case 'BURN': mode = 6
            case 'LIGHTEN': mode = 8
            case 'SCREEN': mode = 9
            case 'DODGE': mode = 10
            case 'ADD': mode = 2
            case 'OVERLAY': mode = 14
            case 'SOFT_LIGHT': mode = 15
            case 'LINEAR_LIGHT': mode = 16
            case 'DIFFERENCE': mode = 19
            case 'EXCLUSION': mode = 20
            case 'SUBTRACT': mode = 3
            case 'HUE': mode = 21
            case 'SATURATION': mode = 22
            case 'COLOR': mode = 23
            case 'VALUE': mode = 24
            case _:
                # Currently divide only
                NodeContext.registerError(f"Mix mode {node.blend_type} not supported by V-Ray")

    blendModes = [
        0, # Normal
        mode
    ]

    def wrapMixInput(socket: bpy.types.NodeSocketColor):
        if _isSocketConnected(socket):
            tex = _exportCyclesLinkedSocket(nodeCtx, socket)
            if isColorMix and isinstance(tex, AttrPlugin):
                # V-Ray blends based on the alpha of the inputs(and Cycles doesn't) so for now set the alpha to 1.0.
                texAColorOpName = Names.nextVirtualNode(nodeCtx, "TexAColorOp")
                texAColorOpDesc = PluginDesc(texAColorOpName, "TexAColorOp")
                texAColorOpDesc.setAttribute("color_a", tex)
                texAColorOp = _exportCyclesPluginWithStats(nodeCtx, texAColorOpDesc)
                float3ToAColorName = Names.nextVirtualNode(nodeCtx, "Float3ToAColor")
                float3ToAColorDesc = PluginDesc(float3ToAColorName, "Float3ToAColor")
                float3ToAColorDesc.setAttribute("float1", AttrPlugin(texAColorOp.name, "red"))
                float3ToAColorDesc.setAttribute("float2", AttrPlugin(texAColorOp.name, "green"))
                float3ToAColorDesc.setAttribute("float3", AttrPlugin(texAColorOp.name, "blue"))
                float3ToAColorDesc.setAttribute("alpha", 1.0)
                tex = _exportCyclesPluginWithStats(nodeCtx, float3ToAColorDesc)
            return wrapAsTexture(nodeCtx, tex)
        else:
            return wrapAsTexture(nodeCtx, _getSocketValue(socket, SocketValueType.Color))

    if isLegacy:
        factorSocket = nodeCtx.node.inputs["Fac"]
    else:
        factorSocket = nodeCtx.node.inputs["Factor"]
    if isColorMix or node.factor_mode=='UNIFORM':
        factor = _exportCyclesLinkedSocket(nodeCtx, factorSocket) if _isSocketConnected(factorSocket) else _getSocketValue(factorSocket, SocketValueType.Float)
        if not isLegacy and node.clamp_factor:
            factor = _wrapClampPlugin01(nodeCtx, factor)
        floatToColor = _wrapFloatToColor(nodeCtx, factor)
        masks = [wrapAsTexture(nodeCtx, WHITE_COLOR), floatToColor]
    else:
        nodeCtx.registerError("V-Ray does not support non-uniform Vector Mix")
        # Still export the color, but V-Ray will use the intensity
        if _isSocketConnected(factorSocket):
            facTex = _exportCyclesLinkedSocket(nodeCtx, factorSocket)
            masks = [wrapAsTexture(nodeCtx, WHITE_COLOR), wrapAsTexture(nodeCtx, facTex)]
        else:
            masks = [wrapAsTexture(nodeCtx, WHITE_COLOR), wrapAsTexture(nodeCtx, _getSocketValue(factorSocket, SocketValueType.Color))]

    if isLegacy:
        A, B = node.inputs["Color1"], node.inputs["Color2"]
    else:
        A, B = node.inputs["A"], node.inputs["B"]
    textures = [wrapMixInput(A), wrapMixInput(B)]
    texDesc.setAttribute("blend_modes", blendModes)
    texDesc.setAttribute("textures", textures)
    texDesc.setAttribute("masks", masks)

    if not isColorMix:
        texDesc.setAttribute("allow_negative_colors", True)
    else:
        texDesc.setAttribute("allow_negative_colors", False)

    plugin = _exportCyclesPluginWithStats(nodeCtx, texDesc, True)
    if isColorMix and (node.use_clamp if isLegacy else node.clamp_result):
        texClampName = Names.nextVirtualNode(nodeCtx, "TexClamp")
        texClampDesc = PluginDesc(texClampName, "TexClamp")
        texClampDesc.setAttribute("texture", plugin)
        texClampDesc.setAttribute("min_color", BLACK_COLOR)
        texClampDesc.setAttribute("max_color", WHITE_COLOR)
        return _exportCyclesPluginWithStats(nodeCtx, texClampDesc)
    return plugin

def _exportCyclesFloatMixNode(nodeCtx: NodeContext):
    pluginName = Names.treeNode(nodeCtx)
    texDesc = PluginDesc(pluginName, "TexFloatOp")
    texDesc.setAttribute("mode", 2) # sum

    factorSocket = nodeCtx.node.inputs["Factor"]
    # A * Factor
    aTexName = Names.nextVirtualNode(nodeCtx, "TexFloatOp")
    aTexDesc = PluginDesc(aTexName, "TexFloatOp")
    aTexDesc.setAttribute("mode", 0) # product
    _exportCyclesFloatAttribute(nodeCtx, aTexDesc, factorSocket, "float_a")
    _exportCyclesFloatAttribute(nodeCtx, aTexDesc, "B", "float_b")
    aTex = _exportCyclesPluginWithStats(nodeCtx, aTexDesc)
    texDesc.setAttribute("float_a", aTex)

    bTexName = Names.nextVirtualNode(nodeCtx, "TexFloatOp")
    bTexDesc = PluginDesc(bTexName, "TexFloatOp")
    bTexDesc.setAttribute("mode", 0) # product
    _exportCyclesFloatAttribute(nodeCtx, bTexDesc, "A", "float_a")
    if not _isSocketConnected(factorSocket):
        bTexDesc.setAttribute("float_b", 1 - _getSocketValue(factorSocket, SocketValueType.Float))
    else:
        # 1 - Factor
        invertName = Names.nextVirtualNode(nodeCtx, "TexInvertFloat")
        invertDesc = PluginDesc(invertName, "TexInvertFloat")
        _exportCyclesFloatAttribute(nodeCtx, invertDesc, factorSocket, "texture")
        texInvert = _exportCyclesPluginWithStats(nodeCtx, invertDesc)
        bTexDesc.setAttribute("float_b", texInvert)
    bTex = _exportCyclesPluginWithStats(nodeCtx, bTexDesc)
    texDesc.setAttribute("float_b", bTex)

    return _exportCyclesPluginWithStats(nodeCtx, texDesc, True)

def _exportCyclesMixNode(nodeCtx: NodeContext):
    node: bpy.types.ShaderNodeMix = nodeCtx.node
    if node.data_type == 'RGBA':
        return _exportCyclesColorMixNode(nodeCtx, True)
    elif node.data_type == 'VECTOR':
        return _exportCyclesColorMixNode(nodeCtx, False)
    elif node.data_type == 'FLOAT':
        return _exportCyclesFloatMixNode(nodeCtx)

def _exportCyclesClampNode(nodeCtx: NodeContext):
    valueSocket = nodeCtx.node.inputs["Value"]
    value = _exportCyclesLinkedSocket(nodeCtx, valueSocket) if _isSocketConnected(valueSocket) else _getSocketValue(valueSocket, SocketValueType.Float)
    minSocket = nodeCtx.node.inputs["Min"]
    min = _exportCyclesLinkedSocket(nodeCtx, minSocket) if _isSocketConnected(minSocket) else _getSocketValue(minSocket, SocketValueType.Float)
    maxSocket = nodeCtx.node.inputs["Max"]
    max = _exportCyclesLinkedSocket(nodeCtx, maxSocket) if _isSocketConnected(maxSocket) else _getSocketValue(maxSocket, SocketValueType.Float)
    return _wrapClampPlugin(nodeCtx, value, min, max)

def _saturationGain(nodeCtx: NodeContext, inputColor):
    """ Blender's Saturation, as a per-pixel gain that TexColorCorrect can use directly.

        Blender clamps the saturation itself - min(s * k, 1) - before converting back to RGB, while
        'sat_gain' multiplies s unclamped and lets the channel that goes negative clip. That loses
        the middle channel: sat 2 on a 0.673-saturated brown reads 0.050 green against Cycles'
        0.178. Since min(k, 1/s) * s == min(k * s, 1), clamping the gain is the same operation and
        keeps the whole correction inside one TexColorCorrect.
    """
    satSocket = nodeCtx.node.inputs["Saturation"]
    isTextured = _isSocketConnected(satSocket)
    gain = _exportCyclesLinkedSocket(nodeCtx, satSocket) if isTextured \
        else _getSocketValue(satSocket, SocketValueType.Float)

    # A gain of 1 or less can never push s past 1, so the common case stays a single plugin.
    if not isTextured and gain <= 1.0:
        return gain

    hsvDesc = PluginDesc(Names.nextVirtualNode(nodeCtx, "TexRGBToHSV"), "TexRGBToHSV")
    hsvDesc.setAttribute("inRgb", inputColor)
    hsv = _exportCyclesPluginWithStats(nodeCtx, hsvDesc)

    # 'green' is the saturation, and the channel outputs read color_a - so the HSV colour has to
    # be re-hosted as color_a to get at it.
    chanDesc = PluginDesc(Names.nextVirtualNode(nodeCtx, "TexAColorOp"), "TexAColorOp")
    chanDesc.setAttribute("mode", 0) # result_a
    chanDesc.setAttribute("color_a", hsv)
    chan = _exportCyclesPluginWithStats(nodeCtx, chanDesc)

    # A fully desaturated pixel has s = 0, and its gain cannot change the colour either way.
    sat = _texFloatOp(nodeCtx, 8, AttrPlugin(chan.name, "green"), 1e-4)   # max
    return _texFloatOp(nodeCtx, 7, _texFloatOp(nodeCtx, 1, 1.0, sat), gain)   # min(1/s, k)


def _exportCyclesHSVNode(nodeCtx: NodeContext):
    # Note that this public is different than the one used by VBLD but support everything needed.
    texName = Names.treeNode(nodeCtx)
    texDesc = PluginDesc(texName, "TexColorCorrect")
    # The default Hue in blender is 0.5, 0 means -180, 1 means +180, so we subtract 180 now.
    hue = _convertBlenderHSVToVRay(nodeCtx, nodeCtx.node.inputs["Hue"])
    if isinstance(hue, AttrPlugin):
        subtractName = Names.nextVirtualNode(nodeCtx, "TexFloatOp")
        subtractDesc = PluginDesc(subtractName, "TexFloatOp")
        subtractDesc.setAttribute("mode", 3) # subtract
        subtractDesc.setAttribute("float_a", hue)
        subtractDesc.setAttribute("float_b", 180.0)
        hue = _exportCyclesPluginWithStats(nodeCtx, subtractDesc)
    else:
        # A negative hue_shift rotates the wrong way, so wrap into [0, 360) - the rotation is
        # periodic, and -90 has to be sent as 270. Measured: Hue 0.25 came out +120 deg off.
        texDesc.setAttribute("hue_shift", (hue - 180.0) % 360.0)
    _exportCyclesFloatAttribute(nodeCtx, texDesc, "Value", "val_gain")
    colorSocket = nodeCtx.node.inputs["Color"]
    if _isSocketConnected(colorSocket):
        inputColor = _exportCyclesLinkedSocket(nodeCtx, colorSocket)
    else:
        inputColor = _getSocketValue(colorSocket, SocketValueType.Color)
    texDesc.setAttribute("in_color", inputColor)
    texDesc.setAttribute("sat_gain", _saturationGain(nodeCtx, inputColor))
    colorCorrect = _exportCyclesPluginWithStats(nodeCtx, texDesc)
    factorSocket = nodeCtx.node.inputs["Fac"]
    return _wrapColorFactorSocket(nodeCtx, factorSocket, inputColor, colorCorrect)

def _exportCyclesGammaNode(nodeCtx: NodeContext):
    texName = Names.treeNode(nodeCtx)
    # Note that this plugin is different than the one used by VBLD but support everything needed.
    texDesc = PluginDesc(texName, "TexColorCorrect")
    gammaSocket = nodeCtx.node.inputs["Gamma"]
    if _isSocketConnected(gammaSocket):
        invertName = Names.nextVirtualNode(nodeCtx, "TexFloatOp")
        invertDesc = PluginDesc(invertName, "TexFloatOp")
        invertDesc.setAttribute("float_a", 1.0)
        _exportCyclesFloatAttribute(nodeCtx, invertDesc, gammaSocket, "float_b")
        invertDesc.setAttribute("mode", 1) # ratio
        gamma = _exportCyclesPluginWithStats(nodeCtx, invertDesc)
        gamma = _wrapFloatToColor(nodeCtx, gamma)
        texDesc.setAttribute("col_gamma", gamma)
    else:
        gamma = 1.0 / _getSocketValue(gammaSocket, SocketValueType.Float)
        texDesc.setAttribute("col_gamma", Color((gamma, gamma, gamma)))
    _exportCyclesColorAttribute(nodeCtx, texDesc, "Color", "in_color")
    return _exportCyclesPluginWithStats(nodeCtx, texDesc)

def _exportCyclesBrightnessNode(nodeCtx: NodeContext):
    texName = Names.treeNode(nodeCtx)
    texDesc = PluginDesc(texName, "ColorCorrection")
    contrastSocket = nodeCtx.node.inputs["Contrast"]
    brightnessSocket = nodeCtx.node.inputs["Bright"]
    if _isSocketConnected(brightnessSocket) or _isSocketConnected(contrastSocket):
        NodeContext.registerError("V-Ray does not support textured Brightness/Contrast node inputs")
    # Cycles is (1 + contrast) * c + (bright - contrast/2), ColorCorrection is
    # contrast * (c - 0.5) + 0.5 + brightness; the offsets only agree with Bright unscaled.
    texDesc.setAttribute("contrast", _getSocketValue(contrastSocket, SocketValueType.Float) + 1)
    texDesc.setAttribute("brightness", _getSocketValue(brightnessSocket, SocketValueType.Float))
    _exportCyclesColorAttribute(nodeCtx, texDesc, "Color", "texture_map")
    return _exportCyclesPluginWithStats(nodeCtx, texDesc)

_NOISE_DIMENSIONS_MAP = {'1D': 1, '2D': 2, '3D': 3, '4D': 4}
_NOISE_TYPE_MAP = {
    'MULTIFRACTAL':        1,
    'RIDGED_MULTIFRACTAL': 2,
    'HYBRID_MULTIFRACTAL': 3,
    'FBM':                 4,
    'HETERO_TERRAIN':      5,
}

def _exportCyclesNoiseNode(nodeCtx: NodeContext, nodeLink: FarNodeLink):
    node: bpy.types.ShaderNodeTexNoise = nodeCtx.node
    texName = Names.treeNode(nodeCtx)
    texDesc = PluginDesc(texName, "TexCyclesNoise")

    dimensions = _NOISE_DIMENSIONS_MAP.get(node.noise_dimensions, 3)
    texDesc.setAttribute("dimensions", dimensions)
    texDesc.setAttribute("type", _NOISE_TYPE_MAP.get(node.noise_type, 4))
    texDesc.setAttribute("normalize", node.normalize)
    
    _exportCyclesFloatAttribute(nodeCtx, texDesc, "Scale",      "scale")
    _exportCyclesFloatAttribute(nodeCtx, texDesc, "Detail",     "detail")
    _exportCyclesFloatAttribute(nodeCtx, texDesc, "Roughness",  "roughness")
    _exportCyclesFloatAttribute(nodeCtx, texDesc, "Lacunarity", "lacunarity")
    _exportCyclesFloatAttribute(nodeCtx, texDesc, "Distortion", "distortion")
    
    if node.noise_type in ('RIDGED_MULTIFRACTAL', 'HYBRID_MULTIFRACTAL', 'HETERO_TERRAIN'):
        _exportCyclesFloatAttribute(nodeCtx, texDesc, "Offset", "offset")
    
    if node.noise_type in ('RIDGED_MULTIFRACTAL', 'HYBRID_MULTIFRACTAL'):
        _exportCyclesFloatAttribute(nodeCtx, texDesc, "Gain", "gain")
    

    # W socket is only present in 1D and 4D modes.
    if dimensions in (1, 4):
        _exportCyclesFloatAttribute(nodeCtx, texDesc, "W", "w_coord")

    if dimensions != 1:
        uvwMatrix = _computeImageMappingTransform(node.texture_mapping)
        if _isSocketConnected(node.inputs["Vector"]):
            nodeCtx.pushUVWTransform(uvwMatrix)
            _exportCyclesVectorAttribute(nodeCtx, texDesc, "Vector")
            nodeCtx.popUVWTransform()
        else:
            nodeCtx.pushUVWTransform(uvwMatrix)
            uvwgen = _exportCyclesGeneratedCoordsUVWGen(nodeCtx, fromMappingNode=False)
            nodeCtx.popUVWTransform()
            texDesc.setAttribute("uvwgen", uvwgen)

    return _exportCyclesPluginWithStats(nodeCtx, texDesc)

