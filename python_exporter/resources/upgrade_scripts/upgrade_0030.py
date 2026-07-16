# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy

from mathutils import Vector
import numpy as np

from vray_blender import debug
from vray_blender.exporting.tools import isObjectVrayScene
from vray_blender.resources.upgrade_scripts.upgrade_0027 import computeBasisMatrix


def arrayFromMeshVertices(mesh: bpy.types.Mesh):
    count = len(mesh.vertices)

    # 1. Create a flat buffer (3 floats per vertex)
    coords = np.empty(count * 3, dtype=np.float32)

    # 2. Fast copy from C to Python
    mesh.vertices.foreach_get("co", coords)
    return coords.reshape(count, 3)


def run():
    for obj in bpy.data.objects:
        if isObjectVrayScene(obj):
            from mathutils import Matrix
            import vray_blender.vray_tools.vray_proxy as proxy
            from vray_blender.exporting.tools import mat4x4ToTuple

            vrayScene = obj.data.vray.VRayScene
            absFilePath = bpy.path.abspath(vrayScene.filepath) 
            
            # Regardless of whether this is a point preview, we need to compute the anchor transform 
            # from an actual mesh
            originalPreviewType = vrayScene.previewType
            vrayScene['previewType'] = 'Preview'
            meshData, boxVertices, err = proxy._constructPreview(vrayScene, absFilePath, isProxy=False)
            if err:
                debug.reportError(f"Failed to upgrade V-Ray Scene object: {err}")
                continue

            # For some old scenes computing the transform of the object from the saved GeomMeshFile properties does not place the object
            # in the exactly same position after the upgrade. Compute the basis matrix from actual points of the mesh. 
            basisMatrix, pointIndices, addedVertices = computeBasisMatrix(meshData['vertices'], boxVertices, isFlatObject=False)

            if len(addedVertices) > 0:
                # The object is too flat, try again with greater tolerance for flatness
                basisMatrix, pointIndices, addedVertices = computeBasisMatrix(meshData['vertices'], boxVertices, isFlatObject=True)
            
            vrayScene['basis_matrix'] = mat4x4ToTuple(basisMatrix)
            vrayScene['basis_vertex_indices'] = pointIndices
            vrayScene['previewType'] = originalPreviewType

            if len(addedVertices) > 0:
                # If any anchor vertices were added, set them to the mesh
                mesh: bpy.types.Mesh = obj.data
                vertices = np.empty(len(mesh.vertices) * 3, dtype=np.float32)
                faces = np.empty(len(mesh.loop_triangles) * 3, dtype=np.int32)

                mesh.vertices.foreach_get('co', vertices)
                mesh.loop_triangles.foreach_get('vertices', faces)
                vertices = vertices.reshape((-1,3))

                allVertices = np.concatenate((vertices, addedVertices), axis=0)
                
                newMeshData = {
                    'vertices': allVertices,
                    'faces': faces.reshape((-1,3))
                }

                proxy._replaceObjMesh(obj, newMeshData)

            proxy.loadVRayScenePreviewMesh(obj, vrayScene.filepath)


def check():
    return any(isObjectVrayScene(obj) for obj in bpy.data.objects)
