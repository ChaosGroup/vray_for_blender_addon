# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later


from vray_blender.lib.defs import ExporterContext, PluginDesc
from vray_blender.lib import export_utils, plugin_utils

plugin_utils.loadPluginOnModule(globals(), __name__)


def exportCustom(ctx: ExporterContext, pluginDesc: PluginDesc):
    propGroup = pluginDesc.vrayPropGroup

    pluginDesc.setAttributes( {
        "generate_gi"         : propGroup.generate_gi if propGroup.use_generate_gi else 0.0,
        "receive_gi"          : propGroup.receive_gi if propGroup.use_receive_gi else 0.0,
        "generate_caustics"   : propGroup.generate_caustics if propGroup.use_generate_caustics else 0.0,
        "receive_caustics"    : propGroup.receive_caustics if propGroup.use_receive_caustics else 0.0
    })

    return export_utils.exportPluginCommon(ctx, pluginDesc)