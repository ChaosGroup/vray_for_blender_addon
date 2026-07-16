# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy

from vray_blender.lib import plugin_utils
from vray_blender.nodes.utils import getUpdateCallbackPropertyContext

plugin_utils.loadPluginOnModule(globals(), __name__)


def nodeUpdate(node: bpy.types.Node):
    if node.mute:
        node.mute = False


def onUpdateUnits(src, context, attrName):
    from vray_blender.plugins.light.light_tools import convertIntensityForUnitChange
    propGroup = getUpdateCallbackPropertyContext(src, 'LightOmni').propGroup
    convertIntensityForUnitChange(propGroup, 'LightOmni', context)
