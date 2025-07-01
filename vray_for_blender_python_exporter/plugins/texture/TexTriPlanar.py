import math
from re import S

import mathutils

from vray_blender.exporting import node_export as commonNodesExport
from vray_blender.exporting.tools import getLinkedFromSocket
from vray_blender.nodes.utils import getInputSocketByVRayAttr, getNodeOfPropGroup, getVrayPropGroup
from vray_blender.lib import  export_utils, plugin_utils
from vray_blender.lib.defs import ExporterContext, PluginDesc, AttrPlugin, NodeContext
from vray_blender.lib.names import Names

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


def exportTreeNode(nodeCtx: NodeContext):
    node = nodeCtx.node
    propGroup = getVrayPropGroup(node)

    pluginName = Names.treeNode(nodeCtx)
    pluginDesc = PluginDesc(pluginName, 'TexTriPlanar')
    pluginDesc.vrayPropGroup = propGroup

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
        refObj = nodeCtx.exporterCtx.sceneObjects.get(propGroup.ref_object)
        pluginDesc.setAttribute("node_ref_transform", refObj.matrix_world)


    # Export texture rotation. 
    # The 'Texture Rotation' socket is created from the 'texture_rotation_map' property.
    # However, we need to export either 'texture_rotation_map' or 'texture_rotation' depending
    # on what is connected to the socket:
    #  - 'texture_rotation' - use to set a constant value - unlinked or linked to a V-Ray Vector node
    #  - 'texture_rotation_map' - linked to a node that exports to AttrPlugin

    sockRotation = getInputSocketByVRayAttr(node, "texture_rotation_map")

    if sockRotation.shouldExportLink():
        sockSource = getLinkedFromSocket(sockRotation)

        if sockSource.node.bl_idname == 'VRayNodeVector':
            vecRotation = sockSource.node.getValue()
            rotation = mathutils.Vector(tuple(map(math.degrees, vecRotation)))
            pluginDesc.setAttribute("texture_rotation", rotation)
            pluginDesc.setAttribute("texture_rotation_map", AttrPlugin())
        else:
            pluginRotation = commonNodesExport.exportLinkedSocket(nodeCtx, sockRotation)
            pluginDesc.setAttribute("texture_rotation_map", pluginRotation)
            pluginDesc.setAttribute("texture_rotation", AttrPlugin())
    else:
        pluginDesc.setAttribute("texture_rotation_map", None)
        pluginDesc.setAttribute("texture_rotation", mathutils.Vector(tuple(map(math.degrees, sockRotation.value))))
    
    processed = ["node_ref_transform", "texture_rotation","texture_rotation_map"]
    commonNodesExport.exportNodeTree(nodeCtx, pluginDesc, skippedSockets=(processed))
    return commonNodesExport.exportPluginWithStats(nodeCtx, pluginDesc)


def widgetDrawRefObject(context, layout, propGroup, widgetAttr):
    layout.prop_search(propGroup, 'ref_object',  context.scene, 'objects', text=widgetAttr['label'])