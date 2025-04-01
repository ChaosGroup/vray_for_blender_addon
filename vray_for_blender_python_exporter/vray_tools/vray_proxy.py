
import struct
from pathlib import PurePath
import os
from io import BufferedReader

import bpy
from mathutils import Vector

from vray_blender import debug
from vray_blender.lib import blender_utils, sys_utils, path_utils
from vray_blender.vray_tools import vray_proxy


# Channels that can be read from a mesh file
_MESH_CHANNEL_FULL = 1      # Mesh channel with full geometry
_MESH_CHANNEL_PREVIEW = 2   # Mesh channel with preview (simplified) geometry


def launchPly2Vrmesh(vrsceneFilepath, 
                     vrmeshFilepath=None, 
                     nodeName=None, 
                     frames=None,
                     applyTm=False, 
                     useVelocity=False, 
                     previewOnly=False, 
                     previewFaces=None):

    ply2vrmesh = path_utils.getBinTool(sys_utils.getPlatformName('ply2vrmesh'))

    if not os.path.exists(ply2vrmesh):
        return "ply2vrmesh binary not found!"

    cmd = [ply2vrmesh]
    cmd.append(vrsceneFilepath) # input file
    cmd.append(vrmeshFilepath)  # output file
    
    if previewOnly:
        cmd.append('-previewType')
        cmd.append('combined')
        
        if previewFaces:
            cmd.append('-previewFaces')
            cmd.append(f"{previewFaces}")
    
    if nodeName:
        cmd.append('-vrsceneNodeName')
        cmd.append(nodeName)
    else:
        cmd.append('-vrsceneWholeScene')

    if useVelocity:
        cmd.append('-vrsceneVelocity')
    
    if applyTm:
        cmd.append('-vrsceneApplyTm')
    
    if frames is not None:
        cmd.append('-vrsceneFrames')
        cmd.append(f"{frames[0]}-{frames[1]}")

    debug.printInfo(f"Calling: {' '.join(cmd)}")

    from subprocess import PIPE, run

    result = run(cmd, stdout=PIPE, stderr=PIPE, universal_newlines=True)

    # err = subprocess.call(cmd)
    if result.returncode != 0:
        debug.printError(result.stdout)
        debug.printError(result.stderr)
        return "Error generating vrmesh file!"

    return None


def _dumpMeshFile(meshChannelType: int, vrmeshFile: str, binFile: str):
    """ Run vraytools utility to dump the requested data from a mesh file (.vrmesh, .abc etc) into a simplified 
        binary format which can that be easily loaded by the Python code.

    Args:
        meshChannelType (int): one of MVF_PREVIEW_VOXEL or MVF_GEOMETRY_VOXEL
        vrmeshFile (str): path to the mesh file to dump
        binFile (str): path to the resulting binary file

    Returns:
        string | None: Error message on failure, None on success
    """
    from subprocess import PIPE, run

    vrayToolsApp = path_utils.getBinTool(sys_utils.getPlatformName("vraytools"))

    cmd = [vrayToolsApp]
    cmd.extend(['-action', '2' if meshChannelType == _MESH_CHANNEL_PREVIEW else '1'])
    cmd.extend(['-input', vrmeshFile])
    cmd.extend(['-output', binFile])
    result = run(cmd, stdout=PIPE, stderr=PIPE, universal_newlines=True)

    debug.printDebug(f"Running vrmesh tool: {' '.join(cmd)}")
    
    if result.returncode != 0:
        debug.printError(result.stdout)
        debug.printError(result.stderr)
        return "Error generating vrmesh file!"
    
    return None


def _readProxyMesh(filePath: str, meshChannelType: int):
    """ Reads data from a mesh file, which involves dumping the relevant contents of the file into a simplified binary format
        and reading the binary data into structures compatible with Blender.

    Args:
        filePath (str): the input file in one of the formats supported by the vraytools utlity
        voxelType (int): the type of data to read - preview or full

    Returns:
        _type_: _description_
    """
    try:
        binMeshFile = str(PurePath(path_utils.getV4BTempDir(), PurePath(filePath).stem).with_suffix('.vrbin'))
        err = _dumpMeshFile(meshChannelType, filePath, binMeshFile)
        
        if (not err) and os.path.isfile(binMeshFile):
            meshData = vray_proxy.readBinMeshFile(binMeshFile)
        
            if len(meshData['vertices']) == 0:
                debug.printDebug(f"Mesh contains no {'preview' if meshChannelType == _MESH_CHANNEL_PREVIEW else 'geometry'} data")

            return meshData, None
    except Exception as ex:
        debug.printExceptionInfo(ex, f"Reading .vrmesh file: {filePath}")
    
    return None, f"Failed to read .vrmesh file: {filePath}"


def loadVRayProxyPreviewMesh(ob: bpy.types.Object, filePath, animType, animOffset, animSpeed, animFrame):
    """ Load the preview voxel from a .vrmesh file, if any.
    
        Returns: None on success, error message on error.
    """
    assert ob is not None, "Proxy object must have been created"
    
    geomMeshFile = ob.data.vray.GeomMeshFile

    if geomMeshFile.previewType == 'None':
        # Replace with empty mesh
        mesh = bpy.data.meshes.new("VRayProxyPreviewTemporary")
        blender_utils.replaceObjectMesh(ob, mesh)
        bpy.data.meshes.remove(mesh)
        geomMeshFile['num_preview_faces'] = 0
        return

    PREVIEW_VOXELS = {'Preview' : _MESH_CHANNEL_PREVIEW, 'Full': _MESH_CHANNEL_FULL}
    voxelType = PREVIEW_VOXELS[geomMeshFile.previewType]
    
    meshData, err = _readProxyMesh(filePath, voxelType)

    if err:
        return err
    
    assert meshData is not None
    mesh = bpy.data.meshes.new("VRayProxyPreviewTemporary")
    mesh.from_pydata(meshData['vertices'], [], meshData['faces'])
    mesh.update()
    
    if (voxelType == _MESH_CHANNEL_PREVIEW):
        geomMeshFile['num_preview_faces'] = len(meshData['faces'])

    # File might or might not contain uv info
    if meshData['uv_sets']:
        for uvName in meshData['uv_sets']:
            mesh.uv_layers.new(name=uvName)

    blender_utils.replaceObjectMesh(ob, mesh)
    bpy.data.meshes.remove(mesh)
    
    blender_utils.selectObject(ob)

    # Depending on the source of the model, scaling may be necessary. E.g. Cosmos assets are in 
    # cm. Scale the model gometry without affecting any currently set scale value for the object.
    if geomMeshFile.scale != 1.0:
        oldScale = ob.scale
        ob.scale = Vector((1.0, 1.0, 1.0))
        ob.scale *= geomMeshFile.scale
        # Apply the selected object's scale. This will apply the transform to the mesh and 
        # reset the 'scale' field to 1.0.
        bpy.ops.object.transform_apply(location=False, rotation=False, scale=True) 
        ob.scale = oldScale


def loadVRayScenePreviewMesh(vrsceneFilepath, scene, ob: bpy.types.Object):
    sceneFilepath = bpy.path.abspath(vrsceneFilepath)
    if not os.path.exists(sceneFilepath):
        return "Scene file doesn't exists!"

    proxyFilepath = str(PurePath(sceneFilepath).with_suffix('.vrmesh'))

    if not os.path.exists(proxyFilepath):
        return "Preview proxy file doesn't exist!"

    err = loadVRayProxyPreviewMesh(
        ob,
        proxyFilepath,
        '0', # TODO
        0,   # TODO
        1.0, # TODO
        scene.frame_current-1
    )

    return err


def isAlembicFile(filePath: str):
    # Cannot use functions from pathlib here because the path may be in Blender relative format.
    return filePath.endswith('.abc')


def generateAlembicPreview(abcFilePath: str, geomMeshFile):
    """ Generate preview geometry for an alembic file as .vrmesh.

    Args:
        context (bpy.types.Context)
        abcFilePath (str): Path to an alembic file.
        geomMeshFile (GeomMeshFile): the GeomMeshFile attached to an object's data

    Returns:
        str | None: The path to the generated .vrmesh file or None if the conversion has failed. 
    """
    assert geomMeshFile.previewType != 'None', "Preview for this object is not required"
    
    filename = PurePath(abcFilePath).stem
    previewFilePath = str(PurePath(path_utils.getV4BTempDir(), filename).with_suffix('.vrmesh'))
    
    isPreview = geomMeshFile.previewType == 'Preview'
    launchPly2Vrmesh(abcFilePath, previewFilePath, 
                        previewOnly=isPreview, 
                        previewFaces=geomMeshFile.num_preview_faces)
    return previewFilePath if os.path.exists(previewFilePath) else None



def _binRead(file: BufferedReader, dataType: str, numItems: int):
    """ Read typed data items from a binary file.

    Args:
        file (BufferedReader): input file object
        dataType (str): the type of the data item, one of the format specifiers defined for struct.unpack()
        numItems (int): the number of items to read

    Returns:
        The requested data in a compatible format.
    """

    DATA_SIZES = {
        'I': 4,
        'Q': 8,
        'c': 1,
        'f': 4
    }
    rawData = file.read(numItems * DATA_SIZES[dataType])
    format = f"{numItems}{dataType}"
    buffer = struct.unpack(format, rawData)

    data = buffer[0] if len(buffer) == 1 else buffer
    if (dataType == 'c') and (len(buffer) == 1) :
        return data.decode()
    
    return data


def readBinMeshFile(filePath: str):
    """ Read a .vrbin file produced by vraytools utility into Blender-compatible fomat """
    
    # Reshape an array of items by splitting it into subarrays each containing 'size' items
    def _reshapeArray(input, size: int):
        return tuple(zip(*([iter(input)] * size)))
    
    assert os.path.isfile(filePath)

    with open(os.path.expanduser(filePath), "rb") as file:
        chunks = []
        tocSize = _binRead(file, "I", 1)         # uint32

        for _ in range(tocSize):
            itemType  = _binRead(file, 'c', 1)   # char
            itemCount = _binRead(file, 'Q', 1)   # uint64
            offset    = _binRead(file, 'Q', 1)   # uint64
            chunks.append((itemType, offset, itemCount))

        vertices = []
        faces = []

        for itemType, offset, itemCount in chunks:    
            file.seek(offset)
            match itemType:
                case 'v':
                    floatArray  = _binRead(file, 'f', itemCount * 3) # 3 float coords per vertex
                    vertices = _reshapeArray(floatArray, 3)
                case 'f':
                    intArray  = _binRead(file, 'I', itemCount * 3) # 3 int indices per face
                    faces = _reshapeArray(intArray, 3)

        return {
            'vertices' : vertices,
            'faces'    : faces,
            'uv_sets'  : []
        }
