# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import os

import bpy
from vray_blender import debug
from vray_blender.lib import plugin_utils, export_utils
from vray_blender.lib.blender_utils import getShadowAttr
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

    bpy.ops.vray.proxy_generate_preview('EXEC_DEFAULT')


def onUpdatePreview(src, context, attrName):
    bpy.ops.vray.proxy_generate_preview('EXEC_DEFAULT')


def exportCustom(exporterCtx, pluginDesc: PluginDesc):
    geomMeshFile = pluginDesc.vrayPropGroup

    pluginDesc.setAttribute('file', bpy.path.abspath(geomMeshFile.file))
    return export_utils.exportPluginCommon(exporterCtx, pluginDesc)


def isAlembicGet(propGroup, attrName):
    return isAlembicFile(propGroup.file)


def widgetDrawFile(context: bpy.types.Context, layout: bpy.types.UILayout, propGroup: bpy.types.PropertyGroup, widgetAttr):
    row = layout.row()
    row.prop(propGroup, 'file', text=widgetAttr.get('label', 'Mesh File'))
    
    op = row.operator('vray.proxy_path_browser', icon = 'FILE_FOLDER', text="")
    op.is_proxy = True
    op.object_name = context.active_object.name
