import bpy

from vray_blender.lib.defs import ExporterContext, PluginDesc
from vray_blender.lib import draw_utils
from vray_blender.lib.plugin_utils import objectToAttrPlugin
from vray_blender.nodes.tools import getFilterFunction
from vray_blender.nodes.utils import getInputSocketByVRayAttr, getNodeOfPropGroup
from vray_blender.plugins import getPluginAttr
from vray_blender.plugins.templates import common


class TemplateMultiObjectSelect(common.VRayObjectSelector):

    """ Show UI for selecting a list of objects. 

        NOTE: This template should have a corresponding a widget description and is normally
        created for plugin properties of type TEMPLATE.

        Tempalate arguments:
           filter_function (optional): the name of the filter function for the object search field
           bound_property (optional): the name of the property to receive the resulting list of objects
    """     

    # VRayObjectSelectot callback
    def onFilterObject(self, obj):
        # Return the poll (filter) function for the Object Select field
        if filterFn := getFilterFunction(self.vray_plugin, self.getTemplateAttr('filter_function', '')):
            return filterFn(obj)
        return True


    def onSelectionChanged(self, context: bpy.types.Context):
        if node := getattr(context, 'active_node', None):
            node.id_data.update_tag()
        elif obj := getattr(context, 'active_object', None):
            obj.update_tag()


    def draw(self, layout: bpy.types.UILayout, context: bpy.types.Context, 
                    pluginModule, propGroup, widgetAttr: dict, text, nested=False):
        
        sock: bpy.types.NodeSocket = None

        if (boundProperty := self.getTemplateAttr('bound_property')) and (node := getNodeOfPropGroup(propGroup)):
            sock = getInputSocketByVRayAttr(node, boundProperty)
        
        if sock and sock.is_linked:
            return
        
        attrName = widgetAttr['name']
        panel = layout
        panel.use_property_decorate = False # Animation not supported for object lists

        # This class may be used as a base and in this case the UI it draws is nested inside
        # the UI drawn by the derived class. Draw the container only if it is not nested.
        if (not nested) and (drawContainer := widgetAttr.get('draw_container')):
            if drawContainer == 'ROLLOUT':
                label = widgetAttr.get('label', getPluginAttr(pluginModule, attrName))
                uniqueID = f"{self.as_pointer()}_{widgetAttr['name']}"
                panel = draw_utils.rollout(layout, uniqueID,  label)

        # 'panel' will be None if the rollout is collapsed
        if panel:    
            if drawPre := widgetAttr.get('draw_pre', []):
                painter = draw_utils.UIPainter(context, pluginModule, propGroup)
                painter.renderWidgets(layout, drawPre)
                                      
            collectionName = self.getTemplateAttr('collection', '')
            listLabel = widgetAttr['list_label']
            data, prop = TemplateMultiObjectSelect._getSearchCollectionProvider(context, collectionName)
            self.drawSelectorUI(context, layout, dataProvider=data, dataProperty=prop, listLabel=listLabel)
        

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

        collectionName = self.getTemplateAttr('collection', '')
        selectedObjects = self.getSelectedItems(exporterCtx.ctx, collectionName)
        pluginList = [objectToAttrPlugin(o) for  o in selectedObjects]
        pluginDesc.setAttribute(boundProperty, pluginList)
        return True

    @staticmethod
    def _getSearchCollectionProvider(context: bpy.types.Context, collName=''):
        if collName in ('', 'objects'):
            return (context.scene, 'objects')
        else:
            return (bpy.data, collName)
            
class TemplateSelectGeometries(common.VRayObjectSelector):
    """ Select multuple geometry objects from the active scene.

        NOTE: This template is meant to be used without a backing widget description.
    """
    def onFilterObject(self, obj):
        # Return the poll (filter) function for the Object Select field
        if filterFn := getFilterFunction('', 'filters.filterGeometries'):
            return filterFn(obj)
        return True

    def onSelectionChanged(self, context: bpy.types.Context):
        if node := getattr(context, 'active_node', None):
            node.update()
        elif obj := getattr(context, 'active_object', None):
            context.active_object.update_tag()


    def draw(self, layout:bpy.types.UILayout, context: bpy.types.Context):
        super().drawSelectorUI(context, layout, listLabel='Object List')
        

    

def getRegClasses():
    return (
        TemplateMultiObjectSelect,
        TemplateSelectGeometries
    )


def register():
    from vray_blender.lib.class_utils import registerClass
    for regClass in getRegClasses():
        registerClass(regClass)


def unregister():
    for regClass in reversed(getRegClasses()):
        bpy.utils.unregister_class(regClass)
