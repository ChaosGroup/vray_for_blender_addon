from vray_blender.lib import plugin_utils
from vray_blender.nodes.utils import getNodeOfPropGroup
from vray_blender.nodes.sockets import addInput
from vray_blender.exporting.tools import getInputSocketByName, getVRayBaseSockType
from vray_blender.nodes.utils import selectedObjectTagUpdate

plugin_utils.loadPluginOnModule(globals(), __name__)

def _addNewTexSocket(node, texSock, newSockType):
    if getVRayBaseSockType(texSock) != newSockType:
        
        if texSock is not None:
            node.inputs.remove(texSock)
        
        addInput(node, newSockType, "Texture")


def onUpdateDisplacementType(src, context, attrName):
    node = getNodeOfPropGroup(src)
    texSock = getInputSocketByName(node, "Texture")
    selectedObjectTagUpdate(node, context)

    match src.type:
        case '0' |'1': # Normal | 2D
            _addNewTexSocket(node, texSock, 'VRaySocketFloatColor')
                
        case '2' | '3' | '4': # Vector | Vector (Absolute) | Vector (Object)
            _addNewTexSocket(node, texSock, 'VRaySocketColor')