# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy

from vray_blender.lib.names import Names


def handleVfbMenuAction(mode: int, targetName: str, objectName: str, distance: float):
    """ Dispatch a VFB context menu action.

        Args:
            mode:       1 = Select Object, 2 = Select Material, 3 = Set Focus Point
            targetName: V-Ray Node plugin name (object select) or material plugin name.
            objectName: V-Ray Node plugin name of the object owning the material (material select only).
            distance:   Distance to the picked point (focus point only).
    """
    match mode:
        case 1:
            _selectObject(targetName)
        case 2:
            _selectMaterial(targetName, objectName)
        case 3:
            _setFocusPoint(targetName, distance)


def _findObjectByNodePlugin(pluginName: str):
    """ Find the Blender object whose V-Ray Node plugin matches the given name.
        For instanced objects (node@Parent@N_iOriginal@M_randomId), selects the instancer parent.
    """
    # Strip the "node@" prefix to get the object identifier
    objId = pluginName.removeprefix("node@") if pluginName.startswith("node@") else pluginName

    # Instance IDs have format "Parent@N_iOriginal@M_randomId".
    # Find the last '@' followed by digits and '_i' to extract the parent ID.
    idx = objId.find("_i")
    if idx > 0 and '@' in objId[:idx]:
        objId = objId[:idx]

    for obj in bpy.context.scene.objects:
        if Names.object(obj) == objId:
            return obj

    return None


def _selectObject(pluginName: str):
    """ Select the Blender object whose V-Ray Node plugin matches the given name. """
    if not pluginName:
        return

    if obj := _findObjectByNodePlugin(pluginName):
        bpy.ops.object.select_all(action='DESELECT')
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj


def _selectMaterial(materialPluginName: str, objectPluginName: str):
    """ Select the material on the object that was picked in the VFB. """
    if not materialPluginName:
        return

    # Find the material by plugin name
    mat = None
    for m in bpy.data.materials:
        if hasattr(m, 'vray') and m.vray.unique_id and materialPluginName.startswith(m.vray.unique_id):
            mat = m
            break

    if not mat:
        return

    # Find the object using the Node plugin name from the pick
    obj = _findObjectByNodePlugin(objectPluginName) if objectPluginName else None

    if not obj:
        # Just find any object that uses this material, select it and activate the slot directly
        for obj in bpy.context.scene.objects:
            for i, slot in enumerate(obj.material_slots):
                if slot.material and slot.material.name == mat.name:
                    bpy.ops.object.select_all(action='DESELECT')
                    obj.select_set(True)
                    bpy.context.view_layer.objects.active = obj
                    obj.active_material_index = i
                    return
    else:
        bpy.ops.object.select_all(action='DESELECT')
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj

        for i, slot in enumerate(obj.material_slots):
            if slot.material and slot.material.name == mat.name:
                obj.active_material_index = i
                break


def _setFocusPoint(pluginName: str, distance: float):
    """ Set the active camera's focus point.

        If a focus object is already set, replaces it with the picked object.
        Otherwise sets the numeric focus distance.
    """
    camera = bpy.context.scene.camera

    if not camera or camera.type != 'CAMERA':
        return

    camData = camera.data

    if camData.dof.focus_object:
        # Camera is using a focus object — replace it with the picked object
        if pickedObj := _findObjectByNodePlugin(pluginName):
            camData.dof.focus_object = pickedObj
    else:
        camData.dof.focus_distance = distance
