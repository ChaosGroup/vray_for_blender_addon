# SPDX-FileCopyrightText: Blender Foundation
# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Based on Blender's Node Wrangler add-on (GPL-2.0-or-later).

""" Link and topology operators: delete unused nodes, swap links, detach
    outputs, add reroutes, link active to selected, link to output.
"""

import bpy

from vray_blender.lib.mixin import VRayOperatorBase
from vray_blender.nodes.tools import deselectNodes
from vray_blender.nodes.utils import getLightOutputNode, getOutputNode
from vray_blender.nodes.operators.wrangler.helpers import (
    _END_NODE_TYPES, _INPUT_PREFERENCE_BY_PLUGIN, _OUTPUT_BY_TREE_TYPE,
    getPreferredOutputInputName,
    socketsCompatible,
)
from vray_blender.nodes.operators.wrangler.poll import (
    isVrayEditor, hasEditTree, hasActive, hasSelection,
)


########## Delete Unused ##########

def _isTerminalNode(node: bpy.types.Node) -> bool:
    if node.bl_idname in _END_NODE_TYPES:
        return True
    # Light output nodes come in many varieties; they share vray_type == 'LIGHT'.
    return getattr(node, 'vray_type', '') == 'LIGHT'


def _isUnused(node: bpy.types.Node) -> bool:
    if _isTerminalNode(node):
        return False
    return all(not output.links for output in node.outputs)


class VRAY_OT_WR_del_unused(VRayOperatorBase):
    """Delete all nodes with unused outputs"""
    bl_idname = "vray.wr_del_unused"
    bl_label = "Delete Unused Nodes"
    bl_options = {'REGISTER', 'UNDO'}

    delete_frames: bpy.props.BoolProperty(
        name="Delete Empty Frames",
        description="Delete all frames that have no nodes inside them",
        default=True,
    )

    @classmethod
    def poll(cls, context):
        return isVrayEditor(context) and hasEditTree(context) and bool(context.space_data.edit_tree.nodes)

    def execute(self, context):
        ntree = context.space_data.edit_tree
        nodes = ntree.nodes
        savedSelection = {node.name for node in nodes if node.select}

        deselectNodes(ntree)

        deleted: set[str] = set()
        # Iterate until fixed point - deleting a node may leave its upstream dangling.
        while True:
            unused = [n for n in nodes if _isUnused(n)]
            if not unused:
                break
            for n in unused:
                n.select = True
                deleted.add(n.name)
            bpy.ops.node.delete()

        if self.delete_frames:
            while True:
                framesInUse = {node.parent for node in nodes if node.parent}
                empties = [n for n in nodes if n.type == 'FRAME' and n not in framesInUse]
                if not empties:
                    break
                for n in empties:
                    n.select = True
                    deleted.add(n.name)
                bpy.ops.node.delete()

        if not deleted:
            self.report({'INFO'}, "Nothing to delete")
        elif len(deleted) == 1:
            self.report({'INFO'}, "Deleted 1 node")
        else:
            self.report({'INFO'}, f"Deleted {len(deleted)} nodes")

        for node in nodes:
            node.select = node.name in savedSelection
        return {'FINISHED'}

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)


########## Swap Links ##########


def _isLinkableInput(socket) -> bool:
    """ Whether `socket` is an input slot that can carry a real link.
        Excludes hidden / disabled sockets and the structural sockets
        (`VRaySocketRollout`, `VRaySocketExtend`) used for rollout headers and the
        per-node "+" rows on Effects/RenderChannels.
    """
    if not socket.enabled or socket.hide:
        return False
    return socket.bl_idname not in {'VRaySocketRollout', 'VRaySocketExtend'}


class VRAY_OT_WR_swap_links(VRayOperatorBase):
    """Swap the output connections of the two selected nodes, or two similar inputs of a single node"""
    bl_idname = "vray.wr_swap_links"
    bl_label = "Swap Links"
    bl_options = {'REGISTER', 'UNDO'}

    reverse: bpy.props.BoolProperty(
        name="Reverse",
        description="In the single-linked-input cycle case, advance backward instead of forward",
        default=False,
    )

    @classmethod
    def poll(cls, context):
        if not (isVrayEditor(context) and hasEditTree(context)):
            return False
        selectedCount = len(context.selected_nodes or [])
        return 1 <= selectedCount <= 2

    def execute(self, context):
        ntree = context.space_data.edit_tree
        links = ntree.links
        selected = list(context.selected_nodes)

        if len(selected) == 2:
            self._swapOutputs(selected[0], selected[1], links)
        else:
            self._swapInputs(selected[0], links)
        return {'FINISHED'}

    def _swapOutputs(self, nodeA, nodeB, links):
        if not (nodeA.outputs and nodeB.outputs):
            if nodeA.outputs or nodeB.outputs:
                self.report({'WARNING'}, "One of the nodes has no outputs")
            else:
                self.report({'WARNING'}, "Neither of the nodes have outputs")
            return

        def capture(node):
            captured = []
            for idx, output in enumerate(node.outputs):
                for link in list(output.links):
                    captured.append((idx, link.to_socket))
                    links.remove(link)
            return captured

        nodeALinks = capture(nodeA)
        nodeBLinks = capture(nodeB)

        warned = False
        def reconnect(captured, dstNode):
            nonlocal warned
            for idx, toSocket in captured:
                if idx >= len(dstNode.outputs):
                    continue
                try:
                    links.new(dstNode.outputs[idx], toSocket)
                except RuntimeError:
                    if not warned:
                        self.report({'WARNING'}, "Some connections lost (output socket count mismatch)")
                        warned = True

        reconnect(nodeALinks, nodeB)
        reconnect(nodeBLinks, nodeA)

    def _swapInputs(self, node, links):
        """ Swap the two most similar linked inputs.
            Group linked inputs by socket type, rank groups by size.
            2-of-a-kind: swap the pair. 1-of-a-kind with one linked input: move its source
            to the next free input. Two different solos: cross-swap them.
        """
        if not node.inputs:
            self.report({'WARNING'}, "This node has no inputs to swap")
            return
        if node.inputs[0].is_multi_input:
            self.report({'WARNING'}, "Cannot swap inputs of a multi-input socket")
            return

        # V-Ray sockets can't always be distinguished by the stock .type enum, so group
        # by bl_idname instead.
        groups = []
        for idx, input in enumerate(node.inputs):
            if input.is_linked and not input.is_multi_input:
                similar = sum(
                    1 for other in node.inputs
                    if other.bl_idname == input.bl_idname and other.is_linked and not other.is_multi_input
                )
                groups.append([input, similar, idx])
        groups.sort(key=lambda group: group[1], reverse=True)

        if not groups:
            self.report({'WARNING'}, "This node has no input connections to swap")
            return

        top = groups[0]
        if top[1] == 2:
            # Find the pair sharing the top socket type.
            pair = None
            for other in node.inputs:
                if (other is not top[0]
                        and other.bl_idname == top[0].bl_idname
                        and other.is_linked
                        and not other.is_multi_input):
                    pair = (top[0], other)
                    break
            if pair:
                firstInput, secondInput = pair
                firstFrom = firstInput.links[0].from_socket
                secondFrom = secondInput.links[0].from_socket
                links.new(firstFrom, secondInput)
                links.new(secondFrom, firstInput)
            return

        if top[1] == 1:
            if len(groups) == 1:
                # Single linked input - move its source to the next free input.
                sourceInput = top[0]
                idx = top[2]
                fromSocket = sourceInput.links[0].from_socket
                links.remove(sourceInput.links[0])
                inputCount = len(node.inputs)
                step = -1 if self.reverse else 1
                nextIdx = (idx + step) % inputCount
                # Skip already-linked inputs AND structural sockets (rollouts, the "+"
                # extend rows) that can't carry a real link. The original input is the
                # wrap-around stop because we just freed it.
                while (nextIdx != idx
                       and (node.inputs[nextIdx].is_linked
                            or not _isLinkableInput(node.inputs[nextIdx]))):
                    nextIdx = (nextIdx + step) % inputCount
                try:
                    links.new(fromSocket, node.inputs[nextIdx])
                except RuntimeError:
                    pass
            elif len(groups) >= 2:
                # Two different-type single links - cross-swap them.
                firstInput, secondInput = groups[0][0], groups[1][0]
                firstFrom = firstInput.links[0].from_socket
                secondFrom = secondInput.links[0].from_socket
                links.new(firstFrom, secondInput)
                links.new(secondFrom, firstInput)


########## Detach Outputs ##########

class VRAY_OT_WR_detach_outputs(VRayOperatorBase):
    """Detach outputs of selected nodes, leaving inputs linked"""
    bl_idname = "vray.wr_detach_outputs"
    bl_label = "Detach Outputs"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return isVrayEditor(context) and hasEditTree(context) and hasSelection(context)

    def execute(self, context):
        selected = list(context.selected_nodes)
        bpy.ops.node.duplicate_move_keep_inputs()
        newNodes = list(context.selected_nodes)

        bpy.ops.node.select_all(action='DESELECT')
        for node in selected:
            node.select = True
        bpy.ops.node.delete_reconnect()

        for node in newNodes:
            node.select = True
        bpy.ops.transform.translate('INVOKE_DEFAULT')
        return {'FINISHED'}


########## Add Reroutes ##########

class VRAY_OT_WR_add_reroutes(VRayOperatorBase):
    """Add Reroute nodes and link them to outputs of selected nodes"""
    bl_idname = "vray.wr_add_reroutes"
    bl_label = "Add Reroutes"
    bl_description = "Add reroutes to outputs of selected nodes"
    bl_options = {'REGISTER', 'UNDO'}

    option: bpy.props.EnumProperty(
        name="Option",
        items=[
            ('ALL', 'To All', 'Add to all outputs'),
            ('LOOSE', 'To Loose', 'Add only to loose outputs'),
            ('LINKED', 'To Linked', 'Add only to linked outputs'),
        ],
        default='ALL',
    )

    @classmethod
    def poll(cls, context):
        return isVrayEditor(context) and hasEditTree(context) and hasSelection(context)

    def execute(self, context):
        ntree = context.space_data.edit_tree
        nodes = ntree.nodes
        links = ntree.links
        yStep = -22.0
        newReroutes = []

        selected = [node for node in nodes if node.select]
        for node in selected:
            if not node.outputs:
                continue
            x = node.location.x + node.width + 20.0
            y = node.location.y - (0.0 if node.type == 'REROUTE' else 35.0)

            if node.type == 'REROUTE':
                node.hide = False

            addedHere = []
            rerouteCount = 0
            for output in node.outputs:
                if isinstance(output, bpy.types.NodeSocketVirtual):
                    continue
                visible = (self.option == 'ALL'
                           or (self.option == 'LOOSE' and not output.links)
                           or (self.option == 'LINKED' and output.links))
                if visible:
                    reroute = nodes.new('NodeReroute')
                    nodes.active = reroute
                    for link in list(output.links):
                        links.new(reroute.outputs[0], link.to_socket)
                    links.new(output, reroute.inputs[0])
                    reroute.location = (x, y)
                    addedHere.append(reroute)
                    newReroutes.append(reroute)
                if visible or not output.hide:
                    rerouteCount += 1
                    y += yStep

            if node.hide and addedHere:
                yTranslate = rerouteCount * yStep / 2.0 - yStep - 35.0
                for reroute in addedHere:
                    reroute.location.y -= yTranslate

        if not newReroutes:
            return {'CANCELLED'}

        for node in nodes:
            node.select = node in newReroutes
        return {'FINISHED'}


########## Link Active to Selected ##########

def _hasSemanticMatch(srcNode, dstNodes) -> bool:
    """Return True if any output of srcNode matches any input of any dstNode by bl_idname,
    preferring V-Ray material/BRDF family matches so direction detection is meaningful."""
    for output in srcNode.outputs:
        if not output.enabled or output.hide:
            continue
        for dst in dstNodes:
            for inp in dst.inputs:
                if not inp.enabled or inp.hide:
                    continue
                if output.bl_idname == inp.bl_idname:
                    return True
    return False


class VRAY_OT_WR_link_active_to_selected(VRayOperatorBase):
    """Link the active node to selected nodes"""
    bl_idname = "vray.wr_link_active_to_selected"
    bl_label = "Link Active Node to Selected"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return (isVrayEditor(context)
                and hasEditTree(context)
                and hasActive(context)
                and hasSelection(context, minCount=2))

    def execute(self, context):
        ntree = context.space_data.edit_tree
        nodes = ntree.nodes
        links = ntree.links
        active = nodes.active
        # Exclude the active by both identity and name; `nodes.active` sometimes
        # hands back a different Python proxy than the one iteration yields,
        # which has caused active to slip into `selected` and self-link.
        selected = [node for node in nodes
                    if node.select and node is not active and node.name != active.name]

        if not selected:
            return {'CANCELLED'}

        # Auto-detect reversed direction: if the user clicked source first then the
        # target (making it active), the intended flow is selected -> active.
        fwdMatch = _hasSemanticMatch(active, selected)
        revMatch = _hasSemanticMatch(selected[0], [active]) if len(selected) == 1 else False
        if revMatch and not fwdMatch:
            self._linkOneToOne(links, selected[0], active)
            return {'FINISHED'}

        self._linkActiveToSelected(links, active, selected)
        return {'FINISHED'}

    def _linkActiveToSelected(self, links, active, selected):
        outputs = [o for o in active.outputs if o.enabled and not o.hide]
        done = False
        for output in outputs:
            if done:
                break
            for node in selected:
                if node is active or node.name == active.name:
                    continue
                for inp in node.inputs:
                    if not inp.enabled or inp.hide or inp.is_linked:
                        continue
                    if not (socketsCompatible(output, inp) or node.type == 'REROUTE'):
                        continue
                    try:
                        links.new(output, inp)
                    except RuntimeError:
                        continue
                    done = True
                    break

    def _linkOneToOne(self, links, srcNode, dstNode):
        """Wire the first compatible output of srcNode into dstNode's first matching input."""
        if srcNode is dstNode or srcNode.name == dstNode.name:
            return
        for output in srcNode.outputs:
            if not output.enabled or output.hide:
                continue
            for inp in dstNode.inputs:
                if not inp.enabled or inp.hide or inp.is_linked:
                    continue
                if not (socketsCompatible(output, inp) or dstNode.type == 'REROUTE'):
                    continue
                try:
                    links.new(output, inp)
                    return
                except RuntimeError:
                    continue


########## Link to Output ##########


def _findVRayOutputNode(ntree: bpy.types.NodeTree):
    """ Find the output node for a V-Ray tree. Returns the node or None. """
    tt = getattr(getattr(ntree, 'vray', None), 'tree_type', '')
    if tt == 'LIGHT':
        return getLightOutputNode(ntree)
    return getOutputNode(ntree, tt) if tt else None


def _createVRayOutputNode(ntree: bpy.types.NodeTree, anchor: bpy.types.Node):
    """ Create an output node for the tree if one is missing. Positions it to
        the right of `anchor`. Returns the new node or None if the tree's
        type isn't auto-creatable (e.g. LIGHT).
    """
    tt = getattr(getattr(ntree, 'vray', None), 'tree_type', '')
    bl_idname = _OUTPUT_BY_TREE_TYPE.get(tt)
    if not bl_idname:
        return None
    try:
        node = ntree.nodes.new(bl_idname)
    except RuntimeError:
        return None
    node.location = (anchor.location.x + anchor.width + 80.0, anchor.location.y)
    return node


class VRAY_OT_WR_link_to_output(VRayOperatorBase):
    """Link the active node to the node tree's output"""
    bl_idname = "vray.wr_link_out"
    bl_label = "Connect to Output"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        if not (isVrayEditor(context) and hasEditTree(context) and hasActive(context)):
            return False
        active = context.space_data.edit_tree.nodes.active
        return bool(active and active.outputs)

    def execute(self, context):
        ntree = context.space_data.edit_tree
        active = ntree.nodes.active

        outputNode = _findVRayOutputNode(ntree)
        autoCreated = False
        if outputNode is None:
            outputNode = _createVRayOutputNode(ntree, active)
            if outputNode is None:
                self.report({'WARNING'}, "No V-Ray output node and tree type doesn't support auto-create")
                return {'CANCELLED'}
            autoCreated = True
        if outputNode is active:
            return {'CANCELLED'}

        # Specialised slots (Outlines on Material; Environment/Effects/Channels on World;
        # Displacement/Subdivision/Matte/Surface/Visibility on Object) are reserved for
        # their matching source nodes. A generic source can never land on them - it would
        # silently overwrite the existing specialised connection.
        reservedInputs = set(_INPUT_PREFERENCE_BY_PLUGIN.values())
        visibleOutputs = [output for output in active.outputs if output.enabled and not output.hide]

        chosenOutput = None
        chosenInput = None

        preferredName = getPreferredOutputInputName(active)
        if preferredName:
            preferredInput = outputNode.inputs.get(preferredName)
            if preferredInput and preferredInput.enabled and not preferredInput.hide:
                for output in visibleOutputs:
                    if socketsCompatible(output, preferredInput):
                        chosenOutput, chosenInput = output, preferredInput
                        break

        candidateInputs = [
            input for input in outputNode.inputs
            if input.enabled and not input.hide and input.name not in reservedInputs
        ]

        # Prefer strict bl_idname match before the looser V-Ray cross-type rule -
        # otherwise a Color output could land on a Float/Vector slot when an exact
        # Color slot is sitting right there.
        if chosenOutput is None:
            for input in candidateInputs:
                for output in visibleOutputs:
                    if output.bl_idname == input.bl_idname:
                        chosenOutput, chosenInput = output, input
                        break
                if chosenOutput:
                    break

        if chosenOutput is None:
            for input in candidateInputs:
                for output in visibleOutputs:
                    if socketsCompatible(output, input):
                        chosenOutput, chosenInput = output, input
                        break
                if chosenOutput:
                    break

        if not (chosenOutput and chosenInput):
            if autoCreated:
                ntree.nodes.remove(outputNode)
            self.report({'WARNING'}, "Could not find a compatible socket pair")
            return {'CANCELLED'}

        try:
            ntree.links.new(chosenOutput, chosenInput)
        except RuntimeError as e:
            if autoCreated:
                ntree.nodes.remove(outputNode)
            self.report({'WARNING'}, f"Could not connect: {e}")
            return {'CANCELLED'}
        return {'FINISHED'}


########## Registration ##########

def getRegClasses():
    return (
        VRAY_OT_WR_del_unused,
        VRAY_OT_WR_swap_links,
        VRAY_OT_WR_detach_outputs,
        VRAY_OT_WR_add_reroutes,
        VRAY_OT_WR_link_active_to_selected,
        VRAY_OT_WR_link_to_output,
    )


def register():
    for regClass in getRegClasses():
        bpy.utils.register_class(regClass)


def unregister():
    for regClass in reversed(getRegClasses()):
        bpy.utils.unregister_class(regClass)
