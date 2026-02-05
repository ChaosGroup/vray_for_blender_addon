"""Functionality related to the BRDF Toon Material node. Includes logic for
node initialization, destruction, copy, update, export, and widget-specific logic. """
import bpy

import vray_blender.nodes.curves_node as cn
from vray_blender.exporting import node_export as commonNodesExport
from vray_blender.exporting.tools import FarNodeLink, getFarNodeLink, getInputSocketByAttr, getLinkedFromSocket
from vray_blender.lib import plugin_utils
from vray_blender.lib.defs import NodeContext, PluginDesc
from vray_blender.lib.draw_utils import getAttrLabel
from vray_blender.lib.names import Names
from vray_blender.plugins import getPluginAttr, getPluginModule
from vray_blender.nodes.utils import getNodeOfPropGroup
from vray_blender.nodes.specials.gradient_ramp import VRaySocketColorRamp, createColorRampNode
from vray_blender.nodes.tools import isVraySocket

plugin_utils.loadPluginOnModule(globals(), __name__)

_PLUGIN_TYPE = 'BRDFToonMtl'

REMAP_PRESETS = {
    "0": { # Default
        "points": ( (0.0, 1.0), (1.0, 1.0) ),
        "type": ('AUTO', 'AUTO')
    },
    "1": { # Square
        "points": (
            (0.0, 0.95), (0.125, 1.0), (0.25, 0.95), (0.375, 1.0),
            (0.5, 0.95), (0.625, 1.0), (0.75, 0.95), (0.875, 1.0),
            (1.0, 0.95)
        ),
        "type": ('VECTOR',) * 9
    },
    "2": { # Hexagon
        "points": (
            (0.0,    0.975), (0.0833, 1.0), (0.1667, 0.975), (0.25,   1.0),
            (0.3333, 0.975), (0.4167, 1.0), (0.5,    0.975), (0.5833, 1.0),
            (0.6667, 0.975), (0.75,   1.0), (0.8333, 0.975), (0.9167, 1.0),
            (1.0,    0.975)
        ),
        "type": ('VECTOR',) * 13
    },
    "3": { # Star
        "points": (
            (0.0,   1.0),  (0.05,  0.3),  (0.125, 0.15), (0.2, 0.3), (0.25,  1.0),
            (0.3,   0.3),  (0.375, 0.15), (0.45,  0.3),  (0.5, 1.0), (0.55,  0.3),
            (0.625, 0.15), (0.7,   0.3),  (0.75,  1.0),  (0.8, 0.3), (0.875, 0.15),
            (0.95,  0.3),  (1.0,   1.0)
        ),
        "type": ('VECTOR',) * 17
    },
    "4": { # Heart
        "points": ( (0.0, 0.0), (0.5, 1.0), (1.0, 0.0) ),
        "type": ('VECTOR', 'VECTOR', 'VECTOR')
    }
}


def nodeInsertLink(link: bpy.types.NodeLink):
    normal_map_textures = ('VRayNodeTexNormalBump', 'VRayNodeTexBlendBumpNormal', 'VRayNodeTexNormalMapFlip')
    attrMap = { 'bump_map':      'bump_type',
                'coat_bump_map': 'coat_bump_type' }

    toSock = link.to_socket

    if not isVraySocket(toSock) or (toSock.vray_attr not in attrMap):
        return

    if toSock.vray_attr in attrMap:
        if (attrName := attrMap.get(toSock.vray_attr)) is None:
            return

        fromNode = getLinkedFromSocket(toSock).node
        toNode = toSock.node

        if fromNode.bl_idname in normal_map_textures:
            # Set 'explicit' type to indicate that the connected texture is a normal map
            setattr(toNode.BRDFVRayMtl, attrName, '6')
        elif getattr(toNode.BRDFVRayMtl, attrName) == '6':
            # Set default bump type
           setattr(toNode.BRDFVRayMtl, attrName, '0')


def nodeInit(node: bpy.types.Node):
    """Executes upon node initialization. Creates gradient ramp nodes and
    attaches them to the Toon node and the mouse."""
    rampSockets = [sock for sock in node.inputs if sock.bl_idname == VRaySocketColorRamp.bl_idname]

    for sock in rampSockets:
        createColorRampNode(node, sock)

    node.id_data.nodes.active = node

    # The static id is needed for the name of the curves node.
    node.assignStaticId()

    # The current "bpy" API does not allow direct creation of a CurveMapping
    # widget. To work around this, we create a ShaderNodeRGBCurve in a hidden
    # node tree and use its widget.
    cn.createCurvesNode(node)

    attrName = "highlight_preset"
    propGroup = node.BRDFToonMtl
    updateRemapWidget(propGroup, None, attrName)


def nodeFree(node: bpy.types.Node):
    """Executes upon node deletion."""
    cn.removeCurvesNode(node)


def nodeUpdate(node: bpy.types.Node):
    """Executes upon node topology changes.
    Currently only used for Remap widget changes."""
    if not cn.hasCurvesNode(node):
        return

    # Encode the curves data as a string so that it could be saved in the
    # BRDFToonMtl node, and not just be available from the auxiliary
    # ShaderNodeRGBCurve node. This will allow us to restore the curves state
    # if the node is imported into another scene.
    curvesNode = cn.getCurvesNode(node)
    node.BRDFToonMtl['curves_data'] = cn.encodeMapping(curvesNode.mapping)


def nodeCopy(copyNode: bpy.types.Node, origNode: bpy.types.Node):
    """Handles the logic for copying the node."""
    cn.curvesCopy(copyNode, origNode)


def widgetDrawRamp(context, layout: bpy.types.UILayout, propGroup, widgetAttr):
    """Draws the gradient ramp widgets in the material editor's side menu."""
    node = getNodeOfPropGroup(propGroup)
    sock = getInputSocketByAttr(node, widgetAttr.get("name", ""))

    # If the ramp socket isn't connected, there's no data to draw.
    if sock is None or not sock.is_linked:
        return

    for link in sock.links:
        # Draw color ramp.
        rampNode = link.from_node
        layout.separator()
        box = layout.box()
        socketLabel = sock.identifier
        box.label(text=socketLabel)
        box.template_color_ramp(rampNode.texture, 'color_ramp')

        # Draw all point textures for current color ramp.
        for sock in rampNode.inputs:
            box.prop(sock, 'value', text=sock.identifier)


def floatToTex(values: list[float], nodeCtx: NodeContext) -> list[bpy.types.Texture]:
    """Converts float values to Texture types with a single
    parameter "input" holding the float value."""
    texValues = []

    # For each value create a FloatToTex texture type and assign the value to the "input" attribute.
    for idx, value in enumerate(values):
        float2tex = PluginDesc(f"{Names.treeNode(nodeCtx)}_{idx}", "FloatToTex")
        float2tex.setAttribute("input", value)
        texValues.append(commonNodesExport.exportPluginWithStats(nodeCtx, float2tex))
    return texValues


def exportTreeNode(nodeCtx: NodeContext):
    """Exports the whole node's tree to V-Ray for rendering."""
    node = nodeCtx.node

    pluginDesc = PluginDesc(Names.nextVirtualNode(nodeCtx, _PLUGIN_TYPE), _PLUGIN_TYPE)
    pluginDesc.vrayPropGroup = node.BRDFToonMtl

    _fillCurvesMapWidgetData(nodeCtx, pluginDesc)

    # Export all properties associated with the color ramps.
    # Variable names follow the description attribute name property.
    diffuse_ramp = {
        'colAttrName':    "diffuse_colors",
        'posAttrName':    "diffuse_positions",
        'interpAttrName': "diffuse_interpolations"
    }
    specular_ramp = {
        'colAttrName':    "specular_colors",
        'posAttrName':    "specular_positions",
        'interpAttrName': "specular_interpolations"
    }

    sock_vray_attrs = []

    for sock in node.inputs:
        sock_vray_attrs.append(sock.vray_attr)
        if nodeLink := getFarNodeLink(sock):
            if sock.bl_idname == VRaySocketColorRamp.bl_idname:
                assert(sock.vray_attr in {"diffuse_ramp", "specular_ramp"})

                if sock.vray_attr == "diffuse_ramp":
                    currRamp = diffuse_ramp
                elif sock.vray_attr == "specular_ramp":
                    currRamp = specular_ramp

                originNode = sock.links[0].from_node
                colors, positions, interpolation = originNode.exportGradTreeNode(nodeCtx)
                interpolations = [interpolation] * len(positions)
                pluginDesc.setAttribute(currRamp['colAttrName'], colors)
                pluginDesc.setAttribute(currRamp['posAttrName'], positions)
                pluginDesc.setAttribute(currRamp['interpAttrName'], interpolations)
                continue

            linkedPlugin = commonNodesExport.exportVRayNode(nodeCtx, nodeLink)

            if linkedPlugin is None:
                continue

            linkedPlugin = commonNodesExport._exportConverters(
                    nodeCtx, nodeLink.to_socket, linkedPlugin)
            pluginDesc.setAttribute(sock.vray_attr, linkedPlugin)
        elif sock.vray_attr not in ('', *diffuse_ramp.values(), *specular_ramp.values()):
            # rampAttributes were already exported, dynamic texture sockets have empty vray_attr
            if hasattr(sock, 'exportUnlinked'):
                attrDesc = getPluginAttr(getPluginModule(_PLUGIN_TYPE), sock.vray_attr)
                sock.exportUnlinked(nodeCtx, pluginDesc, attrDesc)

    return commonNodesExport.exportPluginWithStats(nodeCtx, pluginDesc)


def _fillCurvesMapWidgetData(nodeCtx, pluginDesc):
    """Handles the export for the Remap curves mapping widget values."""
    node = nodeCtx.node

    # Unit conversion.
    pluginDesc.vrayPropGroup.reflect_dim_distance *= 100 # cm -> m
    if pluginDesc.vrayPropGroup.fog_mult != 0.0:
        pluginDesc.vrayPropGroup.fog_mult = 0.01 / pluginDesc.vrayPropGroup.fog_mult # cm -> m

    curvesNode = cn.getCurvesNode(node)
    curveMapping = curvesNode.mapping

    # BRDFToonMtl only supports combined curve, no separate R, G, B channels.
    curve  = curveMapping.curves[3] # "C" (combined) curve.
    pointsX, pointsY, types = cn.fillSplineData(curve, curveMapping.extend)
    pointsY = floatToTex(pointsY, nodeCtx)

    pluginDesc.setAttribute("highlight_positions", pointsX)
    pluginDesc.setAttribute("highlight_values", pointsY)
    pluginDesc.setAttribute("highlight_interpolations", types)


def updateRemapWidget(propGroup: bpy.types.Node, context: bpy.types.Context, attrName: str):
    """Updates the remap widget upon choosing a highlight shape preset from the dropdown."""
    def _updatePointCount(curve):
        """Adds or removes points from the curve until the number
        of points matches the ones in the preset."""
        # Add points to get the needed amount.
        while len(curve.points) < len(REMAP_PRESETS[remapType]["points"]):
            curve.points.new(0.0, 0.0)

        # Remove points to get the needed amount.
        while len(curve.points) > len(REMAP_PRESETS[remapType]["points"]):
            curve.points.remove(curve.points[0])

    node = getNodeOfPropGroup(propGroup)
    curvesNode = cn.getCurvesNode(node)
    curveMapping = curvesNode.mapping
    remapType = getattr(propGroup, attrName)

    # BRDFToonMtl only supports combined curve, no separate R, G, B channels.
    curve  = curveMapping.curves[3] # "C" (combined) curve.

    _updatePointCount(curve)

    for idx, pt in enumerate(REMAP_PRESETS[remapType]['points']):
        curve.points[idx].location = pt
        curve.points[idx].handle_type = REMAP_PRESETS[remapType]["type"][idx]

    # Update the widget to match the points.
    curveMapping.update()


def drawCurveTemplate(context: bpy.types.Context, layout: bpy.types.UILayout, propGroup, widgetAttr):
    """A "custom_draw" function for the CurveMapping widget."""
    attrName = widgetAttr['name']
    attrLabel = getAttrLabel(getPluginModule('BRDFToonMtl'), widgetAttr, propGroup, node=None)
    layout.prop(propGroup, attrName, text=attrLabel)

    layout.separator()

    node = getNodeOfPropGroup(propGroup)
    curvesNode = cn.getCurvesNode(node)
    layout.template_curve_mapping(curvesNode, "mapping", type="COLOR")
