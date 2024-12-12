
import math
import os
import subprocess
import sys
import tempfile
import time

import bpy
import bmesh

HAS_VB30 = True
try:
    import _vray_for_blender
except:
    HAS_VB30 = False

from vray_blender.lib import blender_utils, path_utils, lib_utils, sys_utils

from vray_blender.nodes import tools as NodesTools

from vray_blender.vray_tools import vray_proxy
from vray_blender import debug


def launchPly2Vrmesh(vrsceneFilepath, vrmeshFilepath=None, nodeName=None, frames=None, applyTm=False, useVelocity=False, previewOnly=False, previewFaces=None):
    ply2vrmeshBin  = "ply2vrmesh{arch}{ext}"
    ply2vrmeshArch = ""

    if sys.platform == 'win32':
        ply2vrmeshExt = ".exe"
        ply2vrmeshArch = "_%s" % sys_utils.getArch()
    elif sys.platform == 'linux':
        ply2vrmeshExt = ".bin"
    else:
        ply2vrmeshExt = ".mach"

    ply2vrmeshBin = ply2vrmeshBin.format(arch=ply2vrmeshArch, ext=ply2vrmeshExt)

    exporterPath = sys_utils.getExporterPath()
    if not exporterPath:
        return "Exporter path is not found!"

    ply2vrmesh = os.path.join(exporterPath, "bin", ply2vrmeshBin)
    if not os.path.exists(ply2vrmesh):
        return "ply2vrmesh binary not found!"

    cmd = [ply2vrmesh]
    cmd.append(vrsceneFilepath)
    if previewFaces:
        cmd.append('-previewFaces')
        cmd.append('%i' % previewFaces)
    if previewOnly:
        cmd.append('-vrscenePreview')
    if nodeName:
        cmd.append('-vrsceneNodeName')
        cmd.append(nodeName)
    if useVelocity:
        cmd.append('-vrsceneVelocity')
    if applyTm:
        cmd.append('-vrsceneApplyTm')
    if frames is not None:
        cmd.append('-vrsceneFrames')
        cmd.append('%i-%i' % (frames[0], frames[1]))
    if vrmeshFilepath is not None:
        cmd.append(vrmeshFilepath)

    debug.printInfo("Calling: %s" % " ".join(cmd))

    err = subprocess.call(cmd)
    if err:
        return "Error generating vrmesh file!"

    return None


def exportMeshSample(o, ob):
    nodeName = blender_utils.getObjectName(ob)
    geomName = blender_utils.getObjectName(ob, prefix='ME')

    o.set('OBJECT', 'Node', nodeName)
    o.writeHeader()
    o.writeAttibute('geometry', geomName)
    o.writeAttibute('transform', ob.matrix_world)
    o.writeFooter()

    _vray_for_blender.exportMesh(
        bpy.context.as_pointer(),   # Context
        ob.as_pointer(),            # Object
        geomName,                   # Result plugin name
        None,                       # propGroup
        o.output                    # Output file
    )

    return nodeName


def loadProxyPreviewMesh(ob, filePath, animType, animOffset, animSpeed, animFrame):
    meshFile = vray_proxy.MeshFile(filePath)

    result = meshFile.readFile()
    if result is not None:
        return "Error parsing VRayProxy file!"

    meshData = meshFile.getPreviewMesh(
        animType,
        animOffset,
        animSpeed,
        animFrame
    )

    if meshData is None:
        return "Can't find preview voxel!"

    mesh = bpy.data.meshes.new("VRayProxyPreview")
    mesh.from_pydata(meshData['vertices'], [], meshData['faces'])
    mesh.update()

    # File might or might not contain uv info
    if meshData['uv_sets']:
        for uvName in meshData['uv_sets']:
            mesh.uv_layers.new(name=uvName)

    # Replace object mesh
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bm.to_mesh(ob.data)
    ob.data.update()

    # Remove temp
    bm.free()
    bpy.data.meshes.remove(mesh)


 #######  ########   ##        ########  ########  ######## ##     ## #### ######## ##      ##
##     ## ##     ## ####       ##     ## ##     ## ##       ##     ##  ##  ##       ##  ##  ##
##     ## ##     ##  ##        ##     ## ##     ## ##       ##     ##  ##  ##       ##  ##  ##
##     ## ########             ########  ########  ######   ##     ##  ##  ######   ##  ##  ##
##     ## ##         ##        ##        ##   ##   ##        ##   ##   ##  ##       ##  ##  ##
##     ## ##        ####       ##        ##    ##  ##         ## ##    ##  ##       ##  ##  ##
 #######  ##         ##        ##        ##     ## ########    ###    #### ########  ###  ###

def loadVRayScenePreviewMesh(vrsceneFilepath, scene, ob):
    sceneFilepath = bpy.path.abspath(vrsceneFilepath)
    if not os.path.exists(sceneFilepath):
        return "Scene file doesn't exists!"

    sceneDirpath, sceneFullFilename = os.path.split(sceneFilepath)

    sceneFileName, sceneFileExt = os.path.splitext(sceneFullFilename)

    proxyFilepath = os.path.join(sceneDirpath, "%s.vrmesh" % sceneFileName)
    if not os.path.exists(proxyFilepath):
        return "Preview proxy file doesn't exists!"

    err = loadProxyPreviewMesh(
        ob,
        proxyFilepath,
        '0', # TODO
        0,   # TODO
        1.0, # TODO
        scene.frame_current-1
    )

    return err


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

        err = loadVRayScenePreviewMesh(filepath, context.scene, ob)

        if err is not None:
            self.report({'ERROR'}, err)
            return {'CANCELLED'}

        return {'FINISHED'}


class VRAY_OT_proxy_load_preview(bpy.types.Operator):
    bl_idname      = "vray.proxy_load_preview"
    bl_label       = "Load Preview"
    bl_description = "Loads mesh preview from vrmesh file"

    def execute(self, context):
        GeomMeshFile  = context.node.GeomMeshFile
        proxyFilepath = bpy.path.abspath(GeomMeshFile.file)

        if not proxyFilepath:
            self.report({'ERROR'}, "Proxy filepath is not set!")
            return {'FINISHED'}

        if not os.path.exists(proxyFilepath):
            return {'FINISHED'}

        err = loadProxyPreviewMesh(
            context.object,
            proxyFilepath,
            GeomMeshFile.anim_type,
            GeomMeshFile.anim_offset,
            GeomMeshFile.anim_speed,
            context.scene.frame_current-1
        )

        if err is not None:
            self.report({'ERROR'}, err)
            return {'CANCELLED'}

        return {'FINISHED'}


 #######  ########   ##         ######  ########  ########    ###    ######## ########
##     ## ##     ## ####       ##    ## ##     ## ##         ## ##      ##    ##
##     ## ##     ##  ##        ##       ##     ## ##        ##   ##     ##    ##
##     ## ########             ##       ########  ######   ##     ##    ##    ######
##     ## ##         ##        ##       ##   ##   ##       #########    ##    ##
##     ## ##        ####       ##    ## ##    ##  ##       ##     ##    ##    ##
 #######  ##         ##         ######  ##     ## ######## ##     ##    ##    ########

class VRAY_OT_vrayscene_generate_preview(bpy.types.Operator):
    bl_idname      = "vray.vrayscene_generate_preview"
    bl_label       = "Generate VRayScene Preview"
    bl_description = "Generate *.vrscene preview into vrmesh file"

    def execute(self, context):
        sce = context.scene
        ob  = context.object

        sceneFilepath = bpy.path.abspath(ob.vray.VRayAsset.filePath)
        if not sceneFilepath:
            self.report({'ERROR'}, "Scene filepath is not set!")
            return {'FINISHED'}

        launchPly2Vrmesh(sceneFilepath, previewOnly=True, previewFaces=ob.vray.VRayAsset.maxPreviewFaces)
        loadVRayScenePreviewMesh(sceneFilepath, context.scene, ob)

        return {'FINISHED'}


class VRAY_OT_create_proxy(bpy.types.Operator):
    bl_idname      = "vray.create_proxy"
    bl_label       = "Create proxy"
    bl_description = "Creates proxy from selection"

    def execute(self, context):
        # TODO: use AppSDK to create proxy from selected meshes
        return {'CANCELLED'}


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
        VRAY_OT_create_proxy,

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
