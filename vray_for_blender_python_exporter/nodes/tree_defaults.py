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
    nonVRayNodes = [n for n in ntree.nodes if not hasattr(n, 'vray_plugin')]
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

def createNodeTreeForLightObject(light: bpy.types.Light):
    # Setting use_nodes to True will synchronously add a node tree to the node.
    light.use_nodes = True
    ntree = light.node_tree
    ntree.use_fake_user = True

    # We are using the main node tree of the light to attach V-Ray nodes to. We don't have
    # control over its creation, and Blender is free to attach some default nodes to it. E.g. 
    # for Cycles/EEVEE/Workbench an Emission + Light Output are atttached.
    # We cannot export these nodes, so we remove them here and leave only V-Ray nodes in 
    # the newly created tree.
    removeNonVRayNodes(ntree)

    NodeTools.addVRayNodeTreeSettings(ntree, 'LIGHT')

    return ntree

def addLightNodeTree(light: bpy.types.Light):
    ntree = createNodeTreeForLightObject(light)

    lightPluginType = lib_utils.getLightPluginType(light)
    lightNode = ntree.nodes.new(f"VRayNode{lightPluginType}")

    # Copy property values from light property group to the node property group 
    propGroup = getattr(light.vray, lightPluginType)
    nodePropGroup = getattr(lightNode, lightPluginType)

    pluginModule = getPluginModule(lightPluginType)

    copyPropGroupValues(propGroup, nodePropGroup, pluginModule)

    light.vray.is_vray_class = True

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

def addMaterialNodeTree(mtl: bpy.types.Material, addDefaultTree = True):
    # Setting use_nodes to True will synchronously add a node tree to the node.
    mtl.use_nodes = True
    ntree = mtl.node_tree

    NodeTools.addVRayNodeTreeSettings(ntree, 'MATERIAL')

    # The .vray property is added to the bpy.types.Material class, so all materials
    # in the scene will have it regardless of whether they are V-Ray or others'.
    # This flag will tell us that the material is a V-Ray one and we should process it.
    mtl.vray.is_vray_class = True

    # Add a default material tree consisting of an output node and a VRayBRDF material node

    if addDefaultTree:
        outputNode = NodeUtils.getOutputNode(mtl.node_tree, 'MATERIAL')

        if outputNode is None:
            outputNode = ntree.nodes.new('VRayNodeOutputMaterial')

        if not outputNode.inputs[0].hasActiveFarLink():
            brdfNode = ntree.nodes.new('VRayNodeBRDFVRayMtl')
            ntree.links.new(brdfNode.outputs['BRDF'], outputNode.inputs['Material'])

        bounds = NodeTools.calculateTreeBounds(ntree)
        NodeTools.rearrangeTree(ntree, outputNode, bounds=bounds)

    NodeTools.deselectNodes(ntree)
