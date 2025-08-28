
from vray_blender.nodes.nodes import setUniqueRenderChannelName
from vray_blender.nodes.utils import getNodeOfPropGroup



def drawChannelType(layout, channelType):
    """ Draw a label with the channel type. """
    split = layout.split(factor=0.4)
    
    colLabel = split.column()
    colLabel.alignment='RIGHT'
    colLabel.label(text=f"Channel Type  ")
    
    split.column().label(text=channelType)
    layout.separator()


def widgetDrawChannelType(context, layout, propGroup, widgetAttr):
    assert widgetAttr['name'] == 'channelType'
    
    node = getNodeOfPropGroup(propGroup)
    drawChannelType(layout, node.bl_label)
    

def onUpdateChannelName(propGroup, context, attrName):
    setUniqueRenderChannelName(getNodeOfPropGroup(propGroup), isNewNode = False)
