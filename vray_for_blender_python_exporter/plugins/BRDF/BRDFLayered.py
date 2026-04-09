# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
import mathutils

from vray_blender.lib import plugin_utils
from vray_blender.lib.export_utils import wrapAsTexture
from vray_blender.lib.defs import NodeContext, PluginDesc
from vray_blender.lib.draw_utils import UIPainter
from vray_blender.lib.mixin import VRayOperatorBase
from vray_blender.lib.names import Names
from vray_blender.exporting import node_export as commonNodesExport
from vray_blender.exporting.tools import getInputSocketByName, getFarNodeLink
from vray_blender.nodes.sockets import addInput, addOutput, getHiddenInput, moveExtendSocketToBottom
from vray_blender.nodes.utils import getVrayPropGroup
from vray_blender.plugins import getPluginModule

plugin_utils.loadPluginOnModule(globals(), __name__)


_MAX_LAYERED_BRDFS = 64 # maximum number of brdfs that could be added in BRDFLayered

def addBRDFLayeredExtendSocket(node):
    sockExtend = addInput(node, 'VRaySocketExtend', "")
    sockExtend.add_operator = 'vray.node_add_brdf_layered_sockets'
    sockExtend.del_operator = 'vray.node_del_brdf_layered_sockets'

def nodeInit(node: bpy.types.Node):
    # Base material
    addInput(node,'VRaySocketBRDF',  "Base Material")

    layerIndex = 1
    brdfSockName, weightSockName, opacitySockName = getLayerSocketNames(layerIndex)

    addInput(node,'VRaySocketBRDF',  brdfSockName)
    addInput(node, 'VRaySocketColor', weightSockName).setValue((0.5, 0.5, 0.5))
    addInput(node, 'VRaySocketWeight', opacitySockName, visible=False).setValue(1.0)

    addBRDFLayeredExtendSocket(node)

    addOutput(node, 'VRaySocketBRDF', "BRDF")


_ScheduledUpdates = set()

def nodeUpdate(node: bpy.types.Node):
    def _validateLinks():
        _ScheduledUpdates.discard(node.as_pointer())

        if not node or not node.id_data:
            return

        transpSock = node.inputs.get("Transparency Tex")
        if transpSock and transpSock.is_linked:
            baseMatSock = node.inputs.get("Base Material")
            if baseMatSock and not baseMatSock.is_linked:
                link = transpSock.links[0]
                if link.from_socket.bl_idname in {'VRaySocketBRDF', 'VRaySocketMtl'}:
                    node.id_data.links.new(link.from_socket, baseMatSock)
                    node.id_data.links.remove(link)

    # When creating a BRDFLayered on top on an existing node link between materials it will get connected
    # to the Transparency socket. In this case insert_link doesn't get called so we do it here manually.
    nodePtr = node.as_pointer()
    if nodePtr not in _ScheduledUpdates:
        _ScheduledUpdates.add(nodePtr)
        bpy.app.timers.register(_validateLinks)


def nodeInsertLink(link: bpy.types.NodeLink):
    node = link.to_node
    if link.to_socket.bl_idname == 'VRaySocketExtend':
        from_socket = link.from_socket
        ntree = node.id_data

        layersCount = _getLayersCount(node)
        newIndex = layersCount + 1

        brdfSockName, weightSockName, opacitySockName = getLayerSocketNames(newIndex)

        newBrdfSock = node.inputs.new('VRaySocketBRDF',  brdfSockName)
        node.inputs.new('VRaySocketColor', weightSockName).setValue((0.5, 0.5, 0.5))
        opacitySocket = node.inputs.new('VRaySocketWeight', opacitySockName)
        opacitySocket.setValue(1.0)
        opacitySocket.hide = True

        moveExtendSocketToBottom(node)

        # Update the link to use the new socket by creating a new link and removing the old one
        ntree.links.new(from_socket, newBrdfSock)
        ntree.links.remove(link)


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

    if brdfLink := getFarNodeLink(brdfSock):
        brdfs.append(commonNodesExport.exportSocketLink(nodeCtx, brdfLink))
        weights.append(wrapAsTexture(nodeCtx, mathutils.Color((1.0, 1.0, 1.0))))
        opacities.append(1.0)

    # Export layers
    for i in range(1, _MAX_LAYERED_BRDFS + 1):
        brdfSockName, weightSockName, opacitySockName = getLayerSocketNames(i)

        if not (brdfSock := getInputSocketByName(node, brdfSockName)):
            break

        if brdfLink := getFarNodeLink(brdfSock):
            brdf = commonNodesExport.exportSocketLink(nodeCtx, brdfLink)
            weight = None

            weightSock = getInputSocketByName(node, weightSockName)
            opacitySock = getInputSocketByName(node, opacitySockName)

            if weightLink := getFarNodeLink(weightSock):
                weight = commonNodesExport.exportSocketLink(nodeCtx, weightLink)
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
    bl_options     = {'INTERNAL', 'UNDO'}

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

        # Move the extend socket to the end
        from vray_blender.nodes.sockets import moveExtendSocketToBottom
        moveExtendSocketToBottom(node)

        return {'FINISHED'}


class VRAY_OT_node_del_brdf_layered_sockets(VRayOperatorBase):
    bl_idname      = 'vray.node_del_brdf_layered_sockets'
    bl_label       = "Remove Coat Material"
    bl_description = "Removes the last coat material (only if there are no links to it)"
    bl_options     = {'INTERNAL', 'UNDO'}

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

            if not brdfSock.hasActiveFarLink() and not weightSock.hasActiveFarLink():
                node.inputs.remove(brdfSock)
                node.inputs.remove(weightSock)
                node.inputs.remove(opacitySock)
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