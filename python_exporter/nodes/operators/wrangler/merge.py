# SPDX-FileCopyrightText: Blender Foundation
# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Based on Blender's Node Wrangler add-on (GPL-2.0-or-later).

""" Merge Selected and Lazy Mix operators.

    Plugin-backed merge nodes:
    - TexMix        - mix two colors by factor
    - TexAColorOp   - color arithmetic (Sum/Product/Difference/...)
    - TexFloatOp    - float arithmetic

    Nodes are created via `ntree.nodes.new('VRayNode<Plugin>')`. Blender
    auto-runs `vrayNodeInit` which attaches the dynamic PropertyGroup and
    builds sockets from the plugin description.

    Supported modes: Mix, Add, Subtract, Multiply, Divide, Min, Max.
    The V-Ray op plugins take two operands so the chain is built
    left-to-right across selected nodes without multi-input sockets or
    cycle detection.
"""

import bpy

from vray_blender.lib.mixin import VRayOperatorBase
from vray_blender.nodes.tools import deselectNodes
from vray_blender.nodes.operators.wrangler.helpers import safeSet
from vray_blender.nodes.operators.wrangler.lazy_connect import _drawOverlay, _nodeAtPos
from vray_blender.nodes.operators.wrangler.poll import (
    isVrayEditor, hasEditTree, hasSelection,
)


# ---------- mode table ----------
# (mode_id, plugin_type, mode_attr_value, input_a, input_b, output_sock_name, flavor)
# Socket names use display-name form (what Blender shows / what `inputs.get()` expects),
# not the attr name - V-Ray formats attr names into socket labels (e.g. `color_a` -> "Color A").
# Output is the default (mode-aware) slot so mode switching still yields the right value and
# the chain link remains visible to the user.
# `flavor`: 'COLOR' -> color math; 'FLOAT' -> float math; 'MIX' -> TexMix special-case.
_MODES = {
    'MIX':        ('TexMix',        None,  'Color 1', 'Color 2', 'Color',  'MIX'),
    'ADD_COLOR':  ('TexAColorOp',   '3',   'Color A', 'Color B', 'Result', 'COLOR'),
    'SUB_COLOR':  ('TexAColorOp',   '4',   'Color A', 'Color B', 'Result', 'COLOR'),
    'MUL_COLOR':  ('TexAColorOp',   '2',   'Color A', 'Color B', 'Result', 'COLOR'),
    'DIV_COLOR':  ('TexAColorOp',   '6',   'Color A', 'Color B', 'Result', 'COLOR'),
    'MIN_COLOR':  ('TexAColorOp',   '7',   'Color A', 'Color B', 'Result', 'COLOR'),
    'MAX_COLOR':  ('TexAColorOp',   '8',   'Color A', 'Color B', 'Result', 'COLOR'),
    'ADD_FLOAT':  ('TexFloatOp',    '2',   'Float A', 'Float B', 'Result', 'FLOAT'),
    'SUB_FLOAT':  ('TexFloatOp',    '3',   'Float A', 'Float B', 'Result', 'FLOAT'),
    'MUL_FLOAT':  ('TexFloatOp',    '0',   'Float A', 'Float B', 'Result', 'FLOAT'),
    'DIV_FLOAT':  ('TexFloatOp',    '1',   'Float A', 'Float B', 'Result', 'FLOAT'),
    'MIN_FLOAT':  ('TexFloatOp',    '7',   'Float A', 'Float B', 'Result', 'FLOAT'),
    'MAX_FLOAT':  ('TexFloatOp',    '8',   'Float A', 'Float B', 'Result', 'FLOAT'),
}

_MODE_ITEMS = [
    ('AUTO',       'Auto (by Socket)', 'Pick TexMix / TexFloatOp based on selected output types'),
    ('MIX',        'Mix Colors',       'Mix using TexMix'),
    ('ADD_COLOR',  'Add (Color)',      'TexAColorOp - Sum'),
    ('SUB_COLOR',  'Subtract (Color)', 'TexAColorOp - Difference'),
    ('MUL_COLOR',  'Multiply (Color)', 'TexAColorOp - Product'),
    ('DIV_COLOR',  'Divide (Color)',   'TexAColorOp - Division'),
    ('MIN_COLOR',  'Min (Color)',      'TexAColorOp - Minimum'),
    ('MAX_COLOR',  'Max (Color)',      'TexAColorOp - Maximum'),
    ('ADD_FLOAT',  'Add (Float)',      'TexFloatOp - Sum'),
    ('SUB_FLOAT',  'Subtract (Float)', 'TexFloatOp - Difference'),
    ('MUL_FLOAT',  'Multiply (Float)', 'TexFloatOp - Product'),
    ('DIV_FLOAT',  'Divide (Float)',   'TexFloatOp - Ratio'),
    ('MIN_FLOAT',  'Min (Float)',      'TexFloatOp - Min'),
    ('MAX_FLOAT',  'Max (Float)',      'TexFloatOp - Max'),
]


# Float-y output socket idnames - keep in sync with the float sockets in
# `nodes/sockets.py`. AUTO mode uses this set to decide float vs. color.
_FLOAT_OUTPUT_SOCKETS = {
    'VRaySocketFloat',
    'VRaySocketFloatNoValue',
    'VRaySocketInt',
}


def _resolveAutoMode(selected) -> str:
    """Pick a concrete mode for AUTO. All-float selection -> ADD_FLOAT; anything
       else falls back to MIX (which works for colors and also gracefully for
       mixed selections via V-Ray's implicit conversions).
    """
    for node in selected:
        firstOutput = _firstOutput(node)
        if firstOutput is None:
            return 'MIX'
        if firstOutput.bl_idname not in _FLOAT_OUTPUT_SOCKETS:
            return 'MIX'
    return 'ADD_FLOAT'


def _firstOutput(node):
    """First enabled, non-hidden output socket - the one we pipe into the merge op."""
    for output in node.outputs:
        if output.enabled and not output.hide:
            return output
    return None


def _createMerge(ntree, mode, x, y):
    """Create a merge op node for `mode` at (x, y). Returns (node, input_a_sock,
       input_b_sock, output_sock) or (None, ...) if creation failed.
    """
    pluginType, modeValue, inputAName, inputBName, outputName, _flavor = _MODES[mode]
    bl_idname = f'VRayNode{pluginType}'

    try:
        node = ntree.nodes.new(bl_idname)
    except RuntimeError:
        return None, None, None, None

    # Set the operation mode (TexAColorOp / TexFloatOp have `mode` enum).
    if modeValue is not None:
        propGroup = getattr(node, node.vray_plugin, None)
        if propGroup is not None:
            safeSet(propGroup, 'mode', modeValue)

    node.location = (x, y)
    inputA = node.inputs.get(inputAName)
    inputB = node.inputs.get(inputBName)
    output = node.outputs.get(outputName) or (node.outputs[0] if node.outputs else None)
    return node, inputA, inputB, output


class VRAY_OT_WR_merge_nodes(VRayOperatorBase):
    """Merge selected nodes with a TexMix / TexAColorOp / TexFloatOp chain"""
    bl_idname = "vray.wr_merge_nodes"
    bl_label = "Merge Nodes"
    bl_options = {'REGISTER', 'UNDO'}

    mode: bpy.props.EnumProperty(
        name="Mode",
        items=_MODE_ITEMS,
        default='MIX',
    )

    @classmethod
    def poll(cls, context):
        return isVrayEditor(context) and hasEditTree(context) and hasSelection(context)

    def execute(self, context):
        ntree = context.space_data.edit_tree

        # Chain by descending Y - top node is first operand, next is second, etc.
        selected = [node for node in ntree.nodes if node.select and node.outputs]
        if len(selected) < 2:
            self.report({'WARNING'}, "Select at least two nodes with outputs.")
            return {'CANCELLED'}
        selected.sort(key=lambda node: -node.location.y)

        mode = self.mode
        if mode == 'AUTO':
            mode = _resolveAutoMode(selected)

        # Capture the first-selected's existing consumers so the chain can be
        # re-inserted into the stream. If the first node has no consumers and
        # exactly two nodes are selected, fall back to the second node's
        # consumers so "merge B onto A" still chains correctly.
        firstOutput = _firstOutput(selected[0])
        if firstOutput is None:
            self.report({'WARNING'}, "First node has no usable output.")
            return {'CANCELLED'}
        preservedDownstream = [link.to_socket for link in list(firstOutput.links)]
        if not preservedDownstream and len(selected) == 2:
            secondOutput = _firstOutput(selected[1])
            if secondOutput is not None:
                preservedDownstream = [link.to_socket for link in list(secondOutput.links)]

        # Merge nodes are placed right of the right-most selected node.
        x = max(node.location.x + node.width for node in selected) + 80.0

        prevOutputSocket = firstOutput
        mergedNodes = []
        for idx in range(1, len(selected)):
            nextNode = selected[idx]
            nextOutput = _firstOutput(nextNode)
            if nextOutput is None:
                continue

            midY = (selected[0].location.y + nextNode.location.y) / 2.0
            mergeNode, inputA, inputB, output = _createMerge(
                ntree, mode, x, midY - (idx - 1) * 20.0,
            )
            if mergeNode is None or inputA is None or inputB is None or output is None:
                self.report({'WARNING'}, f"Could not create merge node for mode {mode!r}.")
                return {'CANCELLED'}

            try:
                ntree.links.new(prevOutputSocket, inputA)
                ntree.links.new(nextOutput, inputB)
            except RuntimeError:
                # V-Ray may reject an incompatible socket pair (e.g. uvw into color).
                # Leave the node in place so the user can fix it manually.
                pass
            mergedNodes.append(mergeNode)
            prevOutputSocket = output
            x += 220.0

        if not mergedNodes:
            return {'CANCELLED'}

        # Redirect captured downstream consumers through the final merge.
        # Skip any to_sockets on the operands themselves (would create cycles).
        selectedSet = set(selected)
        for toSocket in preservedDownstream:
            if toSocket.node in selectedSet:
                continue
            try:
                ntree.links.new(prevOutputSocket, toSocket)
            except RuntimeError:
                pass

        # Leave the final merge node active & selected for chaining.
        deselectNodes(ntree)
        for node in mergedNodes:
            node.select = True
        ntree.nodes.active = mergedNodes[-1]
        ntree.update_tag()
        return {'FINISHED'}


# ---------- Lazy Mix ----------
# Alt+Ctrl+Shift+RMB drag from one node to another -> drop a merge op between.
# Same drag-line overlay as Lazy Connect, but inserts a merge node.

class VRAY_OT_WR_lazy_mix(VRayOperatorBase):
    """Drag between two nodes to insert a merge op (TexMix/TexAColorOp)"""
    bl_idname = "vray.wr_lazy_mix"
    bl_label = "Lazy Mix"
    bl_options = {'REGISTER', 'UNDO'}

    mode: bpy.props.EnumProperty(
        name="Mode",
        items=_MODE_ITEMS,
        default='MIX',
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
            if not (self._source and target and target is not self._source):
                # Released over empty space or over the source itself - nothing merged.
                return {'CANCELLED'}

            deselectNodes(ntree)
            self._source.select = True
            target.select = True
            nodes.active = self._source
            # Propagate the delegate's status - it cancels when the nodes cannot be merged.
            return bpy.ops.vray.wr_merge_nodes(mode=self.mode)

        if event.type == 'ESC':
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
        self._drawHandler = bpy.types.SpaceNodeEditor.draw_handler_add(
            _drawOverlay, (self, context, 'MIX'), 'WINDOW', 'POST_PIXEL',
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


def getRegClasses():
    return (
        VRAY_OT_WR_merge_nodes,
        VRAY_OT_WR_lazy_mix,
    )


def register():
    for regClass in getRegClasses():
        bpy.utils.register_class(regClass)


def unregister():
    for regClass in reversed(getRegClasses()):
        bpy.utils.unregister_class(regClass)
