import random

import bpy
import mathutils
from vray_blender.lib import plugin_utils
from vray_blender.lib.mixin import VRayOperatorBase

plugin_utils.loadPluginOnModule(globals(), __name__)


TYPE = 'OBJECT'
ID   = 'Node'
NAME = 'Node'
DESC = "Node settings"


gUserAttributeTypeToValue = {
    '0' : "value_int",
    '1' : "value_float",
    '2' : "value_color",
    '3' : "value_string"
}


class VRAY_OT_user_attribute_assign_to_selected(VRayOperatorBase):
    bl_idname      = 'vray.user_attribute_assign_to_selected'
    bl_label       = "Assign to Selected Objects"
    bl_description = "Copy the currently selected user attribute from the active object to all other selected objects"
    bl_options     = {'INTERNAL', 'UNDO'}

    def execute(self, context):
        activeObject    = context.active_object
        selectedObjects = context.selected_objects

        activeUA = activeObject.vray.UserAttributes
        activeAttrIndex = activeUA.user_attributes_selected

        activeAttrCount = len(activeUA.user_attributes)
        if not activeAttrIndex >= 0 or not activeAttrCount:
            return {'CANCELLED'}

        activeAttribute = activeUA.user_attributes[activeAttrIndex]

        attrName = activeAttribute.name
        attrType = activeAttribute.value_type
        attrValueName = gUserAttributeTypeToValue[attrType]

        activeAttrValue = getattr(activeAttribute, attrValueName)

        selectionFilter = lambda x: hasattr(x, 'vray') and hasattr(x.vray, 'Node') and x != activeObject
        for ob in filter(selectionFilter, selectedObjects):
            itemUA = ob.vray.UserAttributes

            attr = None
            if attrName in itemUA.user_attributes:
                attr = itemUA.user_attributes[attrName]
                # NOTE: Type could be changed
                attr.value_type = attrType
            else:
                attr = itemUA.user_attributes.add()
                attr.name       = attrName
                attr.value_type = attrType

            if not activeUA.user_attributes_rnd_use:
                setattr(attr, attrValueName, activeAttrValue)

            else:
                if activeAttribute.value_type == '3':
                    attr.value_string = activeAttribute.value_string
                else:
                    newValue = None
                    if activeAttribute.value_type == '0':
                        newValue = random.randrange(activeUA.user_attributes_int_rnd_min,
                            activeUA.user_attributes_int_rnd_max)
                    elif activeAttribute.value_type == '1':
                        random.random()
                        randFloat = random.random()
                        randRange = activeUA.user_attributes_float_rnd_max - activeUA.user_attributes_float_rnd_min
                        newValue = activeUA.user_attributes_float_rnd_min + randFloat * randRange
                    elif activeAttribute.value_type == '2':
                        random.random()
                        randR = random.random()
                        random.random()
                        randG = random.random()
                        random.random()
                        randB = random.random()
                        newValue = mathutils.Color((randR, randG, randB))
                    setattr(attr, attrValueName, newValue)

            # Tag the object for update so that user attribute changes are recognized and exported immediately.
            ob.update_tag()

        return {'FINISHED'}


class VRayUserAttributeItem(bpy.types.PropertyGroup):
    value_type: bpy.props.EnumProperty(
        name = "Type",
        items = (
            ('0', "Int",    ""),
            ('1', "Float",  ""),
            ('2', "Color",  ""),
            ('3', "String", ""),
        ),
        default = '1'
    )

    value_int   : bpy.props.IntProperty(default=0)
    value_float : bpy.props.FloatProperty(default=0.0)
    value_string: bpy.props.StringProperty(default="")

    value_color: bpy.props.FloatVectorProperty(
        subtype = 'COLOR',
        min = 0.0,
        max = 1.0,
        soft_min = 0.0,
        soft_max = 1.0,
        default = (1.0, 1.0, 1.0)
    )

    use: bpy.props.BoolProperty(
        name = "",
        description = "Use Attribute",
        default = True
    )

    def asString(self):
        if not self.use:
            return ""

        value = ""

        match self.value_type:
            case '0':   value = str(self.value_int)
            case '1':   value = str(self.value_float)
            case '2':   value = f"{self.value_color[0]},{self.value_color[1]},{self.value_color[2]}"
            case '3':   value = self.value_string

        return f"{self.name}={value}"


class VRAY_UL_UserAttributes(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row(align=True)
        split = row.split(factor=0.05)

        rowHeader = split.column()
        rowHeader.label(text='*')

        splitCol2 = split.column()
        rowItem = splitCol2.row(align=True)
        rowItem.prop(item, 'name', text='')
        rowItem.prop(item, 'value_type', text="")
        rowItem.prop(item, gUserAttributeTypeToValue[item.value_type], text="")
        rowItem.prop(item, 'use', text="")


class VRayUserAttributes(bpy.types.PropertyGroup):
    user_attributes: bpy.props.CollectionProperty(
        name = "User Attributes",
        type =  VRayUserAttributeItem,
        description = "User attributes"
    )

    user_attributes_selected: bpy.props.IntProperty(
        default = -1,
        options = {'HIDDEN', 'SKIP_SAVE'},
        min = -1,
        max = 100
    )

    user_attributes_rnd_use: bpy.props.BoolProperty(name="Randomize", default=False)
    user_attributes_float_rnd_min: bpy.props.FloatProperty(name="Min", default=0.0)
    user_attributes_float_rnd_max: bpy.props.FloatProperty(name="Max", default=1.0)
    user_attributes_int_rnd_min: bpy.props.IntProperty(name="Min", default=0)
    user_attributes_int_rnd_max: bpy.props.IntProperty(name="Max", default=100)


    def getAsString(self):
        items = []

        for item in self.user_attributes:
            if itemStr := item.asString():
                items.append(itemStr)

        return ";".join(items)


class VRAY_OT_user_attribute_add(VRayOperatorBase):
    bl_idname      = 'vray.user_attribute_add'
    bl_label       = "Add User Attribute"
    bl_description = "Add user attribute"
    bl_options     = {'INTERNAL', 'UNDO'}

    def execute(self, context):
        Node = context.object.vray.UserAttributes

        Node.user_attributes.add()
        Node.user_attributes[-1].name = "MyAttr"

        return {'FINISHED'}


class VRAY_OT_user_attribute_del(VRayOperatorBase):
    bl_idname      = 'vray.user_attribute_del'
    bl_label       = "Delete User Attribute"
    bl_description = "Delete user attribute"
    bl_options     = {'INTERNAL', 'UNDO'}

    def execute(self, context):
        ua = context.object.vray.UserAttributes

        if ua.user_attributes_selected >= 0:
           ua.user_attributes.remove(ua.user_attributes_selected)
           ua.user_attributes_selected -= 1

        if len(ua.user_attributes) == 0:
           ua.user_attributes_selected = -1

        if len(ua.user_attributes) == 1:
           ua.user_attributes_selected = 0

        return {'FINISHED'}


def getRegClasses():
    return (
        VRayUserAttributeItem,
        VRAY_OT_user_attribute_add,
        VRAY_OT_user_attribute_del,
        VRAY_UL_UserAttributes,
        VRayUserAttributes,
        VRAY_OT_user_attribute_assign_to_selected,
    )


def register():
    for regClass in getRegClasses():
        bpy.utils.register_class(regClass)

    from vray_blender.plugins import VRayObject
    VRayObject.__annotations__['UserAttributes'] = bpy.props.PointerProperty(
        name = "UserAttributes",
        type =  VRayUserAttributes,
        description = "V-Ray User Attributes"
    )


def unregister():
    for regClass in getRegClasses():
        bpy.utils.unregister_class(regClass)
