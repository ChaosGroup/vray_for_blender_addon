# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later


from vray_blender.nodes.nodes import setUniqueRenderChannelName
from vray_blender.nodes.utils import getNodeOfPropGroup


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
    setUniqueRenderChannelName(getNodeOfPropGroup(propGroup), isNewNode = False)
