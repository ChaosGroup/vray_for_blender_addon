
import bpy



from vray_blender import debug
from vray_blender.lib import attribute_utils

TYPES = {}

def registerPluginPropertyGroup(dataPointer: bpy.types.Node | bpy.types.PropertyGroup, pluginModule, overridePropGroup = False):
    """ Creates a PropertyGroup for the plugin module and attaches it to the 
        dataPointer parameter. 

        Parameters:
        dataPointer (optional): if not None, a member will be created for the newly registered PropertyGroup
                                having the same name as the property grpup class 
        overridePropGroup(bool): set to True to register a property group for a class variant, e.g. different
                                 render channels that use the same underlying plugin
    """
    propGroupName = pluginModule.ID
    typeName      = f"VRay{pluginModule.ID}"

    # The same property group may be attached to different entities. Check whether
    # its class has already been registered.
    dynPropGroup = TYPES.get(typeName)

    if (not dynPropGroup) or overridePropGroup:
        attrContext = attribute_utils.AttributeContext(pluginModule, _getActiveSocketAttributes(pluginModule))

        for param in pluginModule.Parameters:
            attrName = param['attr']

            try:
                attribute_utils.generateAttribute(attrContext, param)
            except KeyError as ex:
                debug.printExceptionInfo(ex, f"Failed to generate attribute {pluginModule.ID}::{attrName}. " + \
                                 f"Missing required property: {ex}. Plugin registration skipped.")
                return
            except Exception as ex:
                debug.printExceptionInfo(ex, f"Failed to generate attribute {pluginModule.ID}::{attrName}. " + \
                           f"Plugin registration skipped. Exception: {ex}")
                return

        if hasattr(pluginModule, 'registerCustomProperties'):
            pluginModule.registerCustomProperties(attrContext.classMembers)

        attrContext.classMembers['parent_node_id'] = bpy.props.StringProperty()
    
        dynPropGroup = type(
            typeName,
            (bpy.types.PropertyGroup,),
            { '__annotations__' : attrContext.classMembers }
        )

        bpy.utils.register_class(dynPropGroup)

        TYPES[typeName] = dynPropGroup
   
    # For some plugins, we need to just register the property group, but not attach it 
    # to an entity at this time 
    if dataPointer:
        dataPointer.__annotations__[propGroupName] = bpy.props.PointerProperty(
            attr        = propGroupName,
            name        = propGroupName,
            type        = dynPropGroup,
            description = pluginModule.DESC
        )


def setVRayCompatibility(uiClass, makeVRayCompatible: bool):
    """ Add/Remove V-Ray renderer to the list of compatible renderers of a Blender class 

        @param uiClass - a Blender UI class that has a COMPAT_ENGINES member
    """
    assert hasattr(uiClass, 'COMPAT_ENGINES'), "Blender class does not support compatibility polling"
    
    if makeVRayCompatible:
        uiClass.COMPAT_ENGINES.add('VRAY_RENDER_RT')
    else:
        uiClass.COMPAT_ENGINES.remove('VRAY_RENDER_RT')


def registerClass(regClass):
    """ Register class with Blender. In DEBUG builds, the labels of V-Ray panels will
        be updated to show a suffix for easy visual identification. 
    """  
    from vray_blender.ui.classes import VRayPanel
    from vray_blender.lib.sys_utils import StartupConfig
    
    if StartupConfig.debugUI and issubclass(regClass, VRayPanel) and hasattr(regClass, 'bl_label'):
        # If the plugin is disabled and then re-enabled, the debug suffix will already be set
        if not regClass.bl_label.endswith("(*)"):
            regClass.bl_label += "(*)"

    bpy.utils.register_class(regClass)


def _getActiveSocketAttributes(pluginModule):
    """ Get all attributes fo a plugin for which an implcit update callback should be registered """
    from vray_blender.lib.condition_processor import UIConditionConverter, isCondition

    attributes = set()
    inputSockets = pluginModule.Node.get('input_sockets', [])
    
    for sockDesc in inputSockets:
        if (visible := sockDesc.get('visible', None)) and isCondition(visible):
            attributes.update(UIConditionConverter.getActiveProperties(visible))


    return attributes