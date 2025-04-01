
from vray_blender.lib.blender_utils import setShadowAttr, hasShadowedAttrChanged
from vray_blender.lib import plugin_utils, export_utils
from vray_blender.lib.defs import PluginDesc
from vray_blender.bin import VRayBlenderLib as vray

plugin_utils.loadPluginOnModule(globals(), __name__)

def camPropertyGet(propGroup, campProp):     
    if camera := propGroup.id_data:
        return getattr(camera, campProp)
    return None
    

def camPropertySet(propGroup, campProp, value):
    if camera := propGroup.id_data:
        if getattr(camera, campProp) != value:
            setattr(camera, campProp, value)


def focalLengthGet(propGroup, attrName):
    return camPropertyGet(propGroup, "lens")
    

def focalLengthSet(propGroup, attrName, value):
    camPropertySet(propGroup, "lens", value)


def fovGet(propGroup, attrName):
    return camPropertyGet(propGroup, "angle")
    

def fovSet(propGroup, attrName, value):
    camPropertySet(propGroup, "angle", value)
    

def exportCustom(exporterCtx, pluginDesc: PluginDesc):
    propGroup = pluginDesc.vrayPropGroup

    if hasShadowedAttrChanged(propGroup, 'use_dof'):
        setShadowAttr(propGroup, 'use_dof', propGroup.use_dof)
        # DOF enable/disable cannot be animated so we need to re-export the whole plugin.
        vray.pluginRemove(exporterCtx.renderer, pluginDesc.name)

    return export_utils.exportPluginCommon(exporterCtx, pluginDesc)
