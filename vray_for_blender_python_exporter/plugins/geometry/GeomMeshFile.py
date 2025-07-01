import os

import bpy 
from vray_blender.lib.blender_utils import getShadowAttr, setShadowAttr
from vray_blender import debug
from vray_blender.lib import plugin_utils, export_utils
from vray_blender.lib.defs import PluginDesc
from vray_blender.vray_tools.vray_proxy import isAlembicFile

plugin_utils.loadPluginOnModule(globals(), __name__)



def onUpdatePreviewFile(src, context, attrName):
    assert attrName == 'file'
    
    geomMeshFile = context.active_object.data.vray.GeomMeshFile
    
    filePrevValue = getShadowAttr(geomMeshFile, 'file')
    
    if filePrevValue == geomMeshFile.file:
        return
    
    if not os.path.exists(bpy.path.abspath(geomMeshFile.file)):
        debug.reportError(f"File not found: {geomMeshFile.file}")
        geomMeshFile['file'] = filePrevValue
        return

    setShadowAttr(geomMeshFile, 'file', geomMeshFile.file)

    bpy.ops.vray.proxy_generate_preview('EXEC_DEFAULT', regenerate=False)


def onUpdatePreview(src, context, attrName):
    bpy.ops.vray.proxy_generate_preview('EXEC_DEFAULT', regenerate=True)


def exportCustom(exporterCtx, pluginDesc: PluginDesc):
    geomMeshFile = pluginDesc.vrayPropGroup
    pluginDesc.setAttribute('file', bpy.path.abspath(geomMeshFile.file))
    return export_utils.exportPluginCommon(exporterCtx, pluginDesc)


def isAlembicGet(propGroup, attrName):
    return isAlembicFile(propGroup.file)