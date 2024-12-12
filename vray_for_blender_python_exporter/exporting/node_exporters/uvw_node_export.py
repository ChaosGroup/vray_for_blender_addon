from vray_blender.exporting import node_export as commonNodesExport
from vray_blender.lib.defs import *
from vray_blender.lib.names import Names
from vray_blender.exporting.tools import *
from vray_blender.nodes.utils import getInputSocketByVRayAttr
import mathutils
import copy

# Flags for the different UVWGenRandomizer modes
UVWGenRandomizerModes = {
    "random_by_face" : 1,
    "random_by_render_id" : 2,
    "random_by_particle" : 4,
    "random_by_instance" : 8,
    "random_by_object_id" : 16,
    "random_by_node_handle" : 32,
    "random_by_node_name" : 64,
    "random_by_mesh_element" : 128,
    "random_by_uv_tile" : 256,
}


def exportDefaultUVWGenChannel(nodeCtx: NodeContext):
    """ Export a UVWGenChannel plugin with the default settings.
     
        @returns A newly exported plugin on the first invocation, a cached copy after that.
    """

    pluginType = 'UVWGenChannel'

    if not (plugin := nodeCtx.exporterCtx.defaultPlugins.get(pluginType)):
        uvwGenChannel = PluginDesc('defaultUVWGenChannel', 'UVWGenChannel')
        uvwGenChannel.setAttribute('uvw_channel', -1)
        plugin = commonNodesExport.exportPluginWithStats(nodeCtx, uvwGenChannel)

    return plugin


def exportDefaultUVWGenEnvironment(nodeCtx: NodeContext):
    """ Export a UVWGenEnvironment plugin with the default settings.
     
        @returns A newly exported plugin on the first invocation, a cached copy after that.
    """
    pluginType = 'UVWGenEnironment'

    if not (plugin := nodeCtx.exporterCtx.defaultPlugins.get(pluginType)):
        uvwGenEnv = PluginDesc('defaultUVWGenEnvironment', 'UVWGenEnvironment')

        assert len(nodeCtx.nodes), "This function should not be executed with NodeContext without nodes"

        if nodeCtx.nodes[0].bl_idname == 'VRayNodeLightDome' and getInputSocketByVRayAttr(nodeCtx.nodes[0], 'dome_lock_texture').value:
            # A separate default UVWGenEnvironment is created when LightDome::dome_lock_texture is enabled,
            # as it requires a unique uvw_matrix to account for the light's rotation.
            uvwGenEnv.name = f'defaultUVWGenEnvironment_{ Names.object(nodeCtx.rootObj) }'

            _, rotQuat, _ = nodeCtx.sceneObj.matrix_world.decompose()
            uvwGenEnv.setAttribute('uvw_matrix', rotQuat.inverted().to_matrix())

        uvwGenEnv.setAttribute('mapping_type', "spherical") # Spherical mapping


        plugin = commonNodesExport.exportPluginWithStats(nodeCtx, uvwGenEnv)

    return plugin


def exportVRayNodeUVWGenRandomizer(nodeCtx: NodeContext):
    global UVWGenRandomizerModes

    node  = nodeCtx.node

    pluginName = Names.treeNode(nodeCtx)
    pluginDesc = PluginDesc(pluginName, 'UVWGenRandomizer')
    propGroup = node.UVWGenRandomizer

    commonNodesExport.exportNodeTree(nodeCtx, pluginDesc)

    # If the 'input' socket is not connected, export with the default mapping
    if not node.inputs['Input'].is_linked:
        pluginDesc.setAttribute('input', exportDefaultUVWGenChannel(nodeCtx))

    # UVWGenRandomizer's 'mode' attribute is used as mask on which the first five bits
    # represent different modes.
    # In the UI they are shown as check boxes
    # and the code below fills the 'mode' mask based on their values.
    mode = 0
    for modeName, modeMask in UVWGenRandomizerModes.items():

        # Making sure that the checkbox for the given mode is checked
        # before setting the flag corresponding to it 
        if getattr(propGroup, modeName):
            mode |= modeMask

    pluginDesc.setAttribute('mode', mode)
    pluginDesc.vrayPropGroup = propGroup
    return commonNodesExport.exportPluginWithStats(nodeCtx, pluginDesc)


def exportVRayNodeUVWMapping(nodeCtx: NodeContext):
    node = nodeCtx.node

    uvwPluginType = MAPPING_TYPE_TO_UVW_PLUGIN[node.mapping_node_type]
    uvwPluginName = Names.nextVirtualNode(nodeCtx, uvwPluginType)
    uvwPluginDesc = PluginDesc(uvwPluginName, uvwPluginType)
    uvwPluginDesc.vrayPropGroup = getattr(node, uvwPluginType)

    commonNodesExport.exportNodeTree(nodeCtx, uvwPluginDesc)
    return commonNodesExport.exportPluginWithStats(nodeCtx, uvwPluginDesc)