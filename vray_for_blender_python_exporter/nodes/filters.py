# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

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
    return filterLights(obj) and (obj.data.type == 'SUN')


def filterTexDistanceTargets(obj: bpy.types.Object):
    if not filterGeometries(obj):
        return False

    # Exclude objects with TexDistance from their own constraint lists so that object
    # could not be set as its own constraint.
    if (activeObj := getattr(bpy.context, 'active_object', None)) and \
       (activeMtl := getattr(activeObj, 'active_material', None)):
        if activeMtl in [s.material for s in obj.material_slots]:
            return False

    return True


def filterRenderMasks(obj):
    # NOTE: Unsupported plugins 
    #  - Instancer2  - unsupported in V-Ray
    #  - Text/Curve  - unsupported in V-Ray, because they are exported as Instancer2
    return not obj.is_instancer and (obj.type in GEOMETRY_OBJECT_TYPES) and (obj.type not in ['CURVE', 'FONT'])