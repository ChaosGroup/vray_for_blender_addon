import bpy

from vray_blender.exporting.tools import getInputSocketByAttr, getLinkedFromSocket
from vray_blender.lib import  export_utils, plugin_utils
from vray_blender.lib.defs import ExporterContext, PluginDesc, AttrPlugin
from vray_blender.lib.lib_utils import  getLightPluginType
from vray_blender.nodes.tools import isInputSocketLinked
from vray_blender.nodes.utils import getNodeOfPropGroup

from vray_blender.bin import VRayBlenderLib as vray

plugin_utils.loadPluginOnModule(globals(), __name__)


def sunFilter(obj):
    # Filtering sun lights
    if (objData := obj.data) and isinstance(objData, bpy.types.Light):
        return getLightPluginType(objData) == 'SunLight'
    return False

def exportCustom(ctx: ExporterContext, pluginDesc: PluginDesc):
    # The 'sun' attribute may have been already exported from a linked selector node
    sunAttr = pluginDesc.getAttribute('sun')
    
    hasObjSelector = False
    sunFromNode = None

    if node := pluginDesc.node:
        if (sunSock := getInputSocketByAttr(node, 'sun')) and isInputSocketLinked(sunSock):
            linkedFromSock = getLinkedFromSocket(sunSock)
            sunFromNode = linkedFromSock.node
            hasObjSelector = sunFromNode.bl_idname in ('VRayNodeMultiSelect', 'VRayNodeSelectObject')

            if (sunFromNode.bl_idname == 'VRayNodeMultiSelect') and (type(sunAttr) is list):
                # MultiObject selector returns a list of objects, select the first sun on the list
                sunAttr = next((i for i in sunAttr if i.pluginType == 'SunLight'), None)

    if not hasObjSelector:
        sunAttr = plugin_utils.objectToAttrPlugin(pluginDesc.vrayPropGroup.sun)
        pluginDesc.setAttribute('sun', sunAttr)
    elif (sunAttr is None) or sunAttr.isEmpty():
        # If object selector has no sun(s), reset the attribute in order to not 
        # use any sun object set directly in the TexSky node.
        pluginDesc.resetAttribute('sun')
    elif sunFromNode.bl_idname == 'VRayNodeMultiSelect':
        # Replace the sun attribute with the first item on the MultiSelect's list
        pluginDesc.setAttribute('sun', sunAttr)

    return export_utils.exportPluginCommon(ctx, pluginDesc)