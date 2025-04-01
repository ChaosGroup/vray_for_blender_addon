import bpy

from vray_blender.lib.defs import ExporterContext, PluginDesc
from vray_blender.lib.plugin_utils import objectToAttrPlugin
from vray_blender.nodes.utils import getInputSocketByVRayAttr, getNodeOfPropGroup
from vray_blender.plugins import getPluginAttr
from vray_blender.plugins.templates import common


class TemplateSingleObjectSelect(common.VRayUITemplate):

    """ Show UI for selecting a single object. 

        NOTE: This template should have a corresponding a widget description and is normally
        created for plugin properties of type TEMPLATE.

        Template arguments:
           bound_property: the name of the property to receive the resulting object reference
           filter_function (optional): the name of the filter function for the object search field
    """     

   
    def draw(self, layout: bpy.types.UILayout, context: bpy.types.Context, 
                    pluginModule, propGroup, widgetAttr: dict, text, nested=False):
        
        sock: bpy.types.NodeSocket = None

        if (boundProperty := self.getTemplateAttr('bound_property')) and (node := getNodeOfPropGroup(propGroup)):
            sock = getInputSocketByVRayAttr(node, boundProperty)
        
        if sock and sock.is_linked:
            # Do not draw if the object is supplied by a linked object selector node
            return
        
        attrName = widgetAttr['name']
        panel = layout
        panel.use_property_decorate = False # Animation not supported for object lists
        label = widgetAttr.get('label', getPluginAttr(pluginModule, attrName))
        collectionName = self.getTemplateAttr('collection', '')
        data, prop = TemplateSingleObjectSelect._getSearchCollectionProvider(context, collectionName)
    
        panel.prop_search( propGroup, boundProperty, data, prop, text=label)
        
        

    def exportToPluginDesc(self, exporterCtx: ExporterContext,  pluginDesc: PluginDesc):
        """ Sets the values of the template bound properties to the pluginDesc.

            Returns:
            True if values have been exported, False if the values should be exported elsewhere.
        """
        if not (boundProperty := self.getTemplateAttr('bound_property')):
            # The plugin has custom export code and only uses the template to obtain the data.
            return False
        
        if node := pluginDesc.node:
            sock = getInputSocketByVRayAttr(node, boundProperty)
            if sock.is_linked:
                # If the socket is linked, it will be exported as part of the tree export
                return False

        selectedObject = getattr(pluginDesc.vrayPropGroup, boundProperty)
        pluginDesc.setAttribute(boundProperty, objectToAttrPlugin(selectedObject))
        return True


    @staticmethod
    def _getSearchCollectionProvider(context: bpy.types.Context, collName=''):
        if collName in ('', 'objects'):
            return (context.scene, 'objects')
        else:
            return (bpy.data, collName)


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
