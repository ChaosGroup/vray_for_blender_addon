# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later


from vray_blender.lib.draw_utils import getAttrLabel
from vray_blender.nodes.nodes import setUniqueRenderChannelName
from vray_blender.nodes.utils import getNodeOfPropGroup
from vray_blender.plugins import getPluginModule


def drawChannelType(layout, channelType):
    """ Draw a label with the channel type. """
    box = layout.box()
    split = box.split(factor=0.4)

    split.column()
    split.column().label(text=f'  {channelType}')
    layout.separator()


def widgetDrawChannelType(context, layout, propGroup, widgetAttr):
    assert widgetAttr['name'] == 'channelType'

    node = getNodeOfPropGroup(propGroup)
    drawChannelType(layout, node.bl_label)


def onUpdateChannelName(propGroup, context, attrName):
    setUniqueRenderChannelName(getNodeOfPropGroup(propGroup), isNewNode=False)


def widgetDrawLightingAnalysisUpdate(context, layout, propGroup, widgetAttr):
    """ 'Update' button on the Lighting Analysis render-channel node: re-applies the analysis
        to the current VFB render without re-rendering (the same operator the Render Channels
        panel used). Lighting Analysis is a post-process over the rendered data, so its
        parameters can be re-applied without a new render. """
    layout.operator("vray.update_lighting_analysis", text="Update", icon='FILE_REFRESH')


def widgetDrawLightPathExpression(context, layout, propGroup, widgetAttr):
    """ "custom_draw" function for the light_path_expression attribute that also draws a button
        opening the online LPE builder, on the same row as the expression it composes. """
    attrLabel = getAttrLabel(getPluginModule('RenderChannelLightSelect'), widgetAttr, propGroup, node=None)

    row = layout.row(align=True)
    row.prop(propGroup, widgetAttr['name'], text=attrLabel)

    op = row.operator("vray.url_open", text="", icon='URL')
    op.url = "https://lpe-builder.chaosgroup.com/"
    op.description = "Open the Light Path Expression builder in a web browser"
