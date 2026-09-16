# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
from bpy.props import EnumProperty, IntProperty

from chaos_scatter import lifecycle, recompute, utils


_LIST_GROUPS = (
    ('targets', "Targets", ""),
    ('models', "Models", ""),
    ('area_modifiers', "Areas", ""),
)

def _activeScatterObject(context):
    obj = context.object
    return obj if utils.isScatterObject(obj) else None


class CSCATTER_OT_add(bpy.types.Operator):
    bl_idname = "chaos_scatter.add"
    bl_label = "New Chaos Scatter"
    bl_description = "Create a Chaos Scatter object; the selected geometry objects become distribution targets"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        obj = lifecycle.createScatterObject(context)
        for selObj in context.selected_objects:
            selObj.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        return {'FINISHED'}


class CSCATTER_OT_refresh(bpy.types.Operator):
    bl_idname = "chaos_scatter.refresh"
    bl_label = "Refresh"
    bl_description = "Recompute the scatter preview"

    @classmethod
    def poll(cls, context):
        return _activeScatterObject(context) is not None

    def execute(self, context):
        recompute.refresh(_activeScatterObject(context))
        return {'FINISHED'}


class CSCATTER_OT_list_add(bpy.types.Operator):
    bl_idname = "chaos_scatter.list_add"
    bl_label = "Add Item"
    bl_options = {'REGISTER', 'UNDO'}

    group: EnumProperty(items = _LIST_GROUPS)

    @classmethod
    def poll(cls, context):
        return _activeScatterObject(context) is not None

    def execute(self, context):
        obj = _activeScatterObject(context)
        cs = obj.chaos_scatter
        collection = getattr(cs, self.group)
        collection.add()
        setattr(cs, f'{self.group}_active_index', len(collection) - 1)
        if self.group == 'models':
            lifecycle.rebuildProtoCollections(obj)
        recompute.markDirty(obj)
        return {'FINISHED'}


class CSCATTER_OT_list_remove(bpy.types.Operator):
    bl_idname = "chaos_scatter.list_remove"
    bl_label = "Remove Item"
    bl_options = {'REGISTER', 'UNDO'}

    group: EnumProperty(items = _LIST_GROUPS)

    @classmethod
    def poll(cls, context):
        obj = _activeScatterObject(context)
        return obj is not None

    def execute(self, context):
        obj = _activeScatterObject(context)
        cs = obj.chaos_scatter
        collection = getattr(cs, self.group)
        index = getattr(cs, f'{self.group}_active_index')
        if 0 <= index < len(collection):
            collection.remove(index)
            setattr(cs, f'{self.group}_active_index', min(index, len(collection) - 1))
            if self.group == 'models':
                lifecycle.rebuildProtoCollections(obj)
            recompute.markDirty(obj)
        return {'FINISHED'}


class CSCATTER_OT_list_move(bpy.types.Operator):
    bl_idname = "chaos_scatter.list_move"
    bl_label = "Move Item"
    bl_options = {'REGISTER', 'UNDO'}

    group: EnumProperty(items = _LIST_GROUPS)
    direction: IntProperty(default = 1)  # 1 = down, -1 = up

    @classmethod
    def poll(cls, context):
        return _activeScatterObject(context) is not None

    def execute(self, context):
        obj = _activeScatterObject(context)
        cs = obj.chaos_scatter
        collection = getattr(cs, self.group)
        index = getattr(cs, f'{self.group}_active_index')
        newIndex = index + self.direction
        if 0 <= index < len(collection) and 0 <= newIndex < len(collection):
            collection.move(index, newIndex)
            setattr(cs, f'{self.group}_active_index', newIndex)
            if self.group == 'models':
                # Model order defines proto_index / topo mapping
                lifecycle.rebuildProtoCollections(obj)
            recompute.markDirty(obj)
        return {'FINISHED'}


def _addMenuEntry(self, context):
    # Under V-Ray the command is offered in the V-Ray submenu of this same menu, so drawing it
    # here too would list it twice.
    if not utils.isVRayEngine(context):
        self.layout.operator(CSCATTER_OT_add.bl_idname, icon='OUTLINER_OB_POINTCLOUD')


_CLASSES = (
    CSCATTER_OT_add,
    CSCATTER_OT_refresh,
    CSCATTER_OT_list_add,
    CSCATTER_OT_list_remove,
    CSCATTER_OT_list_move,
)


def register():
    for cls in _CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.VIEW3D_MT_add.append(_addMenuEntry)


def unregister():
    bpy.types.VIEW3D_MT_add.remove(_addMenuEntry)
    for cls in reversed(_CLASSES):
        bpy.utils.unregister_class(cls)
