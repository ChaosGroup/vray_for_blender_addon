
from re import L
import struct
from pathlib import PurePath
import os
from io import BufferedReader

import bpy
import bmesh
import numpy as np
from mathutils import Vector

from vray_blender import debug
from vray_blender.lib import blender_utils, sys_utils, path_utils
from vray_blender.lib.sys_utils import getAppSdkLibPath
from vray_blender.vray_tools import vray_proxy


# Types of actions that can be performed for generating data for VRayProxy or VRayScene supported formats
# using the vraytools utility.
class PreviewAction:
    MeshPreview    = '1'      # Geometry from a VRayProxy-compatible files
    ScenePreview   = '2'      # Geometry from a VRayScene-compatible files


_PREVIEW_TYPES = {
    'Full':    0,
    'Preview': 1,
    'Boxes' :  2
}


def _dumpMeshFile(vrmeshFile: str, binFile: str, previewType: int, previewFaces: int, flipAxis: int):
    """ Run vraytools utility to dump the requested data from a mesh file (.vrmesh, .abc etc) into a simplified 
        binary format which can that be easily loaded by the Python code.

    Args:
        meshFile    (str): path to a file in format compatible wiht VRayProxy
        binFile     (str): path to the resulting binary file
        previewType (int): one of the _PTEVIEW_TYPES values
        previewFaces(int): upper limit for the number of faces in the preview. Only valid 
                            if isPreview is True.
        flipAxis    (int): one of the GeomMeshFile.flip_axis values
    Returns:
        str | None: Error message on failure, None on success
    """
    from subprocess import PIPE, run

    vrayToolsApp = path_utils.getBinTool(sys_utils.getPlatformName("vraytools"))

    cmd = [vrayToolsApp]
    cmd.extend(['-vrayLib', getAppSdkLibPath()])
    cmd.extend(['-action', PreviewAction.MeshPreview])
    cmd.extend(['-input', vrmeshFile])
    cmd.extend(['-output', binFile])
    cmd.extend(['-previewType', str(previewType)])
    cmd.extend(['-previewFaces', str(previewFaces)])
    cmd.extend(['-flipAxis', str(flipAxis)])

    result = run(cmd, stdout=PIPE, stderr=PIPE, universal_newlines=True)

    debug.printInfo(f"Running mesh preview tool: {' '.join(cmd)}")
    
    if result.returncode != 0:
        debug.printError(result.stdout)
        debug.printError(result.stderr)
        return f"Error generating bin file: {result.returncode}"
    
    if not os.path.isfile(binFile):
       return "Error generating bin file: file is missing"
     
    return None


def _generateScenePreview(sceneFile: str, binFile: str, previewType: int, previewFaces: int):
    """ Run vraytools utility to dump the requested data from a scene file (.vrscene, .usd) into a simplified 
        binary format which can be easily loaded by the Python code.

    Args:
        vrsceneFile (str):  path to the .vrscene file from which to generate the preview
        binFile     (str):  path to the resulting binary file
        previewType (int):  one of the values in _PRWVIEW_TYPES
        previewFaces(int):  upper limit for the number of faces in the preview. Only valid 
                            if isPreview is True.

    Returns:
        str | None: Error message on failure, None on success
    """
    from subprocess import PIPE, run

    vrayToolsApp = path_utils.getBinTool(sys_utils.getPlatformName("vraytools"))

    cmd = [vrayToolsApp]
    cmd.extend(['-vrayLib', getAppSdkLibPath()])
    cmd.extend(['-action', PreviewAction.ScenePreview])
    cmd.extend(['-input', sceneFile])
    cmd.extend(['-output', binFile])
    cmd.extend(['-previewType', str(previewType)])
    cmd.extend(['-previewFaces', str(previewFaces)])

    debug.printInfo(f"Calling: {' '.join(cmd)}")

    result = run(cmd, stdout=PIPE, stderr=PIPE, universal_newlines=True)

    debug.printInfo(f"Running scene preview tool: {' '.join(cmd)}")
    
    if result.returncode != 0:
        debug.printError(result.stdout)
        debug.printError(result.stderr)
        return "Error generating scene preview file!"
    
    return None


def _getDataFromMeshFile(filePath: str, previewType: str, previewFaces: int, flipAxis: int):
    """ Get mesh data from a .vrmesh or .abc file, or a file compatible with VRayScene.
    
    Args:
        meshFile    (str): path to a file in format compatible wiht VRayProxy
        binFile     (str): path to the resulting binary file
        previewType (int): one of the _PTEVIEW_TYPES values
        previewFaces(int): upper limit for the number of faces in the preview. Only valid 
                            if isPreview is True.
        flipAxis    (int): one of the GeomMeshFile.flip_axis values
    Returns:
        tuple(dict[str,list], str) | tuple(None, str):
            tuple of mesh data (vertices, faces) and empty string on success, or None and an error message on failure.

    """
    binMeshFile = str(PurePath(path_utils.getV4BTempDir(), PurePath(filePath).stem).with_suffix('.vrbin'))

    if err := _dumpMeshFile(filePath, binMeshFile, _PREVIEW_TYPES[previewType], previewFaces, flipAxis):
        return None, err
    
    objects = vray_proxy.readBinMeshFile(binMeshFile)

    os.unlink(binMeshFile)

    assert len(objects) == 1
    meshData = list(objects.values())[0]
    
    assert meshData is not None

    return meshData, ""


def _getCenterOfProxyPreview(filePath, geomMeshFile):
    """ Get the center of the bounding box of the proxy mesh preview.
    """
    meshData, err = _getDataFromMeshFile(filePath, 'Boxes', 0, int(geomMeshFile.flip_axis))
    if err:
        debug.printError(f"Proxy preview mesh center calculation error: {err}")
        return Vector((0.0, 0.0, 0.0))

    return Vector(np.mean(np.array(meshData['vertices']), axis=0)) * geomMeshFile.scale

def loadVRayProxyPreviewMesh(ob: bpy.types.Object, filePath, animType, animOffset, animSpeed, animFrame):
    """ Load the preview voxel from a .vrmesh file, if any.
    
        Returns: None on success, error message on error.
    """
    assert ob is not None, "Proxy object must have been created"
    
    if ( fileExt:= PurePath(filePath).suffix) not in ('.vrmesh', '.abc'):
        return f"File format {fileExt} is not supported by V-Ray Proxy"
    
    geomMeshFile = ob.data.vray.GeomMeshFile

    if geomMeshFile.previewType == 'None':
        # Replace with empty mesh
        mesh = bpy.data.meshes.new("VRayProxyPreviewTemporary")
        blender_utils.replaceObjectMesh(ob, mesh)
        bpy.data.meshes.remove(mesh)
        geomMeshFile['num_preview_faces'] = 0

        geomMeshFile.initial_preview_mesh_pos = Vector((0.0, 0.0, 0.0))
        return

    meshData, err = _getDataFromMeshFile(filePath, geomMeshFile.previewType,
                                         geomMeshFile.num_preview_faces, int(geomMeshFile.flip_axis))
    if not meshData:
        return err
    
    mesh = bpy.data.meshes.new("VRayProxyPreviewTemporary")

    if geomMeshFile.initial_preview_mesh_pos != Vector((0.0, 0.0, 0.0)):
        offset = blender_utils.getGeomCenter(ob) - geomMeshFile.initial_preview_mesh_pos 
        if (offset != Vector((0.0, 0.0, 0.0))) or \
            (geomMeshFile.scale != 0): # If scale is zero, all vertices collapse to (0,0,0), so no offset is needed.
            
            offset /= geomMeshFile.scale  # Apply scaling to the object offset.
            vertices = np.array(meshData['vertices']) # Convert the list of tuple vertices to an (N,3) array
            offset_array = np.array([offset.x, offset.y, offset.z]) 
            meshData['vertices'] = (vertices + offset_array).tolist()

    mesh.from_pydata(meshData['vertices'], [], meshData['faces'])
    mesh.update()


    if geomMeshFile.previewType == 'Preview':
        # The preview-generation procedure uses the requested number of preview faces as a guideline only.
        # Write back to the UI the number of actual preview faces
        geomMeshFile['num_preview_faces'] = len(meshData['faces'])

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

    # Set the initial position of the preview mesh to the center of the bounding box of the mesh.
    # This position can't be obtained from the mesh itself, as it may be offset by the object's transform.
    geomMeshFile.initial_preview_mesh_pos = _getCenterOfProxyPreview(filePath, geomMeshFile)


def loadVRayScenePreviewMesh(sceneFilepath, ob: bpy.types.Object):
    """ Load preview from a file format compatible with VRayScene.
    
        Args:
            sceneFilepath   (str)   : path to a file compatible with VRayScene
            ob              (Object): scene object whose mesh will be replaced by the preview mesh

        Returns: 
            None on success, error message on error.
    """
    if ( fileExt:= PurePath(sceneFilepath).suffix) != '.vrscene':
        return f"File format {fileExt} is not supported by V-Ray Scene"
    
    sceneFilepath = bpy.path.abspath(sceneFilepath)
    if not os.path.exists(sceneFilepath):
        return "Scene file doesn't exists!"

    vrayScene = ob.data.vray.VRayScene

    if vrayScene.previewType == 'None':
        # Replace with empty mesh
        mesh = bpy.data.meshes.new("VRayScenePreviewTemporary")
        blender_utils.replaceObjectMesh(ob, mesh)
        bpy.data.meshes.remove(mesh)
        vrayScene['num_preview_faces'] = 0
        return None

    isPreview   = (vrayScene.previewType == 'Preview')
    binMeshFile = str(PurePath(sceneFilepath).with_suffix('.vrbin'))

    if err := _generateScenePreview(sceneFilepath, binMeshFile, _PREVIEW_TYPES[vrayScene.previewType], vrayScene.num_preview_faces):
        return err

    objects = readBinMeshFile(binMeshFile)
    assert len(objects) == 1
    meshData = list(objects.values())[0]

    mesh = bpy.data.meshes.new("VRayScenePreviewTemporary")
    mesh.from_pydata(meshData['vertices'], [], meshData['faces'])
    
    if vrayScene.flip_axis in ('2', '3'):
        flipYZAxes(mesh, yToZ=(vrayScene.flip_axis == '3'))
    mesh.update()

    if isPreview:
        # Set the actual number of preview faces to be shown to the user
        vrayScene['num_preview_faces'] = len(meshData['faces'])

    blender_utils.replaceObjectMesh(ob, mesh)
    bpy.data.meshes.remove(mesh)

    blender_utils.selectObject(ob)

    os.unlink(binMeshFile)

    return None


def isAlembicFile(filePath: str):
    # Cannot use functions from pathlib here because the path may be in Blender relative format.
    return filePath.endswith('.abc')


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
        'f': 4,
        's': 1
    }
    rawData = file.read(numItems * DATA_SIZES[dataType])
    format = f"{numItems}{dataType}"
    buffer = struct.unpack(format, rawData)

    data = buffer[0] if len(buffer) == 1 else buffer
    if (dataType == 'c') and (len(buffer) == 1) :
        return data.decode()
    elif dataType == 's':
        return data.decode('utf-8')
    
    return data


def readBinMeshFile(filePath: str):
    """ Read a .vrbin file produced by vraytools utility into Blender-compatible fomat """
    assert os.path.isfile(filePath)

    objects = {} # name -> meshData

    with open(os.path.expanduser(filePath), "rb") as file:
        numObjects = _binRead(file, 'I', 1) # uint32

        for i in range(numObjects):
            meshData = _readObjectFromBinFile(file)
            objects[meshData['name']] = meshData
    
    return objects


def _readObjectFromBinFile(file: BufferedReader):  
    """ Read a single object from a binary mesh data stream.

    Args:
        file (BufferedReader): an open reader for the binary file

    Raises:
        Exception: 

    Returns:
        dict(str, meshData): a map of object name to mesh data for the object
    """
    def _reshapeArray(input, size: int):
        # Reshape an array of items by splitting it into subarrays each containing 'size' items
        return tuple(zip(*([iter(input)] * size)))

    def _readString(file):
        length = _binRead(file, 'I', 1)
        return _binRead(file, 's', length)
    
    chunks = []
    objName = _readString(file)
    tocSize = _binRead(file, "I", 1)         # uint32

    for _ in range(tocSize):
        itemType  = _binRead(file, 'c', 1)   # char
        itemCount = _binRead(file, 'Q', 1)   # uint64
        offset    = _binRead(file, 'Q', 1)   # uint64
        chunks.append((itemType, offset, itemCount))

    vertices = []
    faces = []
    transform = None

    for itemType, offset, itemCount in chunks:    
        match itemType:
            case 'v':
                floatArray  = _binRead(file, 'f', itemCount * 3) # 3 float coords per vertex
                vertices = _reshapeArray(floatArray, 3)
            case 'f':
                intArray  = _binRead(file, 'I', itemCount * 3) # 3 int indices per face
                faces = _reshapeArray(intArray, 3)
            case _:
                raise Exception(f"Unsupported channel type '{itemType}'")

    return {
        'name'      : objName,
        'vertices'  : vertices,
        'faces'     : faces,
    }


def flipYZAxes(mesh: bpy.types.Mesh, yToZ: bool):
    """ Change mesh orientation by flipping the Y and Z axes in a VRayScene object.

    Args:
        mesh (bpy.types.Mesh)   : The mesh to rotate.
        yToZ (bool)             : True - Y to Z, False - Z to Y
    """
    from mathutils import Matrix
    
    flipMatrix = Matrix()
    flipMatrix[1][1] = 0.0
    flipMatrix[2][2] = 0.0
    # 1) 90 degrees rotation around x axis (Z-up to Y-up: transform maya to max coordinate system)
    # 2) -90 degrees rotation around x axis (Y-up to Z-up: transform max to maya coordinate system)
    flipMatrix[1][2] = -1.0 if yToZ else 1.0
    flipMatrix[2][1] = 1.0 if yToZ else -1.0

    mesh.transform(flipMatrix)