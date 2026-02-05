from __future__ import annotations
import bpy

from vray_blender.lib import draw_utils
from vray_blender.lib.attribute_utils import getAttrDesc
from vray_blender.lib.defs import ExporterContext, PluginDesc
from vray_blender.exporting.tools import getInputSocketByAttr
from vray_blender.nodes.utils import getNodeOfPropGroup, selectedObjectTagUpdate
from vray_blender.plugins import getPluginAttr
from vray_blender.plugins.templates import multi_select
from vray_blender.plugins.templates.common import VRayObjectSelector


class TemplateIncludeExclude(multi_select.TemplateMultiObjectSelect):

    """ Include/exclude list of objects. 

        NOTE: This template has its own list of selected objects which is not shared with any
        sockets that might be defined for the same plugin properties.

        Template arguments:
           filter_function (optional): the name of the filter function for the object search field
           bound_property: the name of the property to receive the resulting list of objects
           mode_bound_property: the name of the property to receive the includeion mode flag
    """     

    inclusionMode: bpy.props.EnumProperty(
        items = [
            ('0', 'Exclude', 'Exclude selected items'),
            ('1', 'Include', 'Include selected items')
        ],
        name = 'Mode',
        default = '0',
        update = selectedObjectTagUpdate
    )

    def init(self, pluginModule, attrDesc: dict):
        modeBoundProperty = self.getTemplateAttr('mode_bound_property')
        inclusionAttr = getAttrDesc(pluginModule, modeBoundProperty)
        self.inclusionMode = '1' if inclusionAttr['default'] else '0' 
        

    def draw(self, layout:bpy.types.UILayout, context, pluginModule, propGroup, widgetAttr: dict, text):
        
        sock: bpy.types.NodeSocket = None

        if (boundProperty := self.getTemplateAttr('bound_property')) and (node := getNodeOfPropGroup(propGroup)):
            sock = getInputSocketByAttr(node, boundProperty)
        
        # Draw the template only when the socket is not linked.
        if sock and sock.hasActiveFarLink():
            return
        
        panel = layout

        if drawContainer := widgetAttr.get('draw_container'):
            if drawContainer == 'ROLLOUT':
                label = widgetAttr.get('label', getPluginAttr(pluginModule, widgetAttr['name']))
                uniqueID = f"{self.as_pointer()}_{widgetAttr['name']}"
                panel = draw_utils.rollout(layout, uniqueID,  label)

        # 'panel' will be None if the rollout is collapsed.
        if panel:                        
            row = panel.row(align=True) 
            row.use_property_decorate = False # Animation not supported for include/exclude lists
            row.prop(self, 'inclusionMode', expand=True)
        
            col = panel.column()
            super().draw(col, context, pluginModule, propGroup, widgetAttr, text, nested=True)
    

    def getInclude(self):
        return self.inclusionMode == '1'
    
    def getSelectedItems(self, context: bpy.types.Context, asIncludes: bool):
        """ Return a list of selected items. 
            Parameters:
                asIncludes (bool) : if True and the control is in exclusive mode, return 
                                    the inclusion list instead
        """
        searchCollection = self.getTemplateAttr('collection', '')
        selectedObjects = set(super().getSelectedItems(context, searchCollection),)
        
        if self.getInclude() or not asIncludes:
            return selectedObjects
        else:
            dataProvider = context.scene.objects if searchCollection in( '', 'objects') else getattr(bpy.data, searchCollection)
            allObjects = {o for o in dataProvider.values() if self._filterObject(o)}

            return list(allObjects.difference(selectedObjects))
        
    def exportToPluginDesc(self, exporterCtx: ExporterContext,  pluginDesc: PluginDesc):
        if super().exportToPluginDesc(exporterCtx, pluginDesc):
            pluginDesc.setAttribute(self.getTemplateAttr('mode_bound_property'), self.getInclude())
            return True
        return False

    def copy(self, dest: TemplateIncludeExclude):
        VRayObjectSelector(self).copy(dest)
        dest.activeItem = self.activeItem
        dest.inclusionMode = self.inclusionMode


def getRegClasses():
    return (
        TemplateIncludeExclude,
    )


def register():
    from vray_blender.lib.class_utils import registerClass
    for regClass in getRegClasses():
        registerClass(regClass)


def unregister():
    for regClass in reversed(getRegClasses()):
        bpy.utils.unregister_class(regClass)
