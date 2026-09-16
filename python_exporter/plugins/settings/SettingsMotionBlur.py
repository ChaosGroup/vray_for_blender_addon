# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later


from vray_blender.lib import plugin_utils, export_utils, camera_utils
from vray_blender.lib.defs import ExporterContext

plugin_utils.loadPluginOnModule(globals(), __name__)

def exportCustom(ctx: ExporterContext, pluginDesc):
    # An enabled Velocity render element needs subframe motion data, but the beauty pass has to
    # stay sharp. Keep 'on' off while forcing the shutter interval V-Ray derives velocity from -
    # the values the user left in the disabled UI would not match the exported subframes.
    commonSettings = ctx.commonSettings

    if commonSettings.velocityMotionData and not commonSettings.hasCameraMotionBlur:
        pluginDesc.setAttribute('on', False)
        pluginDesc.setAttribute('camera_motion_blur', True)
        pluginDesc.setAttribute('duration', camera_utils.VELOCITY_MB_DURATION)
        pluginDesc.setAttribute('interval_center', camera_utils.VELOCITY_MB_INTERVAL_CENTER)

    return export_utils.exportPluginCommon(ctx, pluginDesc)
