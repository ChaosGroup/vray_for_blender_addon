# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later


import bpy

from vray_blender.lib import lib_utils
from vray_blender.lib.attribute_utils import copyPropGroupValues
from vray_blender.plugins import getPluginModule
from vray_blender.nodes import tools as NodeTools
from vray_blender.nodes import utils as NodeUtils


def removeNonVRayNodes(ntree: bpy.types.NodeTree):
    """ Removes all non-vray nodes from a node tree. """
    nonVRayNodes = [n for n in ntree.nodes if not NodeTools.isVrayNode(n)]
    for n in nonVRayNodes:
        ntree.nodes.remove(n)


def addWorldNodeTree(world: bpy.types.World):
    if not world:
        # This function may be called to create a new V-Ray world.
        bpy.ops.world.new()
        world = bpy.data.worlds[-1]
        bpy.context.scene.world = world


    # Setting use_nodes to True will synchronously add a node tree to the node.
    world.use_nodes = True
    ntree = world.node_tree
    ntree.use_fake_user = True

    # We are using the main node tree of the world to attach V-Ray nodes to. We don't have
    # control over its creation, and Blender is free to attach some default nodes to it.
    # We cannot export these nodes, so we remove them here and leave only V-Ray nodes in
    # the newly created tree.
    removeNonVRayNodes(ntree)

    NodeTools.addVRayNodeTreeSettings(ntree, 'WORLD')

    outputNode    = ntree.nodes.new('VRayNodeWorldOutput')
    envNode       = ntree.nodes.new('VRayNodeEnvironment')
    effectNode    = ntree.nodes.new('VRayNodeEffectsHolder')
    channelsNode  = ntree.nodes.new('VRayNodeRenderChannels')

    alignX = outputNode.location.x - envNode.width - 50
    envNode.location.x = alignX
    envNode.location.y = outputNode.location.y + 200

    effectNode.location.x = alignX
    effectNode.location.y = outputNode.location.y

    channelsNode.location.x = alignX
    channelsNode.location.y = outputNode.location.y - 150

    ntree.links.new(envNode.outputs['Environment'], outputNode.inputs['Environment'])
    ntree.links.new(effectNode.outputs['Effects'], outputNode.inputs['Effects'])
    ntree.links.new(channelsNode.outputs['Channels'], outputNode.inputs['Channels'])

    world.vray.is_vray_class = True

    NodeTools.deselectNodes(ntree)


def createNodeTreeForLightObject(light: bpy.types.Light, isNewLight: bool):
    """ Create a V-Ray node tree for the light with an ouput node corresponding to the 
        light type.
        
        Args:
            light:  an existing Light object
            isNewLight: this is a newly created light
    """

    # Setting use_nodes to True will synchronously create a node tree with one or more nodes.
    light.use_nodes = True
    ntree = light.node_tree
    ntree.use_fake_user = True

    NodeTools.addVRayNodeTreeSettings(ntree, 'LIGHT')

    if isNewLight and NodeTools.isVrayLight(light):
        # For new lights Blender will create some nodes in the node tree by default.
        # For V-Ray lights we don't need the Blender nodes
        removeNonVRayNodes(ntree)
    return ntree


def addLightNodeTree(light: bpy.types.Light, isNewLight = False):
    ntree = createNodeTreeForLightObject(light, isNewLight)

    lightPluginType = lib_utils.getLightPluginType(light)
    lightNode = ntree.nodes.new(f"VRayNode{lightPluginType}")

    # Copy property values from light property group to the node property group
    propGroup = getattr(light.vray, lightPluginType)
    nodePropGroup = getattr(lightNode, lightPluginType)

    pluginModule = getPluginModule(lightPluginType)

    copyPropGroupValues(propGroup, nodePropGroup, pluginModule)

    light.vray.is_vray_class = True

    bounds = NodeTools.calculateTreeBounds(ntree)
    NodeTools.rearrangeTree(ntree, lightNode, bounds=bounds, appendLeft=True)
    NodeTools.deselectNodes(ntree)


def addObjectNodeTree(ob):
    VRayObject = ob.vray

    ntree = bpy.data.node_groups.new(ob.name, type='VRayNodeTreeObject')
    ntree.use_fake_user = True
    NodeTools.addVRayNodeTreeSettings(ntree, 'OBJECT')

    ntree.nodes.new('VRayNodeObjectOutput')

    NodeTools.deselectNodes(ntree)

    VRayObject.ntree = ntree


def addFurNodeTree(ob):
    ntree = bpy.data.node_groups.new(ob.name, type='VRayNodeTreeFur')
    ntree.use_fake_user = True

    NodeTools.addVRayNodeTreeSettings(ntree, 'FUR')

    objOutputNode = ntree.nodes.new('VRayNodeObjectOutput')

    furOutputNode = ntree.nodes.new('VRayNodeFurOutput')
    furOutputNode.location.y = objOutputNode.location.y - objOutputNode.height - 150
    copyPropGroupValues(ob.data.vray.GeomHair, furOutputNode.GeomHair, getPluginModule('GeomHair'))

    NodeTools.deselectNodes(ntree)

    ob.vray.ntree = ntree


def addDecalNodeTree(ob):
    ntree = bpy.data.node_groups.new(ob.name, type='VRayNodeTreeDecal')
    ntree.use_fake_user = True

    NodeTools.addVRayNodeTreeSettings(ntree, 'DECAL')

    NodeTools.deselectNodes(ntree)

    ob.vray.ntree = ntree


def addMaterialNodeTree(mtl: bpy.types.Material, addDefaultTree = True, nodeType = None, appendLeft = False):
    # Setting use_nodes to True will synchronously add a node tree to the node.
    mtl.use_nodes = True
    ntree = mtl.node_tree

    NodeTools.addVRayNodeTreeSettings(ntree, 'MATERIAL')

    # The .vray property is added to the bpy.types.Material class, so all materials
    # in the scene will have it regardless of whether they are V-Ray or others'.
    # This flag will tell us that the material is a V-Ray one and we should process it.
    mtl.vray.is_vray_class = True

    if not addDefaultTree:
        return

    # Add a default material tree consisting of an output node and a VRayBRDF material node
    outputNode = NodeUtils.getOutputNode(mtl.node_tree, 'MATERIAL')

    if outputNode is None:
        outputNode = ntree.nodes.new('VRayNodeOutputMaterial')

    if nodeType:
        mainNode = ntree.nodes.new(nodeType)
        ntree.links.new(mainNode.outputs[0], outputNode.inputs['Material'])

        if nodeType in NodeUtils.MATERIAL_WRAPPER_SOCKETS:
            brdfNode = ntree.nodes.new('VRayNodeBRDFVRayMtl')

            sockName = NodeUtils.MATERIAL_WRAPPER_SOCKETS[nodeType]
            if sockName in mainNode.inputs:
                ntree.links.new(brdfNode.outputs['BRDF'], mainNode.inputs[sockName])
    else:
        if not outputNode.inputs[0].hasActiveFarLink():
            brdfNode = ntree.nodes.new('VRayNodeBRDFVRayMtl')
            ntree.links.new(brdfNode.outputs['BRDF'], outputNode.inputs['Material'])

    bounds = NodeTools.calculateTreeBounds(ntree)
    NodeTools.rearrangeTree(ntree, outputNode, bounds=bounds, appendLeft=appendLeft)

    NodeTools.deselectNodes(ntree)
