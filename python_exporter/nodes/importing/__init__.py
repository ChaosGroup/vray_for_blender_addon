# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

""" Import machinery: .vrscene/.vrmat plugin dicts -> V-Ray node trees.

    The public surface of the former nodes/importing.py module. engine.py holds the
    dict-to-node core, creators.py the per-plugin node creators, cycles_convert.py the
    Cycles->V-Ray conversion feature built on the same engine.
"""

from vray_blender.vray_tools.import_common import IndexedVrsceneDict, getPluginByName, getPluginByType
from vray_blender.nodes.importing.engine import (
    ImportContext,
    createNode,
    createLightFromPluginDesc,
    fixPluginParams,
    getInputSocketNameByAttr,
    loadImage,
    scaleDistanceValue,
    _createLinkedNode,
    _getPluginFromLink,
    _isPluginLink,
    _pluginAttrsToNodeProps,
    _pluginAttrsToPropGroup,
)
from vray_blender.nodes.importing.cycles_convert import convertMaterial, convertLight, convertWorld
