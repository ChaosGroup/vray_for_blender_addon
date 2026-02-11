# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy

from vray_blender.exporting.tools import getInputSocketByAttr
from vray_blender.lib.defs import ExporterContext, PluginDesc, AttrPlugin
from vray_blender.lib.plugin_utils import objectToAttrPlugin
from vray_blender.nodes.utils import getNodeOfPropGroup
from vray_blender.plugins import getPluginAttr
from vray_blender.plugins.templates import common
from vray_blender.nodes.tools import getFilterFunction

class TemplateSingleObjectSelect(common.VRayUITemplate):

    """ Show UI for selecting a single object. 

        NOTE: This template should have a corresponding a widget description and is normally
        created for plugin properties of type TEMPLATE.

        Template arguments:
           bound_property: the name of the property to receive the resulting object reference
           filter_function (optional): the name of the filter function for the object search field
    """
    
    def _onUpdateName(self, context):
        if self.boundPropObj:
            self.boundPropObjName = self.boundPropObj.name
        else:
            self.boundPropObjName = ""
    
    
    def _onFilterObject(self, obj):
        # Return the poll (filter) function for the Object Select field
        if filterFn := getFilterFunction(self.vray_plugin, self.getTemplateAttr('filter_function', '')):
            return filterFn(obj)
        return True

    # Property for the name of the selected object, used to check if the object is still valid
    boundPropObjName: bpy.props.StringProperty(
        options     = {'HIDDEN'}
    )

    # Object selector for the "bound_property". We are not using the property directly, because
    # we are adding additional poll and update functions to the selector.
    boundPropObj: bpy.props.PointerProperty(
        type = bpy.types.Object,
        update = _onUpdateName,
        poll = _onFilterObject
    )


    def draw(self, layout: bpy.types.UILayout, context: bpy.types.Context, 
                    pluginModule, propGroup, widgetAttr: dict, text, nested=False):
        
        sock: bpy.types.NodeSocket = None

        if (boundProperty := self.getTemplateAttr('bound_property')) and (node := getNodeOfPropGroup(propGroup)):
            sock = getInputSocketByAttr(node, boundProperty)
        
        if sock and sock.hasActiveFarLink():
            # Do not draw if the object is supplied by a linked object selector node
            return
        
        attrName = widgetAttr['name']
        panel = layout
        panel.use_property_decorate = False # Animation not supported for object lists
        label = widgetAttr.get('label', getPluginAttr(pluginModule, attrName))
        collectionName = self.getTemplateAttr('collection', '')
        data, prop = TemplateSingleObjectSelect._getSearchCollectionProvider(context, collectionName)
    
        panel.prop_search( self, "boundPropObj", data, prop, text=label)
        
        

    def exportToPluginDesc(self, exporterCtx: ExporterContext,  pluginDesc: PluginDesc):
        """ Sets the values of the template bound properties to the pluginDesc.

            Returns:
            True if values have been exported, False if the values should be exported elsewhere.
        """
        if not (boundProperty := self.getTemplateAttr('bound_property')):
            # The plugin has custom export code and only uses the template to obtain the data.
            return False
        
        if node := pluginDesc.node:
            sock = getInputSocketByAttr(node, boundProperty)
            if sock.hasActiveFarLink():
                # If the socket is linked, it will be exported as part of the tree export
                return False

        if not self.boundPropObjName:
            pluginDesc.setAttribute(boundProperty, AttrPlugin())
            return True

        pluginDesc.setAttribute(boundProperty, objectToAttrPlugin(self.boundPropObj))

        return True


    @staticmethod
    def _getSearchCollectionProvider(context: bpy.types.Context, collName=''):
        if collName in ('', 'objects'):
            return (context.scene, 'objects')
        else:
            return (bpy.data, collName)

    def removeDeletedItems(self, context: bpy.types.Context):
        if self.boundPropObjName and self.boundPropObjName not in context.scene.objects:
            self.boundPropObjName = ""
            self.boundPropObj = None
            return True

        return False


def getRegClasses():
    return (
        TemplateSingleObjectSelect,
    )


def register():
    from vray_blender.lib.class_utils import registerClass
    for regClass in getRegClasses():
        registerClass(regClass)


def unregister():
    for regClass in reversed(getRegClasses()):
        bpy.utils.unregister_class(regClass)
