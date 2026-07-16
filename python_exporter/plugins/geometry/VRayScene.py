# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import os

import bpy 
from vray_blender import debug
from vray_blender.lib import plugin_utils, export_utils
from vray_blender.lib.blender_utils import hasShadowedAttrChanged, getShadowAttr, updateShadowAttr, isViewportRenderMode
from vray_blender.lib.defs import PluginDesc

plugin_utils.loadPluginOnModule(globals(), __name__)


def isEditEnabled(propGroup: dict, node: bpy.types.Node):
    return not isViewportRenderMode()


def onUpdatePreviewFile(src, context: bpy.types.Context, attrName: str):
    assert attrName == 'filepath'
    
    vrayScene = context.active_object.data.vray.VRayScene
    filePrevValue = getShadowAttr(vrayScene, 'filepath')

    if filePrevValue == vrayScene.filepath:
        return
    
    if not os.path.exists(bpy.path.abspath(vrayScene.filepath)):
        debug.reportError(f"File not found: {vrayScene.filepath}")
        vrayScene['filepath'] = filePrevValue
        return
    
    bpy.ops.vray.vrayscene_generate_preview('EXEC_DEFAULT')


def onUpdatePreview(src, context: bpy.types.Context, attrName: str):
    bpy.ops.vray.vrayscene_generate_preview('EXEC_DEFAULT')


def exportCustom(exporterCtx, pluginDesc: PluginDesc):
    from vray_blender.exporting.mtl_export import MtlExporter

    vrayScene = pluginDesc.vrayPropGroup
    
    pluginDesc.setAttribute('filepath', bpy.path.abspath(vrayScene.filepath))
    

    if (overrideMtl := bpy.data.materials.get(vrayScene.material_override)) and (overrideMtl.node_tree is not None):
        mtlExporter = MtlExporter(exporterCtx)
        mtlPlugin, _ = mtlExporter.exportMtl(overrideMtl)
        pluginDesc.setAttribute('material_override', mtlPlugin)
    else:
        pluginDesc.resetAttribute('material_override')


    return export_utils.exportPluginCommon(exporterCtx, pluginDesc)


def widgetDrawFilepath(context: bpy.types.Context, layout: bpy.types.UILayout, propGroup: bpy.types.PropertyGroup, widgetAttr):
    row = layout.row()
    row.prop(propGroup, 'filepath', text=widgetAttr.get('label', 'File Path'))
    op = row.operator('vray.proxy_path_browser', icon = 'FILE_FOLDER', text="")
    op.is_proxy = False
    op.object_name = context.active_object.name