# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

""" Fast construction of Blender meshes from triangle arrays.

    Shared by the proxy preview loaders (vray_tools/vray_proxy.py, proxy.py) and the
    .vrscene scene importer (vray_tools/scene_import.py) so the bulk-fill idioms
    cannot drift apart.
"""

import bpy
import numpy as np


def buildTriMeshBase(meshName: str, vertices, faces) -> bpy.types.Mesh:
    """ Create a mesh datablock and bulk-fill its topology from triangle data.

    Args:
        meshName: name for the new mesh datablock.
        vertices: (N, 3) float array-like of vertex positions.
        faces: flat (M*3,) or (M, 3) int array-like of triangle vertex indices.

    Returns:
        The new mesh. Call mesh.update() after adding any further attributes.
    """
    vertexData = np.ascontiguousarray(vertices, dtype=np.float32)
    indexData = np.ascontiguousarray(faces, dtype=np.int32).ravel()

    numVerts = len(vertexData)
    numLoops = len(indexData)
    numFaces = numLoops // 3

    mesh = bpy.data.meshes.new(meshName)
    mesh.vertices.add(numVerts)
    mesh.loops.add(numLoops)
    mesh.polygons.add(numFaces)
    mesh.vertices.foreach_set('co', vertexData.ravel())
    mesh.loops.foreach_set('vertex_index', indexData)
    mesh.polygons.foreach_set('loop_start', np.arange(0, numLoops, 3, dtype=np.int32))
    mesh.polygons.foreach_set('loop_total', np.full(numFaces, 3, dtype=np.int32))

    return mesh
