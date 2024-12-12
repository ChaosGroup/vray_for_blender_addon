from vray_blender.lib import plugin_utils
from vray_blender.nodes import utils as NodesUtils
from vray_blender.nodes import tree_defaults

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
