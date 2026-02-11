# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy

from vray_blender.lib import plugin_utils, export_utils
from vray_blender.lib.defs import  ExporterContext, PluginDesc
from vray_blender.plugins.light.light_tools import onUpdateColorTemperature


plugin_utils.loadPluginOnModule(globals(), __name__)


def nodeUpdate(node: bpy.types.Node):
    if node.mute:
        node.mute = False

        
def onUpdateAttribute(src, context: bpy.types.Context, attrName):
    onUpdateColorTemperature(src, 'SunLight', attrName, colorModeAttrName='color_temp_mode', colorAttrName='filter_color', combinedColorAttrName='filter_color')


def exportCustom(ctx: ExporterContext, pluginDesc: PluginDesc):
    if ctx.commonSettings.isGpu and ctx.interactive:
        # During GPU IPR, changes made to cloud properties are not applied automatically.
        # Toggling SunLight.enabled forces these changes to apply.
        sunLightEnabled = pluginDesc.node.SunLight.enabled if pluginDesc.node else pluginDesc.vrayPropGroup.enabled
        plugin_utils.updateValue(ctx.renderer, pluginDesc.name, "enabled", not sunLightEnabled)

    return export_utils.exportPluginCommon(ctx, pluginDesc)