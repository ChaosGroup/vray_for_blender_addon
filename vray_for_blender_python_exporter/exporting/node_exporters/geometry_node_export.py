# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

from vray_blender.exporting import node_export as commonNodesExport
from vray_blender.exporting.tools import getInputSocketByName, getVRayBaseSockType
from vray_blender.lib.defs import *
from vray_blender.lib.names import Names
from mathutils import Color

def exportVRayNodeDisplacement(nodeCtx: NodeContext, subdivPropGroup):
    node = nodeCtx.node
    pluginName = Names.treeNode(nodeCtx)
    # Use subdiv mesh only if we aren't using 2D displacement similar to other integrations.
    useSubdiv = subdivPropGroup and node.GeomDisplacedMesh.type != '1'
    pluginType = "GeomDisplacedMesh" if not useSubdiv else "GeomStaticSmoothedMesh"
    pluginDesc = PluginDesc(pluginName, pluginType)

    commonNodesExport.exportNodeTree(nodeCtx, pluginDesc)

    texSock = getInputSocketByName(node, "Displacement Texture")
    assert texSock

    # Do not export any displacement when a texture isn't attached.
    if not texSock.hasActiveFarLink():
        # Clearing the textures and water level should be enough to remove any displacement. Reset
        # amount to 1.0 so that it will also work with MtlDisplacement(when implemented).
        pluginDesc.setAttribute("displacement_amount", 1.0)
        pluginDesc.setAttribute("displacement_shift", 0.0)
        pluginDesc.setAttribute("displacement_tex_color", AttrPlugin())
        pluginDesc.setAttribute("displacement_tex_float", AttrPlugin())
        pluginDesc.setAttribute("water_level", -1e30)
    else:
        texVal = commonNodesExport.exportLinkedSocket(nodeCtx, texSock)

        match node.GeomDisplacedMesh.type:
            case '1': # 2D
                pluginDesc.setAttribute("displace_2d", True)
            case '0': # Normal
                pluginDesc.resetAttribute("vector_displacement")
            case '2': # Vector
                pluginDesc.setAttribute("vector_displacement", 1)
            case '3': # Vector (Absolute)
                pluginDesc.setAttribute("vector_displacement", 2)
            case '4': # Vector (Object)
                pluginDesc.setAttribute("vector_displacement", 3)

        if node.GeomDisplacedMesh.type != '1':
            pluginDesc.resetAttribute("displace_2d")

        if node.GeomDisplacedMesh.use_bounds:
            minBound, maxBound = node.GeomDisplacedMesh.min_bound_float, node.GeomDisplacedMesh.max_bound_float
            pluginDesc.setAttribute("min_bound", Color((minBound, minBound, minBound)))
            pluginDesc.setAttribute("max_bound", Color((maxBound, maxBound, maxBound)))

        if not node.GeomDisplacedMesh.use_water_level:
            pluginDesc.setAttribute("water_level", -1e30)

        # The Texture socket could be VRaySocketColorNoValue or VRaySocketFloatNoValue based on the value of GeomDisplacedMesh.type
        useTexColor = getVRayBaseSockType(texSock) == 'VRaySocketColorNoValue'
        pluginDesc.setAttribute("displacement_tex_color", texVal if useTexColor else AttrPlugin())
        pluginDesc.setAttribute("displacement_tex_float", AttrPlugin() if useTexColor else texVal)

        if subdivPropGroup:
            for subdivAttr in ("edge_length", "view_dep", "max_subdivs", "use_globals", "static_subdiv", "preserve_map_borders", "preserve_geometry_borders", "classic_catmark"):
                pluginDesc.setAttribute(subdivAttr, getattr(subdivPropGroup, subdivAttr))

    return commonNodesExport.exportPluginWithStats(nodeCtx, pluginDesc)