# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
import mathutils
import numpy as np

from vray_blender.lib import export_utils, lib_utils
from vray_blender.lib.attribute_types import CompatibleNonVrayNodes
from vray_blender.lib.names import Names
from vray_blender.lib.sys_utils import getUvGridTexturePath
from vray_blender.plugins import PLUGIN_MODULES, getPluginModule, getPluginAttr
from vray_blender.exporting.plugin_tracker import TrackNode, getNodeTrackId, getObjTrackId
from vray_blender.exporting.node_exporters.material_node_export import exportVRayNodeBRDFBump, exportVRayNodeShaderScript
from vray_blender.exporting.node_exporters.uvw_node_export import exportVRayNodeUVWGenRandomizer, exportVRayNodeUVWMapping
from vray_blender.nodes.tools import getLinkInfo, isVrayNode, isVraySocket, isCompatibleNode

from vray_blender.exporting.tools import *
from vray_blender.lib.defs import *
from vray_blender import debug

from vray_blender.vray_tools.vray_proxy import getProxyPreviewAppliedTransform

from vray_blender.lib import plugin_utils
from vray_blender.bin import VRayBlenderLib as vray

def exportPluginWithStats(nodeCtx: NodeContext, plDesc: PluginDesc, trackPlugin = True) -> AttrPlugin:

    plDesc.node = nodeCtx.node
    if trackPlugin:
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


def exportSocketLink(nodeCtx: NodeContext, nodeLink: FarNodeLink):
    sockValue = exportVRayNode(nodeCtx, nodeLink)

    # Not all nodes are exported as plugins. Some may just return a value.
    if type(sockValue) is AttrPlugin:
        if sockValue.isEmpty():
            return None
        # Export any conversions needed between the source and the
        # target socket types
        sockValue = _exportConverters(nodeCtx, nodeLink.to_socket, sockValue)

    return sockValue


def exportLinkedSocket(nodeCtx: NodeContext, inSocket: bpy.types.NodeSocket):
    """ Export the tree branch connected to the input socket, applying any necessary conversions.

    Args:
        nodeCtx (NodeContext): the active node export context
        inSocket (bpy.types.NodeSocket): the input socket to which the branch is connected

    Returns:
        _type_: AttrPlugin if the socket is linked, None otherwise
    """
    assert not inSocket.is_output

    if nodeLink := getFarNodeLink(inSocket):
        return exportSocketLink(nodeCtx, nodeLink)

    return None


def exportSocket(nodeCtx: NodeContext, inSocket: bpy.types.NodeSocket):
    if link := getFarNodeLink(inSocket):
        return exportSocketLink(nodeCtx, link)

    resolvedSock = resolveNodeSocket(inSocket)
    return resolvedSock.value if resolvedSock else inSocket.value


def _exportVRayNodeImpl(nodeCtx: NodeContext, nodeLink: FarNodeLink):
    import vray_blender.exporting.cycles_export as cycles_export
    """ Export V-Ray node using its custom function if it has one or with '_exportArbitraryNode' otherwise
    """
    node = nodeCtx.node

    # Group nodes are traversed transparently by resolveNodeSocket().
    # If we got here, the group has no valid output connection.
    if node.bl_idname in ('VRayNodeGroup', 'NodeGroupInput', 'NodeGroupOutput'):
        return AttrPlugin()

    # Currently the list of compatible nodes contains only cycles nodes and
    # group/re-route which are skipped by the export anyways.
    if not isVrayNode(node) and (node.bl_idname in CompatibleNonVrayNodes):
        try:
            return cycles_export.exportCyclesNode(nodeCtx, nodeLink)
        except Exception as e:
            debug.printError(f"Failed to export Cycles node '{node.name}' from material '{nodeCtx.material.name}'")
            debug.printExceptionInfo(e)
            return AttrPlugin()

    # TODO Move all of the custom export functions into 'exportTreeNode' members of their plugin modules
    match node.bl_idname:
        case "VRayNodeBRDFBump":
            return exportVRayNodeBRDFBump(nodeCtx)
        case "VRayNodeMtlOSL":
            return exportVRayNodeShaderScript(nodeCtx)
        case "VRayPluginListHolder":
            return _exportVRayPluginListHolder(nodeCtx)
        case "VRayNodeSelectObject":
            return _exportVRayNodeSelectObject(nodeCtx, nodeLink)
        case "VRayNodeMultiSelect":
            return _exportVRayNodeMultiSelect(nodeCtx, nodeLink)
        case "VRayNodeSelectObjectGeometry":
            return _exportVRayNodeSelectObjectGeometry(nodeCtx)
        case "VRayNodeUVWGenRandomizer":
            return exportVRayNodeUVWGenRandomizer(nodeCtx)
        case "VRayNodeUVWMapping":
            return exportVRayNodeUVWMapping(nodeCtx)
        case "VRayNodeTexVectorProduct":
            return _exportVRayNodeTexVectorProduct(nodeCtx)
        case "VRayNodeTransform":
            return node.getValue(nodeCtx.exporterCtx.ctx)
        case "VRayNodeMatrix" | "VRayNodeVector":
            return node.getValue()
        case "VRayNodeGenericPlugin":
            return _exportGenericPluginNode(nodeCtx)

        case _:
            if node.vray_plugin and (node.vray_plugin != 'NONE') and \
                    (exportTreeNode := getattr(getPluginModule(node.vray_plugin), "exportTreeNode", None)):
                return exportTreeNode(nodeCtx)
            else:
                return _exportArbitraryNode(nodeCtx, nodeLink)


def exportVRayNode(nodeCtx: NodeContext, nodeLink: FarNodeLink):
    """ Export a VRay node, calling a specialized export function if one exists for the node type. """
    node = nodeLink.from_node

    if not isCompatibleNode(node):
        NodeContext.registerError(f"Skipped export of non V-Ray node: '{node.name}'")
        return AttrPlugin()

    # Change the current context
    nodeFromCache = True
    groupPath = getattr(nodeLink, 'groupPath', ())
    with nodeCtx.push(node), nodeCtx.pushGroupPath(groupPath):
        # Check if the node is already exported.
        # When one node is connected to multiple sockets it will be iterated more than once.
        outputId = None if hasattr(node, "vray_plugin") else getattr(nodeLink.from_socket, "identifier", None)
        if (result := nodeCtx.getCachedNodePlugin(node, outputId)) is None:
            with TrackNode(nodeCtx.nodeTracker, getNodeTrackId(node)):
                result = _exportVRayNodeImpl(nodeCtx, nodeLink)
                nodeCtx.cacheNodePlugin(node, result, outputId)
                nodeFromCache = False

        # Meta nodes, e.g. object selectors, may export values other than AttrPlugin. It is responsibility
        # of their custom export code to set the correct output selector. For the rest, set the correct
        # output selector here
        if hasattr(node, "vray_plugin") and (type(result) is AttrPlugin) and (not result.isEmpty()):
            output = getOutSocketSelector(nodeLink.from_socket)
            if not result.isOutputSet():
                result.output = output
            elif nodeFromCache and result.output != output:
                # Copy the AttrPlugin with a different output name in order to allow the use of different
                # output sockets of a single exported plugin.
                import copy
                result = copy.copy(result)
                result.output = output
        return result


# Some nodes require special handling and we have dedicated export methods for them.
# For the rest, use this method
def _exportArbitraryNode(nodeCtx: NodeContext, nodeLink: FarNodeLink):
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

    return attrPlugin


def _exportGenericPluginNode(nodeCtx: NodeContext):
    """ Export a node standing in for a V-Ray plugin the addon generates no node class for.

        Its values live in its sockets rather than in a property group, and V-Ray may ship no
        description for the plugin at all, so the attribute descriptions exportNodeTree looks
        up are built from the sockets instead. The sockets still do their own marshalling, so
        values are converted exactly as they are for a generated node.

        Only the attributes the node carries a socket for are written, and they are written
        verbatim. The plugin was imported with the parameters the source .vrscene set, and
        V-Ray's own defaults are a better answer for the rest than the addon's authoring
        defaults - which the generic export path would otherwise write out, in the process
        turning a description's default colour (a plain JSON list) into a float list.
    """
    from vray_blender.nodes.importing import generic_node

    node = nodeCtx.node
    plDesc = PluginDesc(Names.treeNode(nodeCtx), node.vray_plugin)

    # Every socket of a list attribute writes to the same name, so they are collected and set
    # once instead of each exporting on its own.
    listValues = {}
    sockByAttr = {}

    for sock in node.inputs:
        if not (attrName := getattr(sock, 'vray_attr', '')):
            continue

        sockByAttr.setdefault(attrName, sock)
        attrDesc = generic_node.socketAttrDesc(sock)
        link = getFarNodeLink(sock)

        if node.isListAttr(attrName):
            if link:
                if linkValue := exportSocketLink(nodeCtx, link):
                    listValues.setdefault(attrName, []).append(linkValue)
            elif (value := getattr(sock, 'value', None)) is not None:
                listValues.setdefault(attrName, []).append(value)
            continue

        if link:
            if linkValue := exportSocketLink(nodeCtx, link):
                sock.exportLinked(plDesc, attrDesc, linkValue)
        else:
            sock.exportUnlinked(nodeCtx, plDesc, attrDesc)

    for attrName, values in listValues.items():
        plDesc.setAttribute(attrName, values)

    plDesc.node = node
    nodeCtx.nodeTracker.trackPlugin(plDesc.name)
    nodeCtx.stats.uniquePlugins.add(plDesc.name)
    nodeCtx.stats.plugins += 1
    nodeCtx.stats.attrs += len(plDesc.attrs)

    if nodeCtx.customHandler:
        return nodeCtx.customHandler(nodeCtx, plDesc)

    exporterCtx = nodeCtx.exporterCtx
    plugin_utils.createPlugin(exporterCtx, plDesc.name, plDesc.type)

    for attrName, value in plDesc.attrs.items():
        value = _coerceSocketArray(value, sockByAttr.get(attrName))
        plugin_utils.updateValue(exporterCtx.renderer, plDesc.name, attrName, value)

    return AttrPlugin(plDesc.name, pluginType=plDesc.type)


# A socket whose value is a plain float vector hands out a bpy_prop_array, which the exporter
# has no type for. Only the socket knows whether three floats are a colour or a vector.
_ARRAY_TO_VRAY_VALUE = {
    'VRaySocketColor':         mathutils.Color,
    'VRaySocketGenericColor':  mathutils.Color,
    'VRaySocketAColor':        AColor,
    'VRaySocketGenericAColor': AColor,
    'VRaySocketVector':        mathutils.Vector,
}


def _coerceSocketArray(value, sock):
    if (type(value) is bpy.types.bpy_prop_array) and (sock is not None) \
            and (convert := _ARRAY_TO_VRAY_VALUE.get(sock.vray_socket_base_type)):
        return convert(value[:])
    return value


def _resolveGroupInputSocket(sock):
    """ If sock is connected (possibly through reroutes) to a NodeGroupInput,
        and the corresponding group node input socket is unlinked, return that
        group node input socket. Otherwise return None.
    """
    if not sock.is_linked:
        return None

    resolved = resolveNodeSocket(sock)
    if resolved and not resolved.is_linked and resolved.node and resolved.node.bl_idname in ('VRayNodeGroup', 'ShaderNodeGroup'):
        return resolved
    return None


def _getGroupInputOverrideSocket(sock):
    """ If sock is connected (possibly through reroutes) to a NodeGroupInput,
        return the corresponding group node input socket (even if that socket
        IS linked from outside). Used to read multiplier/use overrides from
        the group node exterior.
        Unlike resolveNodeSocket, this does NOT traverse past the group node
        boundary — it stops at the group node's input socket.
        Returns None if sock is not connected through a GroupInput.
    """
    current = sock
    while current.is_linked:
        link = current.links[0]
        fromNode = link.from_node
        if fromNode.bl_idname == 'NodeReroute':
            current = fromNode.inputs[0]
            continue
        if fromNode.bl_idname == 'NodeGroupInput':
            groupNode = getGroupNode(fromNode)
            if not groupNode:
                return None
            idx = next((i for i, s in enumerate(fromNode.outputs) if s == link.from_socket), None)
            if idx is not None and idx < len(groupNode.inputs):
                return groupNode.inputs[idx]
            return None
        break
    return None


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

    if not isCompatibleNode(node):
        NodeContext.registerError(f"Non V-Ray node can not be exported: '{node.name}'")
        return

    pluginModule = PLUGIN_MODULES[plDesc.type]

    # Properties driven by a meta socket (e.g. the 'use' flag and target plugin of a
    # BRDF_USE/COLOR_USE socket) are exported by that meta socket. They also get their
    # own hidden stand-alone sockets, whose unlinked export would overwrite the value
    # the meta socket set - skip them here.
    metaBoundProps = set()
    for p in pluginModule.Parameters:
        if boundProps := p.get('bound_props'):
            metaBoundProps.update(boundProps.values())

    for sock in node.inputs:
        if not isVraySocket(sock):
            debug.printError(f"Socket '{sock.name}' of node {node.name}[ {nodeCtx.rootObj.name} ] is not a V-Ray socket. "
                                "Try to recreate the node to obtain the full functionality.")
            continue

        if sock.vray_attr in skippedSockets:
            continue

        if sock.vray_attr in metaBoundProps:
            continue

        attrName = sock.vray_attr
        pluginParam = getPluginAttr(pluginModule, attrName)

        if not pluginParam:
            # Nodetrees for meta nodes are exported one VRay plugin at a time. If the plugin param
            # has not been found, this means that the socket is created for another plugin and would
            # be exported in another call to this function.
            continue

        if link := getFarNodeLink(sock):
            if sockValue := exportSocketLink(nodeCtx, link):
                sock.exportLinked(plDesc, pluginParam, sockValue)
        elif (groupSock := _resolveGroupInputSocket(sock)) is not None:
            # The socket is connected through a GroupInput to an unlinked
            # group node input. Export the value from the group node's socket.
            groupSock.exportUnlinked(nodeCtx, plDesc, pluginParam)
        else:
            sock.exportUnlinked(nodeCtx, plDesc, pluginParam)


def _exportObjMaterials(exporterCtx: ExporterContext, obj: bpy.types.Object, instance: bpy.types.DepsgraphObjectInstance = None):
    from vray_blender.exporting.mtl_export import MtlExporter
    mtlExporter = MtlExporter(exporterCtx)

    exportedMtls: list[AttrPlugin] = []

    objMtls = _getObjectMaterials(obj)
    assert len(objMtls) == len(obj.material_slots)

    # Blender gives an Empty no material slots at all, so the Empty-backed geometry plugins
    # (infinite plane, perfect sphere) keep their material in a pointer on the object instead.
    if not objMtls and (emptyGeomMtl := obj.original.vray.material):
        objMtls = [emptyGeomMtl if emptyGeomMtl.use_nodes and emptyGeomMtl.node_tree else None]

    # Handle V-Ray Proxy material override by geometry node.
    if (slot := _getProxyCollapsedMaterialSlot(obj)) is not None:
        objMtls = [objMtls[slot]]

    viewLayer = exporterCtx.dg.view_layer
    useCustomOverride = not exporterCtx.preview and viewLayer.vray.material_override_mode == '2'
    if not objMtls and useCustomOverride:
        objMtls.append(viewLayer.material_override)

    if not objMtls:
        # No material is assigned to the object, export a 'Default' material
        exportedMtls.append(MtlExporter.exportDefaultMaterial(exporterCtx))

    objMtls = list(map(lambda mat: viewLayer.material_override if (not mat and useCustomOverride) else mat, objMtls))

    for mtl in objMtls:
        # Export default material for unsupported or empty slots.
        if mtl is None:
            exportedMtls.append(MtlExporter.exportDefaultMaterial(exporterCtx))
            continue

        mtlSingleBRDF, _ = mtlExporter.exportMtl(mtl)

        if mtlSingleBRDF is None:
            # This may happen when the output node corresponding to the tree type (V-Ray or Cycles)
            # is missing
            exportedMtls.append(MtlExporter.exportDefaultMaterial(exporterCtx))
            continue

        mtlWrapped = _exportMtlOptions(exporterCtx, obj, mtl, mtlSingleBRDF)

        # Force the update in order to re-establish the link to the wrapping plugin.
        # If the mtlOptions have changed, it was broken because the whole mtl options chain
        # is recreated on each update.
        mtlWrapped.forceUpdate = True

        exportedMtls.append(mtlWrapped)

    if len(exportedMtls) > 1:
        # For objects with mutliple materials in different slots, export MtlMulti plugin which 
        # holds references to all object's materials
        mtlPlugin = _exportMtlMulti(exporterCtx, obj, exportedMtls, instance)
    else:
        mtlPlugin = exportedMtls[0]

    return mtlPlugin


def _exportMtlOptions(exporterCtx: ExporterContext, obj: bpy.types.Object, mtl: bpy.types.Material, singleBRDFMaterial: AttrPlugin):
    """ Export the chain of plugins constituting the material options.
        @return The last plugin in the chain, or the original singleBRDFMaterial if no options were specified
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


def _exportMtlMulti(exporterCtx: ExporterContext, obj: bpy.types.Object, mtlPlugins: list[AttrPlugin], instance: bpy.types.DepsgraphObjectInstance=None):
    matPluginName = Names.pluginObject("multi_mtl", Names.object(obj, instance))

    mtlMultiDesc = PluginDesc(matPluginName, "MtlMulti")
    mtlMultiDesc.setAttribute("mtls_list", list(mtlPlugins))
    mtlMultiDesc.setAttribute("ids_list", [num for num in range(len(mtlPlugins))])
    mtlMultiDesc.setAttribute("wrap_id", True)

    # Those attributes should be in default (not set) state
    mtlMultiDesc.resetAttribute("mtlid_gen")
    mtlMultiDesc.resetAttribute("mtlid_gen_float")

    mtlMultiDesc.attrs['scene_name'] = [matPluginName]

    return export_utils.exportPlugin(exporterCtx, mtlMultiDesc)

def fillNodePluginDesc(exporterCtx: ExporterContext,
                       obj: bpy.types.Object,
                       nodeDesc: PluginDesc,
                       transform: Matrix,
                       objTracker: ObjTracker,
                       geomPlugin = None,
                       hasValidGeometry: bool = True,
                       instance: bpy.types.DepsgraphObjectInstance = None):

    if not hasValidGeometry or exporterCtx.isProxyExport:
        # No valid geometry: no materials (avoids MtlDisplacement on empty geometry).
        # Proxy export: geometry-only; materials are not written to the .vrmesh path.
        matPlugin = AttrPlugin()
    else:
        # Export empty material plugin(s) for the node. They will be filled in later by the
        # material export procedure
        matPlugin = _exportObjMaterials(exporterCtx, obj, instance)

    assert matPlugin is not None, "Default material should be attached if the current material is invalid"

    objTracker.trackPlugin(getObjTrackId(obj), matPlugin.name)
    nodeDesc.setAttribute("material", matPlugin)

    nodeDesc.setAttribute("transform", transform)

    nodeDesc.setAttribute("objectID", obj.pass_index)
    if exporterCtx.commonSettings.exportMotionData:
        objProperties = obj.vray.VRayObjectProperties
        if objProperties.override_motion_blur_samples:
            nodeDesc.setAttribute("nsamples", objProperties.motion_blur_samples)
        else:
            nodeDesc.setAttribute("nsamples", exporterCtx.commonSettings.mbSamples)

    scene = exporterCtx.dg.scene
    if geomPlugin:
        # In the instancer case, geomPlugin is the instancer plugin
        sceneName = geomPlugin.name
        scenePath = f"scene/{geomPlugin.name}"
    else:
        sceneName = obj.name
        scenePath = getSceneNameOfObject(obj, scene)

    nodeDesc.setAttribute("scene_name", buildObjectSceneName(sceneName, scenePath, obj, exporterCtx))
    if userAttributes := obj.vray.UserAttributes.getAsString():
        nodeDesc.setAttribute('user_attributes', userAttributes)


def exportNodePlugin(exporterCtx: ExporterContext,
                     obj: bpy.types.Object,
                     geomPlugin: AttrPlugin,
                     objectName: str,
                     objTracker: ObjTracker,
                     isInstancer = False,
                     instance: bpy.types.DepsgraphObjectInstance = None,
                     visible = True,
                     tmOverride = None):
        """ Exports a V-Ray Node plugin for the specified geometry """

        if tmOverride is None:
            tmOverride = obj.matrix_world

        isInstance = (instance is not None)
        tm = mathutils.Matrix() if (isInstancer or isInstance) else tmOverride

        # Proxy objects must be rendered at the location of their preview meshes.
        if isObjectVrayProxy(obj) and obj.data.vray.GeomMeshFile and not isInstancer:
            appliedTransform = getProxyPreviewAppliedTransform(obj, fromOriginal = not isInstance)
            tm = tm @ appliedTransform

        pluginName = Names.vrayNode(objectName)
        nodeDesc = PluginDesc(pluginName, "Node")

        if isInstancer:
            geomPlugin.forceUpdate = True

        nodeDesc.setAttribute("geometry", geomPlugin)
        nodeDesc.setAttribute("visible", visible)

        fillNodePluginDesc(
            exporterCtx,
            obj,
            nodeDesc,
            tm,
            objTracker,
            geomPlugin if isInstancer else None,
            not geomPlugin.isEmpty(),
            instance
        )

        nodePlugin = export_utils.exportPlugin(exporterCtx, nodeDesc)

        objTracker.trackPlugin(getObjTrackId(obj), nodePlugin.name, isInstance)

        return nodePlugin


def _getProxyCollapsedMaterialSlot(obj: bpy.types.Object):
    """ For a V-Ray proxy, return the material slot index that
        a 'Set Material' or 'Set Material Index' geometry node assigns to the geometry,
        or None when the object is not a proxy, has a single slot, or has no overridden materials.
    """
    if (not isObjectVrayProxy(obj)) or len(obj.material_slots) <= 1:
        return None

    # 'Full' and 'Preview' previews carry the per-face material indices of the .vrmesh shaders, so
    # the presence of the attribute alone is not an override. Only a geometry node tree could have
    # replaced them, and testing the modifiers first keeps the scan below off the common path.
    if not any(m.type == 'NODES' for m in obj.modifiers):
        return None

    attr = obj.data.attributes.get("material_index")
    if attr is None or (numFaces := len(attr.data)) == 0:
        # There are no material overrides by geometry node tree.
        return None

    indices = np.empty(numFaces, dtype=np.int32)
    attr.data.foreach_get("value", indices)

    usedSlot = int(indices[0])
    if (indices != usedSlot).any():
        # Only a part of the faces is overridden. Keep the face material IDs of the .vrmesh.
        return None

    return usedSlot if 0 <= usedSlot < len(obj.material_slots) else None


def _getObjectMaterials(obj: bpy.types.Object):
    mtls = []

    # An object can have several materials assigned to different parts of it. 
    # The materials are stored in the material_slots field. If the material is unsupported
    # or the slot is empty add None which will be exported as a default material to properly
    # account for all slots and the mesh's face material ids.
    for slot in obj.material_slots:
        if slot.material and slot.material.use_nodes and (slot.material.node_tree is not None):
            mtls.append(slot.material)
        else:
            mtls.append(None)

    return mtls


def getTextureUVWGen(nodeCtx: NodeContext, sockNormal):
    """ TODO: not implemented currently """
    return AttrPlugin()


################ IN-PLACE values export ############################
def _exportVRayNodeMultiSelect(nodeCtx: NodeContext, nodeLink: FarNodeLink) -> list[AttrPlugin]:
    node = nodeCtx.node
    selectedObjects = node.getSelected(nodeCtx.exporterCtx.ctx)
    
    result = []

    if not isVrayNode(nodeLink.to_node):
        return result
    
    for obj in selectedObjects:
        socket = nodeLink.to_socket
        linkInfo = getLinkInfo(socket.node.vray_plugin, socket.vray_attr)
        
        if linkInfo.fnFilter(obj):
            isGeometry = (linkInfo.linkType == LinkInfo.OBJECT_DATA)
            result.append(_forwardExportSceneObject(nodeCtx, obj, isGeometry))

    return result


def _exportVRayNodeSelectObject(nodeCtx: NodeContext, nodeLink: FarNodeLink) -> AttrPlugin:
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

            # Make sure the referenced object is actually exported, even if it is invisible /
            # disabled in renders. Without this the forward-created plugin below would stay empty.
            nodeCtx.exporterCtx.registerReferencedObject(obj)
        case "LIGHT":
            # If the object is light it won't be represented by a node,
            # so the name of the light plugin is given
            bpyLight = bpy.types.Light(obj.data)
            if bpyLight.vray:
                pluginType = lib_utils.getLightPluginType(bpyLight)
                pluginName = Names.object(obj)

    if pluginName:
        # Export empty plugin because it might have not been created yet
        plugin_utils.forwardDeclarePlugin(nodeCtx.exporterCtx, pluginName, pluginType)
        result = AttrPlugin(pluginName, pluginType=pluginType)
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
            plugin_utils.forwardDeclarePlugin(nodeCtx.exporterCtx, geomName, "GeomStaticMesh")
            attrPlugin = AttrPlugin(geomName)
            attrPlugin.auxData['object'] = ob.name
            attrPlugin.useDefaultOutput()
            return attrPlugin
    return AttrPlugin()


def _exportVRayNodeTexVectorProduct(nodeCtx: NodeContext):
    # TexVectorProduct is only used by the converter for now and it requires
    # some vector->color->vector hacks because blender clamps colors, but the
    # plugin uses color textures.
    node = nodeCtx.node
    pluginType = node.vray_plugin
    pluginName = Names.treeNode(nodeCtx)
    plDesc = PluginDesc(pluginName, pluginType)
    plDesc.vrayPropGroup = node.TexVectorProduct

    exportNodeTree(nodeCtx, plDesc)
    inp1 = plDesc.getAttribute("input1")
    if isinstance(inp1, Vector):
        plDesc.setAttribute("input1", mathutils.Color(inp1[:]), True)
    attrPlugin = exportPluginWithStats(nodeCtx, plDesc)

    return attrPlugin


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

def _needTexVectorToColorConversion(fromSockType, toSockType):
    return (fromSockType == 'VRaySocketVector') and isColorSocket(toSockType)

def _needTexIntToFloatConversion(fromSockType, toSockType):
    return (fromSockType == 'VRaySocketVectorInt') and isFloatSocket(toSockType)

def _needUVWGenToColorConversion(fromSockType, toSockType, fromAttrPlugin: AttrPlugin):
    return isUVWSocket(fromSockType) and "UVWGen" in fromAttrPlugin.pluginType and isColorSocket(toSockType) and toSockType != 'VECTOR'

def _needColorToUVWConversion(fromSockType, toSockType, fromAttrPlugin: AttrPlugin):
    return isUVWSocket(toSockType) and "UVWGen" not in fromAttrPlugin.pluginType and isColorSocket(fromSockType) and toSockType != 'VECTOR'

def _needUVWGenToBrdfConversion(fromSockType, toSockType, fromAttrPlugin: AttrPlugin):
    """UVWGen → BRDF socket: build a TexBitmap on a UV-grid image driven by the
       UVWGen, then wrap it in a BRDFLight. Lets solo mode preview a UVWGen
       directly on the Material Output's BRDF slot.
    """
    return isUVWSocket(fromSockType) and "UVWGen" in (fromAttrPlugin.pluginType or "") \
        and toSockType == 'VRaySocketBRDF'

def _needColorToBrdfConversion(fromSockType, toSockType, fromAttrPlugin: AttrPlugin):
    """Texture/color → BRDF socket: wrap the source in a BRDFLight so the color
       lights up visibly in a preview. Only fires when the source plugin is
       actually a texture (not already a BRDF). Matches the preview-operator
       use case (Ctrl+Shift+LMB on a texture → Material Output).
    """
    if toSockType != 'VRaySocketBRDF':
        return False
    if not (isColorSocket(fromSockType) or isFloatSocket(fromSockType)):
        return False
    # Already a BRDF (or a BRDF-like wrapper) — nothing to wrap.
    pluginType = fromAttrPlugin.pluginType or ""
    if pluginType.startswith("BRDF"):
        return False
    return True

def _exportBrdfLightConverter(nodeCtx: NodeContext, fromPlugin: AttrPlugin):
    """Wrap a color/texture plugin in a BRDFLight so it can drive a BRDF slot.
       compensateExposure=True keeps the preview visible across camera exposure.
    """
    pluginName = Names.nextVirtualNode(nodeCtx, "BRDFLight")
    plDesc = PluginDesc(pluginName, "BRDFLight")
    plDesc.attrs['color'] = fromPlugin
    plDesc.attrs['compensateExposure'] = True
    plDesc.attrs['affect_gi'] = False

    return exportPluginWithStats(nodeCtx, plDesc)

def _exportUVWGenToBrdfConverter(nodeCtx: NodeContext, fromPlugin: AttrPlugin):
    """UVWGen → BRDF: build a TexBitmap on a built-in UV-grid image driven by
       the source UVWGen, then wrap it in a BRDFLight so the grid lights up
       visibly in solo mode.
    """
    bufName = Names.nextVirtualNode(nodeCtx, "BitmapBuffer")
    bufDesc = PluginDesc(bufName, "BitmapBuffer")
    bufDesc.attrs['file'] = getUvGridTexturePath()
    bufDesc.attrs['transfer_function'] = "2"  # sRGB
    bufDesc.attrs['rgb_color_space'] = "lin_srgb"
    bufPlugin = exportPluginWithStats(nodeCtx, bufDesc)

    bmpName = Names.nextVirtualNode(nodeCtx, "TexBitmap")
    bmpDesc = PluginDesc(bmpName, "TexBitmap")
    bmpDesc.attrs['uvwgen'] = fromPlugin
    bmpDesc.attrs['bitmap'] = bufPlugin
    bmpPlugin = exportPluginWithStats(nodeCtx, bmpDesc)

    lightName = Names.nextVirtualNode(nodeCtx, "BRDFLight")
    lightDesc = PluginDesc(lightName, "BRDFLight")
    lightDesc.attrs['color'] = bmpPlugin
    lightDesc.attrs['compensateExposure'] = True
    lightDesc.attrs['affect_gi'] = False

    return exportPluginWithStats(nodeCtx, lightDesc)

def _exportConverters(nodeCtx: NodeContext, toSock: bpy.types.NodeSocket, fromAttrPlugin: AttrPlugin):
    """ Export one or more 'convert' and/or 'combine' plugins to convert
        between the data types of two linked sockets.
    """
    assert(type(fromAttrPlugin) is AttrPlugin)

    fromSock        = getLinkedFromSocket(toSock)
    mult            = 0.0
    needMult        = False
    fromSockType    = getVRayBaseSockType(fromSock)
    toSockType      = getVRayBaseSockType(toSock)

    # Multiplier is defined on the 'to' socket. If the socket is connected
    # through a GroupInput, read from the group node's exterior socket instead,
    # as that's where the user sets the multiplier.
    multSock = _getGroupInputOverrideSocket(toSock) or toSock
    if hasattr(multSock, 'computeLinkMultiplier') and ((mult := multSock.computeLinkMultiplier()) is not None):
        needMult = True

    asFloat = isFloatSocket(toSockType)

    # Conversions are logically part of the from sock
    with TrackNode(nodeCtx.nodeTracker, getNodeTrackId(fromSock.node)):
        if _needClrToFltConversion(fromSockType, toSockType, needMult):
            if hasattr(fromSock, "vray_socket_base_type"):
                fromAttrPlugin = _exportTexConverter(nodeCtx, fromAttrPlugin, "TexColorToFloat")
            else:
                # In Cycles Color->Float conversion seems to use perceived intensity instead of (r+g+b)/3...
                # Note that currently TexLuminance is not exactly the same as Cycle's intensity(RGBtoBW node).
                fromAttrPlugin = _exportTexConverter(nodeCtx, fromAttrPlugin, "TexLuminance")
        elif _needFltToClrConversion(fromSockType, toSockType, needMult):
            fromAttrPlugin = _exportTexConverter(nodeCtx, fromAttrPlugin, "TexFloatToColor")
        elif _needTexVectorToColorConversion(fromSockType, toSockType):
            fromAttrPlugin = _exportTexConverter(nodeCtx, fromAttrPlugin, "TexVectorToColor")
        elif _needTexIntToFloatConversion(fromSockType, toSockType):
            fromAttrPlugin = _exportTexConverter(nodeCtx, fromAttrPlugin, "TexIntToFloat")
        elif _needUVWGenToColorConversion(fromSockType, toSockType, fromAttrPlugin):
            fromAttrPlugin = _exportUVWToColorConverter(nodeCtx, fromAttrPlugin)
        elif _needColorToUVWConversion(fromSockType, toSockType, fromAttrPlugin):
            fromAttrPlugin = _exportColorToUVWConverter(nodeCtx, fromAttrPlugin)
        elif _needUVWGenToBrdfConversion(fromSockType, toSockType, fromAttrPlugin):
            fromAttrPlugin = _exportUVWGenToBrdfConverter(nodeCtx, fromAttrPlugin)
        elif _needColorToBrdfConversion(fromSockType, toSockType, fromAttrPlugin):
            fromAttrPlugin = _exportBrdfLightConverter(nodeCtx, fromAttrPlugin)
        if needMult:
            fromAttrPlugin = _exportCombineTexture(nodeCtx, multSock.value, fromAttrPlugin, mult, asFloat)

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


def _exportTexConverter(nodeCtx: NodeContext, texPlugin: AttrPlugin, converterPluginType: str):
    """ Export a converter between textures of different underlying types.
        All converters expose the same socket called 'input' which allows us
        to use the same function for all conversions.

        Args:
            texPlugin - the source plugin for the converision 
            convPluginType - the type of the converter plugin, e.g. TexColorToFloat
        Returns:
            The exported AttrPlugin
    """
    pluginName = Names.nextVirtualNode(nodeCtx, converterPluginType)
    plDesc = PluginDesc(pluginName, converterPluginType)
    plDesc.attrs['input'] = texPlugin

    return exportPluginWithStats(nodeCtx, plDesc)

def _exportUVWToColorConverter(nodeCtx: NodeContext, fromPlugin: AttrPlugin):
    pluginName = Names.nextVirtualNode(nodeCtx, "TexUVW")
    plDesc = PluginDesc(pluginName, "TexUVW")
    plDesc.attrs['uvwgen'] = fromPlugin

    return exportPluginWithStats(nodeCtx, plDesc)

def _exportColorToUVWConverter(nodeCtx: NodeContext, fromPlugin: AttrPlugin):
    pluginName = Names.nextVirtualNode(nodeCtx, "UVWGenExplicit")
    plDesc = PluginDesc(pluginName, "UVWGenExplicit")
    plDesc.attrs['uvw'] = fromPlugin

    return exportPluginWithStats(nodeCtx, plDesc)
