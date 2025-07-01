import os

import bpy 
from vray_blender.lib.blender_utils import hasShadowedAttrChanged, updateShadowAttr
from vray_blender import debug
from vray_blender.lib import plugin_utils, export_utils
from vray_blender.lib.defs import PluginDesc

plugin_utils.loadPluginOnModule(globals(), __name__)


def onUpdatePreviewFile(src, context, attrName):
    assert attrName == 'file'
    
    vrayScene = context.active_object.data.vray.VRayScene
    
    if not hasShadowedAttrChanged(vrayScene, 'filepath'):
        return
    
    updateShadowAttr(vrayScene, 'filepath')
    
    if not os.path.exists(bpy.path.abspath(vrayScene.filepath)):
        debug.reportError(f"File not found: {vrayScene.filepath}")
        return
    
    bpy.ops.vray.vrayscene_generate_preview('EXEC_DEFAULT', regenerate=False)


def onUpdatePreview(src, context, attrName):
    bpy.ops.vray.vrayscene_generate_preview('EXEC_DEFAULT', regenerate=True)


def exportCustom(exporterCtx, pluginDesc: PluginDesc):
    vrayScene = pluginDesc.vrayPropGroup
    pluginDesc.setAttribute('filepath', bpy.path.abspath(vrayScene.filepath))
    return export_utils.exportPluginCommon(exporterCtx, pluginDesc)
