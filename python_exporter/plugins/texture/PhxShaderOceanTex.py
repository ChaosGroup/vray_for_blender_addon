# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later


import bpy
from mathutils import Matrix

from vray_blender.exporting import node_export as commonNodesExport
from vray_blender.lib import plugin_utils
from vray_blender.lib.defs import NodeContext, PluginDesc
from vray_blender.lib.names import Names

plugin_utils.loadPluginOnModule(globals(), __name__)


_PLUGIN_TYPE = 'PhxShaderOceanTex'


def _getMetersScale():
    unitSettings = bpy.context.scene.unit_settings
    return unitSettings.scale_length if unitSettings.system != 'NONE' else 1.0


def exportTreeNode(nodeCtx: NodeContext):
    propGroup = nodeCtx.node.PhxShaderOceanTex
    pluginDesc = PluginDesc(Names.treeNode(nodeCtx), _PLUGIN_TYPE)
    pluginDesc.vrayPropGroup = propGroup

    commonNodesExport.exportNodeTree(nodeCtx, pluginDesc)

    # The plugin computes the effective wind speed as wind * meter_scale, so 'wind' is in
    # scene units per second. Convert from the m/s value shown in the UI, as vray4c4d does.
    if propGroup.usewind:
        metersScale = _getMetersScale()
        pluginDesc.setAttribute('wind', propGroup.wind / metersScale)
        pluginDesc.setAttribute('meter_scale', metersScale)
    else:
        pluginDesc.setAttribute('meter_scale', 1.0)

    pluginDesc.setAttribute('uvwgen', _exportUVWGenPlanarWorld(nodeCtx))

    return commonNodesExport.exportPluginWithStats(nodeCtx, pluginDesc)


def _exportUVWGenPlanarWorld(nodeCtx: NodeContext):
    """ The ocean texture is always mapped through a dedicated UVWGenPlanarWorld
        which is not exposed on the node.
    """
    uvwGenDesc = PluginDesc(Names.nextVirtualNode(nodeCtx, 'UVWGenPlanarWorld'), 'UVWGenPlanarWorld')

    # With wind enabled the wave size is derived from the wind speed and the mapping is left
    # at identity scale. Without wind the waves are scaled by the mapping instead.
    scale = 1.0 if nodeCtx.node.PhxShaderOceanTex.usewind else 0.01 * _getMetersScale()

    uvwGenDesc.setAttribute('uvw_transform', Matrix.Scale(scale, 4))
    return commonNodesExport.exportPluginWithStats(nodeCtx, uvwGenDesc)
