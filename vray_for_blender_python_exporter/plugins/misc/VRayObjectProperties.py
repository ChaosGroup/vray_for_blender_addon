from vray_blender.lib import plugin_utils, export_utils
from vray_blender.lib.defs import PluginDesc, AttrPlugin
from vray_blender.nodes import utils as NodesUtils
from vray_blender.nodes import tree_defaults

from vray_blender.bin import VRayBlenderLib as vray

plugin_utils.loadPluginOnModule(globals(), __name__)


def _propSetter(propGroup, pluginName, locationY, useProp):
    """ If 'useProp' is True, connects a 'VRayNodeObject{pluginName}Props' node to the Object node tree.
        If 'useProp' is False, disconnects and removes any such node already connected.
    """

    if obj := propGroup.id_data:
        if not obj.vray.ntree:
            if obj.vray.isVRayFur:
                tree_defaults.addFurNodeTree(obj)
            else:
                tree_defaults.addObjectNodeTree(obj)
        
        objTree = obj.vray.ntree
        objOutput = NodesUtils.getNodeByType(objTree, 'VRayNodeObjectOutput')
        
        # No checks are needed to verify if the connected node is of type "VRayNodeObject{pluginName}Props",
        # as the getter function already handles this logic, and "useProp" will be the inverse of its return value.
        if useProp:
            if not objOutput:
                objOutput = objTree.nodes.new(f"VRayNodeObjectOutput")

            propNode = objTree.nodes.new(f"VRayNodeObject{pluginName}Props")
            propNode.location.x = objOutput.location.x - 200
            propNode.location.y = objOutput.location.y - locationY

            objTree.links.new(propNode.outputs[pluginName], objOutput.inputs[pluginName])
        else:
            objTree.nodes.remove(NodesUtils.getFarNodeLink(objOutput.inputs[pluginName]).from_node)

def _propGetter(propGroup, pluginName):
    """ Checks if 'VRayNodeObject{pluginName}Props' node is connected to the Object tree """
    
    if (obj := propGroup.id_data) and obj.vray.ntree:
        objTree = obj.vray.ntree

        objOutput = NodesUtils.getNodeByType(objTree, 'VRayNodeObjectOutput')     
        if objOutput and (nodeLink := NodesUtils.getFarNodeLink(objOutput.inputs[pluginName])):
            return nodeLink.from_node.bl_idname == f'VRayNodeObject{pluginName}Props'

    return False

def mattePropsSetter(propGroup, attrName, useProp):
    _propSetter(propGroup, "Matte", 80, useProp)

def mattePropsGetter(propGroup, attrName):
    return _propGetter(propGroup, "Matte")


def surfacePropsSetter(propGroup, attrName, useProp):
    _propSetter(propGroup, "Surface", 150, useProp)

def surfacePropsGetter(propGroup, attrName):
    return _propGetter(propGroup, "Surface")


def visibilityPropsSetter(propGroup, attrName, useProp):
    _propSetter(propGroup, "Visibility", 220, useProp)

def visibilityPropsGetter(propGroup, attrName):
    return _propGetter(propGroup, "Visibility")


def exportCustom(exporterCtx, pluginDesc: PluginDesc):
    propGroup = pluginDesc.vrayPropGroup

    # Forward-create all referenced node objects
    # NOTE: This is not an ideal solution and it will not work for types that are not exported as 
    # Node (e.g. lights).
    if propGroup.use_visibility:
        if propGroup.reflection_object_selector.exportToPluginDesc(exporterCtx, pluginDesc):
            for attrPlugin in pluginDesc.getAttribute('reflection_exclude'):
                vray.pluginCreate(exporterCtx.renderer, attrPlugin.name, 'Node')
    
        if propGroup.refraction_object_selector.exportToPluginDesc(exporterCtx, pluginDesc):
            for attrPlugin in pluginDesc.getAttribute('refraction_exclude'):
                vray.pluginCreate(exporterCtx.renderer, attrPlugin.name, 'Node')
        
        return export_utils.exportPluginCommon(exporterCtx, pluginDesc)
    else:
        pluginDesc.setAttribute('reflection_exclude', [])
        pluginDesc.setAttribute('refraction_exclude', [])
        
        # export_utils.exportPluginCommon will also export templates. We don't want this here, as it will
        # override the reflection/refraction exlude lists. 
        vray.pluginCreate(exporterCtx.renderer, pluginDesc.name, pluginDesc.type)
        return export_utils.exportPluginParams(exporterCtx, pluginDesc)

