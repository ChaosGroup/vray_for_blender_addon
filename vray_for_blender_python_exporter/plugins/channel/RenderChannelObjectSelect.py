# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

from vray_blender.exporting.tools import GEOMETRY_OBJECT_TYPES
from vray_blender.lib.defs import ExporterContext, PluginDesc
from vray_blender.lib import  export_utils, plugin_utils


plugin_utils.loadPluginOnModule(globals(), __name__)

def _parseIDsList(idsString):
    idsList = []

    # 'idsString' represents a list of numbers and ranges.
    # Each element is separated by commas
    # and can either be a single number or a range of numbers denoted by hyphens
    # Example string: '1,2,7,4-6'
    for id in idsString.split(','):
        if '-' in id and idsString.count('-') == 1:
            start, end = id.split('-')
            if start.isnumeric() and end.isnumeric():
                idsList.extend(range(int(start), int(end) + 1))
        elif id.isnumeric():
            idsList.append(int(id))

    return idsList

def _generateIDRangeList(idMin, idMax):
    if idMin <= idMax:
        return [i for i in range(idMin, idMax + 1)]
    return [idMin]


def exportCustom(ctx: ExporterContext, pluginDesc: PluginDesc):
    propGroup = pluginDesc.vrayPropGroup

    # NOTE: Only use the 'ids' property in order not to have to reset it explicitly if there is 
    # just 1 ID selected
    match propGroup.id_selection_mode:
        case '0': # Single ID
            pluginDesc.setAttribute('ids',  [propGroup.id])
        case '1': # ID Range
            pluginDesc.setAttribute('ids', _generateIDRangeList(propGroup.id_min, propGroup.id_max))
        case '2': # Custom list of IDs
            pluginDesc.setAttribute('ids',  _parseIDsList(propGroup.light_object_ids_as_string))

    return export_utils.exportPluginCommon(ctx, pluginDesc)