# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

# Operators used by lister rows: selection, group/column toggles, sorting and
# active-camera selection. Kept context-safe so they work from the
# Preferences-hosted lister window.

import bpy

from vray_blender.ui.lister import core, window
from vray_blender.ui.lister.window import ListerOperatorBase


class VRAY_OT_lister_select(ListerOperatorBase):
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


class VRAY_OT_lister_hide(ListerOperatorBase):
    """ The Outliner's eye column ("Hide in Viewport") is a per-view-layer Base flag, not an
        Object property: ObjectBase is not exposed to Python, so the lister cannot draw it with
        layout.prop() and drives hide_get()/hide_set() through this operator instead.
    """
    bl_idname = "vray.lister_hide"
    bl_label = "Temporarily hide in viewport"
    bl_description = "• Shift to set children"
    bl_options = {'UNDO'}

    object_name: bpy.props.StringProperty(options={'HIDDEN'})
    # Shift held: set the object's children to the same state too
    extend: bpy.props.BoolProperty(default=False, options={'HIDDEN'})

    def invoke(self, context, event):
        self.extend = event.shift
        return self.execute(context)

    def execute(self, context):
        obj = context.view_layer.objects.get(self.object_name)
        if obj is None:
            # Not in this view layer (e.g. in an excluded collection), so it has no Base to hide
            return {'CANCELLED'}

        hidden = not obj.hide_get()
        try:
            obj.hide_set(hidden)
        except Exception as ex:
            self.report({'WARNING'}, f"Could not hide '{self.object_name}': {ex}")
            return {'CANCELLED'}

        changed = [self.object_name]
        if self.extend:
            # Shift sets the whole parenting subtree to the clicked row's new state, the same as
            # the Outliner's eye (outliner_object_set_flag_recursive_fn)
            for child in obj.children_recursive:
                try:
                    child.hide_set(hidden)
                except Exception:
                    # In a collection excluded from this view layer, so it has no Base to hide
                    continue
                changed.append(child.name)

        # Solo hides with the same flag and restores the values it saved. Retarget those so
        # exiting solo keeps this manual toggle instead of reverting it.
        for state in core.soloState.values():
            for name in changed:
                if name in state['prev']:
                    state['prev'][name] = hidden

        # Redraw viewport/outliner too so the new visibility shows there
        core.tagAllRedraw(context)
        return {'FINISHED'}


class VRAY_OT_lister_set_group(ListerOperatorBase):
    bl_idname = "vray.lister_set_group"
    bl_label = "Show Type"
    bl_description = "Show this type in the lister"

    group: bpy.props.StringProperty(options={'HIDDEN'})

    def execute(self, context):
        view = core.getListerView(context)
        if view is None:
            return {'CANCELLED'}

        view.active_group = self.group
        core.tagListerRedraw(context)
        return {'FINISHED'}


class VRAY_OT_lister_toggle_group(ListerOperatorBase):
    bl_idname = "vray.lister_toggle_group"
    bl_label = "Toggle Group"
    bl_description = "Expand or collapse this group (Stacked layout)"

    category: bpy.props.StringProperty(options={'HIDDEN'})
    group: bpy.props.StringProperty(options={'HIDDEN'})

    def execute(self, context):
        view = core.getListerView(context)  # collapse state is per-file
        if view is None:
            return {'CANCELLED'}

        core.toggleGroup(view, self.category, self.group)
        core.tagListerRedraw(context)
        return {'FINISHED'}


class VRAY_OT_lister_toggle_column(ListerOperatorBase):
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


class VRAY_OT_lister_reset_columns(ListerOperatorBase):
    bl_idname = "vray.lister_reset_columns"
    bl_label = "Set Columns"
    bl_description = "Show this section's default columns, or all of its columns"

    mode: bpy.props.StringProperty(options={'HIDDEN'})  # 'DEFAULT' or 'ALL'

    def execute(self, context):
        prefs = core.getListerPrefs(context)   # column visibility is global
        view = core.getListerView(context)
        category = core.getCategory(view.active_category) if view is not None else None
        if category is None:
            return {'CANCELLED'}

        core.applyColumnPreset(prefs, category, showAll=(self.mode == 'ALL'))
        core.tagListerRedraw(context)
        return {'FINISHED'}


class VRAY_OT_lister_set_sort(ListerOperatorBase):
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


def selectMaterialUsers(context, mtl) -> bool:
    """ Select the objects using this material and make the first one active on its slot.
        False, with the selection untouched, if no object in the view layer uses it - found
        before anything is deselected, so a material nothing uses cannot clear the selection. """
    users = []
    for obj in context.view_layer.objects:
        slotIndex = next((i for i, slot in enumerate(obj.material_slots) if slot.material == mtl), -1)
        if slotIndex != -1:
            users.append((obj, slotIndex))

    if not users:
        return False

    owners = {obj for obj, _ in users}
    for obj in context.view_layer.objects:
        obj.select_set(obj in owners)

    # The Properties editor's Material tab and the node editor both follow the active object's
    # active material slot (nodes/tree.py::_getVRayShaderTreeData), not the selection - VBLD-2627.
    obj, slotIndex = users[0]
    context.view_layer.objects.active = obj
    obj.active_material_index = slotIndex

    # Redraw all areas so the selection shows in viewport/outliner too
    core.tagAllRedraw(context)
    return True


class VRAY_OT_lister_select_material_users(ListerOperatorBase):
    bl_idname = "vray.lister_select_material_users"
    bl_label = "Select Users"
    bl_description = "Select the objects that use this material"
    bl_options = {'UNDO'}

    material: bpy.props.StringProperty(options={'HIDDEN'})

    def execute(self, context):
        mtl = bpy.data.materials.get(self.material)
        if mtl is None:
            return {'CANCELLED'}
        if not selectMaterialUsers(context, mtl):
            # Asked for this material's users and it has none: an empty selection says so.
            for obj in context.view_layer.objects:
                obj.select_set(False)
            core.tagAllRedraw(context)
        return {'FINISHED'}


class VRAY_OT_lister_set_active_camera(ListerOperatorBase):
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


class VRAY_OT_lister_show_asset(ListerOperatorBase):
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
        from vray_blender.ui.lister import relink, window
        view = core.getListerView(context)
        if view is not None:
            view.active_category = 'ASSETS'
        # The Assets tab lives in the Scene Lister.
        if not any(w.screen.get(window.VRAY_LISTER_FLAG, False) for w in context.window_manager.windows):
            bpy.ops.vray.lister_open()
        relink.selectOnlyAsset(self.locator)
        core.tagListerRedraw(context)
        return {'FINISHED'}


############################################################
# Solo, copy-to-selected, cleanup, rename, render, pinning, material editor operators.
# These mutate runtime view state in core (core.soloState, core.setEditorMaterial).
############################################################

class VRAY_OT_lister_solo(ListerOperatorBase):
    bl_idname = "vray.lister_solo"
    bl_label = "Solo"
    bl_description = ("Isolate this row: hide everything that isn't (or doesn't use) it. "
                      "Shift/Ctrl-click to solo several at once; click again to restore. "
                      "Empty name exits solo")
    bl_options = {'UNDO'}

    # Drawn from both the table rows (Object Lister) and the material list (Material Manager)
    _listerKinds = ('LISTER', 'MATERIALS')

    object_name: bpy.props.StringProperty(options={'HIDDEN'})
    category: bpy.props.StringProperty(options={'HIDDEN'})
    # Shift/Ctrl held: add/remove this row from the solo set instead of replacing it
    extend: bpy.props.BoolProperty(default=False, options={'HIDDEN'})

    def invoke(self, context, event):
        self.extend = event.shift or event.ctrl
        return self.execute(context)

    def execute(self, context):
        category = core.getCategory(self.category)
        if category is None:
            return {'CANCELLED'}

        targets = set(core.soloTargets(self.category))
        # Restore to the pre-solo baseline first, then re-hide for the new target set, so 'prev'
        # always captures the original visibility (not a half-soloed state)
        core.restoreSolo(category, context)

        if not self.object_name:
            # Exit solo outright. No UI draws this, but a keymap or script can.
            if not targets:
                return {'CANCELLED'}
            core.tagAllRedraw(context)
            return {'FINISHED'}

        if self.extend:
            # Multi-solo: toggle this row in/out of the set
            targets.symmetric_difference_update({self.object_name})
        else:
            # Plain click: solo only this, or exit if it was already the sole soloed row
            targets = set() if targets == {self.object_name} else {self.object_name}

        if not targets:
            core.tagAllRedraw(context)
            return {'FINISHED'}

        # Use hide_set (temporary H-key isolate), NOT hide_viewport: solo must not
        # clobber the persistent "disable in viewports" flag shown in the lister
        objects = [o for o in category.soloScope(context) if hasattr(o, 'hide_set')]
        prev = {}
        for o in objects:
            try:
                prev[o.name] = o.hide_get()
                o.hide_set(not any(category.soloMatches(o, t) for t in targets))
            except Exception:
                pass
        core.soloState[self.category] = {'targets': sorted(targets), 'prev': prev}
        core.tagAllRedraw(context)
        return {'FINISHED'}


class VRAY_OT_lister_copy_to_selected(ListerOperatorBase):
    bl_idname = "vray.lister_copy_to_selected"
    bl_label = "Copy to Selected"
    bl_description = ("Copy the active object's values for this section's columns to the other "
                      "selected objects of the same type")
    bl_options = {'UNDO'}

    # drawHeaderExtras() puts this in both the Object Lister's category header and the
    # Material Manager's header.
    _listerKinds = ('LISTER', 'MATERIALS')

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
        # Blender's duplicate suffix is always exactly three digits. Any other numeric suffix is
        # the user's own naming - an image sequence, a UDIM tile (.1001), a version (.2024) - and
        # merging those away would destroy deliberately named datablocks.
        if not dot or len(suffix) != 3 or not suffix.isdigit():
            continue
        original = collection.get(base)
        if original is None or original == block:
            continue
        # user_remap() clears use_fake_user, so read it first: afterwards every duplicate looks
        # unused and unprotected, and a datablock the user explicitly kept would be deleted.
        keepProtected = block.use_fake_user
        block.user_remap(original)
        if keepProtected:
            block.use_fake_user = True  # restore the protection user_remap dropped
        elif block.users == 0:
            collection.remove(block)
        count += 1
    return count


class VRAY_MT_lister_cleanup(bpy.types.Menu):
    bl_idname = "VRAY_MT_lister_cleanup"
    bl_label = "Cleanup"

    @classmethod
    def poll(cls, context):
        return window.isListerOpen(context, 'LISTER', 'MATERIALS')

    def draw(self, context):
        layout = self.layout
        layout.operator("vray.lister_eliminate_dupes", text="Eliminate Duplicate Materials", icon='MATERIAL').mode = 'MATERIALS'
        layout.operator("vray.lister_eliminate_dupes", text="Eliminate Duplicate Images", icon='IMAGE_DATA').mode = 'IMAGES'
        layout.separator()
        layout.operator("vray.lister_purge_orphans", text="Purge Orphan Data", icon='TRASH')


class VRAY_OT_lister_eliminate_dupes(ListerOperatorBase):
    bl_idname = "vray.lister_eliminate_dupes"
    bl_label = "Eliminate Duplicates"
    bl_description = "Remap '.001/.002' duplicate datablocks back onto the original and remove the duplicates"
    bl_options = {'UNDO'}

    _listerKinds = ('LISTER', 'MATERIALS')

    mode: bpy.props.StringProperty(options={'HIDDEN'})  # 'MATERIALS' or 'IMAGES'

    def execute(self, context):
        collection = bpy.data.materials if self.mode == 'MATERIALS' else bpy.data.images
        count = _eliminateDuplicates(collection)
        self.report({'INFO'}, f"Eliminated {count} duplicate(s)")
        core.tagAllRedraw(context)
        return {'FINISHED'} if count else {'CANCELLED'}


class VRAY_OT_lister_purge_orphans(ListerOperatorBase):
    bl_idname = "vray.lister_purge_orphans"
    bl_label = "Purge Orphan Data"
    bl_description = "Remove datablocks with no users (and no fake user) from materials, images, textures and node groups"
    bl_options = {'UNDO'}

    _listerKinds = ('LISTER', 'MATERIALS')

    def execute(self, context):
        removed = 0
        for collection in (bpy.data.materials, bpy.data.images, bpy.data.textures, bpy.data.node_groups):
            for block in list(collection):
                if block.users == 0 and not block.use_fake_user:
                    collection.remove(block)
                    removed += 1
        self.report({'INFO'}, f"Purged {removed} orphan datablock(s)")
        core.tagAllRedraw(context)
        return {'FINISHED'} if removed else {'CANCELLED'}


class VRAY_OT_lister_batch_rename(ListerOperatorBase):
    bl_idname = "vray.lister_batch_rename"
    bl_label = "Batch Rename"
    bl_description = "Rename the rows: find/replace, add a prefix/suffix and an optional running number"
    bl_options = {'UNDO'}

    _listerKinds = ('LISTER', 'MATERIALS')

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
        row.prop(self, 'find')
        row.prop(self, 'replace')
        row = layout.row()
        row.prop(self, 'prefix')
        row.prop(self, 'suffix')
        row = layout.row()
        row.prop(self, 'use_number')
        sub = row.row(align=True)
        sub.enabled = self.use_number
        sub.prop(self, 'start')
        sub.prop(self, 'padding')
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
        return {'FINISHED'} if renamed else {'CANCELLED'}


class VRAY_OT_lister_render_camera(ListerOperatorBase):
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


class VRAY_OT_lister_toggle_pinned(ListerOperatorBase):
    bl_idname = "vray.lister_toggle_pinned"
    bl_label = "Toggle Pinned"
    bl_description = "Pin or unpin this row so it sorts to the top of its group"

    entity: bpy.props.StringProperty(options={'HIDDEN'})

    def execute(self, context):
        view = core.getListerView(context)  # pinned rows are per-file (scene)
        if view is None:
            return {'CANCELLED'}
        pinned = core.pinnedSet(view)
        pinned.symmetric_difference_update({self.entity})
        view.lister_pinned = "\n".join(sorted(pinned))
        core.tagListerRedraw(context)
        return {'FINISHED'}


class VRAY_OT_lister_editor_select_material(ListerOperatorBase):
    bl_idname = "vray.lister_editor_select_material"
    bl_label = "Open Material"
    bl_description = "Show this material's preview and parameters in the editor panel"
    bl_options = {'UNDO'}  # with Sync Selection on, this moves the scene selection

    _listerKinds = ('MATERIALS',)

    material: bpy.props.StringProperty(options={'HIDDEN'})

    def execute(self, context):
        core.setEditorMaterial(self.material)

        # Sync Selection: bring the rest of Blender along, so the Properties editor's Material
        # tab and the node editor show what the Material Manager is showing.
        prefs = core.getListerPrefs(context)
        mtl = bpy.data.materials.get(self.material)
        if prefs.material_sync_selection and mtl is not None:
            selectMaterialUsers(context, mtl)

        core.tagListerRedraw(context)
        return {'FINISHED'}


class VRAY_OT_lister_refresh_previews(ListerOperatorBase):
    bl_idname = "vray.lister_refresh_previews"
    bl_label = "Refresh Previews"
    bl_description = "Re-render every material preview. Use after changes the previews missed"

    _listerKinds = ('MATERIALS',)

    def execute(self, context):
        from vray_blender.nodes import utils as NodesUtils

        # Clearing the image reschedules the render on the next draw, without going through the
        # depsgraph - so this costs no re-export, and reaches materials no object uses.
        count = 0
        for mtl in bpy.data.materials:
            if mtl.preview is not None:
                mtl.preview.reload()
                count += 1

        # Also invalidate the parameters pane's own preview widget.
        NodesUtils.loosePreviewToken['n'] += 1

        core.tagListerRedraw(context)
        self.report({'INFO'}, f"Refreshing {count} material preview(s)")
        return {'FINISHED'}


def _hiddenByFilter(context) -> bool:
    """ True if 'Selected Only' is on, which hides a material nothing uses yet. """
    view = core.getListerView(context)
    return bool(view is not None and view.show_selected_only)


class VRAY_OT_lister_new_material(ListerOperatorBase):
    bl_idname = "vray.lister_new_material"
    bl_label = "New Material"
    bl_description = "Create a V-Ray material and open it in the editor"
    bl_options = {'UNDO'}

    _listerKinds = ('MATERIALS',)

    def execute(self, context):
        from vray_blender.nodes import tree_defaults

        mtl = bpy.data.materials.new(name="Material")
        tree_defaults.addMaterialNodeTree(mtl)
        # Drop Blender's own Principled + Output, as nodes/operators/add_tree.py does.
        tree_defaults.removeNonVRayNodes(mtl.node_tree)

        core.setEditorMaterial(mtl.name)
        core.tagListerRedraw(context)
        if _hiddenByFilter(context):
            self.report({'WARNING'}, f"Created '{mtl.name}' - turn off Selected Only to see it")
        else:
            self.report({'INFO'}, f"Created '{mtl.name}'")
        return {'FINISHED'}


class VRAY_OT_lister_duplicate_material(ListerOperatorBase):
    bl_idname = "vray.lister_duplicate_material"
    bl_label = "Duplicate Material"
    bl_description = "Copy the material open in the editor and open the copy"
    bl_options = {'UNDO'}

    _listerKinds = ('MATERIALS',)

    def execute(self, context):
        mtl = bpy.data.materials.get(core.editorMaterialName())
        if mtl is None:
            self.report({'WARNING'}, "No material to duplicate")
            return {'CANCELLED'}

        from vray_blender.nodes import utils as NodesUtils

        copy = mtl.copy()
        # The copy's sockets all report is_linked False until Blender revalidates the tree.
        NodesUtils.revalidateTreeLinks(copy.node_tree)

        core.setEditorMaterial(copy.name)
        core.tagListerRedraw(context)
        if _hiddenByFilter(context):
            self.report({'WARNING'}, "Duplicated - turn off Selected Only to see the copy")
        else:
            self.report({'INFO'}, f"Duplicated '{mtl.name}' as '{copy.name}'")
        return {'FINISHED'}


def getRegClasses():
    return (
        VRAY_OT_lister_select,
        VRAY_OT_lister_hide,
        VRAY_OT_lister_set_group,
        VRAY_OT_lister_toggle_group,
        VRAY_OT_lister_toggle_column,
        VRAY_OT_lister_reset_columns,
        VRAY_OT_lister_set_sort,
        VRAY_OT_lister_select_material_users,
        VRAY_OT_lister_refresh_previews,
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
        VRAY_OT_lister_new_material,
        VRAY_OT_lister_duplicate_material,
    )
