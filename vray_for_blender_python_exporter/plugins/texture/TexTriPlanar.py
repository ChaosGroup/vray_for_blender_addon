import mathutils

from vray_blender.nodes.utils import getInputSocketByVRayAttr, getNodeOfPropGroup
from vray_blender.lib.defs import ExporterContext, PluginDesc
from vray_blender.lib import  export_utils, plugin_utils

plugin_utils.loadPluginOnModule(globals(), __name__)



def nodeInit(node):
    _onModeChanged(node)


def onUpdateMode(propGroup, context, attrName):
    node = getNodeOfPropGroup(propGroup)
    _onModeChanged(node)


def _onModeChanged(node):
    propGroup = node.TexTriPlanar
    newMode = int(propGroup.texture_mode)
    
    showNegative = (newMode == 2)
    for propName in ("texture_negx", "texture_negy", "texture_negz"):
        sock = getInputSocketByVRayAttr(node, propName)
        sock.enabled = showNegative

    showNonX = (newMode > 0)    
    for propName in ("texture_y", "texture_z"):
        sock = getInputSocketByVRayAttr(node, propName)
        sock.enabled = showNonX


def exportCustom(ctx: ExporterContext, pluginDesc: PluginDesc):
    propGroup = pluginDesc.vrayPropGroup 
    mode = int(propGroup.texture_mode)

    if mode < 2: 
        pluginDesc.setAttributes({
            "texture_negx": None,
            "texture_negy": None,
            "texture_negz": None,
        })

    if mode < 1:
        pluginDesc.setAttributes({
            "texture_y": None,
            "texture_z": None
        })

    if (propGroup.ref_space == '1') and propGroup.ref_object:
        refObj = ctx.ctx.scene.objects.get(propGroup.ref_object)
        pluginDesc.setAttribute("node_ref_transform", refObj.matrix_world)
    
    if node := getNodeOfPropGroup(propGroup):
        if sock:= getInputSocketByVRayAttr(node, "texture_rotation_map"):
            if not sock.is_linked:
                pluginDesc.setAttribute("texture_rotation_map", mathutils.Vector(propGroup.texture_rotation_map[:]))


    return export_utils.exportPluginCommon(ctx, pluginDesc)


def widgetDrawRefObject(context, layout, propGroup, widgetAttr):
    layout.prop_search(propGroup, 'ref_object',  context.scene, 'objects', text="Object")