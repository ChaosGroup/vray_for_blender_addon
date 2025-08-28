
import os
import bpy
import pathlib

from vray_blender.vray_tools.vrscene_parser import getMaterialNamesFromVRScene
from vray_blender.vray_tools.vrmat_parser     import getMaterialNamesFromVRMatFile

from vray_blender.lib.defs import ExporterContext, PluginDesc
from vray_blender.lib.mixin import VRayOperatorBase
from vray_blender.lib import export_utils

from vray_blender.lib import plugin_utils
from vray_blender import debug

plugin_utils.loadPluginOnModule(globals(), __name__)

class VRAY_MT_MaterialName(bpy.types.Menu):
    bl_label = "Select Material Name"
    bl_idname = "VRAY_MT_MaterialName"

    ma_list = []

    def draw(self, context):
        row = self.layout.row()
        sub = row.column()

        for i,maName in enumerate(self.ma_list):
            if i and i % 15 == 0:
                sub = row.column()
            sub.operator("vray.set_vrscene_material_name", text=maName).name = maName


class VRAY_OT_set_vrscene_material_name(VRayOperatorBase):
    bl_idname      = "vray.set_vrscene_material_name"
    bl_label       = "Set Material Name"
    bl_description = "Set material name from *.vrscene file"
    bl_options     = {'INTERNAL'}

    name: bpy.props.StringProperty()

    def execute(self, context):
        node = context.active_node
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
    bl_options     = {'INTERNAL'}
    
    node = None

    def execute(self, context):
        if not context.node:
            debug.printError("No active node!")
            return {'CANCELLED'}

        if context.node.bl_idname != "VRayNodeMtlVRmat":
            debug.printError("Selected node is not of type VRayNodeMtlVRmat!")
            return {'CANCELLED'}

        MtlVRmat = context.node.MtlVRmat
        if not MtlVRmat.filename:
            debug.printError("Filepath is not set!")
            return {'CANCELLED'}


        mtlList = _getMaterialNamesFromMtlFile(MtlVRmat.filename)
        if not mtlList:
            return {'CANCELLED'}

        VRAY_MT_MaterialName.ma_list = mtlList
        bpy.ops.wm.call_menu(name=VRAY_MT_MaterialName.bl_idname)

        return {'FINISHED'}

def onFileNameAttributeUpdate(src, context, attrName):
    mtlList = _getMaterialNamesFromMtlFile(src.filename)
    src.mtlname = "" if not mtlList else mtlList[0]

def nodeDraw(context, layout, node):
    propGroup = node.MtlVRmat
    split = layout.split(factor=0.2, align=True)
    split.column().label(text="File:")
    split.column().prop(propGroup, 'filename', text="")

    split = layout.split(factor=0.2, align=True)
    split.column().label(text="Material Name:")
    row = split.column().row(align=True)
    row.prop(propGroup, 'mtlname', text="")
    row.operator("vray.get_vrscene_material_name", text="Select Material")


def exportCustom(ctx: ExporterContext, pluginDesc: PluginDesc):
    propGroup = pluginDesc.vrayPropGroup
    pluginDesc.setAttribute("filename", os.path.normpath(bpy.path.abspath(propGroup.filename)))

    return export_utils.exportPluginCommon(ctx, pluginDesc)


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
