# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later


from vray_blender.lib import plugin_utils, export_utils
from vray_blender.lib.defs import ExporterContext
from vray_blender.exporting.view_export import RENDER_CAMERA_BASE_NAME, getActiveCamera, cameraSceneName

plugin_utils.loadPluginOnModule(globals(), __name__)

def exportCustom(ctx: ExporterContext, pluginDesc):
    # SettingsCameraDof is a scene-level singleton, so its scene_name is tied to the
    # active camera. During rendering the fixed render camera scene name is used instead.
    scene_name = cameraSceneName(getActiveCamera(ctx)) if ctx.exportOnly else RENDER_CAMERA_BASE_NAME
    pluginDesc.setAttribute('scene_name', scene_name)
    return export_utils.exportPluginCommon(ctx, pluginDesc)
