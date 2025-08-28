
import bpy
import mathutils

from vray_blender.lib import plugin_utils
from vray_blender.lib.export_utils import wrapAsTexture
from vray_blender.lib.defs import NodeContext, PluginDesc
from vray_blender.lib.draw_utils import UIPainter
from vray_blender.lib.names import Names
from vray_blender.nodes.sockets import addInput, addOutput, getHiddenInput
from vray_blender.nodes.tools import isInputSocketLinked
from vray_blender.nodes.utils import getVrayPropGroup
from vray_blender.exporting import node_export as commonNodesExport
from vray_blender.exporting.tools import getInputSocketByName
from vray_blender.plugins import getPluginModule
from vray_blender.lib.mixin import VRayOperatorBase

plugin_utils.loadPluginOnModule(globals(), __name__)


_MAX_LAYERED_BRDFS = 64 # maximum number of brdfs that could be added in BRDFLayered


def nodeInit(node: bpy.types.Node):
    # Base material
    addInput(node,'VRaySocketBRDF',  "Base Material")

    layerIndex = 1
    brdfSockName, weightSockName, opacitySockName = getLayerSocketNames(layerIndex)

    addInput(node,'VRaySocketBRDF',  brdfSockName)
    addInput(node, 'VRaySocketColor', weightSockName).setValue((0.5, 0.5, 0.5))
    addInput(node, 'VRaySocketWeight', opacitySockName, visible=False).setValue(1.0)

    addOutput(node, 'VRaySocketBRDF', "BRDF")


def nodeDraw(context, layout, node):
    split = layout.split()
    row = split.row(align=True)
    row.operator('vray.node_add_brdf_layered_sockets', icon="ADD", text="Add Coat Material")
    row.operator('vray.node_del_brdf_layered_sockets', icon="REMOVE", text="")


def nodeDrawSide(context, layout, node):
    propGroup = getVrayPropGroup(node)
    painter = UIPainter(context, getPluginModule('BRDFLayered'), propGroup)
    painter.renderPluginUI(layout)


def exportTreeNode(nodeCtx: NodeContext):
    node = nodeCtx.node

    brdfs     = []
    weights   = []
    opacities = []

    # Export base material
    brdfSock = getInputSocketByName(node, "Base Material")
    assert brdfSock

    if isInputSocketLinked(brdfSock):
        brdfs.append(commonNodesExport.exportLinkedSocket(nodeCtx, brdfSock))
        weights.append(wrapAsTexture(nodeCtx, mathutils.Color((1.0, 1.0, 1.0))))
        opacities.append(1.0)

    # Export layers
    for i in range(1, _MAX_LAYERED_BRDFS + 1):
        brdfSockName, weightSockName, opacitySockName = getLayerSocketNames(i)

        if not (brdfSock := getInputSocketByName(node, brdfSockName)):
            break

        if isInputSocketLinked(brdfSock):
            brdf = commonNodesExport.exportLinkedSocket(nodeCtx, brdfSock)
            weight = None

            weightSock = getInputSocketByName(node, weightSockName)
            opacitySock = getInputSocketByName(node, opacitySockName)

            if isInputSocketLinked(weightSock):
                weight = commonNodesExport.exportLinkedSocket(nodeCtx, weightSock)
            else:
                weight = mathutils.Color(weightSock.value)

            brdfs.append(brdf)
            weights.append(wrapAsTexture(nodeCtx, weight))
            opacities.append(opacitySock.value)

    pluginName = Names.treeNode(nodeCtx)
    pluginDesc = PluginDesc(pluginName, "BRDFLayered")
    pluginDesc.vrayPropGroup = node.BRDFLayered

    pluginDesc.setAttribute("brdfs", list(reversed(brdfs)))
    pluginDesc.setAttribute("weights", list(reversed(weights)))
    pluginDesc.setAttribute("opacities", list(reversed(opacities)))

    skippedSockets = ['brdfs', 'weights', 'opacities']
    commonNodesExport.exportNodeTree(nodeCtx, pluginDesc, skippedSockets)
    return commonNodesExport.exportPluginWithStats(nodeCtx, pluginDesc)


class VRAY_OT_node_add_brdf_layered_sockets(VRayOperatorBase):
    bl_idname      = 'vray.node_add_brdf_layered_sockets'
    bl_label       = "Add Coat Material"
    bl_description = "Add a new coat material"
    bl_options     = {'INTERNAL'}

    def execute(self, context):
        node = context.node

        layersCount = _getLayersCount(node)
        newIndex = layersCount + 1

        brdfSockName, weightSockName, opacitySockName = getLayerSocketNames(newIndex)

        node.inputs.new('VRaySocketBRDF',  brdfSockName)
        node.inputs.new('VRaySocketColor', weightSockName).setValue((0.5, 0.5, 0.5))
        opacitySocket = node.inputs.new('VRaySocketWeight', opacitySockName)
        opacitySocket.setValue(1.0)
        opacitySocket.hide = True


        return {'FINISHED'}


class VRAY_OT_node_del_brdf_layered_sockets(VRayOperatorBase):
    bl_idname      = 'vray.node_del_brdf_layered_sockets'
    bl_label       = "Remove Coat Material"
    bl_description = "Removes the last coat material (only if there are no links to it)"
    bl_options     = {'INTERNAL'}

    def execute(self, context):
        node = context.node

        layersCount = _getLayersCount(node)

        if layersCount == 0:
            return {'FINISHED'}

        for i in range(layersCount, -1, -1):
            brdfSockName, weightSockName, opacitySockName = getLayerSocketNames(i)

            if not (brdfSockName in node.inputs and weightSockName in node.inputs):
                continue

            brdfSock   = node.inputs[brdfSockName]
            weightSock = node.inputs[weightSockName]
            opacitySock = getHiddenInput(node, opacitySockName)

            if not isInputSocketLinked(brdfSock) and not isInputSocketLinked(weightSock):
                node.inputs.remove(node.inputs[brdfSockName])
                node.inputs.remove(node.inputs[weightSockName])
                node.inputs.remove(node.inputs[opacitySockName])
                break

        return {'FINISHED'}


def _getLayersCount(node: bpy.types.Node):
    opacityInputs = [i for i in node.inputs if i.name.startswith('Opacity')]
    return len(opacityInputs)


def getLayerSocketNames(index: int):
    brdfSockName    = f"Coat Material {index}"
    weightSockName  = f"Blend Amount {index}"
    opacitySockName = f"Opacity {index}"

    return brdfSockName, weightSockName, opacitySockName


def getRegClasses():
    return (
        VRAY_OT_node_add_brdf_layered_sockets,
        VRAY_OT_node_del_brdf_layered_sockets,
    )


def register():
    for regClass in getRegClasses():
        bpy.utils.register_class(regClass)


def unregister():
    for regClass in getRegClasses():
        bpy.utils.unregister_class(regClass)