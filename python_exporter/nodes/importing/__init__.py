# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

""" Import machinery: .vrscene/.vrmat plugin dicts -> V-Ray node trees.

    What is re-exported here is the addon's NodesImport.*; an underscore name in engine.py or
    creators.py belongs to this package alone. engine.py holds the dict-to-node core,
    creators.py the per-plugin creators, cycles_convert.py the Cycles->V-Ray conversion.
"""

from vray_blender.vray_tools.import_common import IndexedVrsceneDict, getPluginByName, getPluginByType
from vray_blender.nodes.importing.engine import (
    ImportContext,
    createLightFromPluginDesc,
    createLinkedNode,
    createNode,
    fixPluginParams,
    getInputSocketNameByAttr,
    getPluginFromLink,
    isPluginLink,
    loadImage,
    pluginAttrsToNodeProps,
    pluginAttrsToPropGroup,
    scaleDistanceValue,
)
from vray_blender.nodes.importing.cycles_convert import convertMaterial, convertLight, convertWorld
