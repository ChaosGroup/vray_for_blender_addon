# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

""" Shared helpers for the V-Ray node-editor wrangler operators.

    Hosts geometry helpers (absolute node location, hit-testing) and routing
    tables that several operator modules need.
"""

from math import hypot

import bpy

from vray_blender.nodes.utils import _OUTPUT_NODE_TYPES


# tree_type -> output-node bl_idname. Derived from utils._OUTPUT_NODE_TYPES,
# minus the Cycles SHADER entry. LIGHT trees aren't here - they have one
# output class per light type, see getLightOutputNode().
_OUTPUT_BY_TREE_TYPE = {k: v for k, v in _OUTPUT_NODE_TYPES.items() if k != 'SHADER'}


# Tree-root and container nodes that Delete Unused must never remove.
# Frames and reroutes are also kept so the user can prune them deliberately.
# NodeGroupOutput has no .outputs at all, so it would otherwise be classified
# as "unused" when running the operator inside a group, taking the whole tree
# down with it. NodeGroupInput is kept for symmetry.
_END_NODE_TYPES = set(_OUTPUT_BY_TREE_TYPE.values()) | {
    'VRayNodeRenderChannels',
    'VRayNodeEffectsHolder',
    'NodeFrame',
    'NodeReroute',
    'NodeGroupInput',
    'NodeGroupOutput',
}


# Source nodes that should preferentially route to a specific output-node
# input by name when auto-picking a target socket. Single source of truth for
# Preview Node, Link to Output, and Lazy Connect.
#
# Keyed by vray_plugin (for nodes generated from a plugin) or bl_idname (for
# special-case container nodes whose vray_plugin is 'NONE'). The two key
# namespaces don't collide - plugin names never start with 'VRayNode'.
#
# Values (preferred input names) are also "reserved" - excluded from the
# generic auto-link ladder when the source isn't the matching node - so a
# random texture can't accidentally land on a specialised slot.
_INPUT_PREFERENCE_BY_PLUGIN = {
    # MATERIAL tree
    'BRDFToonOverride':               'Outlines',
    # OBJECT tree (special container nodes with vray_plugin == 'NONE')
    'VRayNodeDisplacement':           'Displacement',
    'VRayNodeGeomStaticSmoothedMesh': 'Subdivision',
    'VRayNodeObjectMatteProps':       'Matte',
    'VRayNodeObjectSurfaceProps':     'Surface',
    'VRayNodeObjectVisibilityProps':  'Visibility',
    # WORLD tree (container nodes with vray_plugin == 'NONE')
    'VRayNodeEnvironment':            'Environment',
    'VRayNodeEffectsHolder':          'Effects',
    'VRayNodeRenderChannels':         'Channels',
}


def getPreferredOutputInputName(node: bpy.types.Node) -> str:
    """ Return the preferred output-node input name for `node`, or '' if none.
        Looks up by vray_plugin first, then by bl_idname.
    """
    pluginType = getattr(node, 'vray_plugin', '')
    return _INPUT_PREFERENCE_BY_PLUGIN.get(pluginType) or _INPUT_PREFERENCE_BY_PLUGIN.get(node.bl_idname, '')


def treeType(context) -> str:
    ntree = context.space_data.edit_tree if context.space_data else None
    return getattr(getattr(ntree, 'vray', None), 'tree_type', '')


def absLoc(node: bpy.types.Node):
    """Walk parent chain to get absolute node location."""
    loc = node.location.copy()
    parent = node.parent
    while parent:
        loc += parent.location
        parent = parent.parent
    return loc


def socketsCompatible(outputSocket, inputSocket) -> bool:
    """ Whether a direct link from output to input is permitted under V-Ray's rules.

        Note: this is the *loose* predicate - it allows V-Ray-valid cross-type links
        (e.g. Color -> Float for a texture wired to a float-texture slot). Callers
        that want to prefer same-bl_idname matches should check `bl_idname` equality
        explicitly before falling back to this.
    """
    # Local import to avoid a circular dependency at module load.
    from vray_blender.nodes.links import isConnectionAllowed
    return isConnectionAllowed(outputSocket, inputSocket)


def _dpiFac() -> float:
    return bpy.context.preferences.system.dpi / 72.0


def nodeAtPos(nodes, context, event) -> bpy.types.Node | None:
    """Return the node under the mouse, or the nearest node by corner/edge distance.
       Combines point-in-rect with nearest-corner so grazes hit.
    """
    space = context.space_data
    space.cursor_location_from_region(event.mouse_region_x, event.mouse_region_y)
    x, y = space.cursor_location

    dpi = _dpiFac()
    under = []
    points = []
    for node in nodes:
        if node.type == 'FRAME':
            continue
        dx = node.dimensions.x / dpi
        dy = node.dimensions.y / dpi
        loc = absLoc(node)
        lx, ly = loc.x, loc.y
        if lx <= x <= lx + dx and ly - dy <= y <= ly:
            under.append(node)
        for px, py in (
            (lx, ly), (lx + dx, ly), (lx, ly - dy), (lx + dx, ly - dy),
            (lx + dx / 2, ly), (lx + dx / 2, ly - dy),
            (lx, ly - dy / 2), (lx + dx, ly - dy / 2),
        ):
            points.append((node, hypot(x - px, y - py)))

    if not points:
        return None
    nearest = min(points, key=lambda pair: pair[1])[0]
    if len(under) == 1 and under[0] is not nearest:
        return under[0]
    return nearest


def safeSet(obj, attr: str, value) -> bool:
    """ Best-effort `setattr` that swallows the typical V-Ray prop-mismatch errors.
        Used when applying preset / imported values to dynamic PropertyGroups
        whose props may have stricter ranges or enum sets than the source data.
    """
    try:
        setattr(obj, attr, value)
        return True
    except (TypeError, ValueError, AttributeError):
        return False
