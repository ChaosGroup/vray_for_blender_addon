# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

from mathutils import Matrix


def printVec3Arr(arr, attr, num):
    s = []
    for i in range(num):
        vec = getattr(arr[i], attr)
        s.append(f"<{vec.x:.4f},{vec.y:.4f},{vec.z:.4f}>")

    return " ".join(s)

def printVec3RawArr(arr, num):
    s = [
        f"<{arr[i * 3]:.4f},{arr[i * 3 + 1]:.4f},{arr[i * 3 + 2]:.4f}>"
        for i in range(0, num * 3, 3)
    ]
    return " ".join(s)

def logMesh(mesh, meshData):
    print("")
    print(f"Mesh: {mesh.name}")
    print( f"  vertices: {len(mesh.vertices)}")
    print( f"       {printVec3Arr(mesh.vertices, 'co', 3)} ...")
    print( f"  vert_normals: {len(mesh.vertex_normals)}")
    print( f"       {printVec3Arr(mesh.vertex_normals, 'vector', 3)} ...")
    print( f"  face_normals: {len(mesh.polygon_normals)}")
    print( f"       {printVec3Arr(mesh.polygon_normals, 'vector', 3)} ...")
    
    if meshData.splitNormals is not None:
        print( f"  split_normals: {len(meshData.splitNormals)}")
        if len(meshData.splitNormals) > 0:
            print( f"       {printVec3RawArr(meshData.splitNormals, 3)} ...")

    print( f"  loop_normals: {len(mesh.loops)}")
    print( f"       {printVec3Arr(mesh.loops, 'normal', 3)} ...")
    print( f"  merge_channel_vertices: {meshData.merge_channel_vertices}")
    print("")


def printMatrix(mat: Matrix):
    for r in range(len(mat.row)):
        print(mat.row[r])