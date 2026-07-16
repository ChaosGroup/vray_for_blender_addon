# SPDX-FileCopyrightText: Blender Foundation
# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Fills the same role as Blender's built-in NODE_OT_connect_to_output and
# Node Wrangler's preview operator (GPL-2.0-or-later) for V-Ray trees, where
# neither of those polls.

""" Preview Node.
    Ctrl+Shift+LMB on a node wires it into the tree's output for a quick
    look-dev preview.

    Behavior:
    - Picks the first input on the output node (the tree's primary slot,
      e.g. "Material" on VRayNodeOutputMaterial) as the preview target.
    - Repeat clicks on the same node cycle through its outputs.
    - V-Ray's link rules are permissive: implicit cross-type conversion
      (e.g. BRDF -> Material via MtlSingleBRDF) is handled at export.
      Connection validity is delegated to isConnectionAllowed() in nodes/links.py.
    - Undo reverts in a single step.
"""

import bpy

from vray_blender.lib.mixin import VRayOperatorBase
from vray_blender.nodes.utils import getOutputNode, getLightOutputNode
from vray_blender.nodes.links import isConnectionAllowed
from vray_blender.nodes.operators.wrangler.helpers import (
    _INPUT_PREFERENCE_BY_PLUGIN, absLoc, getPreferredOutputInputName,
    nodeAtPos as _nodeAtPos,
)
from vray_blender.nodes.operators.wrangler.poll import isVrayEditor, hasEditTree


# Per (tree, source-node) state: index of the output used on the previous click,
# so the next click advances to the following output.
_cycleState: dict = {}

# Tree types where Preview Node makes no sense and is therefore disabled.
# WORLD  - outputs are Environment/Effects/Channels; wiring random nodes there
#          silently corrupts the scene (e.g. a render-element ends up on the
#          Environment slot).
# OBJECT - output sockets are VRaySocketGeom / VRaySocketObjectProps; a texture
#          node would be force-linked to a geometry or props socket.
# FUR    - same reasoning as OBJECT; the FurOutput drives hair geometry params.
_PREVIEW_DISABLED_TREES = {'WORLD', 'OBJECT', 'FUR'}


def _pickTargetInput(outputNode, source):
    """ Pick the output-node input the source can drive.

        If the source's vray_plugin has a name preference (e.g. V-Ray Outlines -> "Outlines"),
        try that input first. Otherwise the first input the source can reach wins - that's
        usually the tree's primary slot.

        Outlines is reserved for BRDFToonOverride only; any other source goes to the Material
        slot (textures get wrapped into a BRDFLight at export).
    """
    preferred = getPreferredOutputInputName(source)
    if preferred:
        preferredInput = outputNode.inputs.get(preferred)
        if preferredInput and preferredInput.enabled and not preferredInput.hide:
            for output in source.outputs:
                if output.enabled and not output.hide and isConnectionAllowed(output, preferredInput):
                    return preferredInput

    # Outlines should never be a fallback target - only BRDFToonOverride (handled by the
    # preference branch above) belongs there.
    reservedInputs = set(_INPUT_PREFERENCE_BY_PLUGIN.values())

    for input in outputNode.inputs:
        if not input.enabled or input.hide or input.name in reservedInputs:
            continue
        for output in source.outputs:
            if not output.enabled or output.hide:
                continue
            if isConnectionAllowed(output, input):
                return input
    return None


def _pickSourceOutput(source, targetInput, startIdx: int):
    """ Find a source output compatible with targetInput, starting from startIdx and wrapping
        around. Returns (output_socket, index) or (None, -1).
    """
    outputs = list(source.outputs)
    outputCount = len(outputs)
    for offset in range(outputCount):
        idx = (startIdx + offset) % outputCount
        output = outputs[idx]
        if not output.enabled or output.hide:
            continue
        if isConnectionAllowed(output, targetInput):
            return output, idx
    return None, -1


# Approximate Y-layout of output sockets on a Blender node. The header occupies
# ~25 units from the node's top and each visible output is spaced ~22 units
# apart. Not exact - node layouts vary - but good enough to disambiguate which
# socket the user was aiming at when clicking near the right edge.
_HEADER_H = 25.0
_SOCK_H = 22.0


def _outputAtCursorY(source, cursorY: float) -> int:
    """Return the index in source.outputs of the visible output whose drawn Y
       is closest to cursorY, or -1 if the node has no visible outputs.
    """
    visible = [idx for idx, output in enumerate(source.outputs) if output.enabled and not output.hide]
    if not visible:
        return -1
    top = absLoc(source).y
    # Pick the visible slot whose approximate center Y is nearest the cursor.
    best = visible[0]
    bestDist = float('inf')
    for slot, idx in enumerate(visible):
        socketY = top - _HEADER_H - slot * _SOCK_H
        dist = abs(cursorY - socketY)
        if dist < bestDist:
            bestDist = dist
            best = idx
    return best


class VRAY_OT_WR_preview_node(VRayOperatorBase):
    """Connect the clicked node to the tree's output for preview. Repeat clicks cycle outputs"""
    bl_idname = "vray.wr_preview_node"
    bl_label = "Preview Node"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        if not (isVrayEditor(context) and hasEditTree(context)):
            return False
        ntree    = context.space_data.edit_tree
        treeType = getattr(getattr(ntree, 'vray', None), 'tree_type', '')
        return treeType not in _PREVIEW_DISABLED_TREES

    def invoke(self, context, event):
        if context.area.type != 'NODE_EDITOR':
            return {'CANCELLED'}
        ntree = context.space_data.edit_tree

        # Capture cursor Y in view space for socket-aware output picking. This
        # must happen in invoke() - execute() has no event.
        space = context.space_data
        space.cursor_location_from_region(event.mouse_region_x, event.mouse_region_y)
        self._cursorY = space.cursor_location[1]

        # Let Blender's native hit-test pick the node - matches the selection
        # behavior of Cycles' Ctrl+Shift+LMB exactly, instead of approximating
        # with a Python corner-distance check.
        try:
            bpy.ops.node.select(
                'INVOKE_DEFAULT',
                location=(event.mouse_region_x, event.mouse_region_y),
                extend=False,
                deselect_all=True,
            )
        except (RuntimeError, TypeError):
            # Older Blender signatures may not accept `location` - fall back to
            # our own hit-test in that case.
            self._source = _nodeAtPos(ntree.nodes, context, event)
        else:
            self._source = ntree.nodes.active

        if not self._source:
            return {'CANCELLED'}
        return self.execute(context)

    def execute(self, context):
        ntree = context.space_data.edit_tree
        source = getattr(self, '_source', None) or ntree.nodes.active
        if not source:
            self.report({'WARNING'}, "No source node to preview.")
            return {'CANCELLED'}

        treeType = getattr(getattr(ntree, 'vray', None), 'tree_type', '')
        outputNode = getLightOutputNode(ntree) if treeType == 'LIGHT' else getOutputNode(ntree)
        if not outputNode:
            self.report({'WARNING'}, "No output node in this tree.")
            return {'CANCELLED'}
        if source is outputNode:
            return {'CANCELLED'}

        targetInput = _pickTargetInput(outputNode, source)
        if not targetInput:
            self.report({'WARNING'}, f"No compatible socket from '{source.name}' to output.")
            return {'CANCELLED'}

        key = (ntree.name, source.name)
        cursorY = getattr(self, '_cursorY', None)

        sourceOutput = None
        usedIdx = -1

        # If we have a cursor Y (click-driven invoke), prefer the output whose
        # drawn position is closest to where the user clicked. Only use it when
        # it's also a compatible target - otherwise fall through to cycling.
        if cursorY is not None:
            hinted = _outputAtCursorY(source, cursorY)
            if hinted >= 0:
                output = source.outputs[hinted]
                if output.enabled and not output.hide and isConnectionAllowed(output, targetInput):
                    # If the click resolved to the same output as last time,
                    # advance to the next one so repeat-clicks still cycle.
                    if _cycleState.get(key, -2) == hinted:
                        startIdx = (hinted + 1) % max(len(source.outputs), 1)
                        sourceOutput, usedIdx = _pickSourceOutput(source, targetInput, startIdx)
                    else:
                        sourceOutput, usedIdx = output, hinted

        # No click hint (or hint unusable): cycle from where we left off.
        if sourceOutput is None:
            startIdx = (_cycleState.get(key, -1) + 1) % max(len(source.outputs), 1)
            sourceOutput, usedIdx = _pickSourceOutput(source, targetInput, startIdx)

        if not sourceOutput:
            self.report({'WARNING'}, f"No usable output on '{source.name}'.")
            return {'CANCELLED'}
        _cycleState[key] = usedIdx

        # Replace existing links on the target input so only one preview is active.
        for link in list(targetInput.links):
            ntree.links.remove(link)
        ntree.links.new(sourceOutput, targetInput)
        ntree.update_tag()
        return {'FINISHED'}


def getRegClasses():
    return (VRAY_OT_WR_preview_node,)


def register():
    for regClass in getRegClasses():
        bpy.utils.register_class(regClass)


def unregister():
    _cycleState.clear()
    for regClass in reversed(getRegClasses()):
        bpy.utils.unregister_class(regClass)
