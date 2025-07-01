import bpy

PluginTypes = {
    'BRDF',
    'MATERIAL',
    'PLUGIN',
    'TEXTURE',
    'UVWGEN',
}

# Attributes won't be generated for the SkippedTypes
SkippedTypes = {
    'LIST',
    'INT_LIST',
    'FLOAT_LIST',
    'VECTOR_LIST',
    'COLOR_LIST',
    'MAPCHANNEL_LIST',
    'TRANSFORM_LIST',
    'TRANSFORM_TEXTURE',
    'PLUGIN_LIST',
    'INCLUDE_EXCLUDE_LIST',
    'LIST_LIST',
    'TEXTURE_LIST',
    'FLOAT_TEXTURE_LIST',
    'STRING_LIST',
    'BOOL_LIST',
}

# A list of visible input socket types. They will be shown in both the 
# property pages an in the node itself.
NodeInputTypes = {
    'BRDF',
    "ACOLOR",
    "COLOR",
    'COLOR_TEXTURE',
    'INT_TEXTURE',
    'FLOAT_TEXTURE',
    'VECTOR_TEXTURE',
    'GEOMETRY',
    'MATERIAL',
    'OBJECT',
    'PLUGIN',
    'PLUGIN_LIST',
    'BRDF_USE',
    'INCLUDE_EXCLUDE_LIST',
    'TEXTURE',
    'UVWGEN',
    'VECTOR',
    'TRANSFORM',
    'MATRIX',
    'MATRIX_TEXTURE',
}

# A list of hidden input socket types. For them, sockets will be generated, but they will be only
# visible in the property pages and not in the node itself
HiddenNodeInputTypes = {
    'BOOL',
    "INT",
    'FLOAT',
    'STRING',
    'ENUM'
}

AllNodeInputTypes = NodeInputTypes.union(HiddenNodeInputTypes)

# Meta properties are artificial properties which combine one or more 'real' properties
# and have custom draw and/or export logic.
MetaPropertyTypes = {
    'COLOR_TEXTURE',
    'BRDF_USE'
}

NodeOutputTypes = {
    'OUTPUT_PLUGIN',
    'OUTPUT_COLOR',
    'OUTPUT_FLOAT_TEXTURE',
    'OUTPUT_INT_TEXTURE',
    'OUTPUT_VECTOR_TEXTURE',
    'OUTPUT_TRANSFORM_TEXTURE',
    'OUTPUT_TEXTURE',
}

_TypeToSocket = {
    'ACOLOR'                      : 'VRaySocketColor',
    'COLOR'                       : 'VRaySocketColor',
    'COLOR_TEXTURE'               : 'VRaySocketColorTexture',
    'VECTOR'                      : 'VRaySocketVector',
    'FLOAT'                       : 'VRaySocketFloat',
    'INT'                         : 'VRaySocketInt',
    'BOOL'                        : 'VRaySocketBool',
    'STRING'                      : 'VRaySocketString',
    'ENUM'                        : 'VRaySocketEnum',

    'TRANSFORM'                   : 'VRaySocketTransform',
    'MATRIX'                      : 'VRaySocketTransform',
    'MATRIX_TEXTURE'              : 'VRaySocketTransform',

    'BRDF'                        : 'VRaySocketBRDF',
    'GEOMETRY'                    : 'VRaySocketGeom',
    'MATERIAL'                    : 'VRaySocketMtl',
    'PLUGIN'                      : 'VRaySocketObject',
    'OBJECT'                      : 'VRaySocketObject',
    'BRDF_USE'                    : 'VRaySocketPluginUse',
    'PLUGIN_LIST'                 : 'VRaySocketObjectList',
    'INCLUDE_EXCLUDE_LIST'        : 'VRaySocketIncludeExcludeList',
    'UVWGEN'                      : 'VRaySocketCoords',
    'EFFECT'                      : "VRaySocketEffectOutput",
    'RENDERCHANNEL'               : "VRaySocketRenderChannelOutput",

    'TEXTURE'                     : 'VRaySocketColor',
    'FLOAT_TEXTURE'               : 'VRaySocketFloat',
    'INT_TEXTURE'                 : 'VRaySocketInt',
    'VECTOR_TEXTURE'              : 'VRaySocketVector',

    'OUTPUT_COLOR'                : 'VRaySocketColor',
    'OUTPUT_PLUGIN'               : 'VRaySocketObject',
    'OUTPUT_FLOAT_TEXTURE'        : 'VRaySocketFloat',
    'OUTPUT_TEXTURE'              : 'VRaySocketAColor',
    'OUTPUT_VECTOR_TEXTURE'       : 'VRaySocketVector',
    'OUTPUT_INT_TEXTURE'          : 'VRaySocketVectorInt',
    'OUTPUT_TRANSFORM_TEXTURE'    : 'VRaySocketTransform',
}

# SubtypeToSocket is used to map the subtype of a property to a specific socket type.
# If no entry is found for the specific subtype, the socket type from the TypeToSocket
# dictionary will be used.
_SubtypeToSocket = {
    'VECTOR_TEXTURE': {
        'VRAY_OFFSET'       : 'VRaySocketVectorOffset',
        'VRAY_ROTATION'     : 'VRaySocketVectorRotation',
        'VRAY_SCALE'        : 'VRaySocketVectorScale'
    }
}

# Socket types for linked-only sockets - sockets that cannot have any other value than a linkt to another node.
_TypeToSocketNoValue = {
    'VRaySocketColor'       : 'VRaySocketColorNoValue',
    'VRaySocketFloat'       : 'VRaySocketFloatNoValue',
    'VRaySocketFloatColor'  : 'VRaySocketFloatNoValue',
    'VRaySocketInt'         : 'VRaySocketIntNoValue',
}



def getSocketType(type: str, subtype: str = None, linkedOnly = False) -> str:
    """ Return the socket type found from TypeToSocket, SubtypeToSockket and TypeToSocketNoValue dictionaries."""

    sockType = _SubtypeToSocket.get(type, {}).get(subtype, None)

    if not sockType: 
        sockType = _TypeToSocket.get(type, None)

    if linkedOnly:
        # If the socket is linked only, we need to use the NoValue version of the socket if it exists
        sockType = _TypeToSocketNoValue.get(sockType, sockType)
    
    return sockType



bpy.props.TransformProperty = bpy.props.FloatVectorProperty(
    name="Transform",
    size=12,
    subtype="TRANSFORM",
    default = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0),
)


def propertyMatrix(mtr):
    # There is no property for Matrix for that we use FloatVectorProperty
    return bpy.props.FloatVectorProperty(size=16, default=mtr)

TypeToProp = {
    'BOOL'                  : bpy.props.BoolProperty,
    'COLOR'                 : bpy.props.FloatVectorProperty,
    'COLOR_TEXTURE'         : bpy.props.FloatVectorProperty,
    'ACOLOR'                : bpy.props.FloatVectorProperty,
    'VECTOR'                : bpy.props.FloatVectorProperty,
    'ENUM'                  : bpy.props.EnumProperty,
    'FLOAT'                 : bpy.props.FloatProperty,
    'INT'                   : bpy.props.IntProperty,
    'STRING'                : bpy.props.StringProperty,

    'TRANSFORM'             : propertyMatrix,
    'MATRIX'                : propertyMatrix,
    'MATRIX_TEXTURE'        : propertyMatrix,

    'BRDF'                  : bpy.props.StringProperty,
    'GEOMETRY'              : bpy.props.StringProperty,
    'MATERIAL'              : bpy.props.StringProperty,
    'PLUGIN'                : bpy.props.StringProperty,
    'OBJECT'                : bpy.props.PointerProperty,
    'PLUGIN_LIST'           : bpy.props.PointerProperty,
    'INCLUDE_EXCLULDE_LIST' : bpy.props.PointerProperty,
    'BRDF_USE'              : bpy.props.BoolProperty,
    
    'UVWGEN'                : bpy.props.StringProperty,

    'INT_TEXTURE'           : bpy.props.IntProperty,
    'FLOAT_TEXTURE'         : bpy.props.FloatProperty,
    'TEXTURE'               : bpy.props.FloatVectorProperty,
    'VECTOR_TEXTURE'        : bpy.props.FloatVectorProperty,

    'OUTPUT_COLOR'             : bpy.props.FloatVectorProperty,
    'OUTPUT_PLUGIN'            : bpy.props.StringProperty,
    'OUTPUT_FLOAT_TEXTURE'     : bpy.props.FloatProperty,
    'OUTPUT_INT_TEXTURE'       : bpy.props.IntProperty,
    'OUTPUT_TEXTURE'           : bpy.props.FloatVectorProperty,
    'OUTPUT_VECTOR_TEXTURE'    : bpy.props.FloatVectorProperty,
    # TOOD: There is no bpy.props.TransformProperty type. Replace with valid one. 
    'OUTPUT_TRANSFORM_TEXTURE' : bpy.props.TransformProperty,
}


CompatibleNonVrayNodes = {
    'NodeReroute',

    'ShaderNodeGroup',

    # Cycles
    'ShaderNodeBsdfPrincipled', # almost full(no tangent, a few parameters that don't have gpu support)
    'ShaderNodeBsdfDiffuse', # full
    'ShaderNodeEmission', # full
    'ShaderNodeBsdfAnisotropic', # full
    'ShaderNodeBsdfGlass', # full
    'ShaderNodeBsdfRefraction', # full
    'ShaderNodeBsdfSheen', # full
    'ShaderNodeAddShader', # full
    'ShaderNodeMixShader', # full

    'ShaderNodeTexChecker', # partial
    "ShaderNodeNormal", # full
    'ShaderNodeNormalMap', # full
    'ShaderNodeMath', # partial
    'ShaderNodeRGB', # full
    'ShaderNodeInvert', # partial (no factor)
    'ShaderNodeRGBToBW', # full
    'ShaderNodeTexImage', # full-ish (only single image, no projection support)
    'ShaderNodeTexEnvironment', # full-ish
    'ShaderNodeUVMap', # full
    'ShaderNodeCombineColor', # partial (no hsl)
    'ShaderNodeCombineXYZ', # full
    'ShaderNodeValToRGB', # full (only rgb ramp, different interpolations)
    'ShaderNodeBlackbody', # full, temperature is color only(no texture) on gpu
    'ShaderNodeTexGradient', # a few gradients types are missing
    'ShaderNodeWireframe', # full-ish (pixel-size is different depending on the camera angle)
    'ShaderNodeRGBCurve', # a bit hacky and wrong
    'ShaderNodeValue', # full
    'ShaderNodeTexCoord', # full-enough, reflection doesn't work on GPU, generated doesn't work with Texture Space options
    'ShaderNodeVectorCurve', # a bit hacky and wrong
    'ShaderNodeMapping', # only constant params(no textures)
    'ShaderNodeVertexColor', # full
    'ShaderNodeAttribute', # full-ish
    'ShaderNodeCameraData', # none-ish
    'ShaderNodeBevel', # full
    'ShaderNodeAmbientOcclusion', # full
    'ShaderNodeBump', # full
    'ShaderNodeObjectInfo', # partial
    'ShaderNodeNewGeometry', # partial
    'ShaderNodeSeparateColor', # full
    'ShaderNodeSeparateXYZ', # full
    'ShaderNodeLayerWeight', # partial
    'ShaderNodeFresnel', # partial
    'ShaderNodeMix', # full
    'ShaderNodeClamp', # full
    'ShaderNodeGamma', # full
    'ShaderNodeHueSaturation', # full
    'ShaderNodeBrightContrast', # full
    'ShaderNodeTexNoise', # none
}