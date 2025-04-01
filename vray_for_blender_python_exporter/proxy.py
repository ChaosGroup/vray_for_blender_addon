
import os
import sys
from pathlib import PurePath

import bpy

from vray_blender import debug
from vray_blender.lib import blender_utils
from vray_blender.vray_tools import vray_proxy


class VRAY_OT_object_rotate_to_flip(bpy.types.Operator):
    bl_idname      = "vray.object_rotate_to_flip"
    bl_label       = "Rotate Object"
    bl_description = "Rotate object to flip axis"

    def execute(self, context):
        ob = context.object

        bpy.ops.transform.rotate(value=1.5708,
            axis=(1, 0, 0),
            constraint_axis=(True, False, False),
            constraint_orientation='GLOBAL',
            mirror=False,
            proportional='DISABLED'
        )

        return {'FINISHED'}


class VRAY_OT_vrayscene_load_preview(bpy.types.Operator):
    bl_idname      = "vray.vrayscene_load_preview"
    bl_label       = "Load VRayScene Preview"
    bl_description = "Loads *.vrscene preview from vrmesh file"

    def execute(self, context):
        ob = context.object
        ob.vray.VRayAsset.assetType = blender_utils.VRAY_ASSET_TYPE["Scene"]
        filepath = ob.vray.VRayAsset.filePath

        err = vray_proxy.loadVRayScenePreviewMesh(filepath, context.scene, ob)

        if err is not None:
            self.report({'ERROR'}, err)
            return {'CANCELLED'}

        return {'FINISHED'}


class VRAY_OT_proxy_load_preview(bpy.types.Operator):
    bl_idname      = "vray.proxy_load_preview"
    bl_label       = "Load Preview"
    bl_description = "Loads mesh preview from vrmesh file"

    def execute(self, context):
        geomMeshFile  = context.active_object.data.vray.GeomMeshFile
        proxyFilepath = bpy.path.abspath(geomMeshFile.file)

        if not proxyFilepath:
            self.report({'ERROR'}, "Proxy filepath is not set!")
            return {'FINISHED'}

        if not os.path.exists(proxyFilepath):
            return {'FINISHED'}

        err = vray_proxy.loadVRayProxyPreviewMesh(
            context.object,
            proxyFilepath,
            geomMeshFile.anim_type,
            geomMeshFile.anim_offset,
            geomMeshFile.anim_speed,
            context.scene.frame_current-1
        )

        if err is not None:
            self.report({'ERROR'}, err)
            return {'CANCELLED'}

        return {'FINISHED'}


class VRAY_OT_proxy_generate_preview(bpy.types.Operator):
    bl_idname      = "vray.proxy_generate_preview"
    bl_label       = "Generate VRayProxy Preview"
    bl_description = "Generate preview geometry for a VRayProxy object"

    regenerate: bpy.props.BoolProperty(
        default=True,
        description="True if operator is called to regenerate an existing proxy preview")

    def execute(self, context):
        ob  = context.object
        geomMeshFile = ob.data.vray.GeomMeshFile

        # Default the preview to the original mesh file
        previewFilePath = bpy.path.abspath(geomMeshFile.file)

        if not (os.path.exists(previewFilePath)):
            debug.reportError(f"File not found: {previewFilePath}", self)
            return {'CANCELLED'}
        
        if self.regenerate and (geomMeshFile.previewType == 'Preview'):
            if vray_proxy.isAlembicFile(previewFilePath):
                previewFilePath = vray_proxy.generateAlembicPreview(previewFilePath, geomMeshFile)
                if previewFilePath is None:
                    debug.reportError(f"Failed to generate preview from file: {geomMeshFile.file}", self)
                    return {'CANCELLED'}
            
        if err := vray_proxy.loadVRayProxyPreviewMesh(ob, previewFilePath, geomMeshFile.anim_type,
                                            geomMeshFile.anim_offset, geomMeshFile.anim_speed, context.scene.frame_current):
            debug.reportError(err, self)
            return {'CANCELLED'}
        
        return {'FINISHED'}
    


class VRAY_OT_vrayscene_generate_preview(bpy.types.Operator):
    bl_idname      = "vray.vrayscene_generate_preview"
    bl_label       = "Generate VRayScene Preview"
    bl_description = "Generate *.vrscene preview into vrmesh file"

    def execute(self, context):
        ob  = context.object

        sceneFilepath = bpy.path.abspath(ob.vray.VRayAsset.filePath)
        if not sceneFilepath:
            self.report({'ERROR'}, "Scene filepath is not set!")
            return {'FINISHED'}

        vrmeshFile = str(PurePath(sceneFilepath).with_suffix('.vrmesh'))
        vray_proxy.launchPly2Vrmesh(sceneFilepath, vrmeshFile, previewOnly=True, previewFaces=ob.vray.VRayAsset.maxPreviewFaces)
        vray_proxy.loadVRayScenePreviewMesh(vrmeshFile, context.scene, ob)

        return {'FINISHED'}





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
