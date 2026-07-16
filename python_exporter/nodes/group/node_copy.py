# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

""" Direct node-graph copy that bypasses Blender's clipboard.

    The clipboard-based approach has several drawbacks:
      - Wipes the user's real clipboard.
      - Requires an active NODE_EDITOR context (blocks headless use).
      - Re-runs init() which loses PointerProperty state, so we needed
        a fragile _transferIdProperties workaround on top.

    This module copies nodes between trees by creating them fresh,
    copying attributes, V-Ray property groups, socket values, and then
    invoking each node's own `.copy()` hook so per-type state
    (gradient ramp textures, bitmap images, etc.) is preserved.
"""

import bpy

from vray_blender import debug
from vray_blender.lib import blender_utils
from vray_blender.nodes.utils import DisableAutoConnect, copyVRayNodeState


# Generic node attributes copied for all nodes. Subclass-specific attrs
# like NodeFrame.text/label_size/shrink are handled below if present.
_NODE_BASE_ATTRS = (
    'location', 'width', 'height', 'label', 'color', 'use_custom_color',
    'hide', 'mute', 'show_options', 'show_preview', 'show_texture',
)

# Extra attrs copied when present (covers NodeFrame, group nodes, and a few specials).
_NODE_OPT_ATTRS = (
    'label_size', 'shrink', 'text',
    # Nested group nodes (VRayNodeGroup, ShaderNodeGroup) need their tree
    # reference preserved. NodeCustomGroup's default copy() only handles
    # this when invoked by Blender's duplication machinery (Ctrl+D), not
    # when we call it manually from this module.
    'node_tree',
)


def copyNodesBetweenTrees(srcTree, srcNodes, dstTree):
    """ Copy srcNodes from srcTree into dstTree. Returns a dict mapping
        src_node.name -> newly created destination node.

        Preserves: node attributes, V-Ray PropertyGroup values, socket
        values (via VRaySocket.copy), internal links among the copied
        set, NodeFrame parent relationships (only when the frame
        itself is in the copied set), and fcurve animation (keyframes
        and drivers whose data_path targets a copied node).
    """
    nodeMap = {}

    with DisableAutoConnect():
        for srcNode in srcNodes:
            newNode = dstTree.nodes.new(srcNode.bl_idname)
            nodeMap[srcNode.name] = newNode

            # Prefer original name; Blender will suffix on collision.
            try:
                newNode.name = srcNode.name
            except (TypeError, AttributeError):
                pass

            _copyNodeAttrs(srcNode, newNode)
            # Copies V-Ray PropertyGroup values + meta sockets, handling
            # multi-propgroup meta nodes (Image Texture, UVW Mapping).
            copyVRayNodeState(srcNode, newNode)
            # Output sockets aren't covered by copyVRayNodeState (only meta
            # inputs), so copy their values explicitly. Also copy input
            # sockets: this picks up (a) Cycles built-in sockets' default_value
            # — copyVRayNodeState skips Cycles nodes entirely — and (b) the
            # multiplier/use on V-Ray sockets whose value is backed by the
            # propgroup (the meta-socket loop in copyVRayPropGroup skips those).
            _copySocketsByName(srcNode.outputs, newNode.outputs)
            _copySocketsByName(srcNode.inputs, newNode.inputs)

            # Subclass-specific copy hook (gradient ramp, bitmap image, etc.).
            # For plain V-Ray nodes this dispatches to vrayNodeCopy which
            # sets unique_id and runs any plugin-specific nodeCopy callback.
            # We catch Exception broadly because plugin-specific hooks are
            # third-party code; log for diagnostics so bugs aren't silent.
            if fnCopy := getattr(newNode, 'copy', None):
                try:
                    fnCopy(srcNode)
                except Exception as ex:
                    debug.printExceptionInfo(ex, f"copy hook on {srcNode.bl_idname}")

        # Remap NodeFrame parents. Only rebind if the parent frame is in
        # the copied set; otherwise the node is left parentless in dst.
        for srcNode in srcNodes:
            if srcNode.parent and srcNode.parent.name in nodeMap:
                nodeMap[srcNode.name].parent = nodeMap[srcNode.parent.name]

        # Copy internal links (both endpoints inside the copied set).
        srcNames = set(nodeMap.keys())
        for link in srcTree.links:
            if link.from_node.name in srcNames and link.to_node.name in srcNames:
                newFrom = nodeMap[link.from_node.name]
                newTo   = nodeMap[link.to_node.name]
                fromSock = next((s for s in newFrom.outputs if s.name == link.from_socket.name), None)
                toSock   = next((s for s in newTo.inputs   if s.name == link.to_socket.name),   None)
                if fromSock and toSock:
                    dstTree.links.new(fromSock, toSock)

        _copyNodeAnimation(srcTree, dstTree, nodeMap)

    return nodeMap


def _copyNodeAnimation(srcTree, dstTree, nodeMap):
    """ Copy fcurves and drivers from srcTree.animation_data into
        dstTree.animation_data for any data_path that targets a node in
        nodeMap. The src/dst node names may differ (Blender suffixes on
        collision), so we rewrite the 'nodes["..."]' prefix.

        This mirrors Blender's BKE_animdata_copy_by_basepath (not exposed
        to Python). Callers that delete the source nodes after copying
        will leave orphan fcurves in srcTree's action; Blender handles
        those gracefully.
    """
    srcAdt = srcTree.animation_data
    if srcAdt is None:
        return

    pathPairs = []
    for srcName, dstNode in nodeMap.items():
        srcNode = srcTree.nodes.get(srcName)
        if srcNode is None or dstNode is None:
            continue
        try:
            srcPath = srcNode.path_from_id()
            dstPath = dstNode.path_from_id()
        except (RuntimeError, AttributeError):
            continue
        if srcPath and dstPath:
            pathPairs.append((srcPath, dstPath))

    if not pathPairs:
        return

    srcFCurves = blender_utils.getFCurves(srcTree)
    if srcFCurves:
        dstAdt = dstTree.animation_data_create()
        if dstAdt.action is None or dstAdt.action == srcAdt.action:
            dstAdt.action = bpy.data.actions.new(name=f"{dstTree.name}Action")
        dstFCurves = blender_utils.getFCurves(dstTree, ensure=True)
        # `if dstFCurves:` is wrong — a freshly-created channelbag.fcurves
        # collection is empty (bool == False) but is still the valid target
        # to write into.
        if dstFCurves is not None:
            _copyFCurvesByBasepath(srcFCurves, dstFCurves, pathPairs)

    if srcAdt.drivers:
        dstAdt = dstTree.animation_data_create()
        _copyFCurvesByBasepath(srcAdt.drivers, dstAdt.drivers, pathPairs)


def _copyFCurvesByBasepath(srcCurves, dstCurves, pathPairs):
    """ For each fcurve in srcCurves whose data_path begins with one of
        the src basepaths, create a matching fcurve on dstCurves with the
        prefix rewritten to the dst basepath, then copy keyframes and
        modifiers.
    """
    for fc in list(srcCurves):
        match = next(
            ((srcPath, dstPath) for srcPath, dstPath in pathPairs
             if fc.data_path == srcPath or fc.data_path.startswith(srcPath + ".")),
            None,
        )
        if match is None:
            continue
        srcPath, dstPath = match
        newDataPath = dstPath + fc.data_path[len(srcPath):]

        try:
            newFc = dstCurves.new(newDataPath, index=fc.array_index)
        except (RuntimeError, TypeError):
            # data_path doesn't resolve on the destination (e.g. the
            # node-level property doesn't exist yet because init()
            # populated different sockets). Skip rather than fail.
            continue

        if fc.keyframe_points:
            newFc.keyframe_points.add(len(fc.keyframe_points))
            for srcKf, dstKf in zip(fc.keyframe_points, newFc.keyframe_points):
                dstKf.co = srcKf.co
                dstKf.handle_left = srcKf.handle_left
                dstKf.handle_right = srcKf.handle_right
                dstKf.interpolation = srcKf.interpolation
                dstKf.handle_left_type = srcKf.handle_left_type
                dstKf.handle_right_type = srcKf.handle_right_type
                dstKf.type = srcKf.type
                dstKf.easing = srcKf.easing

        newFc.extrapolation = fc.extrapolation
        newFc.update()


def _copyNodeAttrs(srcNode, dstNode):
    for attr in _NODE_BASE_ATTRS:
        try:
            setattr(dstNode, attr, getattr(srcNode, attr))
        except (TypeError, AttributeError):
            pass
    for attr in _NODE_OPT_ATTRS:
        if hasattr(srcNode, attr):
            try:
                setattr(dstNode, attr, getattr(srcNode, attr))
            except (TypeError, AttributeError):
                pass


def _copySocketsByName(srcSocks, dstSocks):
    """ Copy socket values from src to dst. V-Ray sockets are matched by
        their unique `vray_attr`; display names are NOT unique (e.g.
        ColorCorrection has both `brightness` and `adv_brightness`, the
        latter labelled "Brightness"), so a name match would copy a value
        onto the wrong propgroup-backed attribute. We first match by vray_attr
        which should be always correct for plugin parameter sockets and then
        match by socket name for things like layered nodes.

        Tries the socket's own copy() first (V-Ray sockets handle
        value/multiplier/use), then falls back to default_value for Cycles
        built-in NodeSocket types that don't define copy().
    """
    for srcSock in srcSocks:
        srcAttr = getattr(srcSock, 'vray_attr', '')
        if srcAttr:
            dstSock = next((s for s in dstSocks if getattr(s, 'vray_attr', '') == srcAttr), None)
        else:
            dstSock = next((s for s in dstSocks if s.name == srcSock.name), None)
        if dstSock is None:
            continue
        copied = False
        if fnCopy := getattr(srcSock, 'copy', None):
            try:
                fnCopy(dstSock)
                copied = True
            except (TypeError, AttributeError):
                pass
        if not copied:
            srcVal = getattr(srcSock, 'default_value', None)
            if srcVal is not None and hasattr(dstSock, 'default_value'):
                try:
                    dstSock.default_value = srcVal
                except (TypeError, AttributeError):
                    pass
