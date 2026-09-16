# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

""" Cycles -> V-Ray node tree conversion, built on the import engine. """

import bpy

from vray_blender.nodes.tools import deselectNodes, arrangeImportedTree
from vray_blender.nodes import utils as NodeUtils
from vray_blender.lib.names import syncObjectUniqueName
from vray_blender.vray_tools.import_common import getPluginByName
from vray_blender.nodes.importing.engine import (
    ImportContext,
    createNode,
    fixPluginParams,
    _pluginAttrsToPropGroup,
)


def convertMaterial(material: bpy.types.Material, operator: bpy.types.Operator):
    from vray_blender.exporting.mtl_export import MtlExporter
    from vray_blender.exporting.plugin_tracker import FakeScopedNodeTracker, FakeObjTracker
    from vray_blender.exporting.tools import FakeTimeStats
    from vray_blender.lib.defs import ExporterContext, AttrPlugin, RendererMode, NodeContext, ExporterType, PluginDesc
    from vray_blender.engine import NODE_TRACKERS, OBJ_TRACKERS

    syncObjectUniqueName(material, reset=False)
    syncObjectUniqueName(material.original, reset=False)

    # Remove any pre-existing V-Ray nodes from the tree to avoid confusion.
    # It is the responsibility of the caller of this function to notify the user
    # that existing nodes will be deleted.
    _removeVRayNodes(material.node_tree)

    _fakeNodeTrackers = dict([(t, FakeScopedNodeTracker()) for t in NODE_TRACKERS])
    _fakeObjTrackers = dict([(t, FakeObjTracker()) for t in OBJ_TRACKERS])

    exporterContext = ExporterContext()
    exporterContext.rendererMode = RendererMode.Preview
    exporterContext.objTrackers  = _fakeObjTrackers
    exporterContext.nodeTrackers = _fakeNodeTrackers
    exporterContext.ctx          = bpy.context
    exporterContext.fullExport   = True
    exporterContext.ts           = FakeTimeStats()

    vrsceneDict = []
    # Custom handler that converts exportable plugins to an importable vrscene dict.
    def _vrsceneDictCollector(nodeCtx: NodeContext, pluginDesc: PluginDesc):
        for key, value in pluginDesc.attrs.items():
            if isinstance(value, AttrPlugin):
                if value.output:
                    pluginDesc.attrs[key] = value.name+"::"+value.output
                else:
                    pluginDesc.attrs[key] = value.name
            if isinstance(value, list) and all(isinstance(item, AttrPlugin) for item in value):
                plNames = []
                for pl in value:
                    plNames.append(pl.name)
                pluginDesc.attrs[key] = plNames
        # Group instance path, (instanceName, groupDefName) per element, used to rebuild
        # the Cycles groups as V-Ray groups on import; empty for top-level plugins.
        groupPath = tuple(
            (g.name, (g.node_tree.name if getattr(g, 'node_tree', None) else g.name))
            for g in nodeCtx._groupInstancePath
        )
        vrsceneDict.append({
            "ID"         : pluginDesc.type,
            "Name"       : pluginDesc.name,
            "Attributes" : pluginDesc.attrs,
            "GroupPath"  : groupPath,
        })
        return AttrPlugin(pluginDesc.name, pluginType=pluginDesc.type)

    nodeCtx = NodeContext(exporterContext, bpy.context.scene, bpy.data, None)
    nodeCtx.rootObj = material
    nodeCtx.nodeTracker = _fakeNodeTrackers['MTL']
    nodeCtx.ntree = material.node_tree
    nodeCtx.customHandler = _vrsceneDictCollector
    nodeCtx.material = material

    def reportErrors():
        # Intentionally print the warnings here manually. They won't be reported since we use a preview context.
        if (errors := NodeContext.getErrors()):
            msg = 'Error while converting ' + material.name
            for err in errors:
                msg += '\n\t ' + err
            operator.report({ 'WARNING' }, msg)

    mtlExporter = MtlExporter(exporterContext)
    exportedMaterial, _ = mtlExporter.exportMtl(material, nodeCtx)
    if not exportedMaterial:
        reportErrors()
        return

    material.use_nodes = True
    material.vray.is_vray_class = True
    material.node_tree.vray.tree_type = 'MATERIAL'

    with NodeUtils.DisableAutoConnect():
        importContext = ImportContext(material.node_tree, vrsceneDict, isConversion=True)
        _convertMaterialFromDict(importContext)

    syncObjectUniqueName(material, reset=False)
    syncObjectUniqueName(material.original, reset=False)

    reportErrors()


def convertWorld(world: bpy.types.World, operator: bpy.types.Operator = None):
    """ Convert a native Cycles world into an editable V-Ray world node tree.

        Takes the same export->import round trip as convertMaterial, so an Environment or Sky
        Texture arrives as real V-Ray nodes. Same result world_export produces on the fly, only
        editable. Returns True if converted, False if skipped.
    """
    from vray_blender.exporting.plugin_tracker import FakeScopedNodeTracker, FakeObjTracker
    from vray_blender.exporting.tools import FakeTimeStats
    from vray_blender.exporting.world_convert import convertCyclesWorld
    from vray_blender.exporting.node_export import exportLinkedSocket
    from vray_blender.lib.defs import ExporterContext, AttrPlugin, RendererMode, NodeContext, PluginDesc
    from vray_blender.nodes.tree_defaults import addWorldNodeTree
    from vray_blender.engine import NODE_TRACKERS, OBJ_TRACKERS

    if world is None or world.vray.is_vray_class:
        return False

    if (env := convertCyclesWorld(world)) is None:
        return False
    envColor, envStrength, envSocket = env

    # Resolve the texture branch before the Cycles tree is replaced.
    vrsceneDict = []
    rootPlugin = None

    if envSocket is not None:
        exporterContext = ExporterContext()
        exporterContext.rendererMode = RendererMode.Preview
        exporterContext.objTrackers  = dict((t, FakeObjTracker()) for t in OBJ_TRACKERS)
        exporterContext.nodeTrackers = dict((t, FakeScopedNodeTracker()) for t in NODE_TRACKERS)
        exporterContext.ctx          = bpy.context
        exporterContext.fullExport   = True
        exporterContext.ts           = FakeTimeStats()

        def _collect(nodeCtx: NodeContext, pluginDesc: PluginDesc):
            for key, value in pluginDesc.attrs.items():
                if isinstance(value, AttrPlugin):
                    pluginDesc.attrs[key] = f"{value.name}::{value.output}" if value.output else value.name
            vrsceneDict.append({"ID": pluginDesc.type, "Name": pluginDesc.name,
                                "Attributes": pluginDesc.attrs, "GroupPath": ()})
            return AttrPlugin(pluginDesc.name, pluginType=pluginDesc.type)

        nodeCtx = NodeContext(exporterContext, bpy.context.scene, bpy.data, None)
        nodeCtx.rootObj       = world
        nodeCtx.ntree         = world.node_tree
        nodeCtx.nodeTracker   = exporterContext.nodeTrackers['WORLD']
        nodeCtx.customHandler = _collect

        with nodeCtx, nodeCtx.push(envSocket.node):
            rootPlugin = exportLinkedSocket(nodeCtx, envSocket)

        if (errors := NodeContext.getErrors()) and operator is not None:
            operator.report({'WARNING'}, f"Converting world '{world.name}':\n\t"
                                         + "\n\t".join(sorted(errors)))

    # Replace the Cycles tree with a V-Ray one and fill in the environment node.
    _removeVRayNodes(world.node_tree)
    addWorldNodeTree(world)
    ntree = world.node_tree
    envNode = next(n for n in ntree.nodes if n.bl_idname == 'VRayNodeEnvironment')

    texNode = None
    if rootPlugin is not None and vrsceneDict:
        with NodeUtils.DisableAutoConnect():
            importContext = ImportContext(ntree, vrsceneDict, isConversion=True)
            fixPluginParams(importContext.vrsceneDict, forceDefaultUVChannel=True)
            texNode = createNode(importContext, getPluginByName(vrsceneDict, rootPlugin.name))

    # 'value' is clamped to 0-1, so anything brighter rides on the multiplier.
    color = envColor * envStrength
    peak = max(color.r, color.g, color.b, 1.0)
    envNode.location.x -= 260

    for sockName in ("Background", "GI", "Reflection", "Refraction"):
        # An unset slot renders black rather than falling back to the background.
        sock = next(s for s in envNode.inputs if s.name == sockName)
        sock.use = True
        if texNode is not None:
            sock.value = (1.0, 1.0, 1.0)
            sock.multiplier = envStrength
            ntree.links.new(texNode.outputs[0], sock)
        else:
            sock.value = (color.r / peak, color.g / peak, color.b / peak)
            sock.multiplier = peak

    arrangeImportedTree(ntree, NodeUtils.getOutputNode(ntree, 'WORLD'))
    return True


def convertLight(light: bpy.types.Light):
    """ Convert a native Blender light to a V-Ray light.

        Mirrors convertMaterial's export->import flow: the light is first 'exported' to a V-Ray
        light plugin description, which is then 'imported' onto the light's V-Ray property group.

        Returns True if the light was converted, False if skipped (already a V-Ray light or an
        unsupported light type).
    """
    from vray_blender.exporting.light_convert import exportBlenderLight
    from vray_blender.lib import lib_utils

    # Skip lights that have already been converted to a V-Ray type.
    if light.vray.light_type != 'BLENDER':
        return False

    pluginDesc = exportBlenderLight(light)
    if pluginDesc is None:
        return False

    # Switch the light to the matching explicit V-Ray type, then apply the converted attributes
    # to the active light property group (the legacy group on Blender 4.2-4.5, or the light
    # node's group on 5.1+ - getLightPropGroup picks the correct one).
    light.vray.light_type = lib_utils.BlenderToVrayLightType[light.type]

    pluginType = pluginDesc['ID']
    propGroup  = lib_utils.getLightPropGroup(light, pluginType)
    # Apply the converted attributes as a programmatic (non-user) change: 'units' carries an
    # already-converted 'intensity', so the units update callback must not rescale it.
    with NodeUtils.DisableAutoConnect():
        _pluginAttrsToPropGroup(pluginDesc, propGroup, pluginType)

    return True


def _convertMaterialFromDict(importContext: ImportContext):
    fixPluginParams(importContext.vrsceneDict, forceDefaultUVChannel=True)
    deselectNodes(importContext.nodeTree)

    endNodePluginDesc = None
    for plgDesc in importContext.vrsceneDict:
        if plgDesc['ID']=='MtlSingleBRDF':
            endNodePluginDesc = plgDesc
    if endNodePluginDesc is None:
        return

    brdfName = endNodePluginDesc['Attributes']['brdf']
    matPlugin = getPluginByName(importContext.vrsceneDict, brdfName)
    mtlNode = createNode(importContext, matPlugin)

    outputNode = importContext.nodeTree.nodes.new('VRayNodeOutputMaterial')
    importContext.nodeTree.links.new(mtlNode.outputs['BRDF'], outputNode.inputs['Material'])

    # When group nodes are enabled, rebuild the Cycles node groups as V-Ray groups
    # before laying out the tree, then lay out the top tree and every inner group tree.
    from vray_blender.nodes.group.utils import isGroupNodesEnabled
    if isGroupNodesEnabled():
        _regroupConvertedTree(importContext)
        arrangeImportedTree(importContext.nodeTree, outputNode, appendLeft=True, recurse=True)
    else:
        arrangeImportedTree(importContext.nodeTree, outputNode, appendLeft=True)

    return {'FINISHED'}


def _regroupConvertedTree(importContext: ImportContext):
    """ Reconstruct nested V-Ray groups in the converted material tree from the
        per-plugin GroupPath captured during conversion (see _vrsceneDictCollector).

        Each converted plugin carries a 'GroupPath' - a tuple of (instanceName,
        groupDefName) describing which Cycles ShaderNodeGroup instance(s) it came
        from. Nodes are folded back into nested V-Ray groups deepest-first, so a
        child group node is copied (with its node_tree preserved) into its parent
        group. The group construction itself reuses makeGroupFromNodes - the same
        code path as the interactive 'Make Group' operator.
    """
    from vray_blender.nodes.group.utils import VRAY_GROUP_TREE_TYPE, NON_GROUPABLE_NODE_TYPES
    from vray_blender.nodes.group.operators import makeGroupFromNodes

    nodeTree = importContext.nodeTree

    # Group membership was recorded at creation time on importContext.groupPathByNode
    # (both real plugin nodes and the import-side Transform helpers). Top-level nodes
    # have an empty path; tree-level anchors are never grouped.
    pathByNode = {n: p for n, p in importContext.groupPathByNode.items()
                  if p and n.bl_idname not in NON_GROUPABLE_NODE_TYPES}

    if not pathByNode:
        return

    # Every distinct group instance is a non-empty prefix of some node's path.
    instancePaths = set()
    for groupPath in pathByNode.values():
        for depth in range(1, len(groupPath) + 1):
            instancePaths.add(groupPath[:depth])

    # Process deepest paths first: a child group is created (as a VRayNodeGroup node
    # left in the flat tree) before its parent group is built and folds it in.
    for path in sorted(instancePaths, key=len, reverse=True):
        members = [n for n, p in pathByNode.items() if p == path]
        if not members:
            continue

        groupName = path[-1][1]  # the Cycles group definition (node_tree) name
        groupNode = makeGroupFromNodes(nodeTree, members, VRAY_GROUP_TREE_TYPE, groupName)
        if groupNode is None:
            continue

        # The consumed members were moved into the new group; the group node itself
        # belongs to the parent instance so the next (shallower) pass folds it in.
        for n in members:
            del pathByNode[n]
        if parentPath := path[:-1]:
            pathByNode[groupNode] = parentPath


def _removeVRayNodes(nodeTree: bpy.types.NodeTree):
    from vray_blender.nodes.tools import isVrayNode

    vrayNodes = [n for n in nodeTree.nodes if isVrayNode(n)]

    for n in vrayNodes:
        nodeTree.nodes.remove(n)
