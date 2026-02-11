# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy

from vray_blender.exporting.tools import getInputSocketByAttr
from vray_blender.lib import  plugin_utils
from vray_blender.lib.lib_utils import  getLightPluginType
from vray_blender.exporting.tools import getFarNodeLink

plugin_utils.loadPluginOnModule(globals(), __name__)


def sunFilter(obj):
    # Filtering sun lights
    if (objData := obj.data) and isinstance(objData, bpy.types.Light):
        return getLightPluginType(objData) == 'SunLight'
    return False


def showBasicOptions(propGroup, node):
    if propGroup.sun_dir_only:
        return True

    if sunSock := getInputSocketByAttr(node, 'sun'):
        if sunLink := getFarNodeLink(sunSock):
            linkedNode = sunLink.from_node
            if linkedNode.bl_idname in ('VRayNodeMultiSelect', 'VRayNodeSelectObject') and \
                    linkedNode.getSelected(bpy.context):
                return False
            return True
        else:
            return True

    return propGroup.sun_select.boundPropObjName == ''