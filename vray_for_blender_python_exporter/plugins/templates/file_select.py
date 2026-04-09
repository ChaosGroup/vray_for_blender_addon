# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy

from vray_blender.lib.blender_utils import getObjectFromEditorContext
from vray_blender.nodes.utils import getNodeOfPropGroup
from vray_blender.operators import VRAY_OT_FileSelect
from vray_blender.plugins import getPluginAttr
from vray_blender.plugins.templates import common


class TemplateFileSelect(common.VRayUITemplate):
    """ Show UI for selecting a file path.

        NOTE: This template should have a corresponding a widget description and is normally
        created for plugin properties of type TEMPLATE.

        Template arguments:
           bound_property: the name of the property to receive the resulting file path
           obj_type: The type of the file path property holder. Can be one of:
            - shader_node  :for all shader tree nodes and lights without node trees
            - camera
            - settings 
    """

    def draw(self, layout: bpy.types.UILayout, context: bpy.types.Context, 
                    pluginModule, propGroup, widgetAttr: dict, text, nested=False):
        
        boundProperty = self.getTemplateAttr('bound_property')
        objType = self.getTemplateAttr('object_type')
        
        assert boundProperty, "FileSelect template is missing the 'bound_property' argument"
        assert objType, "FileSelect template is missing the 'object_type' argument"

        row = layout.row(align=True)

        row.prop(propGroup, boundProperty, text=text)
        op = row.operator("vray.file_select", text="", icon="FILE_FOLDER")
        
        # Set the filter on file extensions
        attrDesc = getPluginAttr(pluginModule, boundProperty)
        if attrDesc and 'ui' in attrDesc and 'file_extensions' in attrDesc['ui']:
             VRAY_OT_FileSelect.setFilter(op, attrDesc['ui']['file_extensions'])
        

        match objType:
            case "shader_node":
                node = getNodeOfPropGroup(propGroup)
              
                if context.light:
                    # Lights are special because they may have or not have nodes
                    selector = node.name if node else propGroup.name
                    obj = getObjectFromEditorContext(context)
                    op.object_name = obj.name
                    op.selector_name = selector
                    op.object_type = 'light'

                elif context.material:
                    obj = getObjectFromEditorContext(context)
                    op.object_name = obj.active_material.name
                    op.selector_name = node.name
                    op.object_type = 'material'
                
                elif context.world:
                    op.object_type = 'world'
                    op.object_name = context.world.name
                    op.selector_name = node.name

                elif context.object:
                    op.object_type = 'object'
                    op.object_name = context.object.name
                    op.selector_name = node.name

            case "camera":
                op.object_name = context.active_object.name
                op.object_type = objType
                
            case "settings":
                op.selector_name = pluginModule.ID
                op.object_type = objType
        
        op.plugin_type = pluginModule.ID
        op.bound_property = boundProperty
        op.callback = 'plugins.templates.file_select.onFileSelected'

    def exportToPluginDesc(self, exporterCtx,  pluginDesc):
        # Nothing to export
        return False
    
    def copy(self, dest):
        pass


def onFileSelected(op: bpy.types.Operator, filepath: str):
        
    propGroup = ""

    match op.object_type:
        case "light":
            light = bpy.data.lights[op.object_name]
            if light.use_nodes:
                node = light.node_tree.nodes[op.selector_name]
                propGroup = getattr(node, op.plugin_type)
            else:
                propGroup = getattr(light.vray, op.plugin_type)

        case "material":
            node = bpy.data.materials[op.object_name].node_tree.nodes[op.selector_name]
            propGroup = getattr(node, op.plugin_type)

        case "world":
            node = bpy.data.worlds[op.object_name].node_tree.nodes[op.selector_name]
            propGroup = getattr(node, op.plugin_type)
        
        case "object":
            object = bpy.data.objects[op.object_name].vray.ntree.nodes[op.selector_name]
            propGroup = getattr(object, op.plugin_type)

        case "camera":
            camera = bpy.data.cameras[op.object_name]
            propGroup = getattr(camera.vray, op.plugin_type)

        case "settings":
            propGroup = getattr(bpy.context.scene.vray, op.selector_name)

    setattr(propGroup, op.bound_property, filepath)
     

def getRegClasses():
    return (
        TemplateFileSelect,
    )


def register():
    from vray_blender.lib.class_utils import registerClass
    for regClass in getRegClasses():
        registerClass(regClass)


def unregister():
    for regClass in reversed(getRegClasses()):
        bpy.utils.unregister_class(regClass)
