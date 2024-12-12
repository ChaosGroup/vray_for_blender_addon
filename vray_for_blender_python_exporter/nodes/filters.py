from vray_blender.exporting.tools import MESH_OBJECT_TYPES, GEOMETRY_OBJECT_TYPES


def filterLights(obj):
    return (obj.type == 'LIGHT')


def filterMeshes(obj):
    return (obj.type in MESH_OBJECT_TYPES)


def filterGeometries(obj):
    return (obj.type in GEOMETRY_OBJECT_TYPES)


def filterMaterials(mtl):
    return mtl.vray.is_vray_class    