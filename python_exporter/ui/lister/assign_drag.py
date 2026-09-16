# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

""" Drag-and-drop material assignment for the Scene Lister.

    A grab handle on a material row starts this modal operator: the user "picks up" the
    material, a preview of it follows the cursor, and clicking an object in any 3D viewport
    assigns the material to that object (through its material slots). Alt-click adds it as a
    new slot instead of replacing the active one; Esc / right-click cancels.

    The cursor swatch is the full material preview thumbnail when the Material Editor is in its
    Thumbnails ("with previews") mode, and a small icon-sized swatch otherwise.

    NOTE on "drag": Blender fires a panel-button operator on mouse RELEASE (not PRESS), so the
    drag cannot be a real press-hold from the button. Instead the click "picks up" the material
    and the next viewport click drops it - the preview following the cursor sells the same feel.
    The modal only acts on a fresh LEFTMOUSE PRESS, so the button's own release never triggers a
    stray drop.
"""

import bpy
import blf
import gpu
import numpy as np
from gpu_extras.batch import batch_for_shader
from bpy_extras import view3d_utils

from vray_blender.lib.mixin import VRayOperatorBase
from vray_blender.ui import gpu_overlay
from vray_blender.ui.lister import core


# The image shader for the cursor swatch, created lazily on first draw: from_builtin() raises in
# background mode, so it must not run at import time (the lister package is imported during
# register, incl. headless). The flat-color one is gpu_overlay.colorShader().
_imageShaderInstance = None


def _imageShader():
    global _imageShaderInstance
    if _imageShaderInstance is None:
        _imageShaderInstance = gpu.shader.from_builtin('IMAGE')
    return _imageShaderInstance


# Texture coordinates for a quad with bottom-left origin (matches the preview pixel order).
_QUAD_UVS = ((0.0, 0.0), (1.0, 0.0), (1.0, 1.0), (0.0, 1.0))

# Cursor swatch size in pixels: full preview when the editor shows thumbnails, icon-sized otherwise.
_PREVIEW_SIZE = 96
_ICON_SIZE = 28


class VRAY_OT_lister_assign_material(VRayOperatorBase):
    bl_idname = "vray.lister_assign_material"
    bl_label = "Assign Material"
    bl_description = ("Pick up this material, then click an object in the 3D viewport to assign it. "
                      "A preview of the material follows the cursor. "
                      "Alt-click adds it as a new material slot; Esc / right-click cancels")
    bl_options = {'UNDO', 'INTERNAL'}

    material: bpy.props.StringProperty(options={'HIDDEN'})

    # The modal instance currently picking up a material, if any. Re-clicking 'Assign Material'
    # must replace it rather than stack a second instance - each instance pushes its own
    # cursor_modal_set(), so N stacked clicks would need N more clicks to unwind the cursor back
    # to normal.
    _activeInstance = None
    _superseded = False

    def invoke(self, context, event):
        self._mat = bpy.data.materials.get(self.material)
        if self._mat is None:
            return {'CANCELLED'}

        active = VRAY_OT_lister_assign_material._activeInstance
        if active is not None:
            active._superseded = True
            active._finish(context)

        # The lister lives in its own (Preferences) window, which has no viewport. Blender delivers
        # mouse events per-window and a modal handler is bound to a single window, so a modal added
        # in the lister window would never see the viewport's mouse - find the window that actually
        # hosts a 3D viewport and bind the modal there instead.
        self._win = self._findViewportWindow(context)
        if self._win is None:
            self.report({'WARNING'}, "Open a 3D viewport to drop the material onto")
            return {'CANCELLED'}

        self._buildSwatch(context)
        self._resetHover()

        # Drop-target highlight (lister setting): tint the region of the hovered mesh that will
        # actually receive the material. Built on hover and drawn in world space (POST_VIEW).
        prefs = core.getListerPrefs(context)
        self._highlight = prefs.assign_highlight
        self._highlightColor = gpu_overlay.markedObjectColor(context, 0.35)
        self._clearTarget()

        self._drawHandler = bpy.types.SpaceView3D.draw_handler_add(self._draw, (), 'WINDOW', 'POST_PIXEL')
        self._highlightHandler = bpy.types.SpaceView3D.draw_handler_add(
            self._drawHighlight, (), 'WINDOW', 'POST_VIEW')

        # Paintbrush cursor on every window - the pickup is a session-wide mode.
        self._cursorWindows = list(context.window_manager.windows)
        for w in self._cursorWindows:
            w.cursor_modal_set('PAINT_BRUSH')

        with context.temp_override(window=self._win):
            context.window_manager.modal_handler_add(self)

        VRAY_OT_lister_assign_material._activeInstance = self

        # A relay per other window, for viewports outside this one.
        for w in context.window_manager.windows:
            if w != self._win:
                with context.temp_override(window=w):
                    bpy.ops.vray.lister_assign_relay('INVOKE_DEFAULT')

        gpu_overlay.tagRedrawView3D()
        return {'RUNNING_MODAL'}

    def _findViewportWindow(self, context):
        """ The window hosting a usable 3D viewport, preferring one other than the window the
            button was clicked in (the lister's Preferences window). """
        windows = sorted(context.window_manager.windows,
                         key=lambda w: 0 if w != context.window else 1)
        for win in windows:
            for area in win.screen.areas:
                if area.type != 'VIEW_3D':
                    continue
                region = next((r for r in area.regions if r.type == 'WINDOW'), None)
                if region is not None and getattr(area.spaces.active, 'region_3d', None) is not None:
                    return win
        return None

    def modal(self, context, event):
        if self._superseded:
            return {'CANCELLED'}

        if event.type == 'MOUSEMOVE':
            self._updateHover(context, event)
            if self._highlight:
                self._updateTarget(context, event)
            gpu_overlay.tagRedrawView3D()
            return {'RUNNING_MODAL'}

        if event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
            self._finish(context)
            return {'CANCELLED'}

        # Let the user orbit / zoom to aim before dropping.
        if event.type in {'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE'}:
            return {'PASS_THROUGH'}

        # Only a fresh press drops - the button's own release (which invoked us) is a RELEASE and
        # is ignored, so nothing is assigned by accident on start-up.
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            self._updateHover(context, event)
            assigned = self._tryAssign(context, event)
            self._finish(context)
            if assigned:
                self.report({'INFO'}, f"Assigned '{self._mat.name}' to '{assigned}'")
                return {'FINISHED'}
            return {'CANCELLED'}

        return {'RUNNING_MODAL'}

    ############################################################
    # Assignment
    ############################################################

    def _raycast(self, context):
        """ Raycast the hovered viewport. Returns (obj, matIndex) for the ORIGINAL object under the
            cursor and the material slot of the face that was hit, or None on a miss. """
        if self._hoverRegion is None or self._hoverRV3D is None:
            return None

        coord = self._localMouse
        origin = view3d_utils.region_2d_to_origin_3d(self._hoverRegion, self._hoverRV3D, coord)
        direction = view3d_utils.region_2d_to_vector_3d(self._hoverRegion, self._hoverRV3D, coord)

        depsgraph = context.evaluated_depsgraph_get()
        # scene.ray_cast returns the ORIGINAL object (rna_Scene_ray_cast calls DEG_get_original),
        # but the face index is into the EVALUATED mesh the BVH was built from - so the index has
        # to be resolved against the evaluated copy, not obj.data.
        hit, _loc, _nrm, faceIndex, obj, _mtx = context.scene.ray_cast(depsgraph, origin, direction)
        if not hit or obj is None:
            return None
        return obj, self._faceMaterialIndex(obj.evaluated_get(depsgraph), faceIndex)

    @staticmethod
    def _faceMaterialIndex(evalObj, faceIndex) -> int:
        """ The material slot index of a face (from ray_cast) on the evaluated object. Defaults to
            slot 0 when the face / mesh can't provide one. """
        polygons = getattr(getattr(evalObj, 'data', None), 'polygons', None)
        if polygons is None or faceIndex is None or not (0 <= faceIndex < len(polygons)):
            return 0
        return polygons[faceIndex].material_index

    def _tryAssign(self, context, event) -> str:
        """ Assign the material to the slot of the face under the cursor. Returns the object name
            on success, "" otherwise. """
        result = self._raycast(context)
        if result is None:
            return ""
        obj, matIndex = result
        return self._assignTo(obj, matIndex, event.alt)

    def _assignTo(self, obj, matIndex: int, addSlot: bool) -> str:
        """ Assign self._mat to obj: replace the material in the slot of the pointed-at face (or
            create a slot if the object has none), or - with Alt - always append a new slot. """
        data = getattr(obj, 'data', None)
        if data is None or not hasattr(data, 'materials'):
            self.report({'WARNING'}, f"'{obj.name}' cannot hold a material")
            return ""

        slots = obj.material_slots
        if addSlot or len(slots) == 0:
            data.materials.append(self._mat)
            obj.active_material_index = len(obj.material_slots) - 1
        else:
            idx = matIndex if 0 <= matIndex < len(slots) else obj.active_material_index
            obj.material_slots[idx].material = self._mat
            obj.active_material_index = idx  # follow the drop so the slot list reflects it
        return obj.name

    ############################################################
    # Hover tracking (which viewport / region the cursor is over)
    ############################################################

    def _resetHover(self):
        self._hoverRegion = None
        self._hoverRegionPtr = 0
        self._hoverRV3D = None
        self._localMouse = (0.0, 0.0)

    def _updateHover(self, context, event):
        """ Find the 3D viewport region under the cursor in the event's window and cache the
            region-local mouse position for both drawing and raycasting. """
        self._resetHover()
        win = context.window
        if win is None:
            return

        mx, my = event.mouse_x, event.mouse_y
        for area in win.screen.areas:
            if area.type != 'VIEW_3D':
                continue
            region = next((r for r in area.regions if r.type == 'WINDOW'), None)
            if region is None:
                continue
            if region.x <= mx < region.x + region.width and region.y <= my < region.y + region.height:
                self._hoverRegion = region
                self._hoverRegionPtr = region.as_pointer()
                self._hoverRV3D = getattr(area.spaces.active, 'region_3d', None)
                self._localMouse = (mx - region.x, my - region.y)
                return

    ############################################################
    # Drop-target highlight (region of the mesh that will receive the material)
    ############################################################

    def _clearTarget(self):
        self._targetKey = None
        self._targetBatch = None
        self._targetMatrix = None

    def _updateTarget(self, context, event):
        """ Raycast the hovered viewport and cache a tint batch of the region that a drop would
            change on the object under the cursor. Rebuilt only when the target (object, pointed-at
            slot, or the Alt = new-slot modifier) changes, so mouse-move stays cheap on heavy meshes. """
        result = self._raycast(context)
        if result is None:
            self._clearTarget()
            return

        obj, matIndex = result
        key = (obj.name, bool(event.alt), matIndex)
        if key == self._targetKey:
            return  # same target - keep the cached batch

        self._targetKey = key
        depsgraph = context.evaluated_depsgraph_get()
        self._targetBatch, self._targetMatrix = self._buildRegionBatch(obj, depsgraph, matIndex, event.alt)

    def _buildRegionBatch(self, obj, depsgraph, matIndex: int, addSlot: bool):
        """ A TRIS batch (in local space, drawn under matrix_world) of the faces a drop would
            change: the whole mesh for a single-slot object or an Alt new-slot drop, otherwise
            the faces of the pointed-at material slot. Returns (batch, matrix) or (None, None). """
        materialIndex = None
        if not (addSlot or len(obj.material_slots) <= 1):
            materialIndex = matIndex if 0 <= matIndex < len(obj.material_slots) \
                            else obj.active_material_index

        batch = gpu_overlay.buildTintBatch(obj, depsgraph, materialIndex=materialIndex)
        return (batch, gpu_overlay.frozenWorldMatrix(obj)) if batch else (None, None)

    def _drawHighlight(self):
        if not self._highlight or self._targetBatch is None or self._targetMatrix is None:
            return
        region = bpy.context.region
        if region is None or region.as_pointer() != self._hoverRegionPtr:
            return

        gpu_overlay.drawObjectOverlays(
            ((self._targetBatch, self._targetMatrix, self._highlightColor),))

    ############################################################
    # Cursor swatch
    ############################################################

    def _buildSwatch(self, context):
        """ Build the GPU texture for the material preview once, plus a flat-colour fallback
            (the material's viewport colour) for when no preview is available yet. """
        prefs = core.getListerPrefs(context)
        showPreview = prefs.material_editor_list_display == 'THUMBNAILS'
        self._size = _PREVIEW_SIZE if showPreview else _ICON_SIZE
        self._tex = None

        try:
            self._fallbackColor = tuple(self._mat.diffuse_color)
        except Exception:
            self._fallbackColor = (0.5, 0.5, 0.5, 1.0)

        try:
            preview = self._mat.preview_ensure()
            w, h = preview.image_size
            if w and h:
                pixels = np.array(preview.image_pixels_float[:], dtype=np.float32)
                if pixels.size == w * h * 4:
                    buf = gpu.types.Buffer('FLOAT', w * h * 4, pixels)
                    self._tex = gpu.types.GPUTexture((w, h), format='RGBA32F', data=buf)
        except Exception:
            self._tex = None

    def _draw(self):
        # Draw only in the viewport region the cursor is currently over.
        region = bpy.context.region
        if region is None or region.as_pointer() != self._hoverRegionPtr:
            return

        # Keep dpi/72 here, not ui_scale, matching nodes/tools.py _dpiFac.
        uiScale = bpy.context.preferences.system.dpi / 72.0
        x, y = self._localMouse
        size = self._size * uiScale
        # Offset to the lower-right of the cursor so the swatch does not sit under the pointer.
        px = x + 20 * uiScale
        py = y - size - 6 * uiScale

        gpu.state.blend_set('ALPHA')
        pad = 3 * uiScale
        self._fillRect(px - pad, py - pad, size + 2 * pad, size + 2 * pad, (0.0, 0.0, 0.0, 0.55))
        if self._tex is not None:
            self._drawTexRect(px, py, size, size)
        else:
            self._fillRect(px, py, size, size, self._fallbackColor)
        gpu.state.blend_set('NONE')

        textX = px + size + 8 * uiScale
        self._drawText(textX, py + size / 2 + 3 * uiScale, self._mat.name, 13 * uiScale, (1.0, 1.0, 1.0, 1.0))
        self._drawText(textX, py + size / 2 - 14 * uiScale,
                       "Click to assign  -  Alt: new slot  -  Esc: cancel", 11 * uiScale, (0.8, 0.8, 0.8, 1.0))

    def _fillRect(self, x, y, w, h, color):
        shader = gpu_overlay.colorShader()
        verts = ((x, y), (x + w, y), (x + w, y + h), (x, y + h))
        batch = batch_for_shader(shader, 'TRI_FAN', {'pos': verts})
        shader.bind()
        shader.uniform_float('color', color)
        batch.draw(shader)

    def _drawTexRect(self, x, y, w, h):
        shader = _imageShader()
        verts = ((x, y), (x + w, y), (x + w, y + h), (x, y + h))
        batch = batch_for_shader(shader, 'TRI_FAN', {'pos': verts, 'texCoord': _QUAD_UVS})
        shader.bind()
        shader.uniform_sampler('image', self._tex)
        batch.draw(shader)

    def _drawText(self, x, y, text, size, color):
        fontId = 0
        blf.size(fontId, size)
        blf.enable(fontId, blf.SHADOW)
        blf.shadow(fontId, 3, 0.0, 0.0, 0.0, 0.9)
        blf.color(fontId, *color)
        blf.position(fontId, x, y, 0.0)
        blf.draw(fontId, text)
        blf.disable(fontId, blf.SHADOW)

    ############################################################

    def cancel(self, context):
        # File load / window close force-cancels the modal without calling modal(), and Blender only
        # runs this hook if the class defines it. Without it the draw handlers and the paintbrush
        # cursor leak, and _activeInstance points at a freed operator that raises on every access.
        self._finish(context)

    def _finish(self, context):
        if VRAY_OT_lister_assign_material._activeInstance is self:
            VRAY_OT_lister_assign_material._activeInstance = None

        for attr in ('_drawHandler', '_highlightHandler'):
            handle = getattr(self, attr, None)
            if handle is not None:
                try:
                    bpy.types.SpaceView3D.draw_handler_remove(handle, 'WINDOW')
                except (ValueError, RuntimeError):
                    pass
                setattr(self, attr, None)

        self._clearTarget()

        for w in getattr(self, '_cursorWindows', []):
            try:
                w.cursor_modal_restore()
            except Exception:
                pass
        self._cursorWindows = []

        self._tex = None
        gpu_overlay.tagRedrawView3D()
        core.tagAllRedraw(context)


class VRAY_OT_lister_assign_relay(VRayOperatorBase):
    """ Companion modal for every window but the primary assign modal's. Forwards hover and drop
        to the active assign instance, and cancels the pickup on Esc / right-click. """
    bl_idname = "vray.lister_assign_relay"
    bl_label = "Assign Material Relay"
    bl_options = {'UNDO', 'INTERNAL'}

    def invoke(self, context, event):
        self._target = VRAY_OT_lister_assign_material._activeInstance
        if self._target is None:
            return {'CANCELLED'}
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        active = VRAY_OT_lister_assign_material._activeInstance
        if active is not self._target:
            return {'CANCELLED'}  # the pickup already ended (drop / cancel / superseded)

        if event.type == 'MOUSEMOVE':
            # Track the viewport in this window and pass the event through.
            active._updateHover(context, event)
            if active._highlight:
                active._updateTarget(context, event)
            gpu_overlay.tagRedrawView3D()
            return {'PASS_THROUGH'}

        if event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
            active._superseded = True
            active._finish(context)
            return {'CANCELLED'}

        # Drop only over a 3D viewport of this window.
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            active._updateHover(context, event)
            if active._hoverRegion is not None:
                assigned = active._tryAssign(context, event)
                active._superseded = True
                active._finish(context)
                if assigned:
                    self.report({'INFO'}, f"Assigned '{active._mat.name}' to '{assigned}'")
                    return {'FINISHED'}
                return {'CANCELLED'}

        return {'PASS_THROUGH'}


def getRegClasses():
    return (
        VRAY_OT_lister_assign_material,
        VRAY_OT_lister_assign_relay,
    )
