# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later


from vray_blender.exporting.tools import getInputSocketByAttr
from vray_blender.lib import plugin_utils
from vray_blender.nodes.utils import getNodeOfPropGroup

plugin_utils.loadPluginOnModule(globals(), __name__)


def onColorModeUpdate(fastSSSMtl, context, attrName):
    node = getNodeOfPropGroup(fastSSSMtl)
    
    sockConfig = {
        'sub_surface_color': ["Sub-surface Color", "Scatter Coefficient"],
        'scatter_radius':    ["Scatter Color", "Fog Color"]
    }

    for sockName in sockConfig:
        sock  = getInputSocketByAttr(node, sockName)
        sock.name = sockConfig[sockName][int(fastSSSMtl.color_mode)]
