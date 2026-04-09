# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import os
import bpy
import pathlib

from vray_blender import debug

from vray_blender.lib.defs import ExporterContext, PluginDesc
from vray_blender.lib.mixin import VRayOperatorBase
from vray_blender.lib import export_utils, plugin_utils
from vray_blender.lib.blender_utils import getObjectFromEditorContext
from vray_blender.nodes.utils import getNodeOfPropGroup
from vray_blender.vray_tools.vrmat_parser import getMaterialNamesFromVRMatFile
from vray_blender.vray_tools.vrscene_parser import getMaterialNamesFromVRScene


plugin_utils.loadPluginOnModule(globals(), __name__)

class VRAY_MT_MaterialName(bpy.types.Menu):
    bl_label = "Select Material Name"
    bl_idname = "VRAY_MT_MaterialName"

    ma_list = []
    nodeName = ""
    mtlName = ""

    def draw(self, context):
        row = self.layout.row()
        sub = row.column()

        for i,maName in enumerate(self.ma_list):
            if i and i % 15 == 0:
                sub = row.column()
            op = sub.operator("vray.set_vrscene_material_name", text=maName)
            op.name     = maName
            op.nodeName = self.nodeName
            op.mtlName  = self.mtlName


class VRAY_OT_set_vrscene_material_name(VRayOperatorBase):
    bl_idname      = "vray.set_vrscene_material_name"
    bl_label       = "Set Material Name"
    bl_description = "Set material name from *.vrscene file"
    bl_options     = {'INTERNAL', 'UNDO'}

    name: bpy.props.StringProperty()
    nodeName: bpy.props.StringProperty()
    mtlName: bpy.props.StringProperty()

    def execute(self, context):
        node = bpy.data.materials[self.mtlName].node_tree.nodes[self.nodeName]
        if not node:
            return {'CANCELLED'}
        if node.bl_idname != "VRayNodeMtlVRmat":
            return {'CANCELLED'}

        MtlVRmat = node.MtlVRmat
        MtlVRmat.mtlname = self.name

        return {'FINISHED'}


def _getMaterialNamesFromMtlXFile(matlxFile):
    # Returns a list of material names from .mtlx file
    import xml.etree.ElementTree as ET

    # Parse the MaterialX XML file
    tree = ET.parse(matlxFile)
    root = tree.getroot()

    # Iterate through all elements and find those with type="material"
    return [element.get('name', "") for element in root.iter()  if element.get('type', "") == 'material']


def _getMaterialNamesFromMtlFile(fileName):
    # Returns a list of material names from a .vrscene, .vrmat or .mtlx file.
    filePath = os.path.normpath(bpy.path.abspath(fileName))

    if os.path.exists(filePath):
        match pathlib.Path(filePath).suffix:
            case ".vrscene":
                return getMaterialNamesFromVRScene(filePath)
            case ".vrmat" | ".vismat":
                return getMaterialNamesFromVRMatFile(filePath)
            case ".mtlx":
                return _getMaterialNamesFromMtlXFile(filePath)
    else:
        debug.printError(f"Mtl File '{filePath}' doesn't exist!")

    return []


class VRAY_OT_get_vrscene_material_name(VRayOperatorBase):
    bl_idname      = "vray.get_vrscene_material_name"
    bl_label       = "Get Material Name"
    bl_description = "Get material name from *.vrscene file"
    bl_options     = {'INTERNAL', 'UNDO'}

    nodeName: bpy.props.StringProperty()
    mtlName: bpy.props.StringProperty()
    mtlList: bpy.props.StringProperty()

    def execute(self, context):
        node = bpy.data.materials[self.mtlName].node_tree.nodes[self.nodeName]

        MtlVRmat = node.MtlVRmat
        if not MtlVRmat.filename:
            debug.printError("Filepath is not set!")
            return {'CANCELLED'}

        mtlList = self.mtlList.split(';')
        if not mtlList:
            return {'CANCELLED'}

        VRAY_MT_MaterialName.ma_list = mtlList
        VRAY_MT_MaterialName.nodeName = self.nodeName
        VRAY_MT_MaterialName.mtlName = self.mtlName
        bpy.ops.wm.call_menu(name=VRAY_MT_MaterialName.bl_idname)

        return {'FINISHED'}

def onFileNameAttributeUpdate(src, context, attrName):
    if not os.path.exists(bpy.path.abspath(src.filename)):
        src.materials_list = ''
        src.mtlname = ''
        return
    
    mtlList = sorted(_getMaterialNamesFromMtlFile(src.filename))
    src.mtlname = "" if not mtlList else mtlList[0]
    src.materials_list = ';'.join(mtlList)


def exportCustom(ctx: ExporterContext, pluginDesc: PluginDesc):
    propGroup = pluginDesc.vrayPropGroup
    pluginDesc.setAttribute("filename", os.path.normpath(bpy.path.abspath(propGroup.filename)))

    return export_utils.exportPluginCommon(ctx, pluginDesc)

def widgetDrawMaterialName(context: bpy.types.Context, layout: bpy.types.UILayout, propGroup, widgetAttr):
    row = layout.row()
    row.prop(propGroup, 'mtlname')
    contextObj = getObjectFromEditorContext(context)
    assert contextObj, "No valid context object"

    op = row.operator("vray.get_vrscene_material_name", text = "", icon="DOWNARROW_HLT")
    op.nodeName = getNodeOfPropGroup(propGroup).name
    op.mtlName  = contextObj.active_material.name
    op.mtlList = propGroup.materials_list


def getRegClasses():
    return (
        VRAY_MT_MaterialName,
        VRAY_OT_get_vrscene_material_name,
        VRAY_OT_set_vrscene_material_name,
    )


def register():
    for regClass in getRegClasses():
        bpy.utils.register_class(regClass)


def unregister():
    for regClass in getRegClasses():
        bpy.utils.unregister_class(regClass)
