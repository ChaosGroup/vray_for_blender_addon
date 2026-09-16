# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

""" Texture slots - the model behind editing V-Ray textures from the property pages.

    A "texture slot" is an input socket the user can wire a texture into. The property pages
    (Material tab, World tab, Scene Lister detail pane) draw a picker on such sockets so a texture
    can be created, replaced or cleared without opening the Node Editor.

    This module holds the model only - no UILayout, no operators. The drawing and the operators
    live in ui/node_slots.py.

    Note on imports: plugins/__init__.py imports nodes.utils at module level, so nothing under
    nodes/ may import vray_blender.plugins at module level. The plugin lookups below are local
    imports for that reason.
"""

import os
from dataclasses import dataclass

import bpy

from vray_blender.exporting.tools import getFarNodeLinkImpl, getInputSocketByAttr
from vray_blender.exporting.update_tracker import UpdateTracker
from vray_blender.nodes import utils as NodesUtils
from vray_blender.nodes.navigation import setPanelNode
from vray_blender.nodes.tools import NODE_LEVEL_WIDTH


# Attribute types whose socket is connectable, but which take a different KIND of node than a
# texture - a BRDF, a mapping generator, a transform, an object reference. Everything else that is
# connectable accepts a texture.
#
# This is a deny-list on top of "the socket is connectable" rather than an allow-list of texture
# types, because the allow-list is the thing that goes stale: plain COLOR is not a *_TEXTURE type
# but is genuinely texturable on the volumetrics (EnvironmentFog, VolumeVRayToon), and an
# allow-list would have to learn that by hand.
NON_TEXTURE_INPUT_TYPES = frozenset({
    'BRDF',
    'BRDF_USE',
    'MATERIAL',
    'GEOMETRY',
    'OBJECT',
    'PLUGIN',
    'PLUGIN_LIST',
    'INCLUDE_EXCLUDE_LIST',
    'UVWGEN',
    'TRANSFORM',
    'MATRIX',
    'MATRIX_TEXTURE',
    'GRAD_RAMP',

    # Plain VECTOR is a positional/directional constant - pivot_offset, gizmo_center,
    # gravity_vector, scene_upDir, UVWGen coverage, the GeomScatter transform ranges. Same category
    # as TRANSFORM and MATRIX above. VECTOR_TEXTURE is the texturable one and stays out of this set.
    'VECTOR',

    # Plain-value types a texture can never drive. They are normally hidden anyway, but a plugin's
    # Node.input_sockets list can name one explicitly, which makes the socket connectable and would
    # otherwise earn it a picker - MtlGLSL.shader_file (STRING) is the real case.
    # Plain FLOAT is deliberately NOT here: the plugins that expose one (BRDFAlSurface
    # .sss_density_scale, TexNormalBump.bump_tex_mult, VolumeVRayToon.traceBias) are multiplier and
    # bias params where driving them with a float texture is meaningful, and exposing them as node
    # inputs was a deliberate authoring choice. Run scripts/dump_texture_slots.py to review the set.
    'BOOL',
    'INT',
    'STRING',
    'ENUM',
})


# Sockets that carry structure rather than a plugin parameter. These two are the exception to the
# "never compare bl_idname to a literal" rule that holds for value sockets: unlike those, they are
# never generated per plugin parameter, so their bl_idname really is the literal.
STRUCTURAL_SOCKET_TYPES = frozenset({'VRaySocketRollout', 'VRaySocketExtend'})


# (pluginName, attrName) -> bool. Plugin descriptions are immutable after plugins._loadPlugins(),
# so this never needs invalidating within a session. Cleared by clearCaches() on unregister.
_textureAttrCache: dict[tuple[str, str], bool] = {}


def isTextureAttr(pluginName: str, attrName: str) -> bool:
    """ True when the plugin parameter's type is one a texture can drive.

        Metadata only - no node needed - so the headless validator can check the predicate over
        every plugin without instantiating anything. isTextureSlot() adds the runtime half.
    """
    # PLUGIN_MODULES.get, not getPluginModule(): the latter raises for an unknown name, and this
    # runs during a panel draw where nodes legitimately carry vray_plugin == 'NONE'.
    from vray_blender.plugins import PLUGIN_MODULES, getPluginAttr

    key = (pluginName, attrName)
    if (cached := _textureAttrCache.get(key)) is not None:
        return cached

    result = False
    if pluginModule := PLUGIN_MODULES.get(pluginName):
        if attrDesc := getPluginAttr(pluginModule, attrName):
            result = attrDesc['type'] not in NON_TEXTURE_INPUT_TYPES

    _textureAttrCache[key] = result
    return result


def isTextureSlot(socket) -> bool:
    """ True when the user can wire a texture into this socket on the node itself.

        Driven by socket.enabled, which nodes/utils.py already computes from the plugin's
        Node.input_sockets list plus the attribute's 'visible' condition (see
        _getPluginInputSockets and computeSocketVisibility). Never from the socket class: socket
        classes are generated per plugin parameter, and the plain-value types in
        HiddenNodeInputTypes (BOOL/INT/FLOAT/STRING/ENUM) get a socket too - a permanently
        unlinkable one that exists only so the value can be animated. Keying off the socket class
        is what made an earlier prototype offer a texture picker on BRDFVRayMtl.option_cutoff.

        socket.hide is deliberately NOT consulted: it also folds in the parent rollout's collapsed
        state, which is a node-body concern with no meaning in a property page.
    """
    if socket.is_output or not socket.vray_attr:
        return False

    if socket.bl_idname in STRUCTURAL_SOCKET_TYPES:
        return False

    if not socket.enabled:
        return False

    # Try the plugin the socket itself carries first, then the one getPluginName() resolves.
    # Neither alone is enough: VRayNodeMetaImageTexture owns two plugins (BitmapBuffer and
    # TexBitmap) and needs getPluginName's per-socket resolution, while VRayNodeEnvironment holds
    # SettingsEnvironment sockets on a node whose own vray_plugin is 'NONE', so getPluginName
    # falls back to 'NONE' and loses the real plugin.
    for pluginName in (socket.vray_plugin, socket.getPluginName()):
        if pluginName and isTextureAttr(pluginName, socket.vray_attr):
            return True

    return False


############################################################
# Slot addressing
############################################################

# Owner kinds a property page can edit textures for. Materials, worlds and lights own an embedded
# tree; a node group is a standalone tree in bpy.data.node_groups, reached when the user has a
# group open in the Shader Editor and the panel follows them into it.
_OWNER_COLLECTIONS = {
    'MATERIAL':   lambda: bpy.data.materials,
    'WORLD':      lambda: bpy.data.worlds,
    'LIGHT':      lambda: bpy.data.lights,
    'NODE_GROUP': lambda: bpy.data.node_groups,
}


@dataclass(frozen=True)
class SlotRef:
    """ A resolved texture slot: the tree to mutate, the node, the socket, and the datablock that
        owns them (None for a standalone node-group tree). """
    ntree: bpy.types.NodeTree
    node: bpy.types.Node
    socket: bpy.types.NodeSocket
    owner: bpy.types.ID
    ownerType: str


def slotOwnerFor(ownerId, ntree) -> tuple[str, str] | None:
    """ The (ownerType, ownerName) pair that addresses `ntree` for an operator, or None when the
        tree must not be edited from a property page.

        Addressed by name, never by pointer: undo re-reads the datablocks at new addresses, so
        anything derived from as_pointer() is stale by the time the operator runs - the same reason
        draw_utils.panelStateId exists.

        `ntree` is not always ownerId.node_tree: with a node group open in the Shader Editor the
        panel edits the group's tree, and an operator that addressed the owner's root tree instead
        would mutate the wrong graph.
    """
    if ntree is None:
        return None

    # Library-linked data is read-only; nodes.new() on it raises.
    if ntree.library or (ownerId is not None and ownerId.library):
        return None

    if ownerId is not None and getattr(ownerId, 'node_tree', None) == ntree:
        if isinstance(ownerId, bpy.types.Material):
            return ('MATERIAL', ownerId.name)
        if isinstance(ownerId, bpy.types.World):
            return ('WORLD', ownerId.name)
        if isinstance(ownerId, bpy.types.Light):
            return ('LIGHT', ownerId.name)

    # Identity, not name membership: embedded material/world trees are all called
    # "Shader Nodetree", so a name test would address one of them as a same-named node group and
    # mutate the wrong graph.
    if bpy.data.node_groups.get(ntree.name) is ntree:
        return ('NODE_GROUP', ntree.name)

    return None


def resolveSlot(ownerType: str, ownerName: str, nodeName: str, attrName: str) -> SlotRef | None:
    """ Resolve an operator's address back to a live (tree, node, socket).

        Returns None when any link in the chain is gone - the datablock was renamed or deleted, the
        node was removed, or the plugin description changed so the socket no longer exists. Callers
        should return {'CANCELLED'} rather than reporting an error: the row that spawned the click
        is already off the screen.
    """
    if not (getCollection := _OWNER_COLLECTIONS.get(ownerType)):
        return None

    if not (owner := getCollection().get(ownerName)):
        return None

    ntree = owner if ownerType == 'NODE_GROUP' else getattr(owner, 'node_tree', None)
    if ntree is None:
        return None

    if not (node := ntree.nodes.get(nodeName)):
        return None

    if not (socket := getInputSocketByAttr(node, attrName)):
        return None

    return SlotRef(ntree=ntree, node=node, socket=socket,
                   owner=None if ownerType == 'NODE_GROUP' else owner, ownerType=ownerType)


def resolveOwnerTree(ownerType: str, ownerName: str) -> bpy.types.NodeTree | None:
    """ The node tree an (ownerType, ownerName) pair addresses, or None when it is gone. """
    if not (getCollection := _OWNER_COLLECTIONS.get(ownerType)):
        return None

    if not (owner := getCollection().get(ownerName)):
        return None

    return owner if ownerType == 'NODE_GROUP' else getattr(owner, 'node_tree', None)


def resolveSlotNode(ownerType: str, ownerName: str, nodeName: str):
    """ Resolve just (tree, node) from the same address, for navigation, which targets a node and
        has no socket of its own. Returns None when either is gone.
    """
    if not (getCollection := _OWNER_COLLECTIONS.get(ownerType)):
        return None

    if not (owner := getCollection().get(ownerName)):
        return None

    ntree = owner if ownerType == 'NODE_GROUP' else getattr(owner, 'node_tree', None)
    if ntree is None:
        return None

    node = ntree.nodes.get(nodeName)
    return (ntree, node) if node else None


############################################################
# Reading a slot
############################################################

def getSlotSourceNode(socket) -> bpy.types.Node | None:
    """ The node driving `socket`, spanning reroutes, groups and muted nodes.

        Deliberately bypasses socket.getFarLink(): VRaySocketUse and VRaySocketColorUse override it
        to return None while their 'use' toggle is off, so a slot that IS linked would read as empty
        and the picker would happily stack a second texture on top of the first.
    """
    if not socket.is_linked:
        return None
    link = getFarNodeLinkImpl(socket)
    return link.from_node if link else None


def isSlotUseEnabled(socket) -> bool:
    """ False when the socket has a 'use'-style toggle that is currently off - the link exists but
        is ignored on export. The row greys out instead of hiding, so a link doing nothing is still
        visible and clearable. """
    return bool(getattr(socket, 'use', True))


def nodeDisplayName(node) -> str:
    """ What to show for a node in a UI row.

        Never node.name: nodes/utils.createNode names nodes '<Type>_<UUID>', so every node made by
        the .vrscene importer, the Cosmos importer or any programmatic path has a raw UUID for a
        name, which an earlier prototype showed verbatim on the slot button.
    """
    if node is None:
        return ""

    if node.label:
        return node.label

    # draw_label() is the node's own answer and is already specific where it matters - the image
    # texture returns the image name - so only fall back to naming the file ourselves when the node
    # gave us its generic type name. Otherwise a bitmap reads "wood_diff (wood_diff.png)".
    try:
        if drawn := node.draw_label():
            return drawn
    except Exception:
        pass

    # A tree can hold a dozen nodes all called "V-Ray Bitmap", so name the image they carry.
    image = getattr(getattr(node, 'texture', None), 'image', None)
    if image is not None and image.filepath:
        return f"{node.bl_label} ({os.path.basename(image.filepath)})"

    return node.bl_label


############################################################
# Mutating a slot
############################################################

# Vertical step between sibling nodes stacked into the same consumer. Approximate on purpose:
# node.dimensions is only populated once a node editor has drawn the node, which is exactly what
# has not happened when the graph is built from a property page.
_NODE_STACK_STEP = 220.0


def firstOutput(node, name: str):
    return node.outputs.get(name) or (node.outputs[0] if node.outputs else None)


def tryLink(ntree, fromSocket, toSocket) -> bool:
    """ Link two sockets, reporting whether it worked rather than raising. """
    if fromSocket is None or toSocket is None:
        return False
    try:
        ntree.links.new(fromSocket, toSocket)
    except RuntimeError:
        return False
    return True


def loadImage(filepath: str, makeRelative: bool):
    """ Load an image, re-using an existing datablock for the same file.

        Deduplication is left to check_existing, which matches on the resolved path. Looking the
        datablock up by display name instead would bind the wrong file whenever two textures share
        a basename (projA/wood.png vs projB/wood.exr), and renaming whatever came back would rename
        a datablock the rest of the scene is already using.

        Raises RuntimeError if Blender cannot read the file; callers must load before mutating the
        graph, or a failure leaves the slot stripped of its old texture.
    """
    existing = set(bpy.data.images)
    img = bpy.data.images.load(filepath, check_existing=True)

    if img not in existing:
        img.name = bpy.path.display_name_from_filepath(filepath)

    if makeRelative and bpy.data.filepath:
        try:
            img.filepath = bpy.path.relpath(img.filepath)
        except ValueError:
            pass

    return img


def pickOutputSocket(node, targetSock):
    """ Choose which output of a freshly created node should drive `targetSock`, or None when none
        of them may legally connect.

        The first enabled output is the node's primary result by construction:
        nodes/utils._addDefaultOutputForPluginCategory adds it first ("Color" for TEXTURE, "Float"
        for FLOAT_TEXTURE), and plugins with hand-written outputs list theirs first too
        (TexFloatOp.Result, whose 27 other outputs are all disabled).

        Matching the target's socket type instead would be actively wrong: TexChecker has no float
        output at all, so a float slot would get 'Out Alpha' rather than the Color the user expects
        V-Ray to convert. Type compatibility is left to isConnectionAllowed, which permits V-Ray's
        cross-type connections and rejects only the strict same-type sockets.
    """
    from vray_blender.nodes.links import isConnectionAllowed

    candidates = [o for o in node.outputs if o.enabled and not o.hide] or list(node.outputs)

    return next((o for o in candidates if isConnectionAllowed(o, targetSock)), None)


def placeUpstream(ntree, targetNode, newNode):
    """ Put `newNode` one column left of its consumer, below whatever already feeds it.

        Deliberately does not call rearrangeTree: a click in the Properties editor must not
        reshuffle a graph the user hand-arranged and cannot even see.
    """
    # Compared with !=, never 'is not': newNode is already linked by the time this runs and
    # link.from_node is a freshly built RNA wrapper around it, so an identity test never matches -
    # the node counts itself and every new texture lands one stack step too low.
    siblings = [link.from_node for sock in targetNode.inputs for link in sock.links
                if link.from_node != newNode]

    newNode.location = (targetNode.location.x - NODE_LEVEL_WIDTH,
                        targetNode.location.y - _NODE_STACK_STEP * len(siblings))


def clearSlot(slot: SlotRef) -> bool:
    """ Unlink the slot and delete the node that was feeding it.

        Returns False when nothing was linked, so the operator can return {'CANCELLED'} instead of
        pushing an empty undo step (VBLD-2686).

        Only the directly connected node is removed. Its own upstream chain is left in the tree -
        'Delete Unused Nodes' in the Node Editor is the cleanup path for that.
    """
    if not slot.socket.is_linked:
        return False

    sources = {link.from_node.name: link.from_node for link in slot.socket.links}

    for link in list(slot.socket.links):
        slot.ntree.links.remove(link)

    for node in sources.values():
        if not any(out.is_linked for out in node.outputs):
            slot.ntree.nodes.remove(node)

    return True


def assignTexture(slot: SlotRef, nodeType: str) -> bpy.types.Node | None:
    """ Create `nodeType` and wire it into the slot, replacing whatever was there.

        Returns the new node, or None when the connection is not legal. The order matters: the new
        node is built and validated BEFORE the old one is removed, so an illegal or failed
        assignment leaves the existing texture intact. An earlier prototype disconnected first and
        raised second, destroying the user's texture on the way.
    """
    ntree = slot.ntree

    # nodes.new(), not createNode(): we want Blender's readable default name here rather than
    # createNode's '<Type>_<UUID>'. DisableAutoConnect stops vrayNodeInit -> autoConnectNode from
    # wiring the new node somewhere we did not ask for.
    with NodesUtils.DisableAutoConnect():
        newNode = ntree.nodes.new(nodeType)

    if (outSocket := pickOutputSocket(newNode, slot.socket)) is None:
        ntree.nodes.remove(newNode)
        return None

    clearSlot(slot)

    if not tryLink(ntree, outSocket, slot.socket):
        # The old texture is already gone at this point, so leaving the new node unlinked would be
        # a silent data loss reported as success. Remove it and let the caller report the failure.
        ntree.nodes.remove(newNode)
        return None

    # ntree.links.new() does not run the insert_link machinery, so the socket never gets told it
    # was connected. Sockets that gate their link behind a 'use' toggle (VRaySocketColorUse,
    # VRaySocketEnvironmentOverride) would stay switched off and the texture would be linked but
    # ignored on export. lib/mixin.py calls this same hook on the interactive path.
    if onLinkConnected := getattr(slot.socket, 'onLinkConnected', None):
        onLinkConnected()

    placeUpstream(ntree, slot.node, newNode)
    setPanelNode(ntree, newNode)

    return newNode


def retagSlotOwner(context, slot: SlotRef):
    """ Tag the graph edit for re-export and repaint the editors showing it. """
    if slot.ownerType == 'MATERIAL':
        UpdateTracker.tagMtlTopology(context, slot.owner)
        NodesUtils.tagMaterialPreview(slot.owner)
    elif slot.ownerType in ('WORLD', 'LIGHT'):
        slot.owner.update_tag()
    elif slot.ownerType == 'NODE_GROUP':
        # A group tree can be shared, so the update has to reach every material/world using it.
        NodesUtils.tagGroupTreeUsers(slot.ntree)

    slot.ntree.update_tag()
    NodesUtils.tagRedrawShadingEditors()


def clearCaches():
    """ Drop the memoized predicate results. Called on addon unregister; the plugin descriptions
        are immutable within a session, so nothing else needs to invalidate this. """
    _textureAttrCache.clear()
