# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

# VBLD-808: the V-Ray Clipper lost its own material picker and its single-collection exclusion
# field, as no other V-Ray integration has either.
#  - The cut surfaces of a plane clipper are now shaded with the clipper object's first material.
#    Move the picked material there.
#  - As in V-Ray for C4D, 'Fill Inner Cavities' now decides whether the cuts are capped, and it is
#    on by default. Without a picked material a plane clipper left its cuts open, so turn it off
#    there. Every other enabled clipper capped its cuts, so turn it on.
#  - The excluded collection becomes the first item of the new object list.

import bpy

from vray_blender.utils.upgrade_scene import scopedForUpgrade


def _clippers():
    for obj in scopedForUpgrade(bpy.data.objects):
        if hasattr(obj.data, 'materials'):
            yield obj, obj.vray.VRayClipper


def _isPlaneClipper(clipper):
    return not (clipper.use_obj_mesh or clipper.use_obj_mtl)


def _pickedMaterial(clipper):
    # The pointer follows renames, the 'material' name it was mirrored into does not
    if isinstance(mtl := clipper.get('selectedMaterial'), bpy.types.Material):
        return mtl
    return bpy.data.materials.get(clipper.material) if clipper.material else None


def _setCutMaterial(obj, mtl):
    if obj.material_slots and (obj.material_slots[0].material == mtl):
        return

    if not obj.material_slots:
        obj.data.materials.append(None)

    # Link to the object, so that the mesh and any other object sharing it are left as they are
    slot = obj.material_slots[0]
    slot.link = 'OBJECT'
    slot.material = mtl


def run():
    for obj, clipper in _clippers():
        if clipper.enabled:
            picked = _pickedMaterial(clipper) if _isPlaneClipper(clipper) else None
            if picked:
                _setCutMaterial(obj, picked)
            clipper.fill_cavities = bool(picked) or not _isPlaneClipper(clipper)

        if isinstance(coll := clipper.get('exclusion_nodes_ptr'), bpy.types.Collection):
            item = clipper.exclusion_selector.selectedItems.add()
            item.objectPtr = coll
            item.name = coll.name
            item.objectType = 'COLLECTION'

        for key in ('selectedMaterial', 'exclusion_nodes_ptr'):
            if key in clipper:
                del clipper[key]


def check():
    return any(clipper.enabled or ('selectedMaterial' in clipper) or ('exclusion_nodes_ptr' in clipper)
               for _, clipper in _clippers())
