from vray_blender.lib.defs import *
from vray_blender.exporting.tools import getInputSocketByName, getVRayBaseSockType
from vray_blender.lib.names import Names
from vray_blender.exporting import node_export as commonNodesExport

def exportVRayNodeDisplacement(nodeCtx: NodeContext, subdivPropGroup):
    node = nodeCtx.node
    pluginName = Names.treeNode(nodeCtx)
    pluginType = "GeomDisplacedMesh" if not subdivPropGroup else "GeomStaticSmoothedMesh"
    pluginDesc = PluginDesc(pluginName, pluginType)

    commonNodesExport.exportNodeTree(nodeCtx, pluginDesc)

    texSock = getInputSocketByName(node, "Texture")
    assert texSock
    
    texVal = commonNodesExport.exportLinkedSocket(nodeCtx, texSock) if texSock.is_linked else texSock.value

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
    

    # The Texture socket could be VRaySocketColor or VRaySocketFloat based on the value of GeomDisplacedMesh.type
    useTexColor = getVRayBaseSockType(texSock) == 'VRaySocketColor'
    pluginDesc.setAttribute("displacement_tex_color", texVal if useTexColor else AttrPlugin())
    pluginDesc.setAttribute("displacement_tex_float", AttrPlugin() if useTexColor else texVal)

    if subdivPropGroup:
        for subdivAttr in ("edge_length", "view_dep", "max_subdivs", "use_bounds", "displacement_amount"):
            pluginDesc.setAttribute(subdivAttr, getattr(subdivPropGroup, subdivAttr))

    return commonNodesExport.exportPluginWithStats(nodeCtx, pluginDesc)