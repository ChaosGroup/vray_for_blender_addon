# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

from vray_blender.exporting.tools import GEOMETRY_OBJECT_TYPES
from vray_blender.lib.defs import ExporterContext, PluginDesc
from vray_blender.lib import  export_utils, plugin_utils


plugin_utils.loadPluginOnModule(globals(), __name__)

def exportCustom(ctx: ExporterContext, pluginDesc: PluginDesc):
    propGroup = pluginDesc.vrayPropGroup

    if propGroup.mask_type == '0':
        # MaskType == 'White'
        pluginDesc.setAttribute('red_id',  propGroup.red_id)
        pluginDesc.setAttribute('green_id',  propGroup.red_id)
        pluginDesc.setAttribute('blue_id',  propGroup.red_id)

    pluginDesc.setAttribute('use_mtl_id',  propGroup.id_source == '1')

    return export_utils.exportPluginCommon(ctx, pluginDesc)