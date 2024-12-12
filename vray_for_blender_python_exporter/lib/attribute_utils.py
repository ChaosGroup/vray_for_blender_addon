
import re
import bpy
import mathutils


from vray_blender import debug
from vray_blender.lib.attribute_types import TypeToProp, SkippedTypes
from vray_blender.exporting.tools import GEOMETRY_OBJECT_TYPES, tupleTo4x4MatrixLayout, matrixLayoutToMatrix
from vray_blender.lib.blender_utils import getCmToSceneUnitsMultiplier, getMetersToSceneUnitsMultiplier

# Create an additional pointer property to be used for property search UI.
# It updates the original string property with the object name when changed.
# This is needed because the result from property searches in ID collections
# must be stored in a pointer property, we can't directly use the string property.
def createPropSearchPointerProp(origAttr, propSearchAttr, type, name, description):
    def updateFunc(self, context):
        if getattr(self, propSearchAttr):
            setattr(self, origAttr, getattr(self, propSearchAttr).name)
        else:
            setattr(self, origAttr, '')
    return bpy.props.PointerProperty(
        type = type,
        name = name,
        description = description,
        update = updateFunc
    )

def formatAttributeName(nameOrLabel):
    """ Create display name (label) from an attribute name or label.
        The function removes any underscores and capitalizes the first letter of each word in the string. 
    
        Args:
            nameOrLabel (str) - an attribute name or a widget label to format

        Returns:
            str: the formatted string
    """
    attrName = nameOrLabel.strip().replace("_", " ")
    attrName = re.sub(' +', ' ', attrName) # Collapse multuple spaces
    words = attrName.split(' ')
    
    try:
        formattedName = [f"{w[0].title()}{w[1:]}" for w in words]
        return ' '.join(formattedName)
    except Exception as ex:
        debug.printExceptionInfo(ex, f"Failed to format attribute label '{nameOrLabel}'")
        return nameOrLabel
    
def getAttrDesc(pluginModule, attrName):
    return next((p for p in pluginModule.Parameters if p['attr'] == attrName), None)


def valueInEnumItems(attrDesc, enumValue):
    for item in attrDesc['items']:
        if item[0] == enumValue:
            return True
    return False

    
def isAnimatableAttribute(pluginModule, attrDesc):
    pluginAnimatable = pluginModule.Options.get('animatable', True)
    if 'options' in attrDesc:
        flag = attrDesc['options'].get('animatable', None)
        return pluginAnimatable if flag is None else flag
    
    return pluginAnimatable


def convertVRayValueToUI(attrDesc, value):
    """ Convert property value from its V-Ray represenattion to its UI representation 
        in Blender by applying a custom multiplier. This is necessary because for some 
        of the predefined Blender UI subtypes, the measurement units do not coincide
        with those used in V-Ray.
    """
    if multiplier := attrDesc.get('options',{}).get('value_conv_factor'):
        assert type(value) in (int, float)
        return value * multiplier

    return value


def convertUIValueToVRay(attrDesc, value):
    """ Convert property value from its UI representation in Blender to its V-Ray 
        representation.
    """ 
    # Apply custom multiplier. This is necessary because for some 
    # of the predefined Blender UI subtypes, the measurement units do not coincide
    # with those used in V-Ray.
    if multiplier := attrDesc.get('options',{}).get('value_conv_factor'):
        assert type(value) in (int, float)
        return value / multiplier
    
    # TRANSFORM and MATRIX types are stored in a FloatVectorProp property of length 16
    if (type(value) is bpy.types.bpy_prop_array) and (attrDesc['type'] in ('TRANSFORM', 'MATRIX', 'MATRIX_TEXTURE')):
        return matrixLayoutToMatrix(value)
    
    return value
   

# Converters between property group and blender socket value types.
def toColor(value):
    """ Convert value to a valid 3-component float vector """
    return mathutils.Color(value[:3])


def toAColor(value):
    """ Convert value to a valid 4-component float vector """
    match len(value):
        case 3:
            return list(value[:]) + [1.0]
        case 4:
            return value[:]

    assert False, f"Invalid color vector size {len(value)}, should be 3 or 4."

def attrValueToMatrix(value, applyScale: bool):
    """ Convert TRANSFORM or MATRIX value from plugin description to mathutils.Matrix"""

    m = mathutils.Matrix()

    if type(value) in (list, tuple):
        tmM    = value[0]
        tmOffs = value[1]

        for c in range(3):
            # Transpose 3x3 matrix, Blender's format is row-first
            for r in range(3):
                m[c][r] = tmM[r][c]
            # Add the translation value for the current axis
            m[c][3] = scaleToSceneLengthUnit(tmOffs[c], "centimeters") if applyScale else tmOffs[c]

    else:
        import struct, binascii
        
        tmArray = struct.unpack("fffffffffddd", binascii.unhexlify(bytes(attrValue, 'ascii')))
        
        i = 0
        for c in range(3):
            for r in range(3):
                m[c][r] = tmArray[i]
                i += 1
    return m


# Matches attribute's "ui::units" to scene unit multiplier
UNIT_MULTIPLIER_FUNC = { 
    "centimeters": getCmToSceneUnitsMultiplier,
    "meters" : getMetersToSceneUnitsMultiplier
}

def scaleToSceneLengthUnit(attrValue, lengthUnit):
    """ Scales 'attrValue' to scene length unit """
    if lengthUnit in  UNIT_MULTIPLIER_FUNC:
        return attrValue * UNIT_MULTIPLIER_FUNC[lengthUnit](bpy.context)
    else:
        debug.printWarning(f"Unsupported V-Ray Plugin attribute '{lengthUnit}' scaling unit")
    return attrValue



def generateAttribute(classMembers, pluginModule, attrDesc):
    """ Generate Blender Property based on attribute description
        and add it to 'classMembers' dict.
    """
    attrName = attrDesc['attr']
    attrType = attrDesc['type']

    if attrType in SkippedTypes:
        return

    if attrType == 'TEMPLATE':
        _generateTemplate(classMembers, pluginModule, attrDesc)
        return
    
    attrArgs = {
        'attr' : attrName,
        'name' : getAttrDisplayName(attrDesc)
    }

    setAttrDescription(attrArgs, attrDesc)
    setAttrSubtype(attrArgs, attrDesc)
    setAttrPrecision(attrArgs, attrDesc)
    setAttrLimits(attrArgs, attrDesc)
    setAttrOptions(attrArgs, attrDesc, pluginModule)
    setAttrUpdateCallback(attrArgs, attrDesc, pluginModule)
    _setAttrValueCallbacks(attrArgs, attrDesc, pluginModule)
    _setAttrSize(attrArgs, attrDesc)
    _setAttrDefault(attrArgs, attrDesc, pluginModule)
    
    if attrType == 'ENUM':
        # JSON parser returns lists but EnumProperty types expects tuples
        attrArgs['items'] = (tuple(item) for item in attrDesc['items'])

    _addAttrToClassMembers(attrArgs, attrDesc, classMembers, pluginModule.ID)
    
    if attrName in {'render_mask_objects', 'exclusion_nodes'}:
        # Attribute has a collection prop search UI - add an additional pointer property
        # which updates the original property with the object name when changed.
        origAttr = attrName
        propSearchAttr = origAttr + '_ptr'
        classMembers[propSearchAttr] = createPropSearchPointerProp(
            origAttr,
            propSearchAttr,
            bpy.types.Collection,
            attrArgs['name'],
            attrArgs['description']
        )


TypeToUISubtype = {
    'COLOR'         : 'COLOR', 
    'ACOLOR'        : 'COLOR',
    'TEXTURE'       : 'COLOR',
    'COLOR_TEXTURE' : 'COLOR',
    'VECTOR'        : 'TRANSLATION',
}

def _generateTemplate(classMembers, pluginModule, attrDesc):
    from vray_blender.plugins import templates

    attrName = attrDesc['attr']
    template = attrDesc['options']['template']
    templateName = template['type']
    templateClassFunc = getattr(templates, templateName)

    # Register a dedicated class for the template. 
    # Add all template parameters defined in the plugin description as properties to the
    # template class and set their default values. This is necessary because there is no event 
    # we can hook to initialize the property group values dynamically.
    templateMembers = dict()

    # Add properties common to all templates
    templateMembers['vray_plugin'] = bpy.props.StringProperty(default=pluginModule.ID)
    templateMembers['vray_attr'] = bpy.props.StringProperty(default=attrName)
    
    templateClass = templateClassFunc()
    if fnRegister := getattr(templateClass, 'registerProperties', None):
        # Give a chance to the template class to register its own properties. This will usually
        # be some variations in the properties definitions, e.g. names or descriptions which are 
        # shown in the UI.
        fnRegister(pluginModule, attrDesc, templateMembers)

    templatePropGroup = type(
        f"TMPL_{pluginModule.ID}_{attrName}",
        (templateClass,),
        { '__annotations__' : templateMembers }
    )

    bpy.utils.register_class(templatePropGroup)

    # Add the template as a field of the plugin's property group 
    classMembers[attrName] = bpy.props.PointerProperty(
        type = templatePropGroup
    )

    
def setAttrDescription(attrArgs, attrDesc):
    # NOTE: Implicitly created attributes (e.g. for templates) may not have descriptions
    attrArgs['description'] = attrDesc.get('desc', '')
    if attrArgs['description'].endswith("."):
        # Remove the full stop punctuation so that all descriptions were formatted the same
        attrArgs['description'] = attrArgs['description'][:-1]


def getAttrDisplayName(attrDesc):
    """ Get the display name (label) of a plugin attribute.

        If there is 'ui' parameter, get the value from its 'display_name' property.
        If there is no such parameter, construct the string from the name of the attribute.
    """

    if (uiDesc := attrDesc.get('ui')) and ('display_name' in uiDesc):
        return formatAttributeName(uiDesc['display_name'])
    
    return attrDesc.get('name', formatAttributeName(attrDesc['attr']))


def setAttrSubtype(attrArgs, attrDesc):
    attrType = attrDesc['type']
    
    if (attrType == 'STRING') and ('ui' in attrDesc) and ('file_extensions' in attrDesc['ui']):
             attrArgs['subtype'] = 'FILE_PATH'
    elif attrType in TypeToUISubtype:
        attrArgs['subtype'] = attrDesc.get('subtype', TypeToUISubtype[attrType])
    elif 'subtype' in attrDesc:
        attrArgs['subtype'] = attrDesc.get('subtype')


def _setAttrSize(attrArgs, attrDesc):
    """ Set size of the FloatVector property """
    attrType = attrDesc['type']

    if attrType in {'COLOR', 'VECTOR', "VECTOR_TEXTURE", 'COLOR_TEXTURE'}:
        attrArgs['size'] = 3
    elif attrType in {'ACOLOR', 'TEXTURE'}:
        attrArgs['size'] = 3
    
    
def setAttrPrecision(attrArgs, attrDesc):
    attrType = attrDesc['type']

    if attrType in ('FLOAT', 'FLOAT_TEXTURE', 'VECTOR', 'VECTOR_TEXTURE'):
        attrArgs['precision'] = attrDesc.get('precision', 3)
    elif 'precision' in attrDesc:
        attrArgs['precision'] = attrDesc.get('precision')


def _setAttrDefault(attrArgs, attrDesc, pluginModule):
    import mathutils

    attrName = attrDesc['attr']
    attrType = attrDesc['type']
    
    if 'default' in attrDesc:
        attrArgs['default'] = convertVRayValueToUI(attrDesc, attrDesc['default'])

    if attrType in ('PLUGIN', 'UVWGEN'):
        attrArgs['default'] = ""
    elif attrType in ('TRANSFORM', 'MATRIX', 'MATRIX_TEXTURE'):
        # These attribute types are stored as FloatVectorProperty with a subtype of Matrix,
        # which represents a 4x4 matrix. They are exposed as sockets of type VRaySocketTransform.
        assert (len(attrDesc['default']) in (9,12)), "Only (3x3) and (3x4) matrices are supported for 'TRANSFORM', 'MATRIX' and 'MATRIX_TEXTURE' attributes"
        attrArgs['default'] =  tupleTo4x4MatrixLayout(attrDesc['default'])

    elif attrType in ('COLOR', 'COLOR_TEXTURE', 'OUTPUT_TEXTURE'):
        clr = attrDesc['default']
        attrArgs['default'] = toColor(clr)
    elif attrType in ('ACOLOR', 'TEXTURE'):
        # These types' value is defined as AColor in VRay, but in Blender we use them as Color 
        clr = attrDesc['default']
        attrArgs['default'] = toColor(clr)
    elif attrType in ('VECTOR', 'VECTOR_TEXTURE'):
        v = attrDesc['default']
        attrArgs['default'] = mathutils.Vector(v[:])
    elif attrType == 'ENUM':
        if attrArgs['default'] not in [item[0] for item in attrDesc['items']]:
            # Some of the default values read from the json description file are not correct because of
            # omissions in VRay code. Log and set to a valid value 
            debug.printWarning(f"Default value {attrArgs['default']} for ENUM {pluginModule.ID}::{attrName} is invalid. Setting default to the first item of the enum")
            attrArgs['default'] = attrDesc['items'][0][0]



def setAttrLimits(attrArgs, attrDesc):
    attrType = attrDesc['type']
    
    defUi = {
        'min'      : -1<<20,
        'max'      :  1<<20,
        'soft_min' : 0,
        'soft_max' : 64,
    }

    if attrType in {'COLOR', 'ACOLOR', 'TEXTURE', 'COLOR_TEXTURE'}:
        # We allow usage of color values outside the [0, 1] interval. NOTE however that manually setting 
        # a value within the allowed limits but outside [0,1] will interfere with the normal operartion 
        # of the color picker.
        attrArgs['min'] = 0.0
        attrArgs['max'] = 1000.0
        # Be careful with the soft limits for colors as they apply to all inputs in the color picker control.
        # Setting them to an interval other than [0,1] will make the color picker act weird.
        attrArgs['soft_min'] = 0.0
        attrArgs['soft_max'] = 1.0
    elif attrType in {'INT', 'INT_TEXTURE', 'FLOAT', 'FLOAT_TEXTURE'}:
        if 'ui' not in attrDesc:
            attrDesc['ui'] = defUi

        minValue = attrDesc['ui'].get('min', defUi['min'])
        maxValue = attrDesc['ui'].get('max', defUi['max'])

        attrArgs['min'] = convertVRayValueToUI(attrDesc, minValue)
        attrArgs['max'] = convertVRayValueToUI(attrDesc, maxValue)
        attrArgs['soft_min'] = convertVRayValueToUI(attrDesc, attrDesc['ui'].get('soft_min', minValue))
        attrArgs['soft_max'] = convertVRayValueToUI(attrDesc, attrDesc['ui'].get('soft_max', maxValue))
        
        if 'spin_step' in attrDesc['ui']:
            stepValue = attrDesc['ui']['spin_step']
            if attrType in {'FLOAT', 'FLOAT_TEXTURE'}:
                # Step for float values is set in 100ths of the actual step value
                stepValue *= 100
                stepValue = convertVRayValueToUI(attrDesc, stepValue)
            attrArgs['step'] = stepValue


def setAttrOptions(attrArgs, attrDesc, pluginModule):
    # Set attribute options, if present
    attrOptions = set()
    if isAnimatableAttribute(pluginModule, attrDesc):
        attrOptions.add('ANIMATABLE')

    attrDescOptions = attrDesc.get('options', {})
    if not attrDescOptions.get('visible', True):
        attrOptions.add('HIDDEN')

    attrArgs['options'] = attrOptions
    
    # Pass-through values
    for optionalKey in {'size'}:
        if optionalKey in attrDesc:
            attrArgs[optionalKey] = attrDesc[optionalKey]


def setAttrUpdateCallback(attrArgs, attrDesc, pluginModule):
    from vray_blender.nodes.utils import selectedObjectTagUpdate, selectedObjectTagUpdateWrapper

    attrName = attrDesc['attr']

    # Changes to custom node trees do not result in updates, so we need to manually
    # tag the updated objects and the property editor for redraw. 
    updateFunc = selectedObjectTagUpdate
    
    if 'update' in attrDesc:
        # Call the custom update function as part of the update callback
        updateFunc = lambda s, ctx: selectedObjectTagUpdateWrapper(s, ctx, pluginModule, attrName, attrDesc['update'])
    
    attrArgs['update'] = updateFunc

    if attrDesc.get('poll', None):
        if pollFunc := getattr(pluginModule, attrDesc['poll'], None):
            attrArgs['poll'] = pollFunc
        


def _setAttrValueCallbacks(attrArgs, attrDesc, pluginModule):
    attrName = attrDesc['attr']

    if fnSet := attrDesc.get('set'):
        # Call the custom update function as part of the update callback
        getFunc = lambda s, value: _setValueWrapper(pluginModule, s, attrName, value, fnSet)
        attrArgs['set'] = getFunc

    if fnGet := attrDesc.get('get'):
        # Call the custom update function as part of the update callback
        getFunc = lambda s: _getValueWrapper(pluginModule, s, attrName, fnGet)
        attrArgs['get'] = getFunc


def _fillOBjectAttributeArguments(attrType, attrArgs, attrDesc):
    if attrType == "OBJECT":
        attrArgs["type"] = bpy.types.Object
        if ("object_type" in attrDesc) and hasattr( bpy.types, attrDesc["object_type"]):
            attrArgs["type"] = getattr( bpy.types, attrDesc["object_type"] )

        if ("filter_type" in attrDesc):
            match attrDesc["filter_type"]:
                case "geometry":
                    attrArgs["poll"] = lambda self, obj: obj.type in GEOMETRY_OBJECT_TYPES
                case "light":
                    if vrayType := attrDesc.get("filter_vray_type", None):
                        attrArgs["poll"] = lambda self, obj: (obj.type == 'LIGHT') and (obj.data.vray.light_type == vrayType)
                    else:
                        attrArgs["poll"] = lambda self, obj: obj.type == 'LIGHT'
            
        if "default" in attrArgs:
            # Pointer properties don't have default argument
            del attrArgs["default"]


def _addAttrToClassMembers(attrArgs, attrDesc, classMembers, pluginType):
    attrType = attrDesc['type']
    attrName = attrDesc['attr']

    _fillOBjectAttributeArguments(attrType, attrArgs, attrDesc)

    try:
        attrFunc = TypeToProp[attrType]
    except Exception as ex:
        debug.printExceptionInfo(ex, f"Failed to register property {pluginType}::{attrName} of unsupported type {attrType}")
        raise ex

    if attrDesc['type'] in  ('TRANSFORM', 'MATRIX', 'MATRIX_TEXTURE'):
        classMembers[attrName] = attrFunc(attrArgs["default"])
    else:
        classMembers[attrName] = attrFunc(**attrArgs)
    


def _setValueWrapper(pluginModule, propGroup, attrName, value, fnSetName: str):
    if fnSet := getattr(pluginModule, fnSetName, None):
        fnSet(propGroup, attrName, value)

def _getValueWrapper(pluginModule, propGroup, attrName, fnSetName: str):
    if fnGet := getattr(pluginModule, fnSetName, None):
        return fnGet(propGroup, attrName)
    
    return None