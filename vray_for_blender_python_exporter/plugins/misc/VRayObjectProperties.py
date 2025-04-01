import bpy
from vray_blender.lib import plugin_utils, export_utils
from vray_blender.lib.defs import PluginDesc
from vray_blender.nodes import utils as NodesUtils, filters
from vray_blender.nodes import tree_defaults

from vray_blender.bin import VRayBlenderLib as vray

plugin_utils.loadPluginOnModule(globals(), __name__)

def _updatePropPlugins(src, context, pluginName, pluginUseProp, locationY):
    if obj := context.active_object:
        if not obj.vray.ntree:
            tree_defaults.addObjectNodeTree(obj)

        objTree = obj.vray.ntree

        if hasattr(src, pluginUseProp):
            objOutput = NodesUtils.getNodeByType(objTree, 'VRayNodeObjectOutput')
            
            if objOutput.inputs[pluginName].is_linked:
                objTree.nodes.remove(NodesUtils.getNodeByType(objTree, f'VRayNodeObject{pluginName}Props'))
            elif getattr(src, pluginUseProp):
                propNode = objTree.nodes.new(f"VRayNodeObject{pluginName}Props")
                propNode.location.x = objOutput.location.x - 200
                propNode.location.y = objOutput.location.y - locationY

                objTree.links.new(propNode.outputs[pluginName], objOutput.inputs[pluginName])


def onMattePropsUpdate(src, context, attrName):
    _updatePropPlugins(src, context, "Matte", attrName, 80)

def onSurfacePropsUpdate(src, context, attrName):
    _updatePropPlugins(src, context, "Surface", attrName, 150)

def onVisibilityPropsUpdate(src, context, attrName):
    _updatePropPlugins(src, context, "Visibility", attrName, 220)


def exportCustom(exporterCtx, pluginDesc: PluginDesc):
    propGroup = pluginDesc.vrayPropGroup

    # Forward-create all referenced node objects
    # NOTE: This is not an ideal solution and it will not work for types that are not exported as 
    # Node (e.g. lights).
    if propGroup.reflection_object_selector.exportToPluginDesc(exporterCtx, pluginDesc):
        for attrPlugin in pluginDesc.getAttribute('reflection_exclude'):
            vray.pluginCreate(exporterCtx.renderer, attrPlugin.name, 'Node')

    if propGroup.refraction_object_selector.exportToPluginDesc(exporterCtx, pluginDesc):
        for attrPlugin in pluginDesc.getAttribute('refraction_exclude'):
            vray.pluginCreate(exporterCtx.renderer, attrPlugin.name, 'Node')

    return export_utils.exportPluginCommon(exporterCtx, pluginDesc)

