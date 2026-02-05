from __future__ import annotations
import bpy

from vray_blender import debug
from vray_blender.nodes.utils import tagRedrawNodeEditor, tagRedrawPropertyEditor
from vray_blender.lib.blender_utils import isCollection
from vray_blender.plugins import getPluginAttr, getPluginModule
from vray_blender.lib.mixin import VRayOperatorBase
from vray_blender.lib.plugin_utils import TEMPLATE_ATTRIBUTES
from enum import StrEnum

class ObjectType(StrEnum):
    COLLECTION = "COLLECTION"
    OBJECT = "OBJECT"
    MATERIAL = "MATERIAL"
    UNKNOWN = ""

def _getObjectType(obj: bpy.types.ID):
    if obj is not None:
        if isCollection(obj):
            return ObjectType.COLLECTION
        elif isinstance(obj, bpy.types.Object):
            return ObjectType.OBJECT
        elif isinstance(obj, bpy.types.Material):
            return ObjectType.MATERIAL
    return ObjectType.UNKNOWN

class TemplateListItem(bpy.types.PropertyGroup):
    objectPtr: bpy.props.PointerProperty(
        type = bpy.types.ID
    )

    # Save the object type separately from the object pointer, because the object pointer may refer to a deleted object.
    # For example, we want to avoid calling isinstance(objectPtr, bpy.types.Object) on a deleted object in order to get its type.
    objectType: bpy.props.StringProperty(default="")

    enabled: bpy.props.BoolProperty(default=True)


class VRAY_UL_SimpleList(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        row = layout.row()
        row.enabled = item.enabled

        iconID = ''

        # Set the icon of the list item corresponding to the object's type.
        # If the item is not valid (the object/collection it refers to has been deleted from the scene)
        # set the icon to 'X'. Strictly speaking, the user should never see an item with an 'X' icon
        # if the scene is updated and the UI  is redrawn correctly. However ATM we are not sure that all use
        # cases have been covered so the X icon will make it easier to identify the items
        # whose objects have been deleted in case removal of list items does not work as expected.
        match item.objectType:
            case ObjectType.COLLECTION:
                iconID = 'COLLECTION_NEW'
            case ObjectType.OBJECT:
                iconID = f'{item.objectPtr.type}_DATA'
            case ObjectType.MATERIAL:
                iconID = f'{item.objectPtr.id_type.upper()}_DATA'
            case _:
                iconID = 'X'

        row.label(text=item.name, icon=iconID)


class VRAY_OT_simple_button(VRayOperatorBase):
    bl_label = 'Button'
    bl_idname = 'vray.simple_button'

    # Dynamic replacement for bl_description
    description: bpy.props.StringProperty(default=bl_label)

    # The name of the function to call on the node when the button is pressed
    callbackName: bpy.props.StringProperty()

    # Arbitrary string, identifier of the selector element when multuple selectors
    # are shown for the same host
    selectorID: bpy.props.StringProperty()

    def execute(self, context: bpy.types.Context):
        assert getattr(context, 'buttonHost'), "Button callback function should be set using UILayout's context_pointer_set() method"

        callbackFn = getattr(context.buttonHost, self.callbackName)
        callbackFn(context, self.selectorID)
        return {'FINISHED'}


    @staticmethod
    def create(layout: bpy.types.UILayout, label: str, description: str, host: bpy.types.AnyType, callbackName: str, selectorID=''):
        # Context data must be set before the operator is created
        layout.context_pointer_set(name='buttonHost', data=host)
        opButton = layout.operator('vray.simple_button', text=label)

        opButton.callbackName = callbackName
        opButton.description = description
        opButton.selectorID = selectorID


    @classmethod
    def description(cls, context, properties):
        return properties.description



class VRayUITemplate(bpy.types.PropertyGroup):
    """ Base class for UI templates implementing common access to template properties 

        Properties:
           vray_plugin: the name of the plugin hosting the property
           vray_attr:   the name of the template property
    """

    def getTemplate(self):
        """ Return the options.template property of the template description """
        templateAttr = getPluginAttr(getPluginModule(self.vray_plugin), self.vray_attr)
        return templateAttr['options']['template']


    def getTemplateAttr(self, templateAttr: str, default = None):
        """ Return the options.template.{templateArg} property of the template description """
        try:
            return self.getTemplate()['args'].get(templateAttr, default)
        except Exception as ex:
            debug.printExceptionInfo(ex, f"Failed to get template property {self.vray_plugin}::{self.vray_attr}::{templateAttr}")
            raise ex


class VRayObjectSelector(VRayUITemplate):
    """ Base class for object selector templates. Provides uniform UI and filtering.

        This selector can be placed (hosted) on either a node, or a property page of a node.
        Before it can be used, it should be bound to the host by calling its init() method.

        Events to the derived classes:
        onFilterObject(object): called to filter the objects shown in the selection list.
        onSelectionCahanged(): called when the selection list has changed.
    """

    def _filterObject(self, obj):
        # The poll (filter) function for the Object Select field
        if filterFn := getattr(self, 'onFilterObject', None):
            return filterFn(obj)
        return True

    def _onSelectObject(self, context):
        if self.objectSelector:
            self.addListItem(context, self.objectSelector)
        elif self.collectionSelector:
            self.addListItem(context, self.collectionSelector)

    objectSelector: bpy.props.PointerProperty(
        type = bpy.types.Object,
        name = "Object",
        description = "Select object",
        update = _onSelectObject,
        poll = _filterObject
    )

    collectionSelector: bpy.props.PointerProperty(
        type = bpy.types.Collection,
        name = "Collection",
        description = "Select collection",
        update = _onSelectObject
    )

    selectedItems: bpy.props.CollectionProperty(
        type = TemplateListItem
    )

    # The 0-based index of the currently selected list item, -1 for no selection
    activeItem : bpy.props.IntProperty(default = -1)


    @staticmethod
    def registerProperties(pluginModule, attrDesc, templateMembers):
        # Set object selector field properties based on the type of collection
        # to select from.
        searchCollectionName = attrDesc['options']['template']['args'].get('collection', '')

        # For collections other than scene.objects, re-register the objectSelector property with attributes 
        # specific to the collection selection is done from.
        if searchCollectionName == 'materials':
            # So far, the materials collection is the only global one that is accessed in selectors.
            # Any other objects are selected from the scene.objects collection, which is scene-specific.
            fieldType = bpy.types.Material
            fieldName = 'Selected Materials'
            fieldDescription = 'Select material'

            templateMembers['objectSelector'] = bpy.props.PointerProperty(
                                                type = fieldType,
                                                name = fieldName,
                                                description = fieldDescription,
                                                update = VRayObjectSelector._onSelectObject,
                                                poll = VRayObjectSelector._filterObject)

    def drawSelectorUI(self, context: bpy.types.Context, layout: bpy.types.UILayout, 
                       dataProvider: bpy.types.Collection, dataProperty: str, listLabel='Object List'):
        """ Draw the UI for the object selector.

        Args:
            context (bpy.types.Context): Blender context
            layout (bpy.types.UILayout): parent layout
            dataProvier (bpy.types.ID): the data block that hosts the collection to search.
            dataProperty (str): the name of the collection property in the data block.
            listLabel (str, optional): the label to use for the object list. Defaults to 'Object List'.
        """
        layout.prop_search( self, 'objectSelector', dataProvider, dataProperty, text='')

        if isinstance(dataProvider, bpy.types.Scene):
            layout.prop_search(self, 'collectionSelector', bpy.data, 'collections', text='')

        layout.label(text=listLabel)
        layout.template_list('VRAY_UL_SimpleList', self.name, self, 'selectedItems', self, 'activeItem')

        buttonBox = layout.column()
        buttonBox.enabled = (self.activeItem >= 0)

        VRAY_OT_simple_button.create(buttonBox, 'Remove', 'Remove the selected item from the list', self, 'onRemoveListItem')


    def getSelectedItems(self, context: bpy.types.Context, searchCollection: str):
        """ Return a list of all selected objects (flattening the collections and recursing any child collections) 
            filtered by the supplied object filter function. 
        """
        dataProvider = context.scene.objects if searchCollection in( '', 'objects') else getattr(bpy.data, searchCollection)

        selected = set()

        for i in [i for i in self.selectedItems if i.enabled and (i.objectPtr is not None)]:
            obj = i.objectPtr.original
            if isCollection(obj):
                selected.update([o for o in obj.all_objects if o in dataProvider.values() and self._filterObject(o)])
            elif obj in dataProvider.values():
                # The object might have been deleted from the scene, hence the check.
                selected.add(obj)

        return list(selected)


    def copy(self, dest: VRayObjectSelector):
        dest.activeItem = self.activeItem

        for item in self.selectedItems:
            dest.addListItem(bpy.context, item.objectPtr)


    def addListItem(self, context: bpy.types.Context, selected: bpy.types.Object):

        if selected and (selected.name not in self.selectedItems):
            newItem = self.selectedItems.add()
            newItem.objectPtr = selected
            newItem.name = selected.name

            newItem.objectType = _getObjectType(selected)

            if fnUpdate := getattr(self, 'onSelectionChanged', None):
                fnUpdate(context)

        # After adding the item to the list, the object selector field has to be cleared so that 
        # a new object could be selected. We however cannot reset the value of objectSelector here, 
        # because Blender will check for the value at the end of the Eyedropper operator execution
        # and show an error status message if nothing has been selected. To work around this, use
        # a one-time timer to delay resetting the field.
        bpy.app.timers.register(self._clearSelectorFields)


    # Callback for VRAY_OT_node_button
    def onRemoveListItem(self, context: bpy.types.Context, selectorID: str):
        if -1 != self.activeItem:
            self.selectedItems.remove(self.activeItem)
            self.activeItem = max(self.activeItem - 1, len(self.selectedItems) - 1)

            if fnUpdate := getattr(self, 'onSelectionChanged', None):
                fnUpdate(context)


    def removeDeletedItems(self, context):
        """ Remove all deleted items from the selectedItems list and return True if any items were removed. 

            When a scene object is deleted, the item associated with it remains on the selectedItem list.
            This function will delete all such no longer valid items.
        """
        itemsToRemove = [i for i, item in enumerate(self.selectedItems) if not self._isItemValid(context, item)]

        removed = 0         # How many items in total are removed?
        activeItemShift = 0 # How many items before the active one are deleted?

        for index in itemsToRemove:
            self.selectedItems.remove(index + removed)
            removed += 1
            if self.activeItem < index:
                activeItemShift += 1

        if removed != 0:
            self.activeItem -= activeItemShift
            self.id_data.update_tag()

        return removed != 0


    def _clearSelectorFields(self):
        if self.objectSelector is not None:
            self.objectSelector = None
        elif self.collectionSelector is not None:
            self.collectionSelector = None

    def _isItemValid(self, context: bpy.types.Context, item: TemplateListItem):
        """ Return whether the list item points to a valid object. """ 

        if not item.objectType:
            # Try to fix unknown items to allow backward compatibility with old scenes.
            item.objectType = _getObjectType(item.objectPtr)

        match item.objectType:
            case ObjectType.COLLECTION:
                return item.name in bpy.data.collections
            case ObjectType.OBJECT:
                return item.name in context.scene.objects
            case ObjectType.MATERIAL:
                return item.name in bpy.data.materials

        return False


############  ABANDONED LIST ITEMS CLEANUP  ############

def cleanupObjectSelectorLists():
    """ Depending on the way objects are deleted from the scene, the items in the VRayObjectSelector may
        not get their objectPointer reset to None. This function goes through all the object selectors and
        cleans up the abandoned list items.
    """

    needRedraw = False

    def cleanSelectorsOfPropGroup(pluginName: str, data):
        nonlocal needRedraw
        propGroup = getattr(data, pluginName)
        for attrName in TEMPLATE_ATTRIBUTES.get(pluginName, []):
            needRedraw |= getattr(propGroup, attrName).removeDeletedItems(bpy.context)


    def cleanSelectorsOfNodeTree(ntree: bpy.types.NodeTree):
        nonlocal needRedraw
        if not ntree:
            return
        
        for node in ntree.nodes:
            match node.bl_idname:
                case 'VRayNodeMultiSelect' | 'VRayNodeSelectObject':
                    needRedraw |= node.removeDeletedItems(bpy.context)
                case _:
                    if (not hasattr(node, 'vray_plugin')) or node.vray_plugin == 'NONE':
                        continue
                    cleanSelectorsOfPropGroup(node.vray_plugin, node)

    for material in bpy.data.materials:
        cleanSelectorsOfNodeTree(material.node_tree)

    for world in bpy.data.worlds:
        cleanSelectorsOfNodeTree(world.node_tree)

    for object in bpy.data.objects:
        if object.vray.isVRayFur and not object.vray.ntree:
            cleanSelectorsOfPropGroup('GeomHair', object.data.vray)
        cleanSelectorsOfNodeTree(object.vray.ntree)

    for light in bpy.data.lights:
        vrayLight = light.vray
        if vrayLight.light_type == "MESH" and not light.node_tree:
            cleanSelectorsOfPropGroup('LightMesh', vrayLight)

        needRedraw |= vrayLight.objectList.removeDeletedItems(bpy.context) # Include/Exclude list
        cleanSelectorsOfNodeTree(light.node_tree)

    cleanSelectorsOfPropGroup('SettingsImageSampler', bpy.context.scene.vray)

    if needRedraw:
        tagRedrawNodeEditor()
        tagRedrawPropertyEditor()


############  REGISTRATION  ############

def getRegClasses():
    return (
        TemplateListItem,
        VRAY_UL_SimpleList,
        VRAY_OT_simple_button,
        VRayObjectSelector
    )


def register():
    from vray_blender.lib.class_utils import registerClass

    for regClass in getRegClasses():
        registerClass(regClass)


def unregister():
    for regClass in reversed(getRegClasses()):
        bpy.utils.unregister_class(regClass)


