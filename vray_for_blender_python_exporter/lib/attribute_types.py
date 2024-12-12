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
    'PLUGIN_USE',
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
    'PLUGIN_USE'
}

NodeOutputTypes = {
    'OUTPUT_PLUGIN',
    'OUTPUT_COLOR',
    'OUTPUT_FLOAT_TEXTURE',
    'OUTPUT_VECTOR_TEXTURE',
    'OUTPUT_TRANSFORM_TEXTURE',
    'OUTPUT_TEXTURE',
}

TypeToSocket = {
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
    'PLUGIN_USE'                  : 'VRaySocketPluginUse',
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
    'OUTPUT_TRANSFORM_TEXTURE'    : 'VRaySocketTransform',
}

TypeToSocketNoValue = dict(TypeToSocket)
for key in TypeToSocketNoValue:
    if TypeToSocketNoValue[key] == 'VRaySocketColor':
        TypeToSocketNoValue[key] = 'VRaySocketColorNoValue'
    elif TypeToSocketNoValue[key] in {'VRaySocketFloat', 'VRaySocketFloatColor'}:
        TypeToSocketNoValue[key] = 'VRaySocketFloatNoValue'
    elif TypeToSocketNoValue[key] in {'VRaySocketInt'}:
        TypeToSocketNoValue[key] = 'VRaySocketIntNoValue'

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
    'PLUGIN_USE'            : bpy.props.BoolProperty,
    
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