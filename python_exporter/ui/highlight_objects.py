# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

""" Viewport highlight for the objects picked in a V-Ray object selector.

    Every selector UI (include / exclude lists, multi-object lists, single-object fields and the
    selector nodes) draws a small group of toggle buttons which start a modal operator drawing the
    selected objects in the 3D viewport. Three styles are exposed via the 'style' property:
      BOX  - a bounding-box wireframe around each object.
      FILL - a semi-transparent tint over each object's faces.
      XRAY - the same tint, drawn through occluding geometry.

    Objects with no bounding box and no mesh (lights, empties, cameras) are marked with a fixed-size
    box at their origin instead, so light selectors work too.

    The highlight stays on until the lit button is clicked again, another style / selector is picked,
    or Esc is pressed. The modal passes every other event through, so the viewport stays navigable -
    which is the point of a toggle rather than a press-and-hold (Blender fires a panel-button
    operator on mouse RELEASE anyway, so a hold could never own the click that started it).

    The selector hosting the buttons is passed in through the 'highlightHost' context pointer (the
    VRAY_OT_simple_button pattern). A host only has to implement getSelectorObjects(context).
"""

import bpy

from vray_blender.lib.mixin import VRayOperatorBase
from vray_blender.ui import gpu_overlay


# Highlight style => (button icon, overlay alpha, label, description). The single source for the
# buttons, the overlay colors, the 'style' enum items and the per-style button tooltips.
_STYLES = {
    'BOX':  ('SHADING_BBOX',  1.0,  "Box",   "Draw a bounding box around each selected object"),
    'FILL': ('SHADING_SOLID', 0.25, "Fill",  "Draw a semi-transparent tint over each selected object"),
    'XRAY': ('XRAY',          0.35, "X-Ray", "Draw the tint through other objects, so selected "
                                             "objects hidden behind geometry stay visible"),
}

_STYLE_ICON, _STYLE_ALPHA, _STYLE_LABEL, _STYLE_DESC = range(4)

# The (host pointer, style) of the running highlight modal, or None. Read by the selector UI to
# depress the matching button, keyed on the host so only the active selector's button lights up.
#
# NOTE: memfile undo reallocates the host, so after an undo the overlay is drawn with no button
# depressed and it takes two clicks to switch off. No cheap stable id exists - path_from_id() raises
# for the dynamically registered TMPL_<plugin>_<attr> groups that host most selectors. Esc still
# clears it, so it is never stuck.
_activeHighlight = None


def drawHighlightButtons(layout: bpy.types.UILayout, host: bpy.types.AnyType,
                         enabled=True, label="Viewport Highlight"):
    """ Draw the viewport highlight toggle buttons for an object selector.

    Args:
        layout (bpy.types.UILayout): parent layout.
        host (bpy.types.AnyType): the selector hosting the buttons. Must implement
                                  getSelectorObjects(context).
        enabled (bool, optional): False to grey out the buttons, e.g. for an empty selector. Ignored
                                  while this host's highlight is up, so it can still be switched off.
        label (str, optional): text drawn in front of the buttons, '' for none.
    """
    hostPtr = host.as_pointer()
    ownsHighlight = (_activeHighlight is not None) and (_activeHighlight[0] == hostPtr)

    row = layout.row(align=True)
    # Greying out a depressed button would leave no way to switch the overlay off
    row.enabled = enabled or ownsHighlight

    if label:
        row.label(text=label)

    # Context data must be set before the operators are created
    row.context_pointer_set(name='highlightHost', data=host)

    for style, params in _STYLES.items():
        row.operator('vray.highlight_objects', text='', icon=params[_STYLE_ICON],
                     depress=(_activeHighlight == (hostPtr, style))).style = style


def _tagRedrawButtons():
    """ Repaint the editors hosting the highlight buttons so their depress state updates. """
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type in {'PROPERTIES', 'NODE_EDITOR'}:
                area.tag_redraw()


def _findViewportWindow(context: bpy.types.Context):
    """ The window hosting a 3D viewport, preferring the one the button was clicked in. A modal
        handler is bound to one window, so bind it where the overlay is, not where the button was.
    """
    windows = sorted(context.window_manager.windows,
                     key=lambda w: 0 if w == context.window else 1)

    for win in windows:
        if any(area.type == 'VIEW_3D' for area in win.screen.areas):
            return win

    return context.window


def _resolveObjects(context: bpy.types.Context, items: list[bpy.types.ID]):
    """ Map the items of a selector to the scene objects to highlight. Materials are mapped to the
        objects using them, so material selectors highlight the affected geometry.
    """
    objects = [i for i in items if isinstance(i, bpy.types.Object)]

    if materials := {i for i in items if isinstance(i, bpy.types.Material)}:
        objects += [o for o in context.scene.objects
                    if any(slot.material in materials for slot in o.material_slots)]

    # An object can be both listed directly and a user of a listed material. Tinting it twice
    # double-blends the alpha, so de-duplicate (dict.fromkeys keeps the listed order).
    return list(dict.fromkeys(objects))


class VRAY_OT_highlight_objects(VRayOperatorBase):
    """ Show the objects picked in the selector in the viewport. Click again or press Esc to clear """
    bl_idname = 'vray.highlight_objects'
    bl_label = "Highlight Selected Objects"
    bl_options = {'INTERNAL'}

    style: bpy.props.EnumProperty(
        items = tuple((style, params[_STYLE_LABEL], params[_STYLE_DESC])
                      for style, params in _STYLES.items()),
        default = 'BOX',
        options = {'SKIP_SAVE'}
    )

    # The modal instance currently drawing a highlight, if any. Only one highlight is shown at a
    # time: starting another one replaces it, so the viewport never fills up with stale overlays.
    _activeInstance = None
    _superseded = False

    @classmethod
    def description(cls, context, properties):
        # The style-specific help, so each icon says what it draws, plus how to get rid of it
        return f"{_STYLES[properties.style][_STYLE_DESC]}. " \
                "Click again or press Esc to clear the highlight"

    def invoke(self, context: bpy.types.Context, event: bpy.types.Event):
        host = getattr(context, 'highlightHost', None)
        assert host, "The selector should be set using UILayout's context_pointer_set() method"

        key = (host.as_pointer(), self.style)
        active = VRAY_OT_highlight_objects._activeInstance

        if active and (active._key == key):
            # Clicking the lit button again turns the highlight off
            active._superseded = True
            active._finish()
            return {'CANCELLED'}

        objects = _resolveObjects(context, host.getSelectorObjects(context))
        if not objects:
            self.report({'INFO'}, "No objects to highlight")
            return {'CANCELLED'}

        drawsTint = self.style in {'FILL', 'XRAY'}
        depsgraph = context.evaluated_depsgraph_get()
        tintColor = gpu_overlay.markedObjectColor(context, _STYLES[self.style][_STYLE_ALPHA])
        # Wireframes are always opaque - a tint alpha would make them nearly invisible
        wireColor = gpu_overlay.markedObjectColor(context, 1.0)

        # Build the geometry once, in object-local space. Objects are kept by name and re-resolved
        # at draw time: the highlight then follows objects as they are moved, and there is no bpy
        # reference to dangle if one is deleted or the file is reloaded under a running modal.
        # Objects a tint cannot be built for (lights, hair, volumes) fall back to a wireframe.
        self._items = []
        for obj in objects:
            tint = gpu_overlay.buildTintBatch(obj, depsgraph) if drawsTint else None
            batch = tint if tint is not None else gpu_overlay.buildBoxBatch(obj)
            self._items.append((batch, obj.name, tintColor if tint is not None else wireColor))

        # Replace any running highlight only now, so a failed start does not clear the one that is up
        if active:
            active._superseded = True
            active._finish()

        # X-Ray disables depth testing so the highlight draws over the whole scene.
        self._depthTest = 'NONE' if self.style == 'XRAY' else 'LESS_EQUAL'

        global _activeHighlight
        self._key = key
        _activeHighlight = key

        self._drawHandler = bpy.types.SpaceView3D.draw_handler_add(self._draw, (), 'WINDOW', 'POST_VIEW')
        gpu_overlay.tagRedrawView3D()
        _tagRedrawButtons()

        with context.temp_override(window=_findViewportWindow(context)):
            context.window_manager.modal_handler_add(self)

        VRAY_OT_highlight_objects._activeInstance = self
        return {'RUNNING_MODAL'}

    def modal(self, context: bpy.types.Context, event: bpy.types.Event):
        if self._superseded:
            # Another highlight took over and already cleared this one's draw handler
            return {'CANCELLED'}

        if event.type == 'ESC':
            self._finish()
            return {'CANCELLED'}

        # Everything else passes through, so the viewport and the rest of the UI keep working
        return {'PASS_THROUGH'}

    def cancel(self, context: bpy.types.Context):
        # File load / window close force-cancels the modal without calling modal(), and Blender only
        # runs this hook if the class defines it. Without it the draw handler and its batches leak,
        # and _activeInstance points at a freed operator that raises ReferenceError on every access.
        self._finish()

    def _draw(self):
        # Re-resolve by name each frame.
        overlays = [(batch, obj.matrix_world, color)
                    for batch, objName, color in self._items
                    if (obj := bpy.data.objects.get(objName))]
        gpu_overlay.drawObjectOverlays(overlays, depthTest=self._depthTest, lineWidth=2.0)

    def _finish(self):
        handle = getattr(self, '_drawHandler', None)
        if handle is not None:
            try:
                bpy.types.SpaceView3D.draw_handler_remove(handle, 'WINDOW')
            except (ValueError, RuntimeError):
                pass
            self._drawHandler = None

        if VRAY_OT_highlight_objects._activeInstance is self:
            VRAY_OT_highlight_objects._activeInstance = None

        # Only clear the shared state if this instance still owns it, so a modal that just ended
        # does not un-toggle a different one that started in the same click hand-off.
        global _activeHighlight
        if _activeHighlight == self._key:
            _activeHighlight = None

        gpu_overlay.tagRedrawView3D()
        _tagRedrawButtons()


def getRegClasses():
    return (
        VRAY_OT_highlight_objects,
    )


def register():
    from vray_blender.lib.class_utils import registerClass
    for regClass in getRegClasses():
        registerClass(regClass)


def unregister():
    # Unregistering the type does not stop running modals, so a live highlight would leave a draw
    # handler calling into a dead class after a Reload Scripts / addon disable.
    if active := VRAY_OT_highlight_objects._activeInstance:
        active._finish()

    for regClass in reversed(getRegClasses()):
        bpy.utils.unregister_class(regClass)
