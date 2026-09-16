# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

""" Falloff curve widgets.

    CurveMapping cannot live on a custom PropertyGroup, so each scatter object's falloff curves
    (altitude limit, look-at) are hosted on ShaderNodeFloatCurve nodes inside a hidden
    ShaderNodeTree, keyed by the object's curve_ns (same pattern as vray_blender's
    nodes/curves_node.py, ported to keep this addon standalone).

    The durable/exported form is a densely sampled '[[x, y], ...]' JSON kept in the propgroup's
    hidden *_falloff_data StringProperty; a msgbus subscription re-serializes it on every widget
    edit. Both the preview request and the V-Ray render export read only the StringProperty.

    msgbus subscriptions are wiped on file load and undo - recompute.py re-registers them from
    its load_post/undo_post handlers via resubscribeAll().
"""

import json
import bpy



_CURVE_TREE_NAME = ".chaosScatterCurves"
_CURVE_SAMPLES = 32

# slot -> (settings subgroup name, serialized-data field name)
SLOTS = {
    'altitude': ('surface', 'surface_altitude_limit_falloff_data'),
    'lookat':   ('look_at', 'look_at_falloff_data'),
}


def _curveNodeName(curveNs: str, slot: str) -> str:
    return f"cs_{curveNs}_{slot}"


def _getCurveTree(create=False):
    tree = bpy.data.node_groups.get(_CURVE_TREE_NAME)
    if (tree is None) and create:
        tree = bpy.data.node_groups.new(_CURVE_TREE_NAME, 'ShaderNodeTree')
        tree.use_fake_user = True
    return tree


def getCurveNode(cs, slot: str):
    if not cs.curve_ns:
        return None
    if (tree := _getCurveTree()) is None:
        return None
    return tree.nodes.get(_curveNodeName(cs.curve_ns, slot))


def ensureCurveNode(obj, slot: str):
    """ Create (if missing) and subscribe the hidden curve node backing a falloff widget. """
    cs = obj.chaos_scatter
    assert cs.curve_ns, "curve_ns must be assigned before creating curve nodes"

    tree = _getCurveTree(create=True)
    nodeName = _curveNodeName(cs.curve_ns, slot)

    if (node := tree.nodes.get(nodeName)) is None:
        node = tree.nodes.new('ShaderNodeFloatCurve')
        node.name = nodeName

    _subscribeCurveNode(node, cs.curve_ns, slot)
    return node


def removeCurveNodes(curveNs: str):
    if (tree := _getCurveTree()) is None:
        return
    for slot in SLOTS:
        if node := tree.nodes.get(_curveNodeName(curveNs, slot)):
            bpy.msgbus.clear_by_owner(node)
            tree.nodes.remove(node)


def copyCurves(srcNs: str, dstObj):
    """ Clone the curve widgets of a duplicated scatter object under its fresh curve_ns. """
    tree = _getCurveTree()
    if tree is None:
        return
    for slot in SLOTS:
        srcNode = tree.nodes.get(_curveNodeName(srcNs, slot))
        if srcNode is None:
            continue
        dstNode = ensureCurveNode(dstObj, slot)
        _copyMapping(srcNode.mapping, dstNode.mapping)


def _copyMapping(srcMapping, dstMapping):
    for i, curve in enumerate(srcMapping.curves):
        dstCurve = dstMapping.curves[i]
        while len(dstCurve.points) < len(curve.points):
            dstCurve.points.new(0, 0)
        for j, point in enumerate(curve.points):
            dstCurve.points[j].location = point.location
            dstCurve.points[j].handle_type = point.handle_type
    dstMapping.extend = srcMapping.extend
    dstMapping.update()


def sampleCurve(node) -> list:
    """ Sample the widget curve uniformly over [0, 1] into the exported point list. """
    mapping = node.mapping
    mapping.initialize()
    curve = mapping.curves[-1]
    points = []
    for i in range(_CURVE_SAMPLES):
        x = i / (_CURVE_SAMPLES - 1)
        points.append([x, mapping.evaluate(curve, x)])
    return points


def parseFalloffData(falloffData: str):
    """ Decode a serialized falloff curve ('[[x, y], ...]' JSON, written by serializeCurve) into
        the vector list GeomScatter expects. Returns None when unset or unparseable.

        The inverse of serializeCurve, and the ONLY decoder: the preview backend and vray_blender's
        scatter_export both call it, so the two cannot send different samples for the same curve.
    """
    if not falloffData:
        return None
    try:
        points = json.loads(falloffData)
    except ValueError:
        return None
    if not points:
        return None
    return [(float(x), float(y), 0.0) for x, y in points]


def serializeCurve(obj, slot: str):
    """ Re-serialize a widget curve into its propgroup StringProperty. Returns True on change. """
    cs = obj.chaos_scatter
    node = getCurveNode(cs, slot)
    if node is None:
        return False

    groupName, dataField = SLOTS[slot]
    group = getattr(cs, groupName)
    newData = json.dumps(sampleCurve(node))
    if getattr(group, dataField) == newData:
        return False
    setattr(group, dataField, newData)
    return True


def _findScatterObject(curveNs: str):
    for obj in bpy.data.objects:
        cs = getattr(obj, 'chaos_scatter', None)
        if (cs is not None) and cs.is_scatter and cs.curve_ns == curveNs:
            return obj
    return None


def _onCurveEdited(curveNs: str, slot: str):
    obj = _findScatterObject(curveNs)
    if obj is None:
        return
    if serializeCurve(obj, slot):
        from chaos_scatter import recompute
        recompute.markDirty(obj)


def _subscribeCurveNode(node, curveNs: str, slot: str):
    bpy.msgbus.clear_by_owner(node)
    bpy.msgbus.subscribe_rna(
        key = node,
        owner = node,
        args = (curveNs, slot),
        notify = _onCurveEdited,
    )


def resubscribeAll():
    """ Re-register the msgbus subscriptions (wiped on file load / undo). """
    tree = _getCurveTree()
    if tree is None:
        return
    for obj in bpy.data.objects:
        cs = getattr(obj, 'chaos_scatter', None)
        if (cs is None) or not cs.is_scatter or not cs.curve_ns:
            continue
        for slot in SLOTS:
            if node := tree.nodes.get(_curveNodeName(cs.curve_ns, slot)):
                _subscribeCurveNode(node, cs.curve_ns, slot)


def cleanupOrphanCurveNodes(liveCurveNamespaces: set):
    """ Remove curve nodes whose scatter object no longer exists. """
    tree = _getCurveTree()
    if tree is None:
        return
    for node in list(tree.nodes):
        if not node.name.startswith("cs_"):
            continue
        parts = node.name.split("_")
        if len(parts) >= 3 and parts[1] not in liveCurveNamespaces:
            bpy.msgbus.clear_by_owner(node)
            tree.nodes.remove(node)
