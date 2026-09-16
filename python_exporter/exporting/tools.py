# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
from mathutils import Vector, Matrix

import os
import math
import numpy as np
import time
from collections import deque
from io import StringIO

from vray_blender import debug
from vray_blender.lib.blender_utils import VRAY_ASSET_TYPE
from vray_blender.lib.path_utils import getV4BTempDir
from vray_blender.nodes.tools import isCompatibleNode, isVrayNode, isVraySocket


FLOAT_SOCK_TYPES = ( "VRaySocketFloat", "VRaySocketFloatColor", "VRaySocketFloatNoValue")
COLOR_SOCK_TYPES = ( "VRaySocketColor", "VRaySocketColorNoValue", "VRaySocketAColor",
                    "VRaySocketColorTexture", "VRaySocketColorMult",
                    "VRaySocketTexMulti", "VRaySocketColorUse")

# Copied verbatim from C++
IGNORED_PLUGINS = {
    # 'Virtual' plugins
    "SettingsCameraGlobal",

    # TODO: These plugins have to be implemented
    "SettingsPtexBaker",
    "SettingsVertexBaker",
    "SettingsImageFilter",
    # These plugins will be exported manually
    "Includer",
    "SettingsEnvironment",
    "OutputDeepWriter",
    "SettingsRenderChannels",
    # These plugins are exported from camera export
    "BakeView",
    "VRayStereoscopicSettings",
    # Unused plugins for now
    "SettingsLightTree",
    "SettingsColorMappingModo",
    "SettingsDR",
    "OutputTest",
    # Deprecated
    "SettingsPhotonMap",
    # "SettingsColorMapping",
    # "SettingsIrradianceMap",
    "RTEngine",
    "EffectLens",
}

MAPPING_TYPE_TO_UVW_PLUGIN = {
    "UV":           "UVWGenMayaPlace2dTexture",
    "PROJECTION":   "UVWGenProjection",
    "OBJECT":       "UVWGenObject",
    "ENVIRONMENT":  "UVWGenEnvironment",
    "MANUAL":       ""
}

MESH_OBJECT_TYPES = ('MESH', 'META', 'SURFACE', 'FONT', 'CURVE')
GEOMETRY_OBJECT_TYPES = MESH_OBJECT_TYPES + ('CURVES','POINTCLOUD', 'VOLUME')
EXPORTED_OBJECT_TYPES = GEOMETRY_OBJECT_TYPES + ('LIGHT',)

# Blender fills the unused levels of DepsgraphObjectInstance.persistent_id with INT32_MAX.
DUPLI_PID_UNUSED = 0x7FFFFFFF


def isRedundantGeometryInstance(inst: bpy.types.DepsgraphObjectInstance):
    """ Return True for depsgraph instances whose geometry is already delivered by another
        depsgraph entry. Exporting them would render the same mesh twice at the same
        transform, causing z-fighting artifacts (VBLD-2165).

        Blender presents the evaluated mesh of non-mesh objects (e.g. a curve converted
        through bevel or geometry nodes) as an instance of the object itself with a
        single-level persistent id (see make_duplis_geometry_set_impl in Blender). The object
        export pass already exports that mesh via Object.to_mesh(), so the self-instance is
        redundant. Instances of raw geometry-nodes geometry also have parent == object, but
        carry a multi-level persistent id and must be kept.

        Similarly, an instanced CURVE/SURFACE/FONT object is delivered twice: as an instance
        carrying the object's own data, and as a recursive instance carrying the evaluated
        mesh. The mesh instance is the one to keep. The two dupli generators that do not
        recurse into the instanced object (particles and 'Object as Font') deliver only the
        object-typed instance, which then must be exported.
    """
    obj = inst.object

    if obj.type == 'MESH':
        return (inst.parent.type in MESH_OBJECT_TYPES) \
            and (obj.original == inst.parent.original) \
            and (inst.persistent_id[1] == DUPLI_PID_UNUSED)

    if obj.type in ('CURVE', 'SURFACE', 'FONT'):
        isParticleInstance = inst.particle_system is not None
        isObjectFontInstance = (inst.parent.type == 'FONT') and (inst.parent.instance_type == 'VERTS')
        return not (isParticleInstance or isObjectFontInstance)

    return False


def getOutSocketSelector(sock: bpy.types.NodeSocket):
    """ Get output selector string for the socket - the string appended to the exported plugin name
        that identifies the output to use - plugin::selector.
    """
    from vray_blender.plugins import findPluginModule
    from vray_blender.lib.defs import AttrPlugin

    if pluginModule := findPluginModule(sock.node.vray_plugin):

        if not (outputs := pluginModule.Outputs):
            # Plugin has only default output
            return AttrPlugin.OUTPUT_DEFAULT
        elif (len(outputs) == 1):
            # Plugin has one output. If its name is the same as the name of the socket, return default
            return AttrPlugin.OUTPUT_DEFAULT if outputs[0]['attr'] == sock.vray_attr else sock.vray_attr
        else:
            # Plugin has more than one output. Return the name of the socket.
            return sock.vray_attr
    else:
        # Meta nodes do not correspond to a (single) V-Ray plugin. Normally, they should be exported by
        # custom code
        return AttrPlugin.OUTPUT_DEFAULT


def typePrefix(obj):
    match type(obj):
        # For the time being, all prefixes match those used by the existing C++ code
        # TODO: Revise
        case bpy.types.Object:        return 'OB'
        case bpy.types.Collection:    return 'CO'
        case bpy.types.Mesh:          return 'ME'
        case bpy.types.Curves:        return 'CV'
        case bpy.types.SurfaceCurve:  return 'CU'
        case bpy.types.TextCurve:     return 'CU'
        case bpy.types.Curve:         return 'CU'
        case bpy.types.PointCloud:    return 'PC'
        case bpy.types.MetaBall:      return 'MB'
        case _:
            raise Exception(f"typePrefix: Type not found in list: {type(obj)}")

def getNodeByName(tree, nodeName: str):
    return next((n for n in tree.nodes if n.bl_idname == nodeName), None)


def evalMode(isPreview: bool):
    return 'PREVIEW' if isPreview else 'RENDER'


def saveShaderScript(script):
    scriptTxt = StringIO()
    for line in script.lines:
        scriptTxt.write(f"{line.body}\n")

    filepath = os.path.join(getV4BTempDir(), script.name)
    with open(filepath, 'w') as osl_file:
        osl_file.write(scriptTxt.getvalue())

    return filepath

def isObjectVrayScene(obj: bpy.types.Object):
    return obj.vray.VRayAsset.assetType == VRAY_ASSET_TYPE["Scene"]

def isObjectVrayProxy(obj: bpy.types.Object):
    return obj.vray.VRayAsset.assetType == VRAY_ASSET_TYPE["Proxy"]

def isObjectVRayDecal(obj: bpy.types.Object):
    return obj.vray.isVRayDecal

def isObjectVRayGaussian(obj: bpy.types.Object):
    return obj.vray.isVRayGaussian

def isObjectVRayInfinitePlane(obj: bpy.types.Object):
    return obj.vray.isVRayInfinitePlane

def isObjectVRayPerfectSphere(obj: bpy.types.Object):
    return obj.vray.isVRayPerfectSphere

def isObjectChaosScatter(obj: bpy.types.Object):
    """ True for the carrier object of a Chaos Scatter setup - a PointCloud, or a vertices-only
        Mesh on Blender versions where a PointCloud cannot be sized from Python (pre-5.1). The
        'chaos_scatter' property group is registered by the separate Chaos Scatter addon; when
        that addon is disabled, no object can match.
    """
    cs = getattr(obj.original, 'chaos_scatter', None)
    return (obj.type in ('POINTCLOUD', 'MESH')) and (cs is not None) and cs.is_scatter

def isObjectNonMeshClipper(obj: bpy.types.Object):
    vrayClipper = obj.vray.VRayClipper
    return vrayClipper and vrayClipper.enabled and not vrayClipper.use_obj_mesh

def isProxyConvertibleGeometryType(obj: bpy.types.Object) -> bool:
    """True if export produces GeomStaticMesh, GeomMayaHair, or GeomParticleSystem (proxy .vrmesh)."""
    match obj.type:
        case 'MESH' | 'META' | 'SURFACE' | 'FONT' | 'CURVE':
            if isObjectVrayScene(obj) or isObjectVrayProxy(obj) or isObjectVRayDecal(obj):
                return False
            if isObjectNonMeshClipper(obj):
                return False
            return True
        case 'POINTCLOUD':
            return True
        case 'CURVES':
            return not obj.vray.isVRayFur
        case _:
            return False

def getVRayBaseSockType(sock):
    if hasattr(sock, "vray_socket_base_type"):
        return sock.vray_socket_base_type

    return sock.type

def getInputSocketByName(node:bpy.types.Node, socketName):
    """ When searching node.inputs using the [] operator, the implementation
        will match either socket name or socket identifier which may lead to
        confusion when socket name has been changed for any reason. This function
        searches strictly by name.

        Return:
        The found socket or None
    """
    assert node is not None
    return next(iter(s for s in node.inputs if (s.name == socketName)), None)

def getInputSocketByAttr(node, attrName):
    """ Search for input socket by its 'vray_attr' field.

        Return:
        The found socket or None
    """
    assert node is not None
    assert isVrayNode(node)
    try:
        return next((i for i in node.inputs if i.vray_attr == attrName), None)
    except AttributeError as ex:
        debug.printError(f"{node.bl_idname}::{attrName} is not a V-Ray socket")
        return None

def getOutputSocketByAttr(node, attrName) -> bpy.types.NodeSocket:
    """ Search for output socket by its 'vray_attr' field.

        Return:
        The found socket or None
    """
    assert node is not None
    try:
        return next((s for s in node.outputs if s.vray_attr == attrName))
    except StopIteration as ex:
        debug.printError(f"Failed to get input socket {attrName} of node {node.name}")
        debug.printExceptionInfo(ex)


def getGroupNode(node):
    """ Get parent group node.

        Return:
            The parent group node of 'node' or None if the node is not in a group.
    """

    # Node trees of evaluated objects are also 'evaluated'. The node objects in them are
    # different from the 'original' ones.
    nodeTree = node.id_data.original
    users = bpy.context.blend_data.user_map(subset={nodeTree})

    for group in users[nodeTree]:
        nodes = []
        if isinstance(group, bpy.types.Material):
            nodes = group.node_tree.nodes
        elif isinstance(group, bpy.types.NodeGroup):
            nodes = group.nodes
        elif isinstance(group, bpy.types.ShaderNodeTree):
            nodes = group.nodes

        for groupNode in [n for n in nodes if n.bl_idname in ('ShaderNodeGroup', 'VRayNodeGroup')]:
            groupNodeTree = groupNode.node_tree

            if groupNodeTree.session_uid == nodeTree.session_uid:
                # The name is guaranteed to be unique within the node tree.
                if any(n for n in groupNodeTree.nodes if n.name == node.name):
                    return groupNode

    return None


def _isNearSocketLinkActive(l: bpy.types.NodeLink):
    return l.is_valid and (not l.is_muted) and (not l.is_hidden) and \
            (not isVraySocket(l.to_socket) or l.to_socket.ui_enabled)


def _getActiveNearLinks(sock: bpy.types.NodeSocket):
    """ Returns a list of all socket links that should be followed during export. """

    # The is_linked check is an optimization as well as a mitigation for a bug in
    # Blender which may cause a crash. Looks like the call triggers populating an internal
    # structure which will otherwise point to invalid entries.
    if not sock.is_linked:
        return []
    return [l for l in sock.links if _isNearSocketLinkActive(l)]


def socketHasActiveNearLinks(socket: bpy.types.NodeSocket):
    return any(_getActiveNearLinks(socket))


# Stack of group instance paths set by the export loop (node_export.py) when it enters
# the export of a node that lives inside a group tree.  Each entry is a tuple of
# VRayNodeGroup nodes representing the path from the root tree to the current group.
# Used by _resolveNodeSocketImpl so that a fresh getFarNodeLink call made while
# exporting an inner node still knows which group instance it came from.
_groupExportStack: list = []


def _resolveNodeSocketImpl(
    toSocket: bpy.types.NodeSocket,
    groupStack: tuple = (),
    nearLink: bpy.types.NodeLink = None,
) -> tuple:
    """ Internal implementation.  Returns (resolved_socket_or_None, group_path_at_return).

        groupStack accumulates the VRayNodeGroup nodes entered during this recursive
        traversal.  It is separate from _groupExportStack, which reflects the group
        context established by the outer export loop.

        nearLink is toSocket's active incoming link when the caller has already resolved it.
        Purely an optimization: deriving it here yields the same link, at the cost of another
        NodeSocket.links scan.
    """
    if (link := nearLink) is None and toSocket.is_linked:
        candidate = toSocket.links[0]
        link = candidate if _isNearSocketLinkActive(candidate) else None

    if link is not None:

        fromNode   = link.from_node
        fromSocket = link.from_socket

        if fromNode.mute:
            _, resolvedSock, resolvedStack = resolveInternalLink(fromSocket, groupStack)
            return resolvedSock, resolvedStack

        elif fromNode.bl_idname == "NodeReroute":
            return _resolveNodeSocketImpl(fromNode.inputs[0], groupStack)

        elif fromNode.bl_idname in ("ShaderNodeGroup", "VRayNodeGroup"):
            nodeGroup = fromNode.node_tree
            if not nodeGroup:
                return None, groupStack
            groupOutputs = [n for n in nodeGroup.nodes if n.bl_idname == 'NodeGroupOutput']
            groupOutput = next((n for n in groupOutputs if n.is_active_output), groupOutputs[0] if groupOutputs else None)
            if not groupOutput:
                return None, groupStack
            for idx, outSocket in enumerate(fromNode.outputs):
                if outSocket == fromSocket:
                    return _resolveNodeSocketImpl(groupOutput.inputs[idx], groupStack + (fromNode,))
            return None, groupStack

        elif fromNode.bl_idname == "NodeGroupInput":
            # Determine the outer group node.  Prefer the in-traversal stack, then the
            # export-loop context, and fall back to the slower user_map search.
            if groupStack:
                outerGroupNode = groupStack[-1]
                newStack       = groupStack[:-1]
            elif _groupExportStack:
                ctxPath        = _groupExportStack[-1]
                outerGroupNode = ctxPath[-1] if ctxPath else None
                newStack       = ctxPath[:-1]
            else:
                outerGroupNode = getGroupNode(fromNode)
                newStack       = ()

            if not outerGroupNode:
                # GroupInput copied outside a group, or no context available.
                return None, newStack

            for idx, outSocket in enumerate(fromNode.outputs):
                if outSocket == fromSocket:
                    break
            return _resolveNodeSocketImpl(outerGroupNode.inputs[idx], newStack)

        elif not isCompatibleNode(fromNode):
            from vray_blender.lib.defs import NodeContext
            NodeContext.registerError(f"Skipped export of non V-Ray node: '{fromNode.name}'.")
            return None, groupStack

    return toSocket, groupStack


def resolveNodeSocket(toSocket: bpy.types.NodeSocket) -> bpy.types.NodeSocket | None:
    """ Resolve an input socket to either itself or an input socket on a node connected
        through any re-routes, muted nodes and groups.

        Return:
        The resolved socket that can be directly used for export or None if it's connected
        to an unsupported node.
    """
    assert not toSocket.is_multi_input
    socket, _ = _resolveNodeSocketImpl(toSocket)
    return socket


def resolveInternalLink(outSocket: bpy.types.NodeSocket, groupStack: tuple = ()):
    """ Return a resolved input socket following the best-matching internal link of a muted node.

        Return:
        (inSocket, resolvedSocket, groupStack).
    """
    from vray_blender.nodes.utils import getPluginTypeOfNode
    from vray_blender.plugins import getPluginModule

    node = outSocket.node

    if isVrayNode(node):
        if fnResolve := getattr(node, 'resolveInternalLink', None):
            inSock, resolvedSock = fnResolve(outSocket)
            return inSock, resolvedSock, groupStack

        pluginType = getPluginTypeOfNode(node)

        if pluginType is None:
            return None, None, groupStack

        pluginModule = getPluginModule(pluginType)

        if 'internal_links' not in pluginModule.Node:
            # A missing internal_links list means there are no valid internal links
            return None, None, groupStack

        # If there is no internal_links property on Node, treat all possible links as valid
        internalLinks = pluginModule.Node.get('internal_links')

        def getInputSocketNames(inputsList: list):
            if inputsList[0] == '*':
                # case '*' => ['*']
                return [s.name for s in node.inputs if not s.hide]
            else:
                # case '*' => [attr1, attr2]
                return inputsList

        inSocketNames = []

        if '*' in internalLinks:
            # All output sockets
            inSocketNames = getInputSocketNames(internalLinks['*'])
        elif inputsList := internalLinks.get(outSocket.name, []):
            inSocketNames = getInputSocketNames(inputsList)

        for inSockName in inSocketNames:
            if inSockName.endswith('*'):
                # This is the prefix to a socket name and is used for nodes with sockets
                # created at runtime which are usually numbered
                sockNamePrefix = inSockName[:-1]
                for inSock in node.inputs:
                    if inSock.name.startswith(sockNamePrefix):
                        return inSock, *_resolveNodeSocketImpl(inSock, groupStack)
            else:
                inSock = node.inputs.get(inSockName)
                assert inSock, f"Invalid socket '{inSockName}' in internal links list of plugin {pluginType}"
                return inSock, *_resolveNodeSocketImpl(inSock, groupStack)

    else: # Cycles node
        # Walk all internal links and return the first match. The links are
        # ordered by priority.
        for l in node.internal_links:
            # In an internal link, to and from sockets are reversed:
            # 'from_socket' is the input socket and 'to_socket' is the output socket
            if l.to_socket == outSocket:
                return l.to_socket, *_resolveNodeSocketImpl(l.from_socket, groupStack)

    return None, None, groupStack


class FarNodeLink:
    """ Node link that can span one or more Reroute nodes.
        The interface is a drop-in replacement for bpy.types.NodeLink.
    """
    def __init__(self, fromSock: bpy.types.NodeSocket, toSock: bpy.types.NodeSocket,
                 groupPath: tuple = ()):
        self.from_socket  = fromSock
        self.to_socket    = toSock
        self.from_node    = fromSock.node
        self.to_node      = toSock.node
        # Tuple of VRayNodeGroup nodes that were entered to reach from_node.
        # Non-empty when from_node lives inside one or more group trees.
        self.groupPath    = groupPath


def getFarNodeLink(toSock: bpy.types.NodeSocket) -> FarNodeLink | None:
    """ Get the link between toSock and an output socket possibly
        spanning one or more Reroute nodes, groups and muted nodes.

        If no output socket is found, return None.
    """

    if isVraySocket(toSock):
        # Some V-Ray sockets provide custom implementation in their getFarLink() method.
        return toSock.getFarLink()
    else:
        return getFarNodeLinkImpl(toSock)


def getFarNodeLinkImpl(toSock: bpy.types.NodeSocket) -> FarNodeLink | None:
    """ Get the link between toSock and an output socket possibly
        spanning one or more Reroute nodes, groups and muted nodes.
        Only works for single-input sockets.

        If no output socket is found, return None.
    """
    assert not toSock.is_multi_input

    if not toSock.is_linked:
        return None

    # toSock is single-input, so this is its one and only link. It is passed down and reused
    # below instead of being looked up again: NodeSocket.links is implemented in Python and
    # rescans the whole tree's link list per access, which makes it the dominant cost of this
    # function - and this function runs for every link on every node-tree update.
    nearLink = toSock.links[0]
    if not _isNearSocketLinkActive(nearLink):
        return None

    # Seed the group stack from the export-loop context so that nodes inside a
    # group are still associated with the correct group instance even when
    # getFarNodeLink is called fresh (not through an initial group traversal).
    initialStack = _groupExportStack[-1] if _groupExportStack else ()
    socket, groupPath = _resolveNodeSocketImpl(toSock, initialStack, nearLink)
    if socket is toSock:
        # Nothing to see through (no reroute, muted node or group in the way) - by far the
        # common case, and nearLink is already toSock's link.
        return FarNodeLink(nearLink.from_socket, toSock, groupPath=groupPath)
    if socket and socket.is_linked:
        return FarNodeLink(socket.links[0].from_socket, toSock, groupPath=groupPath)

    return None


def getNodeLinkToNode(nodeOutput, sockName, nodeType) -> FarNodeLink | None:
    # Returns node link connecting socket to node with specific type
    if sock := getInputSocketByName(nodeOutput, sockName):
        # The output node of the tree is not connected to anything
        nodeLink = getFarNodeLink(sock)
        if nodeLink and nodeLink.from_node.bl_idname == nodeType:
            return nodeLink
    return None


def getActiveOutputFarNodeLinks(fromSock: bpy.types.NodeSocket) -> list[FarNodeLink]:
    """ Get all far links between fromSock and connected input sockets, possibly
        spanning one or more Reroute nodes.
    """
    assert fromSock is not None
    assert fromSock.is_output

    def getActiveFarSockets(outSock: bpy.types.NodeSocket):
        result = []
        for link in _getActiveNearLinks(outSock):
            if link.to_node.bl_idname != 'NodeReroute':
                result.append(link.to_socket)
            else:
                result.extend(getActiveFarSockets(link.to_node.outputs[0]))
        return result

    farSocks = getActiveFarSockets(fromSock)
    return [FarNodeLink(fromSock, s) for s in farSocks]


def getActiveInputFarNodeLinks(toSock: bpy.types.NodeSocket) -> list[FarNodeLink]:
    """ Get all far links between toSock and connected output sockets, possibly
        spanning one or more Reroute nodes.
    """
    assert toSock is not None
    assert not toSock.is_output

    def getActiveFarSockets(inSock: bpy.types.NodeSocket):
        result = []
        for link in _getActiveNearLinks(inSock):
            if link.from_node.bl_idname != 'NodeReroute':
                result.append(link.from_socket)
            else:
                result.extend(getActiveFarSockets(link.from_node.inputs[0]))
        return result

    farSocks = getActiveFarSockets(toSock)
    return [FarNodeLink(s, toSock) for s in farSocks]


def getLinkedFromSocket(toSock: bpy.types.NodeSocket):
    if link := getFarNodeLink(toSock):
        return link.from_socket
    return None


def removeSocketLinks(sock: bpy.types.NodeSocket):
    """ Remove all links to/from an input/output socket """
    for l in sock.links:
        sock.node.id_data.links.remove(l)


def removeOutputSocketLinks(sock: bpy.types.NodeSocket):
    """ Remove all links to an output socket """
    assert sock.is_output

    ntree: bpy.types.NodeTree = sock.node.id_data
    linksToRemove = list(sock.links)

    for link in linksToRemove:
        ntree.links.remove(link)

def nodePluginType(node):
    return node.vray_plugin if hasattr(node, "vray_plugin") else None


def getSceneNameOfObject(obj: bpy.types.Object, scene: bpy.types.Scene):
    """ Return the path part of the scene_name for a scene object.
        The function is recursive, with scene == None for all recursive invocations

        @param obj   - the object for which to create scene name
        @param scene - the scene containing the object
    """
    # We can use the plain object names instead of their unique names here because Blender
    # guarantees that object names are unique. The scene name can also contain any chars,
    # so they are more human-readable than the unique names.
    name = obj.name
    if obj.parent:
        name = f"{getSceneNameOfObject(obj.parent, None)}/{obj.name}"

    if scene:
        # 'scene' seems to be the name exported for any scene in the other DCCs. In addition,
        # Vantage will look for 'scene', exactly as typed, when it builds the scene tree in
        # its outline view.
        # This should not be an issue even if there are multiple scenes in the same .blend file
        # because we always export and render only one of them.
        name = f"scene/{name}"

    return name


def _buildObjectCollectionMap(scene: bpy.types.Scene) -> dict:
    """ BFS the collection hierarchy once, returning {session_uid: collection_path} for the deepest
        direct collection each object belongs to. The path includes the full collection hierarchy,
        e.g. 'Buildings/Residential'. Uses coll.objects (direct membership only —
        Blender does not inherit collection membership through the hierarchy).
    """
    result: dict = {}  # session_uid -> (depth, path)
    queue = deque([(scene.collection, 0, "")])
    while queue:
        coll, depth, path = queue.popleft()
        if coll is not scene.collection:
            collPath = f"{path}/{coll.name}" if path else coll.name
            for ob in coll.objects:
                uid = ob.original.session_uid
                cur = result.get(uid)
                if cur is None or depth > cur[0]:
                    result[uid] = (depth, collPath)
            for child in coll.children:
                queue.append((child, depth + 1, collPath))
        else:
            for child in coll.children:
                queue.append((child, depth + 1, path))
    return {uid: p for uid, (_, p) in result.items()}


def buildObjectSceneName(name: str, scenePath: str, obj: bpy.types.Object, exporterCtx) -> list:
    """ Build the full scene_name list for a V-Ray Node or light plugin. """
    if exporterCtx.objCollectionMap is None:
        exporterCtx.objCollectionMap = _buildObjectCollectionMap(exporterCtx.dg.scene)
    collPath = exporterCtx.objCollectionMap.get(obj.original.session_uid)
    if collPath:
        # Insert the collection hierarchy between "scene/" and the object path,
        # so Vantage shows collections as groups in its outline.
        scenePath = f"scene/{collPath}/{scenePath[len('scene/'):]}"
    assetRoot = obj
    while assetRoot.parent:
        assetRoot = assetRoot.parent
    return [name, scenePath,
            f"layer/{collPath or 'Scene'}",
            f"asset/{assetRoot.name}"]

def isNodeConnected(node):
    return any(len(o.links) > 0 for o in node.outputs)


def isFloatSocket(sockType):
    return sockType in FLOAT_SOCK_TYPES or sockType in ('VALUE', 'ROTATION')


def isColorSocket(sockType):
    return sockType in COLOR_SOCK_TYPES or sockType in ('RGBA', 'VECTOR')

def isUVWSocket(sockType):
    return sockType == "VRaySocketCoords" or sockType == 'VECTOR'

def isObjectOrphaned(obj: bpy.types.ID):
    # In Python 3.+ True and False are guaranteed to be 1 and 0
    fakeUsers = int(obj.use_fake_user)
    return (obj.users - fakeUsers) == 0


def isObjectVisible(exporterCtx, obj: bpy.types.Object):
        """ Return the visibility of an object taking into account the current rendering mode.
            Note that the visibility of the objects used as instancers may differ from the
            visibility of the instancer itself. The first is determined by the
            show_instancer_for_viewport/render property, the second is returned by the
            visible_get() function for the viewport and hide_render property for prod renders.
        """
        def visibleInViewport(obj: bpy.types.Object):
            return obj.visible_get() and ((not obj.is_instancer) or obj.show_instancer_for_viewport)

        def visibleInProd(obj: bpy.types.Object):
            evalObj = obj.evaluated_get(exporterCtx.dg)
            return not evalObj.hide_render and ((not obj.is_instancer) or obj.show_instancer_for_render)

        if exporterCtx.interactive or exporterCtx.isProxyExport:
            return visibleInViewport(obj)
        else:
            return visibleInProd(obj)


def isModifierVisible(exporterCtx, mod: bpy.types.Modifier):
    """ Get the visibility of a modifier for the current rendering mode """
    return mod.show_viewport if exporterCtx.interactive else mod.show_render


def vrayExporter(ctx):
    return ctx.scene.vray.Exporter


def vec3ToTuple(vec: Vector):
    return (vec[0], vec[1], vec[2])


def mat4x4ToTuple(tm: Matrix):
    """ Convert a matrix to a list of float values that can be passed to V-Ray
        as a value of a MATRIX or TRANSFORM property type.
    """

    # Transpose the matrix to column-first format. The last matrix row
    # (the last column in the block below) is not used by VRay
    return (    tm[0][0], tm[1][0], tm[2][0], tm[3][0],
                tm[0][1], tm[1][1], tm[2][1], tm[3][1],
                tm[0][2], tm[1][2], tm[2][2], tm[3][2],
                tm[0][3], tm[1][3], tm[2][3], tm[3][3])


def mat3x3ToTuple(mat: Matrix):
    """ Convert a matrix to a list of float values that can be passed to V-Ray
        as a value of a MATRIX or TRANSFORM property type.
    """
    return (    mat[0][0], mat[1][0], mat[2][0],
                mat[0][1], mat[1][1], mat[2][1],
                mat[0][2], mat[1][2], mat[2][2] )


def tupleTo4x4MatrixLayout(value):
    """ Transforms V-Ray's 'TRANSFORM', 'MATRIX' or 'MATRIX_TEXTURE' attribute into a
        list with 4x4 Blender Matrix layout. V-Ray uses 3x3 for matrices and 3x4
        (matrix + translation vector) for transforms.
    """
    match len(value):
        case 9:
            # V-Ray matrix
            return [
                value[0], value[3], value[6], 0.0,
                value[1], value[4], value[7], 0.0,
                value[2], value[5], value[8], 0.0,
                0.0, 0.0, 0.0, 1.0
            ]

        case 12:
            # V-Ray transform
            return [
                value[0], value[3], value[6], 0.0,
                value[1], value[4], value[7], 0.0,
                value[2], value[5], value[8], 0.0,
                value[9], value[10], value[11], 1.0,
            ]

    raise Exception(f"Invalid V-Ray matrix/transform length: {len(value)}, should be 9 or 12")


def matrixLayoutToMatrix(value: list[float] | tuple[float]):
    """ Convert a matrix or transform stored in a FloatVectorProp to a Matrix """
    assert len(value) == 16
    return Matrix(( (value[0], value[4], value[8], value[12]),
                    (value[1], value[5], value[9], value[13]),
                    (value[2], value[6], value[10], value[14]),
                    (value[3], value[7], value[11], value[15])))


def flattenMatrix(mat: Matrix):
    return [val for row in mat for val in row]

def foreachGetAttr(coll: bpy.types.bpy_prop_collection, attrName: str, shape: tuple, dtype ):
    # 'foreach_get' does not work with multidimensional arrays.
    # Get data as a flat array and then reshape
    buffer = np.empty(shape=math.prod(shape), dtype=dtype)
    coll.foreach_get(attrName, buffer)
    return np.reshape(buffer, shape)



########### Debugging tools ########################
from threading import Lock

class OpTime:
    def __init__(self):
        self.tm = 0.0
        self.count = 0

    def add(self, tm):
        self.tm += tm
        self.count += 1

class TimeStats:
    def __init__(self, name):
        self._name = name
        self._opTimes = {}   # opType -> opTime
        self.lock = Lock()
        self.path = []  # Op types stack of nested invocations

    def timeThis(self, opType, fn):
        self.path.append(opType)

        startTime = time.perf_counter()
        result = fn()
        endTime = time.perf_counter()

        with self.lock:
            opTime = self._opTimes.setdefault(':'.join(self.path), OpTime())
            opTime.add(endTime - startTime)

        self.path.pop()

        return result


    def printSummary(self):
        print(f'\n{self._name}')
        print('-' * 55)

        print(f"{'Operation':<35} {'Count':>10} {'Time':>13}")
        for opType, opTime in sorted(self._opTimes.items()):
            # Offset each nested level
            stack = opType.split(':')
            op = f"{'  ' * len(stack)}{stack[-1]}"

            print(f'{op:<35} {opTime.count:>10} {opTime.tm * 1000:>10.3f} ms')

        print('-' * 55)


class FakeTimeStats:
    """ A no-op implementation """
    def timeThis(self, opType, fn):
        return fn()

    def printSummary(self):
        pass