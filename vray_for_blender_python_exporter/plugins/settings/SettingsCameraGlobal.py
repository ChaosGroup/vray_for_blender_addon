# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

from vray_blender.lib import plugin_utils
from vray_blender import debug
import bpy


plugin_utils.loadPluginOnModule(globals(), __name__)


def reportAutoSettingsWarning(context):
    vrayScene = context.scene.vray
    if vrayScene.SettingsGI.secondary_engine == "3" and \
        (not vrayScene.SettingsGI.use_light_cache_for_interactive) and  \
        (vrayScene.SettingsCameraGlobal.auto_exposure != "0" or \
        vrayScene.SettingsCameraGlobal.auto_white_balance != False):
        
        debug.report(
            'WARNING',
            "Auto Exposure and Auto White Balance require Light Cache to be enabled."
        )
        


def updateAutoSettings(src, context: bpy.types.Context, attrName: str):
    from vray_blender.engine.renderer_ipr_viewport import VRayRendererIprViewport
    from vray_blender.engine.renderer_ipr_vfb import VRayRendererIprVfb
    
    propEnabled = src[attrName] not in ["0", False]
    for iprRenderer in (VRayRendererIprViewport, VRayRendererIprVfb):
        if iprRenderer.isActive() and propEnabled:
            reportAutoSettingsWarning(context)
            break