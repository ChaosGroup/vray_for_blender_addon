from vray_blender.lib.defs import ExporterContext, PluginDesc, AttrPlugin
from vray_blender.lib import  export_utils, plugin_utils
from vray_blender.lib.lib_utils import  getLightPluginType
from vray_blender.bin import VRayBlenderLib as vray
import bpy

plugin_utils.loadPluginOnModule(globals(), __name__)

def sunFilter(self, obj):
    # Filtering sun lights
    if (objData := obj.data) and isinstance(objData, bpy.types.Light):
        return getLightPluginType(objData, objData.vray) == 'SunLight'
    return False

def exportCustom(ctx: ExporterContext, pluginDesc: PluginDesc):
    sunAttr = plugin_utils.objectToAttrPlugin(pluginDesc.vrayPropGroup.sun)

    # Only the attributes 'sun' and 'invisible' should be exported 
    # if the sun is set and 'sun_dir_only' disabled
    if pluginDesc.vrayPropGroup.sun and not pluginDesc.vrayPropGroup.sun_dir_only:
        vray.pluginCreate(ctx.renderer, pluginDesc.name, pluginDesc.type)
        plugin_utils.updateValue(ctx.renderer, pluginDesc.name, 'sun', sunAttr)
        plugin_utils.updateValue(ctx.renderer, pluginDesc.name, 'invisible', pluginDesc.vrayPropGroup.invisible)

        return AttrPlugin(pluginDesc.name)

    pluginDesc.setAttribute('sun', sunAttr)

    return export_utils.exportPluginCommon(ctx, pluginDesc)