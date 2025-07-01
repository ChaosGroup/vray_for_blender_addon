import bpy
from mathutils import Vector, Matrix 

import os
import math
import numpy as np
import time
from io import StringIO
from xml.dom import NotFoundErr

from vray_blender import debug
from vray_blender.lib.lib_utils import LightTypeToPlugin, LightBlenderToVrayPlugin
from vray_blender.lib.blender_utils import VRAY_ASSET_TYPE
from vray_blender.nodes.tools import isCompatibleNode


FLOAT_SOCK_TYPES = ( "VRaySocketFloat", "VRaySocketFloatColor", "VRaySocketFloatNoValue")
COLOR_SOCK_TYPES = ( "VRaySocketColor", "VRaySocketColorNoValue", "VRaySocketAColor",
                    "VRaySocketColorTexture", "VRaySocketColorMult", "VRaySocketAColor",
                    "VRaySocketTexMulti")

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
            raise NotFoundErr(f"typePrefix: Type not found in list: {type(obj)}")

def getNodeByName(tree, nodeName: str):
    return next(n for n in tree.nodes if n.bl_idname == nodeName)


def evalMode(isPreview: bool):
    return 'PREVIEW' if isPreview else 'RENDER'


def saveShaderScript(script):
    scriptTxt = StringIO()
    for line in script.lines:
        scriptTxt.write(f"{line.body}\n")

    filepath = os.path.join(bpy.app.tempdir, script.name)
    with open(filepath, 'w') as osl_file:
        osl_file.write(scriptTxt.getvalue())

    return filepath

def isObjectVrayScene(obj: bpy.types.Object):
    return obj.vray.VRayAsset.assetType == VRAY_ASSET_TYPE["Scene"]

def isObjectVrayProxy(obj: bpy.types.Object):
    return obj.vray.VRayAsset.assetType == VRAY_ASSET_TYPE["Proxy"]

def isObjectNonMeshClipper(obj: bpy.types.Object):
    vrayClipper = obj.vray.VRayClipper
    return vrayClipper and vrayClipper.enabled and not vrayClipper.use_obj_mesh

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
    return next(iter(s for s in node.inputs if s.name == socketName), None)


def getInputSocketByAttr(node, attrName):
    """ Search for input socket by its 'vray_attr' field.

        Return:
        The found socket or None
    """
    assert node is not None
    try:
        return next((s for s in node.inputs if s.vray_attr == attrName))
    except StopIteration as ex:
        debug.printError(f"Failed to get input socket {attrName} of node {node.name}")
        debug.printExceptionInfo(ex)


def getOutputSocketByAttr(node, attrName):
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


def getFromNode(sock):
    if len(sock.links) > 0:
        return sock.links[0].from_node
    return None


def getGroupNode(node):
    """ Get the parent tree node of another group node.

        Return:
        The parent tree node when nested groups are used.
    """
    res = None
    users = bpy.context.blend_data.user_map(subset={node.id_data})
    for group in users[node.id_data]:
        nodes = []
        if isinstance(group, bpy.types.Material):
            nodes = group.node_tree.nodes
        elif isinstance(group, bpy.types.NodeGroup):
            nodes = group.nodes
        elif isinstance(group, bpy.types.ShaderNodeTree):
            nodes = group.nodes
        for parentTreeNode in nodes:
            if parentTreeNode.bl_idname == 'ShaderNodeGroup':
                for groupNode in parentTreeNode.node_tree.nodes:
                    if groupNode == node:
                        res = parentTreeNode
                        break

    return res


def resolveNodeSocket(socket: bpy.types.NodeSocket) -> bpy.types.NodeSocket | None:
    """ Resolve an input socket to either another output socket or group node inputs socket
    going through all re-routes and nested groups.

        Return:
        The resolved socket that can be directly used for export or None if it's connected
        to an unsupported node.
    """
    if len(socket.links) > 0:

        # If the linked node is 'Reroute', return its link to the input socket to skip the export.
        if fromNode := getFromNode(socket):
            if fromNode.bl_idname == "NodeReroute":
                return resolveNodeSocket(fromNode.inputs[0])
            elif fromNode.bl_idname == "ShaderNodeGroup":
                nodeGroup = fromNode.node_tree
                groupOutput = next((n for n in nodeGroup.nodes if n.bl_idname == 'NodeGroupOutput'), None)
                for idx, outSocket in enumerate(fromNode.outputs):
                    if outSocket == socket.links[0].from_socket:
                        break
                return resolveNodeSocket(groupOutput.inputs[idx])
            elif fromNode.bl_idname == "NodeGroupInput":
                groupNode = getGroupNode(fromNode)
                if not groupNode:
                    return None
                for idx, inSocket in enumerate(fromNode.outputs):
                    if inSocket == socket.links[0].from_socket:
                        break
                return resolveNodeSocket(groupNode.inputs[idx])
            elif not isCompatibleNode(fromNode):
                from vray_blender.lib.defs import NodeContext
                NodeContext.registerError(f"Skipped export of non V-Ray node: '{fromNode.name}'.")
                return None
            else:
                return socket
    return socket


def getNodeLink(sock: bpy.types.NodeSocket) ->bpy.types.NodeLink | None:
    socket = resolveNodeSocket(sock)
    if socket is not None and len(socket.links)>0:
        return socket.links[0]
    return None

def getLinkedFromSocket(sock):
    if link := getNodeLink(sock):
        return link.from_socket
    return None


def removeInputSocketLinks(sock: bpy.types.NodeSocket):
    """ Remove all links to an input socket """
    assert not sock.is_output

    if fromSock := getLinkedFromSocket(sock):
        if link := next((l for l in fromSock.links if l in sock.links), None):
            ntree: bpy.types.NodeTree = sock.node.id_data
            ntree.links.remove(link)


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
        """ Return the visibility of an object taking into account the current rendering mode """
        def visibleInViewport(obj: bpy.types.Object):
            return obj.visible_get() and ((not obj.is_instancer) or obj.show_instancer_for_viewport)

        def visibleInProd(obj: bpy.types.Object):
            return  not obj.hide_render \
                    and ((not obj.is_instancer) or obj.show_instancer_for_render) \
                    and obj.evaluated_get(exporterCtx.dg).is_evaluated

        if exporterCtx.interactive:
            return visibleInViewport(obj)
        else:
            return visibleInProd(obj)


def isModifierVisible(exporterCtx, mod: bpy.types.Modifier):
    """ Get the visibility of a modifier for the current renering mode """
    return mod.show_viewport if exporterCtx.interactive else mod.show_render


def vrayExporter(ctx):
    return ctx.scene.vray.Exporter


def vec3ToTuple(vec: Vector):
    return (vec[0], vec[1], vec[2])


def mat4x4ToTuple(tm: Matrix):
    """ Convert a matrix to a list of float values that can be passed to V-Ray 
        as a value of a MATRIX or TRAMSFORM property type.
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

        
def foreachGetAttr(coll: bpy.types.bpy_prop_collection, attrName: str, shape: tuple, dtype ):
    # 'foreach_get' does not work with multudimensional arrays. 
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