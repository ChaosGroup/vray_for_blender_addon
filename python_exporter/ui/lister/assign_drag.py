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

    The viewport is not the only place a material can be dropped. An editor can offer targets of
    its own - the V-Ray Proxy Object Hierarchy rollout draws the name of every row that can carry
    a material as a button while a pickup is in progress - and those cannot be found by the
    raycast. They are ordinary buttons, so the modal passes every event outside a viewport on
    rather than consuming it, and the operator behind the button calls dropPickedUpMaterial() to
    take the material and end the pickup. One consequence is that a click outside a viewport no
    longer cancels the pickup: it goes to whatever is underneath, and Esc is the way out.

    The cursor swatch follows the pointer over the editors in _SWATCH_EDITORS too, since the
    pickup is one mode wherever the cursor happens to be and losing the swatch at the edge of the
    viewport would read as it having ended.
"""

import bpy
import blf
import gpu
import numpy as np
from gpu_extras.batch import batch_for_shader
from bpy_extras import view3d_utils

from vray_blender.lib.blender_utils import findViewport, WindowScope
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

# The editors the cursor swatch is drawn over, besides the 3D viewport. Purely a drawing concern -
# nothing routes events by it, since everything outside a viewport is passed through anyway - so
# this is only where _updateHover() tracks a region, where _tagHoverRedraw() repaints, and which
# space types _draw() is registered on. Only the Properties editor for now: that is where the
# V-Ray Proxy Object Hierarchy rollout draws the lists whose rows take a drop.
_SWATCH_EDITORS = {'PROPERTIES'}

# Whether a UIList takes a material drop, by class name. Filled in by whatever draws them through
# setListTakesDrop(), so that this module needs to know nothing about them.
_dropTargetLists = {}


def setListTakesDrop(listId: str, takesDrop: bool):
    """ Record whether the rows of a UIList are drop targets for a material pickup.

        Called from the draw of the list rather than declared once at registration, because it is
        not a property of the list but of what it is drawing: the V-Ray Proxy visibility list
        carries a material only where the shader sets of the proxy file are its objects, and
        carries none at all otherwise. The pickup cursor and its swatch appear over a list only
        while this says a drop would land there.
    """
    _dropTargetLists[listId] = takesDrop


def _tagHoverRedraw(context: bpy.types.Context):
    """ Repaint everything the cursor swatch can appear over, so that it follows the pointer and
        the editor it has just left is cleared of it.

        Not every area: this runs on each mouse move, and the swatch is only ever drawn in a
        viewport or in one of the editors that take a drop.
    """
    for window in context.window_manager.windows:
        for area in window.screen.areas:
            if (area.type == 'VIEW_3D') or (area.type in _SWATCH_EDITORS):
                area.tag_redraw()


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
        self._mat = core.materialFromKey(self.material)
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
        # With no viewport open at all, fall back to the window the button was clicked in: the
        # viewport drop is then out of reach, but the drop targets in the lists still work.
        viewport = findViewport(context, WindowScope.ACTIVE_LAST)
        self._win = viewport.window if viewport else None
        if self._win is None:
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

        # The same swatch over the editors that take a drop. A draw handler is per space type, so
        # drawing it in both places takes two of them; _draw() picks the hovered region out.
        self._propsHandler = bpy.types.SpaceProperties.draw_handler_add(
            self._draw, (), 'WINDOW', 'POST_PIXEL')

        # Paintbrush cursor on every window - the pickup is a session-wide mode. _updateCursor()
        # takes it from here, dropping it back to the ordinary pointer away from a drop target.
        self._cursorWindows = list(context.window_manager.windows)
        self._cursor = 'PAINT_BRUSH'
        for w in self._cursorWindows:
            w.cursor_modal_set(self._cursor)

        with context.temp_override(window=self._win):
            context.window_manager.modal_handler_add(self)

        VRAY_OT_lister_assign_material._activeInstance = self

        # A relay per other window, for viewports outside this one.
        for w in context.window_manager.windows:
            if w != self._win:
                with context.temp_override(window=w):
                    bpy.ops.vray.lister_assign_relay('INVOKE_DEFAULT')

        # Every area, not only the viewports: the editors that draw drop targets have to repaint
        # for those to appear. _finish() does the same when the pickup ends and they go away again.
        core.tagAllRedraw(context)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        if self._superseded:
            return {'CANCELLED'}

        if event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
            self._finish(context)
            return {'CANCELLED'}

        if event.type == 'MOUSEMOVE':
            self._updateHover(context, event)
            if self._highlight:
                # Off a viewport the raycast misses and this clears the tint by itself.
                self._updateTarget(context, event)
            _tagHoverRedraw(context)

            if self._hoverRV3D is None:
                # Outside a viewport the cursor may be over a drop target in another editor, and
                # the move has to reach it. Blender activates the button under the cursor only
                # while handling MOUSEMOVE (ui_handle_button_over), and a button that was never
                # activated ignores the click that follows - so swallowing the move here would
                # silently break the drop even though the click itself is passed on.
                return {'PASS_THROUGH'}

            return {'RUNNING_MODAL'}

        # Let the user orbit / zoom to aim before dropping.
        if event.type in {'MIDDLEMOUSE', 'WHEELUPMOUSE', 'WHEELDOWNMOUSE'}:
            return {'PASS_THROUGH'}

        if event.type == 'LEFTMOUSE':
            self._updateHover(context, event)

            if self._hoverRV3D is None:
                # Not over a viewport: the click belongs to whatever is underneath. A drop target
                # in a list is an ordinary button, and it only fires if the modal lets both the
                # press and the release through, since Blender runs a button's operator on the
                # release. The target ends the pickup itself, through dropPickedUpMaterial().
                return {'PASS_THROUGH'}

            # Only a fresh press drops - the button's own release (which invoked us) is a RELEASE
            # and is ignored, so nothing is assigned by accident on start-up.
            if event.value == 'PRESS':
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
        self._hoverList = ""
        self._localMouse = (0.0, 0.0)

    def _updateHover(self, context, event):
        """ Find the region under the cursor in the event's window and cache the region-local
            mouse position for both drawing and raycasting.

            A _SWATCH_EDITORS region counts as well as a 3D viewport, so that the swatch follows
            the pointer over one - the drop is the same drop, and losing the swatch at the edge of
            the viewport would read as the pickup having ended.

            Only a viewport leaves _hoverRV3D set, and that is what everything keys off: _raycast()
            refuses to run without it, and modal() reads it as 'not over a viewport, pass the event
            on'. A region that is tracked here and one that is not therefore behave the same for
            events - the difference is only whether the swatch can be drawn there. """
        self._resetHover()
        win = context.window
        if win is None:
            return

        mx, my = event.mouse_x, event.mouse_y
        for area in win.screen.areas:
            if area.type != 'VIEW_3D' and area.type not in _SWATCH_EDITORS:
                continue
            region = next((r for r in area.regions if r.type == 'WINDOW'), None)
            if region is None:
                continue
            if region.x <= mx < region.x + region.width and region.y <= my < region.y + region.height:
                self._hoverRegion = region
                self._hoverRegionPtr = region.as_pointer()
                self._hoverRV3D = getattr(area.spaces.active, 'region_3d', None)
                self._hoverList = self._listUnderCursor(context, area, region)
                self._localMouse = (mx - region.x, my - region.y)
                break

        self._updateCursor()

    @staticmethod
    def _listUnderCursor(context, area, region):
        """ The class name of the UIList under the cursor, "" when there is none.

            A list is as fine as this gets: Blender exposes no hit test for a single widget, so a
            hover can be narrowed to the list but never to the one row's name inside it.
        """
        if area.type == 'VIEW_3D':
            return ""

        try:
            with context.temp_override(area=area, region=region):
                uiList = context.ui_list
        except (AttributeError, TypeError, RuntimeError):
            # The context member is simply absent when the cursor is over no list at all.
            return ""

        return type(uiList).__name__ if uiList is not None else ""

    def _overDropTarget(self):
        """ True when the cursor is over a list whose rows take the drop. """
        return _dropTargetLists.get(self._hoverList, False)

    def _updateCursor(self):
        """ Wear the pickup cursor only where a drop would actually land.

            Over the rest of an editor it goes back to the ordinary pointer, so that the cursor
            says where the material can go rather than implying the whole editor takes it.
        """
        cursor = 'PAINT_BRUSH' if (self._hoverRV3D is not None) or self._overDropTarget() \
                 else 'DEFAULT'

        if cursor == getattr(self, '_cursor', None):
            return

        self._cursor = cursor

        # Repeating this is safe: WM_cursor_modal_set() records the cursor to go back to only the
        # first time round, so cursor_modal_restore() still restores the one from before the pickup.
        for w in getattr(self, '_cursorWindows', []):
            w.cursor_modal_set(cursor)

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
        # Draw only in the region the cursor is currently over - a viewport, or one of the editors
        # that take a drop. Registered on both space types, so this runs for every region of each.
        region = bpy.context.region
        if region is None or region.as_pointer() != self._hoverRegionPtr:
            return

        # Outside a viewport the swatch only shows over a list that takes the drop, for the same
        # reason the cursor does: over the rest of the editor there is nothing to drop onto.
        if (self._hoverRV3D is None) and not self._overDropTarget():
            return

        # Keep dpi/72 here, not ui_scale, matching nodes/tools.py _dpiFac.
        uiScale = bpy.context.preferences.system.dpi / 72.0
        x, y = self._localMouse
        size = self._size * uiScale
        gap = 8 * uiScale
        offset = 20 * uiScale

        # What a drop does here is not what it does in the viewport, so it is not offered as such:
        # there is no new slot to add, and the target is a cell of a list rather than the object.
        overEditor = getattr(bpy.context.area, 'type', None) in _SWATCH_EDITORS
        hint = "Click a name to assign  -  Esc: cancel" if overEditor \
               else "Click to assign  -  Alt: new slot  -  Esc: cancel"

        nameSize, hintSize = 13 * uiScale, 11 * uiScale
        textWidth = max(self._textWidth(self._mat.name, nameSize), self._textWidth(hint, hintSize))

        # Offset to the lower-right of the cursor so the swatch does not sit under the pointer.
        px = x + offset
        textX = px + size + gap

        # An editor that takes a drop is a good deal narrower than a viewport, and the region
        # clips rather than scrolls, so mirror the whole block to the left of the cursor when it
        # would run off the right - but only where the left has the room the right lacks.
        if (px + size + gap + textWidth > region.width) and (x - offset - size - gap - textWidth >= 0):
            px = x - offset - size
            textX = px - gap - textWidth

        py = y - size - 6 * uiScale

        gpu.state.blend_set('ALPHA')
        pad = 3 * uiScale
        self._fillRect(px - pad, py - pad, size + 2 * pad, size + 2 * pad, (0.0, 0.0, 0.0, 0.55))
        if self._tex is not None:
            self._drawTexRect(px, py, size, size)
        else:
            self._fillRect(px, py, size, size, self._fallbackColor)
        gpu.state.blend_set('NONE')

        self._drawText(textX, py + size / 2 + 3 * uiScale, self._mat.name, nameSize, (1.0, 1.0, 1.0, 1.0))
        self._drawText(textX, py + size / 2 - 14 * uiScale, hint, hintSize, (0.8, 0.8, 0.8, 1.0))

    @staticmethod
    def _textWidth(text, size):
        """ The width the text will take at this size, to decide which side of the cursor it fits. """
        fontId = 0
        blf.size(fontId, size)
        return blf.dimensions(fontId, text)[0]

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

        # Each handler goes back to the space type it was added to - handing one to the wrong space
        # raises, and the swatch would be left painted over that editor for the rest of the session.
        for spaceType, attr in ((bpy.types.SpaceView3D, '_drawHandler'),
                                (bpy.types.SpaceView3D, '_highlightHandler'),
                                (bpy.types.SpaceProperties, '_propsHandler')):
            handle = getattr(self, attr, None)
            if handle is not None:
                try:
                    spaceType.draw_handler_remove(handle, 'WINDOW')
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
            _tagHoverRedraw(context)
            return {'PASS_THROUGH'}

        if event.type in {'ESC', 'RIGHTMOUSE'} and event.value == 'PRESS':
            active._superseded = True
            active._finish(context)
            return {'CANCELLED'}

        # Drop only over a 3D viewport of this window - the same test the primary modal makes, and
        # everything else falls through for the same reason: a drop target in a list is an
        # ordinary button and takes the click itself.
        if event.type == 'LEFTMOUSE' and event.value == 'PRESS':
            active._updateHover(context, event)
            if active._hoverRV3D is not None:
                assigned = active._tryAssign(context, event)
                active._superseded = True
                active._finish(context)
                if assigned:
                    self.report({'INFO'}, f"Assigned '{active._mat.name}' to '{assigned}'")
                    return {'FINISHED'}
                return {'CANCELLED'}

        return {'PASS_THROUGH'}


############################################################
# The pickup as a drop target outside the viewport sees it
############################################################

def pickedUpMaterial():
    """ The material a pickup is currently carrying, None when none is in progress.

        What an editor tests to know whether to draw its drop targets at all.
    """
    active = VRAY_OT_lister_assign_material._activeInstance

    return active._mat if active is not None else None


def dropPickedUpMaterial(context: bpy.types.Context):
    """ End the pickup in progress and hand back the material it was carrying, None when there is
        none.

        For a target that is hit by Blender's own button handling rather than found by the
        raycast. The pickup is ended the way the relay ends it: the modal is still on the handler
        stack, and marking it superseded is what it notices on its next event.
    """
    active = VRAY_OT_lister_assign_material._activeInstance

    if active is None:
        return None

    material = active._mat
    active._superseded = True
    active._finish(context)

    return material


def getRegClasses():
    return (
        VRAY_OT_lister_assign_material,
        VRAY_OT_lister_assign_relay,
    )
