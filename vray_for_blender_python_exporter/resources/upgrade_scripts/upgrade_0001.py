from dataclasses import dataclass

import bpy
from vray_blender import debug
from vray_blender.nodes.nodes import vrayNodeCopy

# Nodes which do not need conversion or for which the conversion is not yet implemented.
SKIPPED_NODES = {
    "VRayNodeTexTriPlanar",

    # Selector
    "VRayNodeMultiSelect",
    "VRayNodeSelectObject",
    
    # Nodes with custom attributes
    "VRayNodeMatrix",
    "VRayNodeTransform",
    "VRayNodeVector",
    
    # Nodes with dynamic sockets
    "VRayNodeTexMulti",
    "VRayNodeEffectsHolder",
    "VRayNodeRenderChannels",
    "VRayNodeBRDFLayered",
    "VRayNodeTexLayeredMax"

    # Meta nodes
    "VRayNodeWorldOutput",
    "VRayNodeEnvironment",
    "VRayNodeObjectOutput",
    "VRayNodeObjectMatteProps",
    "VRayNodeObjectSurfaceProps",
    "VRayNodeObjectVisibilityProps"
}

@dataclass
class NodeLink:
    fromNodeName: str
    fromSockName: str
    fromSockAttr: str
    toNodeName: str
    toSockName: str
    toSockAttr: str


def _copyPropGroup(srcNode, targetNode, propType):
    # Copy the V-Ray property group
    sourceProps = getattr(srcNode, propType)
    targetProps = getattr(targetNode, propType)

    if not hasattr(targetProps, "__annotations__"):
        debug.printDebug(f"\tNo anotations for Node {srcNode}")
        return

    propGroupPropertyNames = sourceProps.__annotations__.keys()

    for propName in targetProps.__annotations__.keys():
        if propName in propGroupPropertyNames:
            setattr(targetProps, propName,getattr(sourceProps, propName))

    # Copy meta sockets (the ones not directly backed by vray propeprties)
    for srcSocket in [s for s in srcNode.inputs if hasattr(s, 'vray_attr')]:
        if srcSocket.vray_attr not in propGroupPropertyNames:
            if fnCopy := getattr(srcSocket, 'copy', None):
                if targetSocket := next((s for s in targetNode.inputs if (not s.is_linked) and (s.name.lower() == srcSocket.name.lower())), None):
                    fnCopy(targetSocket)


def _createNodeLinks(nodeTree, nodeLinks, failedLinks):
    for link in nodeLinks:
        try:
            fromNode = nodeTree.nodes[link.fromNodeName]
            toNode = nodeTree.nodes[link.toNodeName]
            if toNode and fromNode:
                
                # Search first by vray attribute name and then by lowercase socket name (meta sockets don't have an underlying  vray attribute)
                toSocket = next((s for s in toNode.inputs if s.vray_attr and (s.vray_attr == link.toSockAttr) or (s.name.lower() == link.toSockName.lower())), None)
                
                if len(fromNode.outputs) > 1:
                    fromSocket = next((s for s in fromNode.outputs if s.vray_attr and (s.vray_attr == link.toSockAttr) or (s.name.lower() == link.fromSockName.lower())), None)
                else:
                    # For nodes with just a default output, do not try to match the socket by name
                    fromSocket = fromNode.outputs[0]

                if toSocket is None:
                    failedLinks.append(f"'{toNode.name}' doesn't have input socket '{link.toSockName}' connected to '{fromNode.name}'")
                    continue
                if fromSocket is None:
                    failedLinks.append(f"'{fromNode.name}' doesn't have output socket '{link.fromSockName}' connected to '{toNode.name}'")
                    continue
                nodeTree.links.new(fromSocket, toSocket)
        except Exception as e:
            failedLinks.append(f"'{fromNode.name}' couldn't be connected to '{toNode.name}'\nException: {e}")



def _convertNodes(nodesForConversion, nodeTree, failedNodes):
    for nodeInfo in nodesForConversion:
        try:
            node = nodeInfo['node']
            
            if not hasattr(node, 'vray_plugin') or (node.vray_plugin == 'NONE'):
                continue

            debug.printDebug(f"Converting node {node.name}") 
            
            newNode = nodeTree.nodes.new(nodeInfo['type'])
            
            match nodeInfo['type']:
                case "VRayNodeMetaImageTexture":
                    for prop in {"BitmapBuffer", "TexBitmap"}:
                        _copyPropGroup(node, newNode, prop)
                    newNode.texture = node.texture
                case "VRayNodeUVWMapping":
                    newNode.mapping_node_type = node.mapping_node_type
                    for prop in {
                        'UVWGenMayaPlace2dTexture',
                        'UVWGenObject',
                        'UVWGenEnvironment',
                        'UVWGenProjection'}:
                        _copyPropGroup(node, newNode, prop)
                case _:
                    _copyPropGroup(node, newNode, node.vray_plugin)
                    pass
        
            # Call the custom copy procedure AFTER the properties have been copied so that it could 
            # override any values if necessary.
            vrayNodeCopy(newNode, node)

            nodeTree.nodes.remove(node)
            newNode.name = nodeInfo['name']
            newNode.label = nodeInfo['label']
            newNode.location.x = nodeInfo['location'][0]
            newNode.location.y = nodeInfo['location'][1]
        
        except Exception as e:
            failedNodes.append(f"Error copying node '{nodeInfo['name']}' of type '{nodeInfo['type']}'.\nException {e}")


def replaceNodeTree(nodeTree, parentName):
    nodesForConversion = [] # nodes for re-creation
    nodeLinks = []
    failedNodes = []
    skippedNodes = []

    # Store all nodes' types and connections
    for node in nodeTree.nodes:
        if not hasattr(node, "vray_type"):
            continue
        
        for sock in node.inputs:
            if not sock.is_linked:
                continue
            nodeLinks.extend([NodeLink( link.from_node.name, link.from_socket.name, link.from_socket.vray_attr, 
                                        link.to_node.name, link.to_socket.name, link.to_socket.vray_attr )
                                for link in sock.links])

        if node.bl_idname in SKIPPED_NODES:
            skippedNodes.append(f"Node {node.name} of type {node.bl_idname}")
            continue
        
        nodeInfo = {
            'type': node.bl_idname,
            'name':node.name,
            'location': (node.location[0], node.location[1]),
            'label': node.label,
            'node':node
        }

        nodesForConversion.append(nodeInfo)

    _convertNodes(nodesForConversion, nodeTree, failedNodes)

    failedLinks = []
    _createNodeLinks(nodeTree, nodeLinks, failedLinks)

    if failedNodes or failedLinks:
        debug.printError(f"Errors in '{parentName}'")
        if failedNodes:
            debug.printError("\tConversion failed for the following nodes:")
            for node in failedNodes:
                debug.printError(f"\t\t{node}")

        if failedLinks:
            debug.printError("\tConversion failed for the following node links:")
            for linkErr in failedLinks:
                debug.printError(f"\t\t{linkErr}")
        debug.printError("")

def run():
    debug.printDebug("Converting materials:")
    debug.printDebug("=====================")
    for material in bpy.data.materials:
        if material.use_nodes:       
            debug.printDebug(f"MTL: {material.name}")
            replaceNodeTree(material.node_tree, material.name)
    debug.printDebug("\n")


    debug.printDebug("Converting lights:")
    debug.printDebug("==================")
    for light in bpy.data.lights:
        if lightNtree := light.node_tree:
            replaceNodeTree(lightNtree, light.name)
    debug.printDebug("\n")

    debug.printDebug("Converting objects:")
    debug.printDebug("===================")
    for obj in bpy.data.objects:
        if obj.type not in {'MESH', 'META' , 'SURFACE' , 'FONT' , 'CURVE'}:
            continue
        if obj.vray.ntree:
            debug.printDebug(f"OBJ: {obj.name}")
            replaceNodeTree(obj.vray.ntree, obj.name)
    debug.printDebug("\n")

    debug.printDebug("Converting worlds:")
    debug.printDebug("==================")
    if bpy.data.worlds:
        for world in bpy.data.worlds:
            if world.use_nodes:
                debug.printDebug(f"WORLD: {world.name}")
                replaceNodeTree(world.node_tree, world.name)
    debug.printDebug("\n")


    debug.printDebug("Scene conversion complete!\n")


def check():
    # This version has not been made public. It is OK to always try to upgrade because
    # the upgrade script will only ever be run for development purposes.
    return True

