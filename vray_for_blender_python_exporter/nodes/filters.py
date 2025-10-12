import bpy

from vray_blender.exporting.tools import MESH_OBJECT_TYPES, GEOMETRY_OBJECT_TYPES

def filterLights(obj):
    return (obj.type == 'LIGHT')


def filterMeshes(obj):
    return (obj.type in MESH_OBJECT_TYPES)


def filterGeometries(obj):
    return (obj.type in GEOMETRY_OBJECT_TYPES)


def filterMaterials(mtl):
    return mtl.vray.is_vray_class    


def filterSuns(obj):
    return filterLights(obj) and (obj.data.vray.light_type == 'SUN')


def filterTexDistanceTargets(obj: bpy.types.Object):
    if not filterGeometries(obj):
        return False
    
    # Exclude objects with TexDistance from their own constaraint lists so that object
    # could not be set as its own constraint.
    if bpy.context.active_object.active_material in [s.material for s in obj.material_slots]:
        return False
    
    return True