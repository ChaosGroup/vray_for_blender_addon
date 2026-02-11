# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later


from vray_blender.lib.blender_utils import setShadowAttr, getShadowAttr
from vray_blender.lib import plugin_utils, export_utils
from vray_blender.lib.defs import PluginDesc, ExporterContext
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


def getSensorSizeProp(propGroup):
    verticalFit = camPropertyGet(propGroup, "sensor_fit") == 'VERTICAL'
    return "sensor_height" if verticalFit else "sensor_width"


def filmWidthGet(propGroup, attrName):
    if propGroup.specify_fov:
        return getShadowAttr(propGroup, attrName)

    return camPropertyGet(propGroup, getSensorSizeProp(propGroup))


def filmWidthSet(propGroup, attrName, value):
    if propGroup.specify_fov:
        setShadowAttr(propGroup, attrName, value)
    else:
        camPropertySet(propGroup, getSensorSizeProp(propGroup), value)


def onUpdateUseDof(propGroup, context, attrName):
    # Toggling of DoF cannot be animated. Re-export the whole plugin in order to apply 
    # the changes in interactive.
    ExporterContext.pluginsToRecreate.add("_cameraPhysical")


def exportCustom(exporterCtx, pluginDesc: PluginDesc):
    propGroup = pluginDesc.vrayPropGroup

    if not propGroup.enable_vignetting:
        pluginDesc.setAttribute("vignetting", 0)

    return export_utils.exportPluginCommon(exporterCtx, pluginDesc)
