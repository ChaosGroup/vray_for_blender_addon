# SPDX-FileCopyrightText: Blender Foundation
# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Based on Blender's Node Wrangler add-on (GPL-2.0-or-later). The modal
# overlay and hit-test geometry are ported from the upstream draw helpers.

""" Lazy Connect.
    Alt+RMB drag from one node to another to auto-link them. Draws a
    rubber-band line and highlights the source and target nodes.

    On release, picks an output->input pair with the priority:
      1) same bl_idname + same socket name on a free input
      2) same bl_idname on a free input
      3) V-Ray-allowed cross-type on a free input
      4) same bl_idname, overwriting a linked input
      5) V-Ray-allowed cross-type, overwriting a linked input
    No "truly any" fallback - if V-Ray's rules reject every pairing the autolink
    fails rather than creating a structurally invalid link.
"""

from math import cos, sin, pi

import bpy
import gpu
from gpu_extras.batch import batch_for_shader

from vray_blender.lib.mixin import VRayOperatorBase
from vray_blender.nodes.operators.wrangler.helpers import (
    _INPUT_PREFERENCE_BY_PLUGIN, _dpiFac, absLoc,
    getPreferredOutputInputName, nodeAtPos as _nodeAtPos,
    socketsCompatible as _socketsCompatible,
)
from vray_blender.nodes.operators.wrangler.poll import (
    isVrayEditor, hasEditTree,
)


# ------------------------------ helpers ------------------------------ #

def _lineWidth():
    return bpy.context.preferences.system.pixel_size


def _autoLink(node1, node2, links) -> bool:
    """ Connect an output of node1 to an input of node2 using the priority ladder in the
        module docstring, plus a V-Ray guard: the Outlines slot on the material output is
        reserved for BRDFToonOverride (see _INPUT_PREFERENCE_BY_PLUGIN). Any other source
        skips Outlines, and a BRDFToonOverride source routes there first when available.
    """
    outputs = [output for output in node1.outputs if output.enabled and not output.hide]
    inputs  = [input for input in node2.inputs if input.enabled and not input.hide]
    if not outputs or not inputs:
        return False

    preferredName = getPreferredOutputInputName(node1)
    reservedNames = set(_INPUT_PREFERENCE_BY_PLUGIN.values())

    # 0) If the source plugin has a preferred input (e.g. BRDFToonOverride -> Outlines),
    #    try that first. Matches Preview Node / Link to Output.
    if preferredName:
        if preferredInput := next((input for input in inputs if input.name == preferredName), None):
            for output in outputs:
                if _socketsCompatible(output, preferredInput):
                    links.new(output, preferredInput)
                    return True

    # Exclude reserved inputs from the regular ladder when the source isn't the matching
    # preferred plugin - keeps Outlines off-limits for textures, BRDFs, etc.
    inputs = [input for input in inputs if input.name not in reservedNames or input.name == preferredName]
    if not inputs:
        return False

    # 1) Same bl_idname + same socket name on a free input.
    for output in outputs:
        for input in inputs:
            if (not input.is_linked
                    and input.bl_idname == output.bl_idname
                    and input.name == output.name):
                links.new(output, input)
                return True
    # 2) Same bl_idname on a free input.
    for output in outputs:
        for input in inputs:
            if not input.is_linked and input.bl_idname == output.bl_idname:
                links.new(output, input)
                return True
    # 3) V-Ray-allowed cross-type on a free input.
    for output in outputs:
        for input in inputs:
            if not input.is_linked and _socketsCompatible(output, input):
                links.new(output, input)
                return True
    # 4) Same bl_idname, overwrite a linked input.
    for output in outputs:
        for input in inputs:
            if input.bl_idname == output.bl_idname:
                links.new(output, input)
                return True
    # 5) V-Ray-allowed cross-type, overwrite a linked input.
    for output in outputs:
        for input in inputs:
            if _socketsCompatible(output, input):
                links.new(output, input)
                return True
    return False


# ------------------------------ GPU drawing ------------------------------ #

def _drawLine(x1, y1, x2, y2, size, colour):
    shader = gpu.shader.from_builtin('POLYLINE_SMOOTH_COLOR')
    shader.uniform_float("viewportSize", gpu.state.viewport_get()[2:])
    shader.uniform_float("lineWidth", size * _lineWidth())
    vertexColors = (
        (colour[0] + (1.0 - colour[0]) / 4,
         colour[1] + (1.0 - colour[1]) / 4,
         colour[2] + (1.0 - colour[2]) / 4,
         colour[3] + (1.0 - colour[3]) / 4),
        colour,
    )
    batch = batch_for_shader(
        shader, 'LINE_STRIP',
        {"pos": ((x1, y1), (x2, y2)), "color": vertexColors},
    )
    batch.draw(shader)


def _drawCircle(mx, my, radius, colour):
    radius *= _lineWidth()
    sides = 12
    verts = [
        (radius * cos(step * 2 * pi / sides) + mx,
         radius * sin(step * 2 * pi / sides) + my)
        for step in range(sides + 1)
    ]
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    shader.uniform_float("color", colour)
    batch = batch_for_shader(shader, 'TRI_FAN', {"pos": verts})
    batch.draw(shader)


def _addEdgeQuad(verts, idxs, p0, p1, p2, p3):
    base = len(verts)
    verts.extend([p0, p1, p2, p3])
    idxs.extend([(base, base + 1, base + 3), (base + 3, base + 1, base + 2)])


def _drawNodeBorder(node, radius, colour):
    """ Rounded-rectangle highlight around a node. Horizontal clipping from
        the upstream draw helper is dropped since our overlay only appears
        during the modal and the area width is fine.
    """
    sides = 16
    radius *= _lineWidth()
    loc = absLoc(node)
    dpi = _dpiFac()
    nlx = (loc.x + 1) * dpi
    nly = (loc.y + 1) * dpi
    dimx = node.dimensions.x
    dimy = node.dimensions.y
    if node.hide:
        nlx += -1
        nly += 5
    if node.type == 'REROUTE':
        # Reroutes have no real dimensions - collapse to a point and enlarge
        # the corner radius so the four arcs form a small circle.
        nly -= 1
        dimx = 0
        dimy = 0
        radius += 6

    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    shader.uniform_float("color", colour)
    v2r = bpy.context.region.view2d.view_to_region

    # Four rounded corners - each a TRI_FAN of a quarter arc.
    corners = (
        (nlx,         nly,        (4, 8 + 1)),
        (nlx + dimx,  nly,        (0, 4 + 1)),
        (nlx,         nly - dimy, (8, 12 + 1)),
        (nlx + dimx,  nly - dimy, (12, 16 + 1)),
    )
    for wx, wy, (arcStart, arcEnd) in corners:
        mx, my = v2r(wx, wy, clip=False)
        verts = [(mx, my)]
        for step in range(sides + 1):
            if arcStart <= step < arcEnd:
                verts.append((
                    radius * cos(step * 2 * pi / sides) + mx,
                    radius * sin(step * 2 * pi / sides) + my,
                ))
        batch_for_shader(shader, 'TRI_FAN', {"pos": verts}).draw(shader)

    # Four edge quads.
    tl = v2r(nlx, nly, clip=False)
    tr = v2r(nlx + dimx, nly, clip=False)
    bl = v2r(nlx, nly - dimy, clip=False)
    br = v2r(nlx + dimx, nly - dimy, clip=False)
    verts, idxs = [], []
    _addEdgeQuad(verts, idxs, (bl[0] - radius, bl[1]), bl, tl, (tl[0] - radius, tl[1]))
    _addEdgeQuad(verts, idxs, tl, tr, (tr[0], tl[1] + radius), (tl[0], tl[1] + radius))
    _addEdgeQuad(verts, idxs, (tr[0], br[1]), (tr[0] + radius, br[1]), (tr[0] + radius, tr[1]), tr)
    _addEdgeQuad(verts, idxs, bl, br, (br[0], bl[1] - radius), (bl[0], bl[1] - radius))

    batch_for_shader(shader, 'TRIS', {"pos": verts}, indices=idxs).draw(shader)


_OVERLAY_PALETTES = {
    'LINK':     ((1.0, 0.2, 0.2, 0.4), (0.0, 0.0, 0.0, 0.5), (0.3, 0.05, 0.05, 1.0)),
    'LINKMENU': ((0.4, 0.6, 1.0, 0.4), (0.0, 0.0, 0.0, 0.5), (0.08, 0.15, 0.3, 1.0)),
    'MIX':      ((0.2, 1.0, 0.2, 0.4), (0.0, 0.0, 0.0, 0.5), (0.05, 0.3, 0.05, 1.0)),
}


def _drawOverlay(op, context, mode='LINK'):
    if not op._mousePath:
        return

    gpu.state.blend_set('ALPHA')

    colOuter, colInner, colCircle = _OVERLAY_PALETTES.get(mode, _OVERLAY_PALETTES['LINK'])

    source = op._source
    target = op._target
    if source and target and source is target:
        colOuter = (0.4, 0.4, 0.4, 0.4)
        colCircle = (0.2, 0.2, 0.2, 1.0)

    if source:
        _drawNodeBorder(source, 6, colOuter)
        _drawNodeBorder(source, 5, colInner)
    if target and target is not source:
        _drawNodeBorder(target, 6, colOuter)
        _drawNodeBorder(target, 5, colInner)

    m1x, m1y = op._mousePath[0]
    m2x, m2y = op._mousePath[-1]
    _drawLine(m1x, m1y, m2x, m2y, 5, colOuter)
    _drawLine(m1x, m1y, m2x, m2y, 2, colInner)
    _drawCircle(m1x, m1y, 7, colOuter)
    _drawCircle(m2x, m2y, 7, colOuter)
    _drawCircle(m1x, m1y, 5, colCircle)
    _drawCircle(m2x, m2y, 5, colCircle)

    gpu.state.blend_set('NONE')


# ------------------------------ operator ------------------------------ #

_PICKER_STATE = {
    'source_name': '',
    'target_name': '',
    'from_index': 0,
}


class VRAY_OT_WR_lazy_connect(VRayOperatorBase):
    """Connect two nodes by dragging Alt+RMB between them"""
    bl_idname = "vray.wr_lazy_connect"
    bl_label = "Lazy Connect"
    bl_options = {'REGISTER', 'UNDO'}

    with_menu: bpy.props.BoolProperty(
        name="With Menu",
        description="On release, pop up socket picker menus instead of auto-linking",
        default=False,
    )

    @classmethod
    def poll(cls, context):
        return isVrayEditor(context) and hasEditTree(context)

    def modal(self, context, event):
        context.area.tag_redraw()
        ntree = context.space_data.edit_tree
        nodes = ntree.nodes

        if event.type == 'MOUSEMOVE':
            self._mousePath.append((event.mouse_region_x, event.mouse_region_y))
            self._target = _nodeAtPos(nodes, context, event)
            return {'RUNNING_MODAL'}

        if event.type == 'RIGHTMOUSE' and event.value == 'RELEASE':
            self._removeDrawHandler()
            target = _nodeAtPos(nodes, context, event)
            if self._source and target and target is not self._source:
                if self.with_menu:
                    # Expose source & target to the picker menus; they link on pick.
                    _PICKER_STATE['source_name'] = self._source.name
                    _PICKER_STATE['target_name'] = target.name
                    outputs = [output for output in self._source.outputs if output.enabled]
                    if len(outputs) > 1 and target.inputs:
                        bpy.ops.wm.call_menu('INVOKE_DEFAULT', name='VRAY_MT_WR_lazy_connect_outputs')
                    elif len(outputs) == 1:
                        bpy.ops.vray.wr_lazy_connect_pick_input('INVOKE_DEFAULT', from_index=0)
                else:
                    _autoLink(self._source, target, ntree.links)
                    ntree.update_tag()
            return {'FINISHED'}

        if event.type == 'ESC' and event.value == 'PRESS':
            self._removeDrawHandler()
            return {'CANCELLED'}

        return {'RUNNING_MODAL'}

    def invoke(self, context, event):
        if context.area.type != 'NODE_EDITOR':
            return {'CANCELLED'}
        nodes = context.space_data.edit_tree.nodes
        self._source = _nodeAtPos(nodes, context, event)
        if not self._source:
            return {'CANCELLED'}

        self._target = self._source
        self._mousePath = [(event.mouse_region_x, event.mouse_region_y)]

        mode = 'LINKMENU' if self.with_menu else 'LINK'
        self._drawHandler = bpy.types.SpaceNodeEditor.draw_handler_add(
            _drawOverlay, (self, context, mode), 'WINDOW', 'POST_PIXEL',
        )
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def _removeDrawHandler(self):
        handle = getattr(self, '_drawHandler', None)
        if handle is not None:
            try:
                bpy.types.SpaceNodeEditor.draw_handler_remove(handle, 'WINDOW')
            except (ValueError, RuntimeError):
                pass
            self._drawHandler = None


def _pickerSourceTarget(ntree):
    source = ntree.nodes.get(_PICKER_STATE['source_name'])
    target = ntree.nodes.get(_PICKER_STATE['target_name'])
    return source, target


class VRAY_OT_WR_lazy_connect_pick_input(VRayOperatorBase):
    """ Bridge operator between the From-Socket menu and the To-Socket menu.
        Uses only `execute()` (no `invoke()`) - matches Blender Node Wrangler's
        own pattern. Menu-item clicks dispatch through `execute()`, and the
        synchronous `wm.call_menu` from inside `execute()` opens the second
        popup reliably across all node types (timer / modal hacks aren't needed).
    """
    bl_idname = "vray.wr_lazy_connect_pick_input"
    bl_label = "Pick Input"
    bl_options = {'REGISTER', 'UNDO'}

    from_index: bpy.props.IntProperty()

    def execute(self, context):
        _PICKER_STATE['from_index'] = self.from_index
        ntree = context.space_data.edit_tree
        source, target = _pickerSourceTarget(ntree)
        if not source or not target:
            return {'CANCELLED'}
        if self.from_index >= len(source.outputs):
            return {'CANCELLED'}
        if len(target.inputs) > 1:
            bpy.ops.wm.call_menu(
                'INVOKE_DEFAULT', name='VRAY_MT_WR_lazy_connect_inputs',
            )
        elif len(target.inputs) == 1:
            try:
                ntree.links.new(source.outputs[self.from_index], target.inputs[0])
                ntree.update_tag()
            except RuntimeError:
                pass
        return {'FINISHED'}


class VRAY_OT_WR_lazy_connect_make_link(VRayOperatorBase):
    """Make a link from picked output to picked input"""
    bl_idname = "vray.wr_lazy_connect_make_link"
    bl_label = "Make Link"
    bl_options = {'REGISTER', 'UNDO'}

    from_index: bpy.props.IntProperty()
    to_index: bpy.props.IntProperty()

    def execute(self, context):
        ntree = context.space_data.edit_tree
        source, target = _pickerSourceTarget(ntree)
        if not source or not target:
            return {'CANCELLED'}
        if self.from_index >= len(source.outputs) or self.to_index >= len(target.inputs):
            return {'CANCELLED'}
        ntree.links.new(source.outputs[self.from_index], target.inputs[self.to_index])
        ntree.update_tag()
        return {'FINISHED'}


class VRAY_MT_WR_lazy_connect_outputs(bpy.types.Menu):
    bl_idname = "VRAY_MT_WR_lazy_connect_outputs"
    bl_label = "From Socket"

    def draw(self, context):
        layout = self.layout
        ntree = context.space_data.edit_tree
        source, _target = _pickerSourceTarget(ntree)
        if not source:
            layout.label(text="(source node not found)")
            return
        shown = 0
        for idx, output in enumerate(source.outputs):
            if output.enabled and not output.hide:
                layout.operator(
                    "vray.wr_lazy_connect_pick_input", text=output.name,
                ).from_index = idx
                shown += 1
        if shown == 0:
            layout.label(text="(no enabled outputs)")


class VRAY_MT_WR_lazy_connect_inputs(bpy.types.Menu):
    bl_idname = "VRAY_MT_WR_lazy_connect_inputs"
    bl_label = "To Socket"

    def draw(self, context):
        layout = self.layout
        ntree = context.space_data.edit_tree
        source, target = _pickerSourceTarget(ntree)
        if not source or not target:
            layout.label(text="(source or target node not found)")
            return

        fromIndex = _PICKER_STATE['from_index']
        if fromIndex >= len(source.outputs):
            layout.label(text="(invalid source output)")
            return
        fromOutput = source.outputs[fromIndex]

        # Hide reserved inputs (e.g. Outlines, Effects, Channels) unless the source
        # is the node that owns them - same rule the auto-link path applies.
        preferredName = getPreferredOutputInputName(source)
        reservedNames = set(_INPUT_PREFERENCE_BY_PLUGIN.values())

        shown = 0
        # Rollout sockets (`VRaySocketRollout`) are V-Ray's panel headers - they group
        # the property sockets that follow them in the inputs list. They aren't
        # connectable, but we surface their name as a section label so the user can
        # tell which slot belongs to which panel.
        # `input.hide` is NOT used as an exclusion criterion: collapsed-panel children
        # have hide=True but they're still real, link-accepting sockets the user wants.
        # Inputs that V-Ray would refuse a link from `fromOutput` to (e.g. Color -> mesh)
        # are also filtered out.
        pendingPanelHeader = None
        for idx, input in enumerate(target.inputs):
            if not input.enabled:
                continue
            if input.bl_idname == 'VRaySocketExtend':
                continue
            if input.bl_idname == 'VRaySocketRollout':
                pendingPanelHeader = input.name
                continue
            if input.name in reservedNames and input.name != preferredName:
                continue
            if not _socketsCompatible(fromOutput, input):
                continue
            if pendingPanelHeader is not None:
                if shown > 0:
                    layout.separator()
                layout.label(text=pendingPanelHeader)
                pendingPanelHeader = None
            op = layout.operator(
                "vray.wr_lazy_connect_make_link", text=input.name,
            )
            op.from_index = fromIndex
            op.to_index = idx
            shown += 1
        if shown == 0:
            layout.label(text="(no compatible inputs)")


def getRegClasses():
    return (
        VRAY_OT_WR_lazy_connect,
        VRAY_OT_WR_lazy_connect_pick_input,
        VRAY_OT_WR_lazy_connect_make_link,
        VRAY_MT_WR_lazy_connect_outputs,
        VRAY_MT_WR_lazy_connect_inputs,
    )


def register():
    for regClass in getRegClasses():
        bpy.utils.register_class(regClass)


def unregister():
    for regClass in reversed(getRegClasses()):
        bpy.utils.unregister_class(regClass)
