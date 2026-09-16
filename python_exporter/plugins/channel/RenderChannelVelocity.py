# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

from vray_blender.lib.defs import ExporterContext, PluginDesc
from vray_blender.lib import export_utils, plugin_utils


plugin_utils.loadPluginOnModule(globals(), __name__)

# V-Ray writes the largest velocity it has seen into this parameter at the end of each
# frame. We show it in the UI as a read-only value so that the user can pick a matching
# 'max_velocity'.
MAX_VELOCITY_LAST_FRAME = 'max_velocity_last_frame'


def exportCustom(ctx: ExporterContext, pluginDesc: PluginDesc):
    plugin = export_utils.exportPluginCommon(ctx, pluginDesc)

    # 'max_velocity_last_frame' is a pure output - V-Ray never reads it - but its write is
    # skipped unless the parameter exists on the plugin instance, and AppSDK only creates
    # it when a value is set. So send one, exactly like the Maya exporter does. The value
    # itself is irrelevant. The parameter stays in 'excluded_parameters' so that the
    # regular export does not touch it and no node socket is created for it - the UI then
    # keeps showing the property group value we write back after the render.
    plugin_utils.updateValue(ctx.renderer, pluginDesc.name, MAX_VELOCITY_LAST_FRAME, 0.0,
                             animatable=False)

    return plugin
