from vray_blender.bin import VRayBlenderLib as vray
from vray_blender.exporting.mtl_export import MtlExporter
from vray_blender.exporting.node_export import exportLinkedSocket
from vray_blender.exporting.node_exporters.uvw_node_export import exportDefaultUVWGenChannel
from vray_blender.exporting.tools import resolveNodeSocket, FarNodeLink
from vray_blender.lib.attribute_utils import toColor
from vray_blender.lib.defs import AttrPlugin, NodeContext, PluginDesc
from vray_blender.lib.export_utils import wrapAsTexture
from vray_blender.lib.names import Names
from vray_blender.lib.plugin_utils import updateValue
from vray_blender.nodes.tools import isInputSocketLinked
from vray_blender.plugins.texture.TexBitmap import getImageFilePath
from vray_blender.plugins.texture.TexRemap import fillSplineData
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

    vray.pluginCreate(nodeCtx.exporterCtx.renderer, pluginDesc.name, pluginDesc.type, allowTypeChanges)
    for name, value in pluginDesc.attrs.items():
        updateValue(nodeCtx.exporterCtx.renderer, pluginDesc.name, name, value)
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
    return value if type == SocketValueType.Float else toColor(value)


def _getSocketValue(socket: bpy.types.NodeSocket, type: SocketValueType) -> Color | float:
    if socket.is_output or not isInputSocketLinked(socket):
        return _getResolvedSocketValue(socket, type)

    if resolvedSocket := resolveNodeSocket(socket):
        return _getResolvedSocketValue(resolvedSocket, type)
    return _getResolvedSocketValue(socket, type)

def _isSocketTexture(socket: bpy.types.NodeSocket):
    # The check the two basic cases - if we have an unmuted conneciton. Then check if the connection
    # is meaningful i.e. going to an actual node not just re-routed to a node group.
    if not isInputSocketLinked(socket):
        return False
    resolvedSocket = resolveNodeSocket(socket)
    if not resolvedSocket or not isInputSocketLinked(resolvedSocket):
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
    if _isSocketTexture(attribute):
        if sockValue := _exportCyclesLinkedSocket(nodeCtx, attribute):
            for param in plgParamNames:
                pluginDesc.setAttribute(param, sockValue)
    else:
        for param in plgParamNames:
            pluginDesc.setAttribute(param, _getSocketValue(attribute, SocketValueType.Color))

def _exportCyclesFloatAttribute(nodeCtx: NodeContext, pluginDesc: PluginDesc, attr: str|bpy.types.NodeSocketFloat, *plgParamNames: str):
    attribute = nodeCtx.node.inputs[attr] if isinstance(attr, str) else attr
    if _isSocketTexture(attribute):
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
    if not _isSocketTexture(strengthSocket):
        if _getSocketValue(strengthSocket, SocketValueType.Float) == 1.0:
            _exportCyclesColorAttribute(nodeCtx, pluginDesc, colorAttrName, plgParamName)
            return
        else:
            colorSocket = nodeCtx.node.inputs[colorAttrName]
            if not _isSocketTexture(colorSocket):
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

def _exportCyclesWrappedAttribute(nodeCtx: NodeContext, attributeName: str):
    # Export a color and optionally wraps it in a TexAColor if necessary.
    colorAttr = nodeCtx.node.inputs[attributeName]
    if _isSocketTexture(colorAttr):
        return _exportCyclesLinkedSocket(nodeCtx, colorAttr)
    else:
        return wrapAsTexture(nodeCtx, _getSocketValue(colorAttr, SocketValueType.Color))

def _exportCyclesVectorAttribute(nodeCtx: NodeContext, pluginDesc: PluginDesc, vectorAttrName: str, additionalMatrix: Matrix = Matrix()):
    vectorSocket = nodeCtx.node.inputs[vectorAttrName]
    if _isSocketTexture(vectorSocket):
        uvwgen = exportLinkedSocket(nodeCtx, vectorSocket)
        # Blender uses Vector type for UVWGen so it's possible to sometimes end up here with a color as UVWGen.
        if uvwgen is not None and (not isinstance(uvwgen, AttrPlugin) or 'UVWGen' not in uvwgen.pluginType):
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

def _convertBlenderHSVToVRay(nodeCtx: NodeContext, hueSocket: bpy.types.NodeSocket) -> float | AttrPlugin:
    # V-Ray expects [0-360] hue, blender uses values in the range [0-1] so we multiply by 360
    if _isSocketTexture(hueSocket):
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
    if not _isSocketTexture(factorSocket):
        if math.isclose(_getSocketValue(factorSocket, SocketValueType.Float), 0.0):
            return color1
        if math.isclose(_getSocketValue(factorSocket, SocketValueType.Float), 1.0):
            return color2
    texMixName = Names.nextVirtualNode(nodeCtx, "TexMix")
    texMixDesc = PluginDesc(texMixName, "TexMix")
    texMixDesc.setAttribute("color1", color1)
    texMixDesc.setAttribute("color2", color2)
    if _isSocketTexture(factorSocket):
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
    return _isSocketTexture(socket) or _getSocketValue(socket, SocketValueType.Float) != 0.0

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

def _exportCyclesBsdfPrincipled(nodeCtx: NodeContext):
    node: bpy.types.ShaderNodeBsdfPrincipled = nodeCtx.node
    pluginName = Names.treeNode(nodeCtx)
    mtlDesc = PluginDesc(pluginName, "BRDFVRayMtl")

    baseColorSocket = node.inputs["Base Color"]
    baseColor = _exportCyclesLinkedSocket(nodeCtx, baseColorSocket) if _isSocketTexture(baseColorSocket) else _getSocketValue(baseColorSocket, SocketValueType.Color)
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

    _exportCyclesFloatAttribute(nodeCtx, mtlDesc, "IOR", "refract_ior")

    _exportCyclesMixedAttribute(nodeCtx, mtlDesc, "Specular Tint", "Specular IOR Level", "reflect")

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
        _exportCyclesFloatAttribute(nodeCtx, mtlDesc, thinFilmSocket, "thin_film_thickness_min")
        mtlDesc.setAttribute("thin_film_on", True)
    else:
        mtlDesc.setAttribute("thin_film_on", False)

    sssWeightSocket = nodeCtx.node.inputs["Subsurface Weight"]
    transmissionWeight = nodeCtx.node.inputs["Transmission Weight"]
    if _isSocketTexture(transmissionWeight):
        transmission = exportLinkedSocket(nodeCtx, transmissionWeight)
        refract = _wrapFloatToColor(transmission)
    else:
        refract = _getSocketValue(transmissionWeight, SocketValueType.Color)
    mtlDesc.setAttribute("refract", refract)

    # To match Cycles' diffuse dimming we would ideally use option_energy_mode=1(monochrome),
    # however, currently it also affects the coat layer. So here we mimic the diffuse dimming
    # by hand. This was recently changed in cgrepo and this code will have to revised once we
    # switch to stable/7.2. This is currently somewhat wrong, but can't be done properly in
    # export.

    # diffuse = (1-transmission)*base color
    if _isSocketTexture(transmissionWeight):
        invertTransmissionName = Names.nextVirtualNode(nodeCtx, "TexInvertFloat")
        invertTransmissionDesc = PluginDesc(invertTransmissionName, "TexInvertFloat")
        _exportCyclesFloatAttribute(nodeCtx, invertTransmissionDesc, transmissionWeight, "texture")
        invertTransmission = _exportCyclesPluginWithStats(nodeCtx, invertTransmissionDesc)
    else:
        invertTransmission = 1 - transmissionWeight.default_value

    simpleDiffuse=False
    if isinstance(invertTransmission, float):
        if isinstance(baseColor, Color):
            mtlDesc.setAttribute("diffuse", baseColor * invertTransmission)
            simpleDiffuse = True
        elif invertTransmission == 1.0:
            mtlDesc.setAttribute("diffuse", baseColor)
            simpleDiffuse = True

    if not simpleDiffuse:
        texAColorOpName = Names.nextVirtualNode(nodeCtx, "TexAColorOp")
        texAColorOpDesc = PluginDesc(texAColorOpName, "TexAColorOp")
        texAColorOpDesc.setAttribute("color_a", baseColor)
        texAColorOpDesc.setAttribute("mult_a", invertTransmission)
        diffuse = _exportCyclesPluginWithStats(nodeCtx, texAColorOpDesc)
        mtlDesc.setAttribute("diffuse", diffuse)

    # TODO: Remove the mess above after we switch to stable/7.2
    # mtlDesc.setAttribute("diffuse", baseColor)

    if node.subsurface_method != 'BURLEY':
        sssAnisotropySocket = nodeCtx.node.inputs["Subsurface Anisotropy"]
        if _isSocketTexture(sssAnisotropySocket):
            NodeContext.registerError("V-Ray does not support textured Subsurface Anisotropy")
        mtlDesc.setAttribute("translucency_scatter_dir", _getSocketValue(sssAnisotropySocket, SocketValueType.Float))
    else:
        mtlDesc.setAttribute("translucency_scatter_dir", 0.0)

    hasTransmission = _isSocketNonZero(transmissionWeight)
    hasSSS = _isSocketNonZero(sssWeightSocket)
    # This is a bit of a mess... Ignore it for now...
    magicNumber = 1.0
    if hasTransmission and not hasSSS:
        # Try and use the non-textured fog_color parameter as it us supported on GPU, fog_color_tex is not.
        if isinstance(baseColor, AttrPlugin):
            mtlDesc.setAttribute("fog_color", WHITE_COLOR)
            mtlDesc.setAttribute("fog_color_tex", baseColor)
        else:
            mtlDesc.setAttribute("fog_color", baseColor)
            mtlDesc.setAttribute("fog_color_tex", AttrPlugin())
        mtlDesc.setAttribute("fog_depth", 1.0)
        mtlDesc.setAttribute("translucency", 0) # none
    elif not hasTransmission and hasSSS:
        NodeContext.registerError("V-Ray does not properly support Cycles SSS")
        _exportCyclesFloatAttribute(nodeCtx, mtlDesc, sssWeightSocket, "translucency_amount")
        mtlDesc.setAttribute("translucency", 6) # sss

        _exportCyclesColorAttribute(nodeCtx, mtlDesc, "Subsurface Radius", "fog_color_tex")

        # fog_depth = sss_radius * magic number
        texFloatOpName = Names.nextVirtualNode(nodeCtx, "TexFloatOp")
        texFloatOpDesc = PluginDesc(texFloatOpName, "TexFloatOp")
        val = nodeCtx.node.inputs["Subsurface Scale"].default_value
        _exportCyclesFloatAttribute(nodeCtx, texFloatOpDesc, "Subsurface Scale", "float_a")
        texFloatOpDesc.setAttribute("float_b", magicNumber)
        texFloatOpDesc.setAttribute("mode", 0) # product
        fogDepth = _exportCyclesPluginWithStats(nodeCtx, texFloatOpDesc)
        mtlDesc.setAttribute("fog_depth", fogDepth)

    elif hasTransmission and hasSSS:
        NodeContext.registerError("V-Ray does not properly support Cycles SSS")
        _exportCyclesFloatAttribute(nodeCtx, mtlDesc, sssWeightSocket, "translucency_amount")
        mtlDesc.setAttribute("translucency", 6) # sss

        # This is a bit of a mess... But basically when we have both transmission and SSS
        # interpolate between the two. Somewhat based on V-Ray's openpbr.
        mixPluginName = Names.nextVirtualNode(nodeCtx, "TexFloatComposite")
        mixPluginDesc = PluginDesc(mixPluginName, "TexFloatComposite")
        _exportCyclesFloatAttribute(nodeCtx, mixPluginDesc, sssWeightSocket, "float_a")
        _exportCyclesFloatAttribute(nodeCtx, mixPluginDesc, transmissionWeight, "factor")
        mixPluginDesc.setAttribute("float_b", 1.0)
        mixPluginDesc.setAttribute("operation", 2) # mix
        mixPlugin = _exportCyclesPluginWithStats(nodeCtx, mixPluginDesc)

        ratioTexName = Names.nextVirtualNode(nodeCtx, "TexFloatOp")
        ratioTexDesc = PluginDesc(ratioTexName, "TexFloatOp")
        _exportCyclesFloatAttribute(nodeCtx, ratioTexDesc, transmissionWeight, "float_a")
        ratioTexDesc.setAttribute("float_b", mixPlugin)
        ratioTexDesc.setAttribute("mode", 1) # ratio
        ratioTex = _exportCyclesPluginWithStats(nodeCtx, ratioTexDesc)

        blendName = Names.nextVirtualNode(nodeCtx, "TexBlend")
        blendDesc = PluginDesc(blendName, "TexBlend")
        _exportCyclesColorAttribute(nodeCtx, blendDesc, "Subsurface Radius", "color_a")
        blendDesc.setAttribute("color_b", baseColor)
        blendDesc.setAttribute("blend_amount", ratioTex)
        fogColor = _exportCyclesPluginWithStats(nodeCtx, blendDesc)

        mtlDesc.setAttribute("fog_color_tex", fogColor)

        radiusMultName = Names.nextVirtualNode(nodeCtx, "TexFloatOp")
        radiusMultDesc = PluginDesc(radiusMultName, "TexFloatOp")
        _exportCyclesFloatAttribute(nodeCtx, radiusMultDesc, "Subsurface Scale", "float_a")
        radiusMultDesc.setAttribute("float_b", magicNumber)
        radiusMultDesc.setAttribute("mode", 0) # product
        radiusMult = _exportCyclesPluginWithStats(nodeCtx, radiusMultDesc)

        compositeName = Names.nextVirtualNode(nodeCtx, "TexFloatOp")
        compositeDesc = PluginDesc(compositeName, "TexFloatOp")
        compositeDesc.setAttribute("float_a", radiusMult)
        compositeDesc.setAttribute("float_b", ratioTex)
        compositeDesc.setAttribute("mode", 0) # product
        fogDepth = _exportCyclesPluginWithStats(nodeCtx, compositeDesc)

        mtlDesc.setAttribute("fog_depth", fogDepth)
    else:
        mtlDesc.setAttribute("translucency", 0) # none
        mtlDesc.setAttribute("translucency_amount", 0.0)

    # The formula here is purely based on observation. The anisotropy in Cycles seems to be clamped
    # between [0-1] and seems to somewhat match V-Ray's when rescaled in the range [0.0, 0.6].
    anisotropySocket = node.inputs["Anisotropic"]
    if not _isSocketTexture(anisotropySocket):
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
    mtlDesc.setAttribute("fresnel_ior_lock", True)
    mtlDesc.setAttribute("opacity_source", 0) # gray scale alpha
    mtlDesc.setAttribute("option_shading_model", 1) # openpbr for the fuzz sheen
    mtlDesc.setAttribute("fog_unit_scale_on", True)
    mtlDesc.setAttribute("anisotropy_derivation", 0) # local axis
    mtlDesc.setAttribute("anisotropy_axis", 2) # Z axis
    mtlDesc.setAttribute("brdf_type", 4) # ggx
    mtlDesc.setAttribute("fog_mult", 0.0)
    mtlDesc.setAttribute("fresnel", True) # enable fresnel reflections
    mtlDesc.setAttribute("translucency_scatter_coeff", 1.0) # full scattering
    mtlDesc.setAttribute("self_illumination_gi", True)
    mtlDesc.setAttribute("gtr_energy_compensation", 2)
    mtlDesc.setAttribute("option_reflect_on_back", True)
    # TODO: Enable this when switching to stable 7.2
    # mtlDesc.setAttribute("option_energy_mode", 1) # monochrome
    # TODO: Export from cycles render engine->Light Paths, these are default-ish
    mtlDesc.setAttribute("reflect_depth", 4)
    mtlDesc.setAttribute("refract_depth", 12)

    if _isSocketTexture(nodeCtx.node.inputs["Tangent"]):
        NodeContext.registerError("V-Ray does not support tangent textures")

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
    if not _isSocketTexture(anisotropySocket):
        anisotropy = _clamp(_getSocketValue(anisotropySocket, SocketValueType.Float), -0.99, 0.99)
    else:
        anisotropy = _exportCyclesLinkedSocket(nodeCtx, anisotropySocket)
        anisotropy = _wrapClampPlugin(nodeCtx, anisotropy, -0.99, 0.99)
    mtlDesc.setAttribute("anisotropy", anisotropy)
    mtlDesc.setAttribute("coat_anisotropy", anisotropy)
    rotationSocket = nodeCtx.node.inputs["Rotation"]
    if not _isSocketTexture(rotationSocket):
        mtlDesc.setAttribute("anisotropy_rotation", _getSocketValue(rotationSocket, SocketValueType.Float) + 0.25)
    else:
        texFloatOpName = Names.nextVirtualNode(nodeCtx, "TexFloatOp")
        texFloatOpDesc = PluginDesc(texFloatOpName, "TexFloatOp")
        texFloatOpDesc.setAttribute("float_a", anisotropy)
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

def _exportCyclesTranslucentBsdf(nodeCtx: NodeContext):
    pluginName = Names.treeNode(nodeCtx)
    mtlDesc = PluginDesc(pluginName, "BRDFVRayMtl")

    _exportCyclesColorAttribute(nodeCtx, mtlDesc, "Color", "fog_color")
    _exportCyclesColorAttribute(nodeCtx, mtlDesc, "Normal", "bump_map")

    mtlDesc.setAttribute("diffuse", BLACK_COLOR)
    mtlDesc.setAttribute("refract", WHITE_COLOR)
    mtlDesc.setAttribute("fog_mult", 0.0)
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
    if _isSocketTexture(colorSocket):
        mtlDesc.setAttribute("fog_color_tex", _exportCyclesLinkedSocket(nodeCtx, colorSocket))
    else:
        mtlDesc.setAttribute("fog_color", _getSocketValue(colorSocket, SocketValueType.Color))
    _exportCyclesFloatAttribute(nodeCtx, mtlDesc, "Roughness", "refract_glossiness")
    _exportCyclesFloatAttribute(nodeCtx, mtlDesc, "IOR", "refract_ior")
    _exportCyclesColorAttribute(nodeCtx, mtlDesc, "Normal", "bump_map")

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

def _exportCyclesBlendShaderNode(nodeCtx: NodeContext, isMix: bool):
    pluginName = Names.treeNode(nodeCtx)
    mtlDesc = PluginDesc(pluginName, "BRDFLayered")

    brdfs, weights = [], []

    baseLayer = nodeCtx.node.inputs["Shader"]
    if _isSocketTexture(baseLayer):
        material = exportLinkedSocket(nodeCtx, baseLayer)
        if not material or not isinstance(material, AttrPlugin):
            material = MtlExporter.exportDefaultMaterial(nodeCtx.exporterCtx)
        brdfs.append(material)
        weight = wrapAsTexture(nodeCtx, WHITE_COLOR)
        weights.append(weight)

    layer = nodeCtx.node.inputs["Shader_001"]
    if _isSocketTexture(layer):
        material = exportLinkedSocket(nodeCtx, layer)
        if not material or not isinstance(material, AttrPlugin):
            material = MtlExporter.exportDefaultMaterial(nodeCtx.exporterCtx)
        brdfs.append(material)
        if isMix:
            factorSocket = nodeCtx.node.inputs["Fac"]
            if _isSocketTexture(factorSocket):
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

    return _exportCyclesPluginWithStats(nodeCtx, mtlDesc)

def _exportCyclesCheckerTexture(nodeCtx: NodeContext, nodeLink: FarNodeLink):
    # The blender checker node is very very very weird...
    pluginName = Names.treeNode(nodeCtx)
    texDesc = PluginDesc(pluginName, "TexChecker")

    if nodeLink.from_socket.name == "Color":
        _exportCyclesColorAttribute(nodeCtx, texDesc, "Color1", "black_color")
        _exportCyclesColorAttribute(nodeCtx, texDesc, "Color2", "white_color")
    elif nodeLink.from_socket.name == "Fac":
        texDesc.setAttribute("white_color", BLACK_COLOR)
        texDesc.setAttribute("black_color", WHITE_COLOR)

    scaleSocket = nodeCtx.node.inputs['Scale']
    if _isSocketTexture(scaleSocket):
        nodeCtx.registerError("V-Ray does not support textured scale of Checker texture")

    scale = _getSocketValue(scaleSocket, SocketValueType.Float) / 2.0
    smat = Matrix.Scale(scale, 4, Vector((1, 0, 0))) @ Matrix.Scale(scale, 4, Vector((0, 1, 0))) @ Matrix.Scale(scale, 4, Vector((0, 0, 1)))
    vectorSocket = nodeCtx.node.inputs["Vector"]
    if _isSocketTexture(vectorSocket):
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
    if nodeLink.from_socket.name == "Fac":
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
            case 'LOGARITHM': mode = 13 # no base log
            case 'SQRT': mode = 15
            case 'ABSOLUTE': mode = 9
            case 'EXPONENT': mode = 11
            case 'MINIMUM': mode = 7
            case 'MAXIMUM': mode = 8
            case 'FLOOR' | 'TRUNCATE': mode = 12
            case 'CEIL' | 'ROUND': mode = 10
            case 'SINE': mode = 4
            case 'COSINE': mode = 6
            case 'TANGENT': mode = 18
            case 'ARCSINE': mode = 19
            case 'ARCCOSINE': mode = 20
            case 'ARCTANGENT': mode = 21
            case 'ARCTAN2 ': mode = 22
            case 'MODULO': mode = 17

            case 'FRACT':
                mode = 16
                texDesc.setAttribute("float_b", 1.0)
            case 'RADIANS':
                mode = 0
                DEG_TO_RAD = math.pi / 180.0
                texDesc.setAttribute("float_b", DEG_TO_RAD)
            case 'DEGREES':
                mode = 0
                RAD_TO_DEG = 180.0 / math.pi
                texDesc.setAttribute("float_b", RAD_TO_DEG)
            case 'INVERSE_SQRT':
                mode = 15

        texDesc.setAttribute("mode", mode)

        attrPlugin = _exportCyclesPluginWithStats(nodeCtx, texDesc, True)

        if mode == 'INVERSE_SQRT':
            pluginName = Names.nextVirtualNode(nodeCtx, "TexInvertFloat")
            texInvertDesc = PluginDesc(pluginName, "TexInvertFloat")
            texInvertDesc.setAttribute("texture", attrPlugin)

            attrPlugin = _exportCyclesPluginWithStats(nodeCtx, texInvertDesc)

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
    if _isSocketTexture(colorSocket):
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

def _exportCyclesImageNode(nodeCtx: NodeContext, nodeLink: FarNodeLink, isEnvironment = False):
    node: bpy.types.ShaderNodeTexImage = nodeCtx.node
    pluginName = Names.treeNode(nodeCtx)
    texBitmapDesc = PluginDesc(pluginName, "TexBitmap")
    bitmapBufferName = Names.nextVirtualNode(nodeCtx, "BitmapBuffer")
    bitmapBufferDesc = PluginDesc(bitmapBufferName, "BitmapBuffer")
    # TODO: Images in scenes?
    image: bpy.types.Image = node.image
    if image is not None:
        if image.source != 'FILE':
            NodeContext.registerError("V-Ray currently  supports only file images")
            return INVALID_COLOR

        imagePath = getImageFilePath(image)

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
    bitmapBufferDesc.setAttribute("file", bpy.path.abspath(imagePath))

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
        uvwgenDesc.setAttribute("tex_transform", nodeCtx.getUVWTransform())
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

    if image and image.alpha_mode in ('PREMUL', 'STRAIGHT'):
        texAColorOpName = Names.nextVirtualNode(nodeCtx, "TexAColorOp")
        texAColorOpDesc = PluginDesc(texAColorOpName, "TexAColorOp")
        texAColorOpDesc.setAttribute("color_a", bitmapPlugin)
        texAColorOpDesc.setAttribute("mult_a", bitmapPluginAlphaOutput)
        return _exportCyclesPluginWithStats(nodeCtx, texAColorOpDesc)

    return bitmapPlugin

def _exportCyclesUVWMapNode(nodeCtx: NodeContext):
    node: bpy.types.ShaderNodeNormalMap = nodeCtx.node
    pluginName = Names.treeNode(nodeCtx)
    uvwgenDesc = PluginDesc(pluginName, "UVWGenMayaPlace2dTexture")
    uvwgenDesc.setAttribute("uv_set_name", node.uv_map)
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

def _exportCyclesRampNode(nodeCtx: NodeContext, nodeLink: FarNodeLink):
    node: bpy.types.ShaderNodeValToRGB = nodeCtx.node
    pluginName = Names.treeNode(nodeCtx)
    texDesc = PluginDesc(pluginName, "TexGradRamp")

    positions, colors = [], []

    ramp: bpy.types.ColorRamp = node.color_ramp
    for e in ramp.elements:
        positions.append(e.position)
        colors.append(wrapAsTexture(nodeCtx, toColor(e.color)))

    interpolation = 0
    match ramp.interpolation:
        case 'LINEAR': interpolation = 1
        case 'EASE': interpolation = 4 # spline, exact match
        case 'CONSTANT': interpolation = 0 # none, exact match
        case 'B_SPLINE': interpolation = 4 # spline
        case 'CARDINAL': interpolation = 4 # spline

    _exportCyclesFloatAttribute(nodeCtx, texDesc, "Fac", "gradient_position")

    texDesc.setAttribute("interpolation", interpolation)
    texDesc.setAttribute("positions", positions)
    texDesc.setAttribute("colors", colors) # TODO: check if I need wrapper for colors?
    texDesc.setAttribute("gradient_type", 12) # position ramp

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
    if _isSocketTexture(vectorSocket):
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

def _exportCyclesGeneratedCoordsUVWGen(nodeCtx, fromMappingNode=True):
    # This does not handle Texture Space.
    pluginName = Names.treeNode(nodeCtx) if fromMappingNode else Names.nextVirtualNode(nodeCtx, "UVWGenObjectBBox")
    uvwgenDesc = PluginDesc(pluginName, "UVWGenObjectBBox")
    uvwgenDesc.setAttribute("uvw_transform", nodeCtx.getUVWTransform())
    return _exportCyclesPluginWithStats(nodeCtx, uvwgenDesc, fromMappingNode)

def _exportCyclesTextureCoordinatesNode(nodeCtx: NodeContext, nodeLink: FarNodeLink):
    node: bpy.types.ShaderNodeTexCoord = nodeCtx.node
    fromSocket = nodeLink.from_socket
    if fromSocket.name == "Generated":
        return _exportCyclesGeneratedCoordsUVWGen(nodeCtx)
    elif fromSocket.name in ["Normal", "Reflection", "Camera"]:
        uvwgenName = Names.treeNode(nodeCtx)
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
        uvwgenName = Names.treeNode(nodeCtx)
        uvwgenDesc = PluginDesc(uvwgenName, "UVWGenChannel")
        uvwgenDesc.setAttribute("uvw_transform", nodeCtx.getUVWTransform())
        uvwgenDesc.setAttribute("uvw_channel", -1)
        return _exportCyclesPluginWithStats(nodeCtx, uvwgenDesc, True)
    elif fromSocket.name in [ "Object", "Window" ]:
        uvwgenName = Names.treeNode(nodeCtx)
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
        if _isSocketTexture(sizeSocket):
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




def _exportSplineColorData(seperateChannelSplineDesc: PluginDesc, curveMapping: bpy.types.CurveMapping):
    rPoints, rValues, rTypes = fillSplineData(curveMapping.curves[0], curveMapping.extend)
    seperateChannelSplineDesc.setAttribute("red_positions", rPoints)
    seperateChannelSplineDesc.setAttribute("red_values", rValues)
    seperateChannelSplineDesc.setAttribute("red_types", rTypes)

    gPoints, gValues, gTypes = fillSplineData(curveMapping.curves[1], curveMapping.extend)
    seperateChannelSplineDesc.setAttribute("green_positions", gPoints)
    seperateChannelSplineDesc.setAttribute("green_values", gValues)
    seperateChannelSplineDesc.setAttribute("green_types", gTypes)

    bPoints, bValues, bTypes = fillSplineData(curveMapping.curves[2], curveMapping.extend)
    seperateChannelSplineDesc.setAttribute("blue_positions", bPoints)
    seperateChannelSplineDesc.setAttribute("blue_values", bValues)
    seperateChannelSplineDesc.setAttribute("blue_types", bTypes)

def _exportCyclesCurvesNode(nodeCtx: NodeContext, isColor: bool):
    node: bpy.types.ShaderNodeRGBCurve = nodeCtx.node
    # Here we might export 2 TexRemaps - one for the full Color spline and one for the seperate channels ramps
    curveMapping: bpy.types.CurveMapping = node.mapping
    colorSocket = node.inputs["Color"] if isColor else nodeCtx.node.inputs["Vector"]
    input = None
    if _isSocketTexture(colorSocket):
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

            positions, values, types = fillSplineData(rgbCurve, curveMapping.extend)

            for param in ["red_positions", "blue_positions", "green_positions"]:
                rgbSplineDesc.setAttribute(param, positions)
            for param in ["red_values", "blue_values", "green_values"]:
                rgbSplineDesc.setAttribute(param, values)
            for param in ["red_types", "blue_types", "green_types"]:
                rgbSplineDesc.setAttribute(param, types)

            input = _exportCyclesPluginWithStats(nodeCtx, rgbSplineDesc, True)
    else:
        if "UVWGen" in input.pluginType:
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
    if (location is not None and _isSocketTexture(location)) or _isSocketTexture(rotation) or _isSocketTexture(scale):
        NodeContext.registerError("V-Ray does not support linked Mapping node sockets")

    transform = _computeMappingMatrix(
        Vector(_getSocketValue(location, SocketValueType.Color)) if location else Vector(),
        Vector(_getSocketValue(rotation, SocketValueType.Color)),
        Vector(_getSocketValue(scale, SocketValueType.Color)),
        node.vector_type
    )

    vectorSocket: bpy.types.NodeSocketVector = node.inputs["Vector"]
    if _isSocketTexture(vectorSocket):
        nodeCtx.pushUVWTransform(transform)
        sockValue = _exportCyclesLinkedSocket(nodeCtx, vectorSocket)
        nodeCtx.popUVWTransform()
        if isinstance(sockValue, AttrPlugin) and "UVWGen" in sockValue.pluginType:
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

def _exportCyclesAttributeNodeHelper(nodeCtx: NodeContext, nodeLink: FarNodeLink, attribute: str):
    fromSocket = nodeLink.from_socket
    pluginName = Names.treeNode(nodeCtx)
    texDesc = PluginDesc(pluginName, "TexUserColor")

    texDesc.setAttribute("user_attribute", attribute)
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
    if fromSocket.name == "Color" or fromSocket.name == "Vector" or fromSocket.name == "Alpha":
        return _exportCyclesAttributeNodeHelper(nodeCtx, nodeLink, node.attribute_name)
    elif fromSocket.name == "Fac":
        pluginName = Names.treeNode(nodeCtx)
        texDesc = PluginDesc(pluginName, "TexUserColor")

        texDesc.setAttribute("user_attribute", node.attribute_name)
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
    pluginName = Names.treeNode(nodeCtx)
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
    if _isSocketTexture(inputNormalSocket):
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
    pluginName = Names.treeNode(nodeCtx)
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

    if not _isSocketTexture(heightSocket):
        if _isSocketTexture(normalSocket):
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
    if _isSocketTexture(normalSocket):
        # Strength is technically a blend between the original normal and the bump normal
        # but for now all sockets are just multiplied together.
        if not _isSocketTexture(strengthSocket) and not _isSocketTexture(distanceSocket):
            bump = _exportCyclesLinkedSocket(nodeCtx, heightSocket) if _isSocketTexture(heightSocket) else _getSocketValue(heightSocket, SocketValueType.Float)
            bump = _wrapFloatToColor(nodeCtx, bump)
            texDesc.setAttribute("bump_map_mult", bumpMultDefault)
        else:
            strength = _exportCyclesLinkedSocket(nodeCtx, strengthSocket) if _isSocketTexture(strengthSocket) else _getSocketValue(strengthSocket, SocketValueType.Float)
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
        if not _isSocketTexture(strengthSocket) and not _isSocketTexture(distanceSocket):
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
        bump = _exportCyclesLinkedSocket(nodeCtx, heightSocket) if _isSocketTexture(heightSocket) else _getSocketValue(heightSocket, SocketValueType.Float)
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

def _exportCyclesGeometryNode(nodeCtx: NodeContext, nodeLink: FarNodeLink):
    fromSocket = nodeLink.from_socket
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
    texAColorOp.output = "red"
    if fromSocket.name == "Red":
        texAColorOp.output = "red"
    elif fromSocket.name == "Green":
        texAColorOp.output = "green"
    elif fromSocket.name == "Blue":
        texAColorOp.output = "blue"
    return texAColorOp

def _exportCyclesSeperateXYZNode(nodeCtx: NodeContext, nodeLink: FarNodeLink):
    fromSocket = nodeLink.from_socket
    pluginName = Names.treeNode(nodeCtx)
    texDesc = PluginDesc(pluginName, "TexAColorOp")
    vectorSocket = nodeCtx.node.inputs["Vector"]
    value = _exportCyclesLinkedSocket(nodeCtx, vectorSocket) if _isSocketTexture(vectorSocket) else _getSocketValue(vectorSocket, SocketValueType.Color)
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

def _exportCyclesFresnelNode(nodeCtx: NodeContext):
    NodeContext.registerError("V-Ray Fresnel will render differently, you may need to adjust your IOR values")

    pluginName = Names.treeNode(nodeCtx)
    texDesc = PluginDesc(pluginName, "TexFresnel")

    _exportCyclesFloatAttribute(nodeCtx, texDesc, "IOR", "fresnel_ior_tex")
    texFresnel = _exportCyclesPluginWithStats(nodeCtx, texDesc)
    return _exportIntensityOutput(nodeCtx, texFresnel)

def _exportLayerWeightBias(nodeCtx: NodeContext, input: AttrPlugin):
    # When nothing is connected here use the calculation from blender,
    # otherwise use schlick bias which is a bit different.
    blendSocket = nodeCtx.node.inputs["Blend"]
    if _isSocketTexture(blendSocket):
            texFloatOpName = Names.nextVirtualNode(nodeCtx, "TexFloatOp")
            texFloatOpDesc = PluginDesc(texFloatOpName, "TexFloatOp")
            texFloatOpDesc.setAttribute("float_a", input)
            _exportCyclesFloatAttribute(nodeCtx, texFloatOpDesc, blendSocket, "float_b")
            texFloatOp = _exportCyclesPluginWithStats(nodeCtx, texFloatOpDesc)
            texFloatOp.output = "bias_schlick"
            return texFloatOp
    else:
        blend = _getSocketValue(blendSocket, SocketValueType.Float)
        if blend != 0.5:
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
    pluginName = Names.treeNode(nodeCtx)

    if fromSocket.name == "Facing":
        texDesc = PluginDesc(pluginName, "TexSampler")
        texSampler = _exportCyclesPluginWithStats(nodeCtx, texDesc, True)
        texSampler.output = "facing_ratio"

        return _exportLayerWeightBias(nodeCtx, texSampler)
    elif fromSocket.name == "Fresnel":
        pluginName = Names.treeNode(nodeCtx)
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
        if _isSocketTexture(socket):
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
        factor = _exportCyclesLinkedSocket(nodeCtx, factorSocket) if _isSocketTexture(factorSocket) else _getSocketValue(factorSocket, SocketValueType.Float)
        if not isLegacy and node.clamp_factor:
            factor = _wrapClampPlugin01(nodeCtx, factor)
        floatToColor = _wrapFloatToColor(nodeCtx, factor)
        masks = [wrapAsTexture(nodeCtx, WHITE_COLOR), floatToColor]
    else:
        nodeCtx.registerError("V-Ray does not support non-uniform Vector Mix")
        # Still export the color, but V-Ray will use the intensity
        if _isSocketTexture(factorSocket):
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
    if not _isSocketTexture(factorSocket):
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
    value = _exportCyclesLinkedSocket(nodeCtx, valueSocket) if _isSocketTexture(valueSocket) else _getSocketValue(valueSocket, SocketValueType.Float)
    minSocket = nodeCtx.node.inputs["Min"]
    min = _exportCyclesLinkedSocket(nodeCtx, minSocket) if _isSocketTexture(minSocket) else _getSocketValue(minSocket, SocketValueType.Float)
    maxSocket = nodeCtx.node.inputs["Max"]
    max = _exportCyclesLinkedSocket(nodeCtx, maxSocket) if _isSocketTexture(maxSocket) else _getSocketValue(maxSocket, SocketValueType.Float)
    return _wrapClampPlugin(nodeCtx, value, min, max)

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
        texDesc.setAttribute("hue_shift", hue - 180.0)
    _exportCyclesFloatAttribute(nodeCtx, texDesc, "Saturation", "sat_gain")
    _exportCyclesFloatAttribute(nodeCtx, texDesc, "Value", "val_gain")
    colorSocket = nodeCtx.node.inputs["Color"]
    if _isSocketTexture(colorSocket):
        inputColor = _exportCyclesLinkedSocket(nodeCtx, colorSocket)
    else:
        inputColor = _getSocketValue(colorSocket, SocketValueType.Color)
    texDesc.setAttribute("in_color", inputColor)
    colorCorrect = _exportCyclesPluginWithStats(nodeCtx, texDesc)
    factorSocket = nodeCtx.node.inputs["Fac"]
    return _wrapColorFactorSocket(nodeCtx, factorSocket, inputColor, colorCorrect)

def _exportCyclesGammaNode(nodeCtx: NodeContext):
    texName = Names.treeNode(nodeCtx)
    # Note that this plugin is different than the one used by VBLD but support everything needed.
    texDesc = PluginDesc(texName, "TexColorCorrect")
    gammaSocket = nodeCtx.node.inputs["Gamma"]
    if _isSocketTexture(gammaSocket):
        invertName = Names.nextVirtualNode(nodeCtx, "TexFloatOp")
        invertDesc = PluginDesc("TexFloatOp", invertName)
        invertDesc.setAttribute("float_a", 1.0)
        invertDesc.setAttribute("mode", 1) # ratio
        gamma = _exportCyclesPluginWithStats(nodeCtx, invertDesc)
        gamma = _wrapFloatToColor(gamma)
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
    if _isSocketTexture(brightnessSocket) or _isSocketTexture(contrastSocket):
        NodeContext.registerError("V-Ray does not support textured Brightness/Contrast node inputs")
    texDesc.setAttribute("contrast", _getSocketValue(contrastSocket, SocketValueType.Float) + 1)
    texDesc.setAttribute("brightness", _getSocketValue(brightnessSocket, SocketValueType.Float) / 2)
    _exportCyclesColorAttribute(nodeCtx, texDesc, "Color", "texture_map")
    return _exportCyclesPluginWithStats(nodeCtx, texDesc)

def _exportCyclesNoiseNode(nodeCtx: NodeContext, nodeLink: FarNodeLink):
    # The node is not at all well supported but allow export for it since it's commonly used.
    NodeContext.registerError("Noise is not well supported by V-Ray. You may need you adjust your setup")
    fromSocket = nodeLink.from_socket
    texName = Names.treeNode(nodeCtx)
    texDesc = PluginDesc(texName, "TexNoiseMax")

    texDesc.setAttribute("size", 0.05)
    noisePlugin = _exportCyclesPluginWithStats(nodeCtx, texDesc)
    if fromSocket.name == "Fac":
        return _exportIntensityOutput(nodeCtx, noisePlugin)
    return noisePlugin
