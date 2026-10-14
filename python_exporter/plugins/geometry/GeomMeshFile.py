# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import os

import bpy
from vray_blender import debug
from vray_blender.lib import plugin_utils, export_utils
from vray_blender.lib.blender_utils import getShadowAttr
from vray_blender.lib.defs import PluginDesc
from vray_blender.vray_tools.vray_proxy import isAlembicFile, loadVRayProxyPreviewMesh, applyProxyPreviewTransform

plugin_utils.loadPluginOnModule(globals(), __name__)


def onUpdatePreviewFile(src, context, attrName):
    assert attrName == 'file'
    geomMeshFile = src
    filePrevValue = getShadowAttr(geomMeshFile, 'file')

    if filePrevValue == geomMeshFile.file:
        return

    if not os.path.exists(bpy.path.abspath(geomMeshFile.file)):
        debug.reportError(f"File not found: {geomMeshFile.file}")
        geomMeshFile['file'] = filePrevValue
        return

    # loadVRayProxyPreviewMesh() fills the Object Hierarchy from the same dump that produced the
    # preview, so there is nothing to rebuild here.
    if err := loadVRayProxyPreviewMesh(geomMeshFile, geomMeshFile.file, context.scene.frame_current):
        debug.reportError(err)


def rebuildObjectHierarchy(geomMeshFile):
    """ Re-read the object list shown in the Object Hierarchy rollout by running the tool.

        Only for the paths that set the proxy file without loading a preview - the .vrscene
        import. Everywhere else the preview load already brings the names back with it.
    """
    if err := geomMeshFile.object_hierarchy.rebuild():
        debug.reportError(err)


def onUpdatePreview(src, context, attrName):
    # 'scale' and 'flip_axis' only reorient/rescale the existing preview geometry, so transform
    # the loaded mesh in place instead of regenerating it through the external tool (which reads
    # the whole source file and freezes the UI). 'previewType'/'num_preview_faces' change what
    # geometry the tool produces, so they still require a full regeneration.
    if (attrName in ('scale', 'flip_axis')):
        applyProxyPreviewTransform(src, context)  
    elif err := loadVRayProxyPreviewMesh(src, src.file, context.scene.frame_current):
        debug.reportError(err)


def exportCustom(exporterCtx, pluginDesc: PluginDesc):
    geomMeshFile = pluginDesc.vrayPropGroup

    pluginDesc.setAttribute('file', bpy.path.abspath(geomMeshFile.file, library=geomMeshFile.id_data.library))
    return export_utils.exportPluginCommon(exporterCtx, pluginDesc)


def isAlembicGet(propGroup, attrName):
    return isAlembicFile(propGroup.file)


def widgetDrawFile(context: bpy.types.Context, layout: bpy.types.UILayout, propGroup: bpy.types.PropertyGroup, widgetAttr):
    row = layout.row()
    row.prop(propGroup, 'file', text=widgetAttr.get('label', 'Mesh File'))
    
    op = row.operator('vray.proxy_path_browser', icon='FILE_FOLDER', text="")
    op.is_proxy = True
    op.mesh_name = context.active_object.data.name
