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
    propGroup = getUpdateCallbackPropertyContext(src, 'LightRectangle').propGroup
    convertIntensityForUnitChange(propGroup, 'LightRectangle', context)


def onUpdateWidth(src, context, attrName):
    propGroup = src

    if propGroup.is_disc:
        propGroup.v_size = propGroup.u_size

def widgetDrawSize(context, layout, propGroup, widgetAttr):
    light = context.active_object
    attrName = widgetAttr['name']

    attr = 'size' if attrName == 'u_size' else  'size_y'
    layout.prop(light.data, attr, text=widgetAttr.get('label', attrName))
