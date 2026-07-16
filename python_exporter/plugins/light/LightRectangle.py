# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy

from vray_blender.lib import plugin_utils
plugin_utils.loadPluginOnModule(globals(), __name__)


def nodeUpdate(node: bpy.types.Node):
    if node.mute:
        node.mute = False


def onUpdateWidth(src, context, attrName):
    propGroup = src
    
    if propGroup.is_disc:
        propGroup.v_size = propGroup.u_size

def widgetDrawSize(context, layout, propGroup, widgetAttr):
    light = context.active_object
    attrName = widgetAttr['name']
    
    attr = 'size' if attrName == 'u_size' else  'size_y'
    layout.prop(light.data, attr, text=widgetAttr.get('label', attrName))
