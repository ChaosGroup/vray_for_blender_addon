# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

# Operators used by lister rows: selection, group/column toggles, sorting and
# active-camera selection. Kept context-safe so they work from the
# Preferences-hosted lister window.

import bpy

from vray_blender.lib.mixin import VRayOperatorBase
from vray_blender.ui.lister import core


class VRAY_OT_lister_select(VRayOperatorBase):
    bl_idname = "vray.lister_select"
    bl_label = "Select Object"
    bl_description = "Select this object in the scene. Hold Shift to extend the selection"
    bl_options = {'UNDO'}

    object_name: bpy.props.StringProperty(options={'HIDDEN'})
    extend: bpy.props.BoolProperty(default=False, options={'HIDDEN'})

    def invoke(self, context, event):
        self.extend = event.shift
        return self.execute(context)

    def execute(self, context):
        obj = context.scene.objects.get(self.object_name)
        if obj is None:
            return {'CANCELLED'}

        if not self.extend:
            for other in context.view_layer.objects:
                other.select_set(False)

        obj.select_set(True)
        context.view_layer.objects.active = obj
        # Redraw viewport/outliner too so the new selection shows there
        core.tagAllRedraw(context)
        return {'FINISHED'}


class VRAY_OT_lister_set_group(VRayOperatorBase):
    bl_idname = "vray.lister_set_group"
    bl_label = "Show Type"
    bl_description = "Show this type in the lister"

    group: bpy.props.StringProperty(options={'HIDDEN'})

    def execute(self, context):
        view = core.getListerView(context)
        if view is not None:
            view.active_group = self.group
        core.tagListerRedraw(context)
        return {'FINISHED'}


class VRAY_OT_lister_toggle_group(VRayOperatorBase):
    bl_idname = "vray.lister_toggle_group"
    bl_label = "Toggle Group"
    bl_description = "Expand or collapse this group (Stacked layout)"

    category: bpy.props.StringProperty(options={'HIDDEN'})
    group: bpy.props.StringProperty(options={'HIDDEN'})

    def execute(self, context):
        view = core.getListerView(context)  # collapse state is per-file
        if view is not None:
            core.toggleGroup(view, self.category, self.group)
        core.tagListerRedraw(context)
        return {'FINISHED'}


class VRAY_OT_lister_toggle_column(VRayOperatorBase):
    bl_idname = "vray.lister_toggle_column"
    bl_label = "Toggle Column"
    bl_description = "Show or hide this column for this type"

    category: bpy.props.StringProperty(options={'HIDDEN'})
    column: bpy.props.StringProperty(options={'HIDDEN'})

    def execute(self, context):
        prefs = core.getListerPrefs(context)  # column visibility is global
        core.toggleColumn(prefs, self.category, self.column)
        core.tagListerRedraw(context)
        return {'FINISHED'}


class VRAY_OT_lister_reset_columns(VRayOperatorBase):
    bl_idname = "vray.lister_reset_columns"
    bl_label = "Set Columns"
    bl_description = "Show this section's default columns, or all of its columns"

    mode: bpy.props.StringProperty(options={'HIDDEN'})  # 'DEFAULT' or 'ALL'

    def execute(self, context):
        prefs = core.getListerPrefs(context)   # column visibility is global
        view = core.getListerView(context)
        category = core.getCategory(view.active_category) if view is not None else None
        if category is not None:
            core.applyColumnPreset(prefs, category, context, showAll=(self.mode == 'ALL'))
        core.tagListerRedraw(context)
        return {'FINISHED'}


class VRAY_OT_lister_set_sort(VRayOperatorBase):
    bl_idname = "vray.lister_set_sort"
    bl_label = "Sort"
    bl_description = "Sort rows by this column. Click again to reverse"

    column: bpy.props.StringProperty(options={'HIDDEN'})

    def execute(self, context):
        view = core.getListerView(context)  # sort is per-file
        if view is None:
            return {'CANCELLED'}
        if view.sort_column == self.column:
            view.sort_reverse = not view.sort_reverse
        else:
            view.sort_column = self.column
            view.sort_reverse = False
        core.tagListerRedraw(context)
        return {'FINISHED'}


class VRAY_OT_lister_select_material_users(VRayOperatorBase):
    bl_idname = "vray.lister_select_material_users"
    bl_label = "Select Users"
    bl_description = "Select the objects that use this material"
    bl_options = {'UNDO'}

    material: bpy.props.StringProperty(options={'HIDDEN'})

    def execute(self, context):
        mtl = bpy.data.materials.get(self.material)
        if mtl is None:
            return {'CANCELLED'}

        for obj in context.view_layer.objects:
            obj.select_set(any(slot.material == mtl for slot in obj.material_slots))

        # Redraw all areas so the selection shows in viewport/outliner too
        core.tagAllRedraw(context)
        return {'FINISHED'}


class VRAY_OT_lister_set_active_camera(VRayOperatorBase):
    bl_idname = "vray.lister_set_active_camera"
    bl_label = "Set Active Camera"
    bl_description = "Make this the scene's active camera"
    bl_options = {'UNDO'}

    camera: bpy.props.StringProperty(options={'HIDDEN'})

    def execute(self, context):
        obj = context.scene.objects.get(self.camera)
        if obj is None or obj.type != 'CAMERA':
            return {'CANCELLED'}
        context.scene.camera = obj
        core.tagListerRedraw(context)
        return {'FINISHED'}


class VRAY_OT_lister_show_asset(VRayOperatorBase):
    bl_idname = "vray.lister_show_asset"
    bl_label = "Show in Assets"

    # Bitmap file path (shown as tooltip) and the Assets-tab locator to select
    path: bpy.props.StringProperty(options={'HIDDEN'})
    locator: bpy.props.StringProperty(options={'HIDDEN'})

    @classmethod
    def description(cls, context, properties):
        path = getattr(properties, 'path', "")
        if path:
            return f"{path}\n\nClick to show this texture in the Assets tab"
        return "Show this texture in the Assets tab"

    def execute(self, context):
        from vray_blender.ui.lister import relink
        view = core.getListerView(context)
        if view is not None:
            view.active_category = 'ASSETS'
        relink.selectOnlyAsset(self.locator)
        core.tagListerRedraw(context)
        return {'FINISHED'}


############################################################
# Solo, copy-to-selected, cleanup, rename, render, pinning, material editor operators.
# These mutate runtime view state in core (core._solo, core._editorMaterial).
############################################################

class VRAY_OT_lister_solo(VRayOperatorBase):
    bl_idname = "vray.lister_solo"
    bl_label = "Solo"
    bl_description = "Hide every other object in this section; click again to restore. Empty name exits solo"
    bl_options = {'UNDO'}

    object_name: bpy.props.StringProperty(options={'HIDDEN'})
    category: bpy.props.StringProperty(options={'HIDDEN'})

    def execute(self, context):
        category = core.getCategory(self.category)
        if category is None:
            return {'CANCELLED'}

        wasSoloed = self.category in core._solo
        prevTarget = core._soloTarget(self.category)
        core._restoreSolo(category, context)

        # Empty name (header "Exit Solo") or clicking the current solo row again just exits
        if not self.object_name or (wasSoloed and prevTarget == self.object_name):
            core.tagAllRedraw(context)
            return {'FINISHED'}

        # Use hide_set (temporary H-key isolate), NOT hide_viewport: solo must not
        # clobber the persistent "disable in viewports" flag shown in the lister
        objects = [o for o in category.enumerate(context) if hasattr(o, 'hide_set')]
        prev = {}
        for o in objects:
            try:
                prev[o.name] = o.hide_get()
                o.hide_set(o.name != self.object_name)
            except Exception:
                pass
        core._solo[self.category] = {'target': self.object_name, 'prev': prev}
        core.tagAllRedraw(context)
        return {'FINISHED'}


class VRAY_OT_lister_copy_to_selected(VRayOperatorBase):
    bl_idname = "vray.lister_copy_to_selected"
    bl_label = "Copy to Selected"
    bl_description = ("Copy the active object's values for this section's columns to the other "
                      "selected objects of the same type")
    bl_options = {'UNDO'}

    category: bpy.props.StringProperty(options={'HIDDEN'})

    def execute(self, context):
        category = core.getCategory(self.category)
        if category is None or not category.selectable:
            return {'CANCELLED'}

        entities = category.enumerate(context)
        selected = [e for e in entities if getattr(e, 'select_get', None) and e.select_get()]
        if len(selected) < 2:
            self.report({'WARNING'}, "Select two or more objects (the active one is the source)")
            return {'CANCELLED'}

        active = context.view_layer.objects.active
        if active not in selected:
            active = selected[0]

        srcKey = category.groupKey(active)
        srcPropGroup = category.getPropGroup(active)
        if srcPropGroup is None:
            return {'CANCELLED'}

        # Copy every editable property, not just visible columns: type-dispatched
        # columns (Intensity / Focal Length) have no single 'attr' and would be missed
        attrs = [p.identifier for p in srcPropGroup.bl_rna.properties
                 if p.identifier != 'rna_type' and not p.is_readonly and p.type != 'POINTER']

        copied = 0
        for entity in selected:
            if entity == active or category.groupKey(entity) != srcKey:
                continue
            dstPropGroup = category.getPropGroup(entity)
            if dstPropGroup is None:
                continue
            for attr in attrs:
                if attr in dstPropGroup.bl_rna.properties:
                    try:
                        setattr(dstPropGroup, attr, getattr(srcPropGroup, attr))
                    except Exception:
                        pass
            copied += 1

        self.report({'INFO'}, f"Copied '{active.name}' to {copied} object(s)")
        core.tagAllRedraw(context)
        return {'FINISHED'}


def _eliminateDuplicates(collection) -> int:
    """ Remap '<base>.NNN' duplicate datablocks back onto '<base>' and remove the now
        unused duplicates (the meshlogic 'Eliminate Duplicates' behaviour). """
    count = 0
    for block in list(collection):
        base, dot, suffix = block.name.rpartition('.')
        if not dot or not suffix.isdigit():
            continue
        original = collection.get(base)
        if original is None or original == block:
            continue
        block.user_remap(original)
        if block.users == 0 and not block.use_fake_user:
            collection.remove(block)
        count += 1
    return count


class VRAY_MT_lister_cleanup(bpy.types.Menu):
    bl_idname = "VRAY_MT_lister_cleanup"
    bl_label = "Cleanup"

    def draw(self, context):
        layout = self.layout
        layout.operator("vray.lister_eliminate_dupes", text="Eliminate Duplicate Materials", icon='MATERIAL').mode = 'MATERIALS'
        layout.operator("vray.lister_eliminate_dupes", text="Eliminate Duplicate Images", icon='IMAGE_DATA').mode = 'IMAGES'
        layout.separator()
        layout.operator("vray.lister_purge_orphans", text="Purge Orphan Data", icon='TRASH')


class VRAY_OT_lister_eliminate_dupes(VRayOperatorBase):
    bl_idname = "vray.lister_eliminate_dupes"
    bl_label = "Eliminate Duplicates"
    bl_description = "Remap '.001/.002' duplicate datablocks back onto the original and remove the duplicates"
    bl_options = {'UNDO'}

    mode: bpy.props.StringProperty(options={'HIDDEN'})  # 'MATERIALS' or 'IMAGES'

    def execute(self, context):
        collection = bpy.data.materials if self.mode == 'MATERIALS' else bpy.data.images
        count = _eliminateDuplicates(collection)
        self.report({'INFO'}, f"Eliminated {count} duplicate(s)")
        core.tagAllRedraw(context)
        return {'FINISHED'}


class VRAY_OT_lister_purge_orphans(VRayOperatorBase):
    bl_idname = "vray.lister_purge_orphans"
    bl_label = "Purge Orphan Data"
    bl_description = "Remove datablocks with no users (and no fake user) from materials, images, textures and node groups"
    bl_options = {'UNDO'}

    def execute(self, context):
        removed = 0
        for collection in (bpy.data.materials, bpy.data.images, bpy.data.textures, bpy.data.node_groups):
            for block in list(collection):
                if block.users == 0 and not block.use_fake_user:
                    collection.remove(block)
                    removed += 1
        self.report({'INFO'}, f"Purged {removed} orphan datablock(s)")
        core.tagAllRedraw(context)
        return {'FINISHED'}


class VRAY_OT_lister_batch_rename(VRayOperatorBase):
    bl_idname = "vray.lister_batch_rename"
    bl_label = "Batch Rename"
    bl_description = "Rename the rows: find/replace, add a prefix/suffix and an optional running number"
    bl_options = {'UNDO'}

    category: bpy.props.StringProperty(options={'HIDDEN'})
    find: bpy.props.StringProperty(name="Find")
    replace: bpy.props.StringProperty(name="Replace")
    prefix: bpy.props.StringProperty(name="Prefix")
    suffix: bpy.props.StringProperty(name="Suffix")
    use_number: bpy.props.BoolProperty(name="Append Number", default=False)
    start: bpy.props.IntProperty(name="Start", default=1, min=0)
    padding: bpy.props.IntProperty(name="Padding", default=2, min=1, max=8)
    selected_only: bpy.props.BoolProperty(name="Selected Only", default=True)

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def draw(self, context):
        layout = self.layout
        row = layout.row()
        row.prop(self, 'find'); row.prop(self, 'replace')
        row = layout.row()
        row.prop(self, 'prefix'); row.prop(self, 'suffix')
        row = layout.row()
        row.prop(self, 'use_number')
        sub = row.row(align=True)
        sub.enabled = self.use_number
        sub.prop(self, 'start'); sub.prop(self, 'padding')
        layout.prop(self, 'selected_only')

    def execute(self, context):
        category = core.getCategory(self.category)
        if category is None:
            return {'CANCELLED'}

        entities = category.enumerate(context)
        if self.selected_only and category.selectable:
            entities = [e for e in entities if getattr(e, 'select_get', None) and e.select_get()]

        number = self.start
        renamed = 0
        for entity in entities:
            if not hasattr(entity, 'name'):
                continue
            name = entity.name
            if self.find:
                name = name.replace(self.find, self.replace)
            name = f"{self.prefix}{name}{self.suffix}"
            if self.use_number:
                name = f"{name}{number:0{self.padding}d}"
                number += 1
            try:
                entity.name = name
                renamed += 1
            except Exception:
                pass
        self.report({'INFO'}, f"Renamed {renamed} row(s)")
        core.tagAllRedraw(context)
        return {'FINISHED'}


class VRAY_OT_lister_render_camera(VRayOperatorBase):
    bl_idname = "vray.lister_render_camera"
    bl_label = "Render From Camera"
    bl_description = "Make this the active camera and start a still render"
    bl_options = {'UNDO'}

    camera: bpy.props.StringProperty(options={'HIDDEN'})

    def execute(self, context):
        obj = context.scene.objects.get(self.camera)
        if obj is None or obj.type != 'CAMERA':
            return {'CANCELLED'}
        context.scene.camera = obj
        core.tagListerRedraw(context)
        try:
            bpy.ops.render.render('INVOKE_DEFAULT')
        except Exception as ex:
            self.report({'WARNING'}, f"Could not start render: {ex}")
        return {'FINISHED'}


class VRAY_OT_lister_toggle_pinned(VRayOperatorBase):
    bl_idname = "vray.lister_toggle_pinned"
    bl_label = "Toggle Pinned"
    bl_description = "Pin or unpin this row so it sorts to the top of its group"

    entity: bpy.props.StringProperty(options={'HIDDEN'})

    def execute(self, context):
        view = core.getListerView(context)  # pinned rows are per-file (scene)
        if view is None:
            return {'CANCELLED'}
        pinned = core._pinnedSet(view)
        pinned.discard(self.entity) if self.entity in pinned else pinned.add(self.entity)
        view.lister_pinned = "\n".join(sorted(pinned))
        core.tagListerRedraw(context)
        return {'FINISHED'}


class VRAY_OT_lister_editor_select_material(VRayOperatorBase):
    bl_idname = "vray.lister_editor_select_material"
    bl_label = "Open Material"
    bl_description = "Show this material's preview and parameters in the editor panel"

    material: bpy.props.StringProperty(options={'HIDDEN'})

    def execute(self, context):
        core._editorMaterial['name'] = self.material
        core.tagListerRedraw(context)
        return {'FINISHED'}


def getRegClasses():
    return (
        VRAY_OT_lister_select,
        VRAY_OT_lister_set_group,
        VRAY_OT_lister_toggle_group,
        VRAY_OT_lister_toggle_column,
        VRAY_OT_lister_reset_columns,
        VRAY_OT_lister_set_sort,
        VRAY_OT_lister_select_material_users,
        VRAY_OT_lister_set_active_camera,
        VRAY_OT_lister_show_asset,
        VRAY_OT_lister_solo,
        VRAY_OT_lister_copy_to_selected,
        VRAY_MT_lister_cleanup,
        VRAY_OT_lister_eliminate_dupes,
        VRAY_OT_lister_purge_orphans,
        VRAY_OT_lister_batch_rename,
        VRAY_OT_lister_render_camera,
        VRAY_OT_lister_toggle_pinned,
        VRAY_OT_lister_editor_select_material,
    )
