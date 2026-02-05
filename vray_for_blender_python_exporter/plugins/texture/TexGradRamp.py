
import bpy

from vray_blender.exporting import node_export as commonNodesExport
from vray_blender.exporting.tools import getFarNodeLink, getInputSocketByAttr, FarNodeLink
from vray_blender.lib import plugin_utils
from vray_blender.lib.defs import  PluginDesc, NodeContext
from vray_blender.lib.names import Names
from vray_blender.nodes.utils import getNodeOfPropGroup
from vray_blender.plugins import getPluginAttr, getPluginModule
from vray_blender.nodes.specials.gradient_ramp import VRaySocketColorRamp, createColorRampNode

plugin_utils.loadPluginOnModule(globals(), __name__)


_PLUGIN_TYPE = 'TexGradRamp'


def nodeInit(node: bpy.types.Node):
    """Executes upon node initialization. Prepares initial values and creates a
    gradient ramp node and attaches it to the TexGradRamp node and the mouse."""
    positionSocket = getInputSocketByAttr(node, "gradient_position")
    positionSocket.hide = True
    textureMapSocket = getInputSocketByAttr(node, "texture_map")
    textureMapSocket.hide = True

    rampSocket = next(sock for sock in node.inputs if sock.bl_idname == VRaySocketColorRamp.bl_idname)
    createColorRampNode(node, rampSocket)

    # Make the original node (Toon) active.
    node.id_data.nodes.active = node


def widgetDrawRamp(context, layout: bpy.types.UILayout, propGroup, widgetAttr):
    node = getNodeOfPropGroup(propGroup)
    sock = getInputSocketByAttr(node, widgetAttr.get("name", ""))

    if sock is None or not sock.is_linked:
        return

    for link in sock.links:
        # Draw color ramp
        ramp_node = link.from_node
        layout.separator()
        box = layout.box()
        socket_label = sock.identifier
        box.label(text=socket_label)
        box.template_color_ramp(ramp_node.texture, 'color_ramp')

        # Draw all point textures for current color ramp
        for sock in ramp_node.inputs:
            box.prop(sock, 'value', text=sock.identifier)


def onUpdateGradientType(texGradRamp, context, attrName):
    node = getNodeOfPropGroup(texGradRamp)

    positionSocket = getInputSocketByAttr(node, "gradient_position")
    positionSocket.hide = texGradRamp.gradient_type!="12"

    textureMapSocket = getInputSocketByAttr(node, "texture_map")
    textureMapSocket.hide = texGradRamp.gradient_type!="5"

    # The Lighting, Normal, Mapped, Position types do not use the uvwgen.
    NoMappingTypes = ( "3", "5", "6", "12" )
    textureMapSocket = getInputSocketByAttr(node, "uvwgen")
    textureMapSocket.hide = (texGradRamp.gradient_type in NoMappingTypes)

def exportTreeNode(nodeCtx: NodeContext):
    node = nodeCtx.node

    pluginDesc = PluginDesc(Names.nextVirtualNode(nodeCtx, _PLUGIN_TYPE), _PLUGIN_TYPE)
    pluginDesc.vrayPropGroup = node.TexGradRamp

    # Export all properties associated with the color ramp.
    rampAttributes = ("texture", "colors", "positions", "interpolation")

    for sock in node.inputs:
        if nodeLink := sock.getFarLink():
            if sock.bl_idname == VRaySocketColorRamp.bl_idname:
                origin_node = sock.links[0].from_node
                colors, positions, interpolation = origin_node.exportGradTreeNode(nodeCtx)
                pluginDesc.setAttribute('colors', colors)
                pluginDesc.setAttribute('positions', positions)
                pluginDesc.setAttribute('interpolation', [interpolation] * len(positions))
                continue

            linkedPlugin = commonNodesExport.exportVRayNode(nodeCtx, nodeLink)

            linkedPlugin = commonNodesExport._exportConverters(
                    nodeCtx, nodeLink.to_socket, linkedPlugin)
            pluginDesc.setAttribute(sock.vray_attr, linkedPlugin)
        elif sock.vray_attr not in ('', *rampAttributes):
            # rampAttributes were already exported, dynamic texture sockets have empty vray_attr
            if hasattr(sock, 'exportUnlinked'):
                attrDesc = getPluginAttr(getPluginModule(_PLUGIN_TYPE), sock.vray_attr)
                sock.exportUnlinked(nodeCtx, pluginDesc, attrDesc)

    return commonNodesExport.exportPluginWithStats(nodeCtx, pluginDesc)
