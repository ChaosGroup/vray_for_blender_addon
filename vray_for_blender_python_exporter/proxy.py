# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later


import os
from bpy_extras.io_utils import ImportHelper

import bpy

from vray_blender import debug
from vray_blender.vray_tools import vray_proxy
from vray_blender.lib.mixin import VRayOperatorBase


VRAY_SCENE_FILTER_GLOB ="*.vrscene;*.usd;*.usda;*.usdc;*.usdz"
VRAY_PROXY_FILTER_GLOB ="*.vrmesh;*.abc"

class VRAY_OT_object_rotate_to_flip(VRayOperatorBase):
    bl_idname      = "vray.object_rotate_to_flip"
    bl_label       = "Rotate Object"
    bl_description = "Rotate object to flip axis"

    def execute(self, context):
        bpy.ops.transform.rotate(value=1.5708,
            axis=(1, 0, 0),
            constraint_axis=(True, False, False),
            constraint_orientation='GLOBAL',
            mirror=False,
            proportional='DISABLED'
        )

        return {'FINISHED'}


class VRAY_OT_vrayscene_load_preview(VRayOperatorBase):
    bl_idname      = "vray.vrayscene_load_preview"
    bl_label       = "Load VRayScene Preview"
    bl_description = "Load VRayScene preview from *.vrscene file"

    def execute(self, context):
        ob = context.object
        filepath = ob.data.vray.VRayScene.filepath

        if err := vray_proxy.loadVRayScenePreviewMesh(ob, filepath):
            debug.report('ERROR', err)
            return {'CANCELLED'}

        return {'FINISHED'}


class VRAY_OT_proxy_load_preview(VRayOperatorBase):
    bl_idname      = "vray.proxy_load_preview"
    bl_label       = "Load Preview"
    bl_description = "Load VRayProxy preview from .vrmesh or .abc file"

    def execute(self, context):
        geomMeshFile  = context.active_object.data.vray.GeomMeshFile
        proxyFilepath = bpy.path.abspath(geomMeshFile.file)

        if not proxyFilepath:
            self.report({'ERROR'}, "Proxy filepath is not set!")
            return {'FINISHED'}

        if not os.path.exists(proxyFilepath):
            return {'FINISHED'}

        err = vray_proxy.loadVRayProxyPreviewMesh(context.object, context.scene.frame_current-1)

        if err is not None:
            self.report({'ERROR'}, err)
            return {'CANCELLED'}

        return {'FINISHED'}


class VRAY_OT_proxy_generate_preview(VRayOperatorBase):
    bl_idname      = "vray.proxy_generate_preview"
    bl_label       = "Generate VRayProxy Preview"
    bl_description = "Generate preview mesh for a VRayProxy object and load it into the scene"

    def execute(self, context):
        ob = context.object
        geomMeshFile = ob.data.vray.GeomMeshFile

        # Default the preview to the original mesh file
        previewFilePath = bpy.path.abspath(geomMeshFile.file)

        if not (os.path.exists(previewFilePath)):
            debug.reportError(f"File not found: {geomMeshFile.file} [resolves to {previewFilePath}]")
            return {'CANCELLED'}

        if err := vray_proxy.loadVRayProxyPreviewMesh(ob, geomMeshFile.file, context.scene.frame_current):
            debug.reportError(err)
            return {'CANCELLED'}

        return {'FINISHED'}


class VRAY_OT_vrayscene_generate_preview(VRayOperatorBase):
    bl_idname      = "vray.vrayscene_generate_preview"
    bl_label       = "Generate VRayScene Preview"
    bl_description = "Generate preview mesh for a VRayScene object and load it into the scene"

    def execute(self, context):
        ob  = context.object
        vrayScene = ob.data.vray.VRayScene

        if not (sceneFilepath := bpy.path.abspath(vrayScene.filepath)):
            self.report({'ERROR'}, "Scene filepath is not set!")
            return {'CANCELLED'}

        if err:= vray_proxy.loadVRayScenePreviewMesh(ob, sceneFilepath):
            debug.report('ERROR', err)
            return {'CANCELLED'}

        return {'FINISHED'}



class VRAY_OT_proxy_path_browser(bpy.types.Operator, ImportHelper):
    bl_idname = "vray.proxy_path_browser"
    bl_label = "Select File"
    bl_description = "Show a file browser for selecting the path to a V-Ray Proxy or V-Ray Scene compatibe file"

    object_name: bpy.props.StringProperty(default="", options={'HIDDEN'})
    filter_glob: bpy.props.StringProperty(default="", options={'HIDDEN'})
    
    relative_path: bpy.props.BoolProperty(
        name="Relative Path",
        description="Use Relative Path",
        default=True,
    )

    is_proxy: bpy.props.BoolProperty(
        default=True, 
        description="True for V-Ray Proxy, False for V-Ray Scene",
        options={'HIDDEN'}
    )

    filepath: bpy.props.StringProperty(subtype='FILE_PATH', default="")

    def execute(self, context):
        if self.is_proxy:
            bpy.data.meshes[self.object_name].vray.GeomMeshFile.file = self.filepath
        else:
            bpy.data.meshes[self.object_name].vray.VRayScene.filepath = self.filepath

        return {'FINISHED'}
    
    def invoke(self, context, event):
        # Set the initial path to the file browser
        if self.is_proxy:
            filePath = bpy.data.meshes[self.object_name].vray.GeomMeshFile.file
        else:
            filePath = bpy.data.meshes[self.object_name].vray.VRayScene.filepath
        
        absPath = bpy.path.abspath(filePath)
        if os.path.exists(absPath):
            self.filepath = absPath
        else:
            # Trying to set an invalid path to the file browser crashes Blender.
            # Set a fake name to just give user a hint
            self.filepath = ""

        self.filter_glob = VRAY_PROXY_FILTER_GLOB if self.is_proxy else VRAY_SCENE_FILTER_GLOB 

        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}


########  ########  ######   ####  ######  ######## ########     ###    ######## ####  #######  ##    ##
##     ## ##       ##    ##   ##  ##    ##    ##    ##     ##   ## ##      ##     ##  ##     ## ###   ##
##     ## ##       ##         ##  ##          ##    ##     ##  ##   ##     ##     ##  ##     ## ####  ##
########  ######   ##   ####  ##   ######     ##    ########  ##     ##    ##     ##  ##     ## ## ## ##
##   ##   ##       ##    ##   ##        ##    ##    ##   ##   #########    ##     ##  ##     ## ##  ####
##    ##  ##       ##    ##   ##  ##    ##    ##    ##    ##  ##     ##    ##     ##  ##     ## ##   ###
##     ## ########  ######   ####  ######     ##    ##     ## ##     ##    ##    ####  #######  ##    ##

def getRegClasses():
    return (
        VRAY_OT_proxy_load_preview,
        VRAY_OT_proxy_generate_preview,
        VRAY_OT_proxy_path_browser,

        VRAY_OT_vrayscene_load_preview,
        VRAY_OT_vrayscene_generate_preview,
        VRAY_OT_object_rotate_to_flip,
    )


def register():
    for regClass in getRegClasses():
        bpy.utils.register_class(regClass)


def unregister():
    for regClass in getRegClasses():
        bpy.utils.unregister_class(regClass)
