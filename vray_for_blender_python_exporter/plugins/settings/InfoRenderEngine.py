# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later


import bpy

from vray_blender.lib.defs import ExporterContext, PluginDesc
from vray_blender.lib.settings_defs import RenderMode
from vray_blender.lib import export_utils
from vray_blender.lib import plugin_utils
from vray_blender import version
from vray_blender import debug



plugin_utils.loadPluginOnModule(globals(), __name__)


def exportCustom(ctx: ExporterContext, pluginDesc: PluginDesc):
    deviceType=''
    match ctx.commonSettings.renderMode:
        case RenderMode.Production | RenderMode.RtCpu:
            deviceType='cpu'
        case RenderMode.ProductionGpuCUDA | RenderMode.RtGpuCUDA:
            deviceType='cuda'
        case RenderMode.ProductionGpuOptiX | RenderMode.RtGpuOptiX:
            deviceType='rtx'
        case RenderMode.ProductionGpuMetal | RenderMode.RtGpuMetal:
            deviceType='metal'
        case _:
            debug.printError("Unknown engine type!")
    pluginDesc.setAttribute("engine_type", deviceType)
    pluginDesc.setAttribute("host", "Blender")
    pluginDesc.setAttribute("host_version", bpy.app.version_string)
    pluginDesc.setAttribute("exporter_version", f"{version.getBuildVersionString()}")
    pluginDesc.setAttribute("build_mod", "ADV")
    return export_utils.exportPluginCommon(ctx, pluginDesc)
