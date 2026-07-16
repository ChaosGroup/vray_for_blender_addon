# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later


from vray_blender.lib import blender_utils
from vray_blender.lib import plugin_utils

plugin_utils.loadPluginOnModule(globals(), __name__)


def nodeDraw(context, layout, node):
    propGroup = node.TexMeshVertexColorChannel
    ob = context.object

    layout.prop(propGroup, 'channelIndex')
    layout.prop(propGroup, 'data_select', expand=True)

    if ob and ob.type not in blender_utils.NonGeometryTypes:
        hasUvChannles = hasattr(ob.data, 'uv_textures')
        hasColorSets  = hasattr(ob.data, 'vertex_colors')

        if propGroup.data_select == '0':
            if hasUvChannles:
                layout.prop_search(propGroup, 'channel_name',
                                   ob.data, 'uv_textures',
                                   text="")
        else:
            if hasColorSets:
                layout.prop_search(propGroup, 'channel_name',
                                   ob.data, 'vertex_colors',
                                   text="")
    else:
        layout.prop(propGroup, 'channel_name', text="Name")
