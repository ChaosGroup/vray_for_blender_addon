import bpy
import mathutils

from vray_blender.lib import export_utils, lib_utils
from vray_blender.lib.names import Names
from vray_blender.plugins import PLUGIN_MODULES, getPluginModule
from vray_blender.exporting.plugin_tracker import TrackNode, getNodeTrackId, getObjTrackId 
from vray_blender.exporting.node_exporters.material_node_export import exportVRayNodeBRDFLayered, exportVRayNodeBRDFBump, exportVRayNodeMtlMulti, exportVRayNodeShaderScript
from vray_blender.exporting.node_exporters.texture_node_export import exportVRayNodeMetaImageTexture, exportVRayNodeTexLayered
from vray_blender.exporting.node_exporters.uvw_node_export import exportVRayNodeUVWGenRandomizer, exportVRayNodeUVWMapping
from vray_blender.nodes import utils as NodesUtils
from vray_blender.nodes.tools import getLinkInfo, isVraySocket

from vray_blender.exporting.tools import *
from vray_blender.lib.defs import *
from vray_blender import debug


from vray_blender.bin import VRayBlenderLib as vray

def exportPluginWithStats(nodeCtx: NodeContext, plDesc: PluginDesc) -> AttrPlugin:
    
    plDesc.node = nodeCtx.node
    nodeCtx.nodeTracker.trackPlugin(plDesc.name)
    
    nodeCtx.stats.uniquePlugins.add(plDesc.name)
    nodeCtx.stats.plugins += 1
    nodeCtx.stats.attrs += len(plDesc.attrs)

    # Call custom node handler, if registered with this node context
    if nodeCtx.customHandler:
        attrPlugin = nodeCtx.customHandler(nodeCtx, plDesc)
        assert attrPlugin, "Custom node exporter should return the exported AttrPlugin() object"
        return attrPlugin

    return export_utils.exportPlugin(nodeCtx.exporterCtx, plDesc)



def exportLinkedSocket(nodeCtx: NodeContext, inSocket: bpy.types.NodeSocket):
    """ Export the tree branch starting at an output socket, applying any encessary conversions
        on the node link defined by the from socket

    Args:
        nodeCtx (NodeContext): the active node export context
        inSocket (bpy.types.NodeSocket): the input socket to which the branch is connected

    Returns:
        _type_: AttrPlugin if the socket is linked, None otherwise
    """
    assert not inSocket.is_output

    if nodeLink := getNodeLink(inSocket):
        sockValue = exportVRayNode(nodeCtx, nodeLink)
        
        # Not all nodes are exported as plugins. Some may just return a value.
        if type(sockValue) is AttrPlugin:
            # Export any conversions needed between the source and the
            # target socket types
            sockValue = _exportConverters(nodeCtx, inSocket, sockValue)
        
        return sockValue
    
    return None


def exportSocket(nodeCtx: NodeContext, inSocket: bpy.types.NodeSocket):
    if inSocket.is_linked:
        return exportLinkedSocket(nodeCtx, inSocket)

    return inSocket.value


def exportSocketByName(nodeCtx: NodeContext, inSocketName: str):
    socket = getInputSocketByName(nodeCtx.node, inSocketName)
    return exportSocket(nodeCtx, socket)


def exportVRayNode(nodeCtx: NodeContext, nodeLink: bpy.types.NodeLink):
    """ Export a VRay node, calling a specialized export function if one exists for the node type. """
    node = nodeLink.from_node

    if not isVrayNode(node):
        NodeContext.registerError(f"Skipped export of non V-Ray node: '{node.name}'")
        return AttrPlugin()
    
    # Change the current context
    with nodeCtx.push(node):

        nodeClass = node.bl_idname
        
        with TrackNode(nodeCtx.nodeTracker, getNodeTrackId(node)):
            nodeCtx.exportedNodes.add(getNodeTrackId(node))

            result = None

            match nodeClass:
                case "VRayNodeMetaImageTexture":
                    result = exportVRayNodeMetaImageTexture(nodeCtx, nodeLink)
                case "VRayNodeTexLayeredMax":
                    result = exportVRayNodeTexLayered(nodeCtx)
                case "VRayNodeBRDFLayered":
                    result = exportVRayNodeBRDFLayered(nodeCtx)
                case "VRayNodeBRDFBump":
                    result = exportVRayNodeBRDFBump(nodeCtx)
                case "VRayNodeMtlOSL":
                    result = exportVRayNodeShaderScript(nodeCtx)
                case "VRayNodeMtlMulti":
                    result = exportVRayNodeMtlMulti(nodeCtx)
                case "VRayPluginListHolder":
                    result = _exportVRayPluginListHolder(nodeCtx)
                case "VRayNodeSelectObject":
                    result = _exportVRayNodeSelectObject(nodeCtx, nodeLink)
                case "VRayNodeMultiSelect":
                    result = _exportVRayNodeMultiSelect(nodeCtx, nodeLink)
                case "VRayNodeSelectObjectGeometry":
                    result = _exportVRayNodeSelectObjectGeometry(nodeCtx)
                case "VRayNodeUVWGenRandomizer":
                    result = exportVRayNodeUVWGenRandomizer(nodeCtx)
                case "VRayNodeUVWMapping":
                    result = exportVRayNodeUVWMapping(nodeCtx)
                case "VRayNodeTransform":
                    result = node.getValue(nodeCtx.exporterCtx.ctx)
                case "VRayNodeMatrix" | "VRayNodeVector":
                    result = node.getValue()
                case _:
                    if node.vray_plugin and (node.vray_plugin != 'NONE') and \
                            (exportTreeNode := getattr(getPluginModule(node.vray_plugin), "exportTreeNode", None)):
                        result = exportTreeNode(nodeCtx)
                    else:
                        result = _exportArbitraryNode(nodeCtx, nodeLink)
            
            # Meta nodes, e.g. object selectors, may export values other than AttrPlugin. It is responsibility
            # of their custom export code to set the correct output selector. For the rest, set the correct 
            # output selector here
            if (type(result) is AttrPlugin) and (not result.isEmpty()) and (not result.isOutputSet()):
                result.output = getOutSocketSelector(nodeLink.from_socket)
    
            return result


# Some nodes require special handling and we have dedicated export methods for them.
# For the rest, use this method   
def _exportArbitraryNode(nodeCtx: NodeContext, nodeLink: bpy.types.NodeLink):
    node = nodeLink.from_node
    if node and not hasattr(node, "vray_plugin"):
        if nodeCtx.material:
            debug.printError(f"Invalid '{node.name}' node in '{nodeCtx.material.name}' material node tree")
        else:
            debug.printError(f"Invalid '{node.name}' node in one of the non-material node tree")
        return AttrPlugin()

    if node.vray_plugin == 'NONE':
        debug.printError(f"Node '{node.name}' ({node.bl_idname}) is not associated with a VRay plugin. It should be exported by a specialized function.")
        return AttrPlugin()
   
    pluginType = node.vray_plugin
    pluginName = Names.treeNode(nodeCtx)
    plDesc = PluginDesc(pluginName, pluginType)
    
    if hasattr(node, pluginType):
        plDesc.vrayPropGroup = getattr(node, pluginType)

    exportNodeTree(nodeCtx, plDesc)
    
    attrPlugin = exportPluginWithStats(nodeCtx, plDesc)
    nodeCtx.exportedPlugins[pluginName] = pluginName
   
    return attrPlugin
    
 
def exportNodeTree(nodeCtx: NodeContext, plDesc: PluginDesc, skippedSockets=()):
    """ Export values set to the input sockets of the node. If the socket is connected,
        export the linked node. Otherwise, export the value explicitly set to the node.
        When the node is a meta node representing several VRay plugins, this function 
        will be called for each plugin in turn, and will only export the sockets of this
        plugin. 

        @param nodeCtx - a NodeContext with the .node member set to the node to export
        @param plDesc - plugin description of the plugin backing the node

        @return None
    """
    node = nodeCtx.node

    if not isVrayNode(node):
        NodeContext.registerError(f"Non V-Ray node can not be exported: '{node.name}'")
        return AttrPlugin()
    
    pluginAttrs = PLUGIN_MODULES[plDesc.type].Parameters
    
    for sock in node.inputs:
        if not isVraySocket(sock):
            debug.printError(f"Socket '{sock.name}' of node {node.name}[ {nodeCtx.rootObj.name} ] is not a V-Ray socket. "
                                "Try to recreate the node to obtain the full functionality.")
            continue

        if sock.vray_attr in skippedSockets:
            continue
        
        attrName = sock.vray_attr
        pluginParam = next(iter(i for i in pluginAttrs if i['attr'] == attrName), None)
        
        if not pluginParam:
            # Nodetrees for meta nodes are exported one VRay plugin at a time. If the plugin param
            # has not been found, this means that the socket is created for another plugin and would 
            # be exported in another call to this function.
            continue

        if sock.shouldExportLink():
            if sockValue := exportLinkedSocket(nodeCtx, sock):
                sock.exportLinked(plDesc, pluginParam, sockValue)
        else:
            sock.exportUnlinked(nodeCtx, plDesc, pluginParam)
        
    
def _exportObjMaterials(exporterCtx: ExporterContext, obj: bpy.types.Object):
    from vray_blender.exporting.mtl_export import MtlExporter
    mtlExporter = MtlExporter(exporterCtx)
        
    exportedMtls: list[AttrPlugin] = []

    objMtls = getObjectMaterialIdNames(obj)
    if not objMtls:
        # No material is assigned to the object, export a 'Default' material
        defaultMtl = mtlExporter._exportDefaultMaterial()
        defaultMtl.auxData['mtl'] = {'name': 'Default'} 
        exportedMtls.append(defaultMtl)
    
    for mtl in objMtls:
        mtlSingleBRDF, _ = mtlExporter.exportMtl(mtl)
        mtlWrapped = _exportMtlOptions(exporterCtx, obj, mtl, mtlSingleBRDF)
        mtlWrapped.auxData['mtl'] = {'name': mtl.name}

        # Force the update in order to re-establish the link to the wrapping plugin.
        # If the mtlOptions have changed, it was broken because the whole mtl options chain
        # is recreated on each update. 
        mtlWrapped.forceUpdate = True 

        exportedMtls.append(mtlWrapped)

    if len(exportedMtls) > 1:
        # For objects with mutliple materials in different slots, export MtlMulti plugin which 
        # holds references to all object's materials
        mtlPlugin = _exportMtlMulti(exporterCtx, obj, exportedMtls)
    else:
        mtlPlugin = exportedMtls[0]
    
    return mtlPlugin


def _exportMtlOptions(exporterCtx: ExporterContext, obj: bpy.types.Object, mtl: bpy.types.Material, singleBRDFMaterial: AttrPlugin):
    """ Export the chain of plugins constituting the material options. 
        @return The last plugin in the chain, or the orininal singleBDRFMAterial if no options were specified
    """

    mtlRenderStats = _exportMtlOption(exporterCtx, obj, mtl, singleBRDFMaterial, 'MtlRenderStats', 'base_mtl')
    mtlRoundEdges  = _exportMtlOption(exporterCtx, obj, mtl, mtlRenderStats, 'MtlRoundEdges', 'base_mtl')

    return mtlRoundEdges


def _exportMtlOption(exporterCtx: ExporterContext, 
                     obj: bpy.types.Object, 
                     mtl: bpy.types.Material, 
                     mtlPlugin: AttrPlugin, 
                     pluginType: str, 
                     baseMtlName: str):
    """ Export a material option plugin. Material options can be set at two levels: the object and the material
        itself. The options set on the material take precedence.

        @param obj - The object for which to export the material option.
        @param mtl - A material attached to the object.
        @param mtlPlugin - The next material plugin in chain (the one that should be 'wrapped').
        
        @return - The exported material option plugin, or the original plugin, if no export is needed.
    """
    if getattr(mtl.vray, pluginType).use:
        # The options set on the material itself take precedence. They are exported as part
        # of the material, so just return the original plugin here.
        return mtlPlugin
    
    propGroup = getattr(obj.vray, pluginType)
    pluginName = Names.pluginObject(pluginType, Names.object(mtl), Names.object(obj))
    
    if propGroup.use:
        plDesc = PluginDesc(pluginName, pluginType)
        
        # Material options set on the material override the ones set on the object
        plDesc.vrayPropGroup = propGroup
        plDesc.setAttribute(baseMtlName, mtlPlugin)

        attrPlugin = export_utils.exportPlugin(exporterCtx, plDesc)
        exporterCtx.objTrackers['OBJ_MTL'].trackPlugin(getObjTrackId(obj), attrPlugin.name)
        return attrPlugin
    else:
        # No need to export this option. Remove plugin in VRay and return the original one
        return mtlPlugin


def _exportMtlMulti(exporterCtx: ExporterContext, obj: bpy.types.Object, mtlPlugins: list[AttrPlugin]):
    matPluginName = Names.pluginObject("multi_mtl", Names.object(obj))
    
    mtlMultiDesc = PluginDesc(matPluginName, "MtlMulti")
    mtlMultiDesc.setAttribute("mtls_list", [AttrPlugin(mtl.name) for mtl in mtlPlugins])
    mtlMultiDesc.setAttribute("ids_list", [num for num in range(len(mtlPlugins))])
    mtlMultiDesc.setAttribute("wrap_id", True)

    # Those attributes should be in default (not set) state
    mtlMultiDesc.resetAttribute("mtlid_gen")
    mtlMultiDesc.resetAttribute("mtlid_gen_float")

    mtlMultiDesc.attrs['scene_name'] = [matPluginName]

    return export_utils.exportPlugin(exporterCtx, mtlMultiDesc)
    

def exportNodePlugin(exporterCtx: ExporterContext, 
                     obj: bpy.types.Object, 
                     geomPlugin: AttrPlugin,
                     objectName: str,
                     isInstancer = False,
                     visible = True): 
        """ Exports a V-Ray Node plugin for the specified geometry """
        tm = mathutils.Matrix() if isInstancer else obj.matrix_world
        
        pluginName = Names.vrayNode(objectName)
        nodeDesc = PluginDesc(pluginName, "Node")
        nodeDesc.setAttribute("geometry", geomPlugin)
        nodeDesc.setAttribute("objectID", obj.pass_index)
        nodeDesc.setAttribute("transform", tm)
        nodeDesc.setAttribute("visible", visible)

      
        if exporterCtx.commonSettings.useMotionBlur:
            objProperties = obj.vray.VRayObjectProperties
            if objProperties.override_motion_blur_samples:
                nodeDesc.setAttribute("nsamples", objProperties.motion_blur_samples)
            else:
                nodeDesc.setAttribute("nsamples", exporterCtx.commonSettings.mbSamples)
    
        # Export empty material plugin(s) for the node. They will be filled in later by the 
        # material export procedure
        matPlugin = _exportObjMaterials(exporterCtx, obj.original)
        nodeDesc.setAttribute("material", matPlugin)

        scene = exporterCtx.dg.scene
        
        if isInstancer:
            # In the instancer case, geomPlugin is the instancer plugin
            sceneName = geomPlugin.name
            scenePath = f"scene/{geomPlugin.name}"
        else:
            sceneName = obj.name
            scenePath = getSceneNameOfObject(obj, scene)
        
        nodeDesc.setAttribute("scene_name", [sceneName, scenePath])
        if userAttributes := obj.vray.UserAttributes.getAsString():
            nodeDesc.setAttribute('user_attributes', userAttributes)

        nodePlugin = export_utils.exportPlugin(exporterCtx, nodeDesc)
        nodePlugin.auxData['material'] = matPlugin
        return nodePlugin


def exportNodeVisibility(exporterCtx: ExporterContext, obj: bpy.types.Object, visible: bool):
    """ Export visibility for an already exported Node plugin """
    pluginName = Names.vrayNode(Names.object(obj))
    nodeDesc = PluginDesc(pluginName, "Node")
    nodeDesc.setAttribute("visible", visible)
    export_utils.exportPlugin(exporterCtx, nodeDesc)


def getObjectMaterialIdNames(obj: bpy.types.Object):
    matAttrPlugins = []

    # An object can have several materials assigned to different parts of it. 
    # The materials are stored in the material_slots field
    for slot in obj.material_slots:
        if slot.material and (ntree := slot.material.node_tree):
            if NodesUtils.getOutputNode(ntree, "MATERIAL") is None:
                continue
            
            if slot.material.vray.is_vray_class:
                matAttrPlugins.append(slot.material)
            else:
                debug.report(severity="WARNING",
                             msg=f"Material {slot.material.name} of object {obj.name} has a V-Ray Output node but no V-Ray node tree."\
                                " Check if 'Use V-Ray Material Nodes' has been pressed" )

    return matAttrPlugins


def getTextureUVWGen(nodeCtx: NodeContext, sockNormal):
    """ TODO: not implemented currently """
    return AttrPlugin()


################ IN-PLACE values export ############################
def _exportVRayNodeMultiSelect(nodeCtx: NodeContext, nodeLink: bpy.types.NodeLink) -> list[AttrPlugin]:
    node = nodeCtx.node
    selectedObjects = node.getSelected(nodeCtx.exporterCtx.ctx)
    
    result = []

    for obj in selectedObjects:
        socket = nodeLink.to_socket
        linkInfo = getLinkInfo(socket.node.vray_plugin, socket.vray_attr)
        
        if linkInfo.fnFilter(obj):
            isGeometry = (linkInfo.linkType == LinkInfo.OBJECT_DATA)
            result.append(_forwardExportSceneObject(nodeCtx, obj, isGeometry))

    return result


def _exportVRayNodeSelectObject(nodeCtx: NodeContext, nodeLink: bpy.types.NodeLink) -> AttrPlugin:
    node = nodeCtx.node

    if obj := node.getSelected(nodeCtx.exporterCtx.ctx):
        socket = nodeLink.to_socket
        linkInfo = getLinkInfo(socket.node.vray_plugin, socket.vray_attr)
        
        if linkInfo.fnFilter(obj):
            isGeometry = (linkInfo.linkType == LinkInfo.OBJECT_DATA)
            return _forwardExportSceneObject(nodeCtx, obj, isGeometry)
    
    return AttrPlugin()


def _forwardExportSceneObject(nodeCtx: NodeContext, obj: bpy.types.Object, isGeometry: bool) -> AttrPlugin:
    """ Export empty plugin for an object referenced by another plugin. It is needed
        so that V-Ray could create a valid link to it from the refering plugin when 
        the linked property is set.

    Args:
        obj (bpy.types.Object): the object for which to export an empty plugin
        isGeometry (bool): if True, the plugin will be exported for the object data, otherwise - for the object itself

    Returns:
        AttrPlugin: reference to the exported plugin
    """
    pluginName = ""
    pluginType = ""

    match obj.type:
        case 'MESH'| 'META' | 'SURFACE' | 'FONT' | 'CURVE':
            if isGeometry:
                pluginName = Names.objectData(obj)
            else:
                pluginName = Names.vrayNode(Names.object(obj))
            pluginType = "Node"
        case "LIGHT":
            # If the object is light it won't be represented by a node,
            # so the name of the light plugin is given
            bpyLight = bpy.types.Light(obj.data)
            if bpyLight.vray:
                pluginType = lib_utils.getLightPluginType(bpyLight)
                pluginName = Names.object(obj)

    if pluginName:
        # Export empty plugin because it might have not been created yet
        vray.pluginCreate(nodeCtx.renderer, pluginName, pluginType)
        result = AttrPlugin(pluginName)
        result.auxData['object'] = obj
        result.useDefaultOutput()
        return result
    
    return AttrPlugin()


def _exportVRayNodeSelectObjectGeometry(nodeCtx: NodeContext):
    """ This node is used to select a geometry object from the scene """
    node = nodeCtx.node
    sceneObjects = nodeCtx.scene.objects

    if hasattr(node, "objectName") and node.objectName in sceneObjects:
        ob = sceneObjects[node.objectName]
        if ob.type == 'MESH':
            geomName = Names.objectData(ob)
            vray.pluginCreate(nodeCtx.renderer, geomName, "GeomStaticMesh")
            attrPlugin = AttrPlugin(geomName)
            attrPlugin.auxData['object'] = ob.name
            attrPlugin.useDefaultOutput()
            return attrPlugin
    return AttrPlugin()


def _exportVRayPluginListHolder(nodeCtx: NodeContext):
    pluginList = []
    for inSock in nodeCtx.node.inputs:
        plugin = exportSocket(nodeCtx, inSock)
        if type(plugin) is AttrPlugin:
            pluginList.append(plugin)

    return pluginList


################ CONVERTERS ########################################

def _needClrToFltConversion(fromSockType, toSockType, needMult):
    # If source color needs multiplication, there is no need to convert because
    # TexCombineFloat expects color texture    
    return isColorSocket(fromSockType) and isFloatSocket(toSockType) and not needMult 

def _needFltToClrConversion(fromSockType, toSockType, needMult):
    return ( isFloatSocket(fromSockType) and isColorSocket(toSockType) ) or \
                ( isFloatSocket(toSockType) and (not isColorSocket(fromSockType) and needMult) )


def _exportConverters(nodeCtx: NodeContext, toSock: bpy.types.NodeSocket, fromAttrPlugin: AttrPlugin):
    """ Export conversions between color and float sockets"""
    assert(type(fromAttrPlugin) is AttrPlugin)

    fromSock        = getLinkedFromSocket(toSock)
    mult            = 0.0
    needMult        = False
    fromSockType    = getVRayBaseSockType(fromSock)
    toSockType      = getVRayBaseSockType(toSock)
                
    # Multiplier is defined on the 'to' socket
    if hasattr(toSock, 'computeLinkMultiplier') and ((mult := toSock.computeLinkMultiplier(fromSock)) is not None):
        needMult = True
        
    asFloat = isFloatSocket(toSockType)

    # Conversions are logically part of the from sock        
    with TrackNode(nodeCtx.nodeTracker, getNodeTrackId(fromSock.node)):
        if _needClrToFltConversion(fromSockType, toSockType, needMult):
            fromAttrPlugin = _exportFltClrConverter(nodeCtx, fromAttrPlugin, "TexColorToFloat")
        elif _needFltToClrConversion(fromSockType, toSockType, needMult):
            fromAttrPlugin = _exportFltClrConverter(nodeCtx, fromAttrPlugin, "TexFloatToColor")
                            
        if needMult:
            fromAttrPlugin = _exportCombineTexture(nodeCtx, toSock.value, fromAttrPlugin, mult, asFloat)
                               
    return fromAttrPlugin
            

def _exportCombineTexture(ctxNode: NodeContext, value, txValue: AttrPlugin, txMult: float, asFloat: bool):
    """ Export a TexCombineFloat or TexCombineColor

    Args:
        value (color | float): the value of the non-texture input
        txValue (AttrPlugin): the value of the texture input
        txMult (float): blend factor between the value and the texture
        asFloat (bool): export TexCombineFloat if True, TexCombineColor otherwise

    Returns:
        AttrPlugin - the exported plugin
    """
    # Export virtual node
    pluginType = "TexCombineFloat" if asFloat else "TexCombineColor"
    pluginName = Names.nextVirtualNode(ctxNode, pluginType)
    plDesc = PluginDesc(pluginName, pluginType)
    
    if asFloat:
        plDesc.attrs["value"] = value
    else:
        plDesc.attrs["color"] = AColor(value)
    
    plDesc.attrs["texture"] = txValue
    plDesc.attrs["texture_multiplier"] = txMult
    
    return exportPluginWithStats(ctxNode, plDesc)


def _exportFltClrConverter(nodeCtx: NodeContext, texPlugin: AttrPlugin, opType):    
    pluginName = Names.nextVirtualNode(nodeCtx, opType)
    plDesc = PluginDesc(pluginName, opType)
    plDesc.attrs['input'] = texPlugin

    return exportPluginWithStats(nodeCtx, plDesc)


