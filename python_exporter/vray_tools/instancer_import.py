# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

""" Import V-Ray instancers (GeomInstancer / Instancer2 / Instancer) as a point cloud driven
    by a geometry-nodes "Instance on Points" setup, mirroring Blender's own USD point-instancer
    import.

    The per-instance points are carried by a PointCloud, matching the USD importer. Blender
    exposes no way to size one from Python before 5.1 (PointCloud.resize()); there the points
    are authored on a vertices-only Mesh and converted to a PointCloud once the attributes
    are in place.

    All three plugins are normalized into a common ParsedInstancer (per-instance position /
    rotation / scale / prototype index, plus optional ids and custom point attributes) and
    materialized by a single builder. Prototypes are the instancer's source Node objects,
    moved into a hidden collection tree prototypes/<instancer>/proto_NN and picked per point
    by a shared node group.

    Instancer2 and the legacy Instancer share one 'instances' row layout and one parser; they
    differ only in the default of use_time_instancing (1 and 0). The legacy plugin is what
    3ds Max writes for every instancing source it has - particle flows, Forest Pack, RailClone.
"""

from dataclasses import dataclass, field

import bpy
import mathutils
import numpy as np

from vray_blender import debug
from vray_blender.lib import attribute_utils, transform_utils
from vray_blender.nodes.tools import rearrangeTree
from vray_blender.vray_tools.import_common import refName as _refName


# Name of the shared geometry-nodes group and the per-point attributes it reads.
_NODE_GROUP_NAME = "VRayInstances"
_ATTR_PROTO_INDEX = "proto_index"
_ATTR_MASK = "mask"
_ATTR_SCALE = "scale"
_ATTR_ORIENTATION = "orientation"

# V-Ray user-attribute / BinUserAttribute type -> VRayUserAttributeItem.value_type enum.
# (Same mapping the server uses when it emits decoded user attributes.)


@dataclass
class ParsedInstancer:
    """ Common representation of a GeomInstancer / Instancer2 / Instancer plugin. """
    name: str
    prototypes: list                          # list[tuple[str, ...]] source Node names per proto
    protoIndex: np.ndarray                     # (N,) int32
    translation: np.ndarray                    # (N, 3) float32, source units * vertexScale
    quaternion: np.ndarray                     # (N, 4) float32, (w, x, y, z)
    scale: np.ndarray                          # (N, 3) float32
    mask: np.ndarray                           # (N,) bool - False hides the instance
    ids: np.ndarray = None                     # (N,) int32 or None
    velocity: np.ndarray = None                # (N, 3) float32 or None
    customAttrs: dict = field(default_factory=dict)   # name -> (N,) or (N, k) numpy array
    useSourceTransform: int = 1                # 0 ignore source Node transform, 1/2 apply it
    baseLocal: mathutils.Matrix = None         # extra source-space transform for all instances

    @property
    def count(self) -> int:
        return int(self.protoIndex.shape[0]) if self.protoIndex is not None else 0


# --------------------------------------------------------------------------
# Public entry points
# --------------------------------------------------------------------------

def parseInstancer(pluginDesc: dict, importer) -> ParsedInstancer | None:
    """ Build a ParsedInstancer from a GeomInstancer / Instancer2 / Instancer descriptor. """
    pluginType = pluginDesc['ID']
    if pluginType == 'GeomInstancer':
        return _parseGeomInstancer(pluginDesc, importer)
    if pluginType in ('Instancer2', 'Instancer'):
        return _parseInstancerRows(pluginDesc, importer)
    return None


def buildInstancerObject(importer, parsed: ParsedInstancer, ownerTm) -> bpy.types.Object | None:
    """ Materialize a ParsedInstancer: a points carrier holding the per-instance attributes, a
        geometry-nodes modifier that instances the prototype collection onto the points, and
        the hidden prototype collection tree. ownerTm is the wrapping Node's transform (the
        instancer node), applied to the whole points object. """
    n = parsed.count
    if n == 0:
        return None

    protoCollection = _buildPrototypeCollections(importer, parsed)
    if protoCollection is None:
        importer.stats.skipped[f"{parsed.name} (no prototypes resolved)"] += 1
        return None

    points = _createPointsCarrier(parsed.name, parsed.translation, n)

    _writePointAttr(points, _ATTR_SCALE, 'FLOAT_VECTOR', parsed.scale)
    _writePointAttr(points, _ATTR_ORIENTATION, 'QUATERNION', parsed.quaternion)
    _writePointAttr(points, _ATTR_PROTO_INDEX, 'INT', parsed.protoIndex)
    _writePointAttr(points, _ATTR_MASK, 'BOOLEAN', parsed.mask)
    if parsed.ids is not None:
        _writePointAttr(points, 'id', 'INT', parsed.ids)
    if parsed.velocity is not None:
        _writePointAttr(points, 'velocity', 'FLOAT_VECTOR', parsed.velocity)
    for attrName, values in parsed.customAttrs.items():
        _writeCustomPointAttr(points, attrName, values)

    obj = bpy.data.objects.new(parsed.name, points)
    importer.collection.objects.link(obj)
    # Must happen before the modifier is added: object.convert applies modifiers, which would
    # bake the instances into the points instead of converting the carrier.
    _convertMeshCarrierToPointCloud(obj)
    # Track the final data, and the object after it, so rollback removes the object first.
    importer.ledger.track(obj.data)
    importer.ledger.track(obj)

    # coordAdjust and the instancer node's own transform live on the points object; the
    # per-point transforms carry only the (unit-scaled) instance transform, and each prototype
    # object keeps its source-space transform (see _buildPrototypeCollections). Instance on
    # Points then composes them: coordAdjust @ owner @ base @ instance @ sourceNode.
    matrix = importer.worldMatrix(ownerTm)
    if parsed.baseLocal is not None:
        matrix = matrix @ parsed.baseLocal
    obj.matrix_world = matrix

    mod = obj.modifiers.new(_NODE_GROUP_NAME, 'NODES')
    mod.node_group = _getOrCreateNodeGroup(importer)
    collSocketId = _collectionInputIdentifier(mod.node_group)
    if collSocketId is not None:
        _setModifierInput(mod, collSocketId, protoCollection)

    return obj


def _createPointsCarrier(name: str, translation: np.ndarray, n: int):
    """ The datablock holding the per-instance points, sized to n.

        A PointCloud where Python can size one (Blender 5.1+, PointCloud.resize()), otherwise a
        vertices-only Mesh that _convertMeshCarrierToPointCloud turns into one. Both accept the
        same POINT-domain attributes, so the attribute writers do not care which it is.
    """
    positions = np.ascontiguousarray(translation, dtype=np.float32).ravel()

    if "resize" in bpy.types.PointCloud.bl_rna.functions:
        pointCloud = bpy.data.pointclouds.new(name)
        pointCloud.resize(n)
        # 'position' is the point cloud's built-in point-domain float3 attribute.
        pointCloud.attributes['position'].data.foreach_set('vector', positions)
        return pointCloud

    mesh = bpy.data.meshes.new(name)
    mesh.vertices.add(n)
    mesh.vertices.foreach_set('co', positions)
    mesh.update()
    return mesh


def _convertMeshCarrierToPointCloud(obj: bpy.types.Object):
    """ Turn a Mesh carrier into a PointCloud in place; no-op when it already is one.

        Only reached before Blender 5.1, where a PointCloud cannot be sized from Python.
        object.convert carries the point attributes across, but it acts on the selection and
        on the active object, so the context is overridden to just this object - otherwise
        whatever the user had selected would be converted too. The emptied Mesh is left behind
        by the operator and is removed here rather than leaking a datablock per instancer.
    """
    if not isinstance(obj.data, bpy.types.Mesh):
        return

    mesh = obj.data
    with bpy.context.temp_override(active_object=obj, selected_editable_objects=[obj]):
        bpy.ops.object.convert(target='POINTCLOUD')

    if mesh.users == 0:
        bpy.data.meshes.remove(mesh)


# --------------------------------------------------------------------------
# GeomInstancer parsing (structure-of-arrays)
# --------------------------------------------------------------------------

def _parseGeomInstancer(pluginDesc: dict, importer) -> ParsedInstancer | None:
    attrs = pluginDesc['Attributes']

    tms12 = _transformsToArray(attrs.get('transforms'))
    if tms12 is None:
        tms12 = _transformsVectorToArray(attrs.get('transforms_vector'))
    if tms12 is None or len(tms12) == 0:
        return None
    n = tms12.shape[0]

    translation, quaternion, scale = _decomposeTransforms(tms12, importer.vertexScale)

    prototypes, protoIndex, mask = _parseSources(attrs.get('sources'), n)

    ids = None
    rawIds = attrs.get('instance_ids')
    if rawIds is not None and len(rawIds) == n:
        ids = np.ascontiguousarray(rawIds, dtype=np.int32)

    velocity = _vectorListToArray(attrs.get('velocities'), n)

    customAttrs = {}
    _parseGeomInstancerUserAttributes(attrs.get('user_attributes'), n, customAttrs)

    baseLocal = None
    if (baseTm := attrs.get('base_transform')) is not None:
        baseLocal = _localMatrix(importer, baseTm)

    return ParsedInstancer(
        name=pluginDesc['Name'], prototypes=prototypes, protoIndex=protoIndex,
        translation=translation, quaternion=quaternion, scale=scale, mask=mask,
        ids=ids, velocity=velocity, customAttrs=customAttrs,
        useSourceTransform=int(attrs.get('use_source_transform', 1)), baseLocal=baseLocal,
    )


def _parseSources(sources, n: int):
    """ Parse a GeomInstancer 'sources' LIST_LIST into (prototypes, protoIndex, mask).

        Forms: [node] / flat [node0, ...] (one per instance, or a single broadcast value),
        or [[node0..nodeK], [idx0..idx(N-1)]] with idx == -1 meaning "unset" (masked off).
        Prototypes are deduped single-node tuples in first-seen order.
    """
    if not sources:
        return [], np.zeros(n, dtype=np.int32), np.zeros(n, dtype=bool)

    if (len(sources) == 2
            and _isSequence(sources[0]) and _isSequence(sources[1])
            and not _isSequence(sources[0][0] if len(sources[0]) else None)):
        # Two-list form: palette + per-instance index.
        palette = [_refName(s) for s in sources[0]]
        idx = np.asarray(sources[1], dtype=np.int32)
    else:
        flat = [_refName(s) for s in sources]
        if len(flat) <= 1:
            palette = flat
            idx = np.zeros(n, dtype=np.int32)
        else:
            palette = flat
            idx = np.arange(n, dtype=np.int32)

    if idx.shape[0] < n:                       # be defensive about count mismatches
        idx = np.concatenate([idx, np.full(n - idx.shape[0], -1, dtype=np.int32)])
    idx = idx[:n]

    mask = (idx >= 0) & (idx < len(palette))
    memberTuples = [((palette[idx[i]],) if mask[i] else None) for i in range(n)]
    prototypes, protoIndex = _dedupePrototypes(memberTuples)
    return prototypes, protoIndex, mask


def _parseGeomInstancerUserAttributes(userAttributes, n: int, out: dict):
    """ Numeric GeomInstancer 'user_attributes' sub-lists ([name, per-instance values]) become
        point attributes. String-valued and index forms are skipped. """
    if not userAttributes:
        return
    for sub in userAttributes:
        if not _isSequence(sub) or len(sub) < 2 or not isinstance(sub[0], str):
            continue
        name, values = sub[0], sub[1]
        if not _isSequence(values) or len(values) != n:
            continue
        try:
            arr = np.asarray(values)
        except Exception:
            continue
        if arr.dtype.kind in ('i', 'u', 'b'):
            out[name] = arr.astype(np.int32)
        elif arr.dtype.kind == 'f':
            out[name] = arr.astype(np.float32)


# --------------------------------------------------------------------------
# Instancer2 / Instancer parsing (array-of-structs)
# --------------------------------------------------------------------------

# HierarchicalParameterizedNodeParameters::useUserAttributes - the only additional parameter
# read here. The rest still have to be counted, which is what makes the trailing node
# references findable at all.
_FLAG_USER_ATTRIBUTES = 1 << 3
# The bits V-Ray reads BEFORE the user-attribute string (useObjectID, usePrimaryVisibility),
# i.e. how far past the flags int the string sits. Read order per the Instancer plugin itself
# (vraysl/vray_geom_instancer/vray_geom_instancer.cpp).
_FLAGS_BEFORE_USER_ATTRIBUTES = (1 << 1) | (1 << 2)


def _parseInstancerRows(pluginDesc: dict, importer) -> ParsedInstancer | None:
    """ Instancer2 and the legacy Instancer: 'instances' is N+1 elements, a leading frame time
        then one row per instance:

            id, transform, velocity transform,
            [hasInstanceTime, instanceTime],          when use_time_instancing
            [flags, additional_param_1, ...],         when use_additional_params
            [visibility],                             when use_visibility
            node, node, ...

        Every optional block is positional and its presence is declared by the plugin's own
        attributes, so the node references are located by walking forward past the blocks the
        plugin says are there - not by guessing from the tail. Each bit set in the flags int
        adds exactly one additional parameter, and several of those (material, geometry, map
        channels, geometry trimming) are plugin references indistinguishable from a node one.
    """
    pluginType = pluginDesc['ID']
    attrs = pluginDesc['Attributes']
    instances = attrs.get('instances')
    if not _isSequence(instances) or len(instances) < 2:
        return None

    # A default-valued attribute never reaches the importer, so the default has to come from the
    # plugin type: Instancer2 defaults use_time_instancing to 1, the legacy Instancer to 0.
    useTime = int(attrs.get('use_time_instancing', 1 if pluginType == 'Instancer2' else 0))
    useAdditional = bool(attrs.get('use_additional_params', False))
    useVisibility = bool(attrs.get('use_visibility', False))

    records = list(instances[1:])              # element 0 is the frame time
    n = len(records)

    if _isSequence(instances[0]):
        debug.printWarning(f"{pluginType} '{pluginDesc['Name']}': 'instances' does not start with a time "
                           f"value; its first instance row was read as one and is not imported")

    tms12 = np.zeros((n, 12), dtype=np.float32)
    ids = np.zeros(n, dtype=np.int32)
    mask = np.ones(n, dtype=bool)
    memberTuples = [None] * n
    userAttrStrings = [None] * n

    malformed = 0
    firstOptional = 5 if useTime else 3

    for i, rec in enumerate(records):
        if not _isSequence(rec) or len(rec) <= firstOptional:
            mask[i] = False
            malformed += 1
            continue
        ids[i] = int(rec[0]) if isinstance(rec[0], (int, float)) else 0
        tms12[i] = _transformTupleTo12(rec[1])

        offs = firstOptional
        if useAdditional:
            flags = int(rec[offs]) if isinstance(rec[offs], (int, float)) else 0
            if flags & _FLAG_USER_ATTRIBUTES:
                index = offs + 1 + _popcount(flags & _FLAGS_BEFORE_USER_ATTRIBUTES)
                if index < len(rec) and isinstance(rec[index], str):
                    userAttrStrings[i] = rec[index]
            offs += _popcount(flags) + 1       # +1 for the flags int itself
        if useVisibility:
            visible = rec[offs] if offs < len(rec) else 1
            if isinstance(visible, (int, float)) and not visible:
                mask[i] = False
            offs += 1

        nodeRefs = _nodeRefs(rec, offs)
        if nodeRefs:
            memberTuples[i] = tuple(nodeRefs)
        else:
            mask[i] = False
            malformed += 1

    if malformed:
        debug.printWarning(f"{pluginType} '{pluginDesc['Name']}': {malformed} of {n} instance rows carry "
                           f"no node reference and were not imported")

    translation, quaternion, scale = _decomposeTransforms(tms12, importer.vertexScale)
    prototypes, protoIndex = _dedupePrototypes(memberTuples)

    customAttrs = {}
    _parseParticleChannels(attrs, n, customAttrs)
    _foldInstanceUserAttributes(attrs.get('instance_user_attributes'), n, customAttrs)
    _foldStringUserAttributes(userAttrStrings, n, customAttrs)

    return ParsedInstancer(
        name=pluginDesc['Name'], prototypes=prototypes, protoIndex=protoIndex,
        translation=translation, quaternion=quaternion, scale=scale, mask=mask,
        ids=ids, velocity=None, customAttrs=customAttrs, useSourceTransform=1, baseLocal=None,
    )


# Per-particle channels of Instancer2 / Instancer -> point attributes.
_PARTICLE_COLOR_CHANNELS = ('colors', 'emission_pp',
                            'user_color_pp_1', 'user_color_pp_2', 'user_color_pp_3',
                            'user_color_pp_4', 'user_color_pp_5')
_PARTICLE_FLOAT_CHANNELS = ('age_pp', 'lifespan_pp', 'opacity_pp',
                            'user_float_pp_1', 'user_float_pp_2', 'user_float_pp_3',
                            'user_float_pp_4', 'user_float_pp_5')
_PARTICLE_VECTOR_CHANNELS = ('acceleration_pp',)


def _parseParticleChannels(attrs: dict, n: int, out: dict):
    for name in _PARTICLE_FLOAT_CHANNELS:
        if _channelLen(attrs.get(name)) == n:
            out[name] = np.asarray(attrs[name], dtype=np.float32)
    for name in _PARTICLE_VECTOR_CHANNELS:
        arr = _vectorListToArray(attrs.get(name), n)
        if arr is not None:
            out[name] = arr
    for name in _PARTICLE_COLOR_CHANNELS:
        arr = _vectorListToArray(attrs.get(name), n)
        if arr is not None:
            out[name] = arr


def _foldInstanceUserAttributes(instanceUserAttrs, n: int, out: dict):
    """ Fold the server-decoded per-instance binary user attributes into point attributes.
        Format: [[name, typeTag, [per-instance values...]], ...]. String user attrs (typeTag
        3) cannot be point attributes and are skipped. """
    if not instanceUserAttrs:
        return
    for entry in instanceUserAttrs:
        if not _isSequence(entry) or len(entry) < 3 or not isinstance(entry[0], str):
            continue
        name, typeTag, values = entry[0], int(entry[1]), entry[2]
        if typeTag == 3 or not _isSequence(values) or len(values) != n:
            continue
        if typeTag == 2:                        # color
            out[name] = np.asarray(values, dtype=np.float32).reshape(n, -1)
        elif typeTag == 0:                      # int
            out[name] = np.asarray(values, dtype=np.int32)
        else:                                   # float
            out[name] = np.asarray(values, dtype=np.float32)


def _foldStringUserAttributes(perInstance: list, n: int, out: dict):
    """ Fold the per-instance "name=value;..." additional parameter into point attributes. This
        is how the legacy Instancer carries per-instance data - 3ds Max writes Forest Pack and
        RailClone randomization through it - and it is the only user-attribute form that lives
        in the row itself rather than in a binary blob the server decodes.

        String values are skipped: a point attribute cannot hold one. A name typed as a colour
        on some instances and as a scalar on others has no single point type and is skipped too.
    """
    from vray_blender.vray_tools.scene_import import _parseUserAttributeString

    collected = {}                             # name -> {instance index: (valueType, value)}
    for i, text in enumerate(perInstance):
        if not text:
            continue
        for name, valueType, value in _parseUserAttributeString(text):
            if valueType != '3':
                collected.setdefault(name, {})[i] = (valueType, value)

    for name, byIndex in collected.items():
        valueTypes = {valueType for valueType, _ in byIndex.values()}
        if valueTypes == {'2'}:
            values = np.zeros((n, 3), dtype=np.float32)
        elif '2' in valueTypes:
            continue
        elif valueTypes == {'0'}:
            values = np.zeros(n, dtype=np.int32)
        else:
            values = np.zeros(n, dtype=np.float32)
        for i, (_, value) in byIndex.items():
            values[i] = value
        out[name] = values


def _nodeRefs(rec: list, start: int) -> list:
    """ The node references at the tail of a record, from the first slot after the optional
        blocks. V-Ray stops at the first NULL reference, which arrives as an empty string. """
    refs = []
    for value in rec[start:]:
        if not isinstance(value, str) or not value:
            break
        refs.append(_refName(value))
    return refs


# --------------------------------------------------------------------------
# Prototype collections
# --------------------------------------------------------------------------

def _buildPrototypeCollections(importer, parsed: ParsedInstancer) -> bpy.types.Collection | None:
    """ Create prototypes/<instancer>/proto_NN and move each source Node object into its
        proto collection. Returns the <instancer> parent collection (the Collection Info
        input) or None if nothing resolved. """
    resolved = []                              # (protoCollName, [objects])
    digits = max(2, len(str(max(len(parsed.prototypes) - 1, 0))))
    anyResolved = False
    for index, protoKey in enumerate(parsed.prototypes):
        objects = []
        for nodeName in protoKey:
            obj = importer.objectByNodePlugin.get(nodeName)
            if obj is not None:
                objects.append(obj)
            else:
                importer.stats.skipped[f"instancer source '{nodeName}'"] += 1
        anyResolved = anyResolved or bool(objects)
        resolved.append((f"proto_{index:0{digits}d}", objects))

    if not anyResolved:
        return None

    top = _getOrCreatePrototypesRoot(importer)
    parent = bpy.data.collections.new(parsed.name)
    top.children.link(parent)
    importer.ledger.track(parent)

    coordAdjustInv = importer.coordAdjust.inverted() if importer.coordAdjust is not None else None
    moved = importer._instancerMovedSources

    for collName, objects in resolved:
        protoColl = bpy.data.collections.new(collName)
        parent.children.link(protoColl)
        importer.ledger.track(protoColl)
        for obj in objects:
            if obj.name not in protoColl.objects:
                protoColl.objects.link(obj)
            if obj.name in moved:
                continue                        # Shared source: link only, adjust matrix once.
            moved.add(obj.name)
            for coll in list(obj.users_collection):
                if coll is not protoColl:
                    coll.objects.unlink(obj)
            # Source template nodes are usually visible=0, so _importObjects hid them with the
            # per-object flags - which drop them from the depsgraph and leave Collection Info
            # with empty geometry (no instances). Clear the per-object hide and rely on the
            # prototypes collection's hide flags, which keep the objects evaluable.
            obj.hide_viewport = False
            obj.hide_render = False
            # Strip coordAdjust (it lives on the point cloud object). use_source_transform:
            # 0 = ignore the source transform, 1 = apply it, 2 = apply it to lights only.
            keepSourceTm = (parsed.useSourceTransform == 1
                            or (parsed.useSourceTransform == 2 and obj.type == 'LIGHT'))
            if not keepSourceTm:
                obj.matrix_world = mathutils.Matrix()
            elif coordAdjustInv is not None:
                obj.matrix_world = coordAdjustInv @ obj.matrix_world

    return parent


def _getOrCreatePrototypesRoot(importer) -> bpy.types.Collection:
    if importer._instancerPrototypesRoot is None:
        root = bpy.data.collections.new("prototypes")
        # Global (not view-layer) hide flags keep the objects in the depsgraph so Collection
        # Info can still read them, while hiding them from direct viewport/render.
        root.hide_viewport = True
        root.hide_render = True
        importer.collection.children.link(root)
        importer.ledger.track(root)
        importer._instancerPrototypesRoot = root
    return importer._instancerPrototypesRoot


# --------------------------------------------------------------------------
# Shared geometry-nodes group
# --------------------------------------------------------------------------

def _getOrCreateNodeGroup(importer) -> bpy.types.NodeTree:
    if importer._instancesNodeGroup is not None:
        return importer._instancesNodeGroup

    ng = bpy.data.node_groups.new(_NODE_GROUP_NAME, 'GeometryNodeTree')
    importer.ledger.track(ng)
    importer._instancesNodeGroup = ng

    ng.interface.new_socket("Geometry", in_out='INPUT', socket_type='NodeSocketGeometry')
    ng.interface.new_socket("Instance Collection", in_out='INPUT', socket_type='NodeSocketCollection')
    ng.interface.new_socket("Geometry", in_out='OUTPUT', socket_type='NodeSocketGeometry')

    nodes, links = ng.nodes, ng.links
    groupIn = nodes.new("NodeGroupInput")
    groupOut = nodes.new("NodeGroupOutput")
    instanceOnPoints = nodes.new("GeometryNodeInstanceOnPoints")
    collectionInfo = nodes.new("GeometryNodeCollectionInfo")

    instanceOnPoints.inputs["Pick Instance"].default_value = True
    collectionInfo.transform_space = 'ORIGINAL'
    collectionInfo.inputs["Separate Children"].default_value = True
    collectionInfo.inputs["Reset Children"].default_value = False

    def namedAttr(attrName, dataType):
        node = nodes.new("GeometryNodeInputNamedAttribute")
        node.data_type = dataType
        node.inputs["Name"].default_value = attrName
        return node

    naIndex = namedAttr(_ATTR_PROTO_INDEX, 'INT')
    naMask = namedAttr(_ATTR_MASK, 'BOOLEAN')
    naScale = namedAttr(_ATTR_SCALE, 'FLOAT_VECTOR')
    naRotation = namedAttr(_ATTR_ORIENTATION, 'QUATERNION')

    links.new(groupIn.outputs["Geometry"], instanceOnPoints.inputs["Points"])
    links.new(groupIn.outputs["Instance Collection"], collectionInfo.inputs["Collection"])
    links.new(collectionInfo.outputs["Instances"], instanceOnPoints.inputs["Instance"])
    links.new(naIndex.outputs["Attribute"], instanceOnPoints.inputs["Instance Index"])
    links.new(naMask.outputs["Attribute"], instanceOnPoints.inputs["Selection"])
    links.new(naScale.outputs["Attribute"], instanceOnPoints.inputs["Scale"])
    links.new(naRotation.outputs["Attribute"], instanceOnPoints.inputs["Rotation"])
    links.new(instanceOnPoints.outputs["Instances"], groupOut.inputs["Geometry"])

    # Nodes are all created at (0, 0); lay them out so the group is readable if opened.
    rearrangeTree(ng, groupOut)

    return ng


def _collectionInputIdentifier(nodeGroup) -> str | None:
    for item in nodeGroup.interface.items_tree:
        if getattr(item, 'in_out', None) == 'INPUT' and item.name == "Instance Collection":
            return item.identifier
    return None


def _setModifierInput(mod: bpy.types.NodesModifier, identifier: str, value):
    """ Set a geometry-nodes modifier input by socket identifier.

        Blender 5.2 moved the modifier's inputs from custom properties (mod[identifier])
        to real RNA under mod.properties.inputs; the old subscript form now raises.
    """
    if (props := getattr(mod, 'properties', None)) is not None:
        getattr(props.inputs, identifier).value = value
    else:
        mod[identifier] = value


# --------------------------------------------------------------------------
# Transform helpers
# --------------------------------------------------------------------------

def _decomposeTransforms(tms12: np.ndarray, vertexScale: float):
    """ Decompose (N, 12) V-Ray transforms (v0, v1, v2, offset - column-major basis) into
        Blender-space translation (N, 3), quaternion (N, 4, w x y z) and scale (N, 3).
        Only the translation is unit-scaled, mirroring SceneImporter.worldMatrix. """
    n = tms12.shape[0]
    basis = tms12[:, :9].reshape(n, 3, 3).astype(np.float64)
    offs = tms12[:, 9:12].astype(np.float64)

    # Blender rotation columns are the V-Ray basis vectors (rows of 'basis'): R = basis^T.
    rot = np.transpose(basis, (0, 2, 1))
    rotNorm, scale = transform_utils.normalizeRotationScale(rot)
    quaternion = transform_utils.matricesToQuaternions(rotNorm)
    translation = (offs * vertexScale).astype(np.float32)
    return translation, quaternion.astype(np.float32), scale.astype(np.float32)


def _transformsToArray(value) -> np.ndarray | None:
    """ Normalize a TRANSFORM_LIST to an (N, 12) float32 array. Accepts the zero-copy ndarray
        fast-path or a Python list of (matrix3x3, offset) tuples. """
    if value is None:
        return None
    if isinstance(value, np.ndarray):
        if value.ndim == 2 and value.shape[1] == 12:
            return np.ascontiguousarray(value, dtype=np.float32)
        return None
    if not _isSequence(value) or len(value) == 0:
        return None
    return np.array([_transformTupleTo12(t) for t in value], dtype=np.float32)


def _transformsVectorToArray(value) -> np.ndarray | None:
    """ Normalize a transforms_vector VECTOR_LIST (4 vectors per transform: v0, v1, v2,
        offset) to an (N, 12) float32 array. """
    if value is None:
        return None
    arr = np.asarray(value, dtype=np.float32).reshape(-1)
    if arr.size == 0 or arr.size % 12 != 0:
        return None
    return arr.reshape(-1, 12)


def _transformTupleTo12(tm) -> tuple:
    """ ((v0), (v1), (v2)), (offset) -> flat 12-tuple. """
    m, offs = tm[0], tm[1]
    return (m[0][0], m[0][1], m[0][2], m[1][0], m[1][1], m[1][2],
            m[2][0], m[2][1], m[2][2], offs[0], offs[1], offs[2])


def _localMatrix(importer, tmValue) -> mathutils.Matrix:
    """ Source-space matrix (translation unit-scaled) WITHOUT the coordinate-system adjustment
        - the coordAdjust is applied once on the point cloud object. """
    m = attribute_utils.attrValueToMatrix(tmValue, applyScale=False)
    m.translation = m.translation * importer.vertexScale
    return m


def _vectorListToArray(value, n: int) -> np.ndarray | None:
    if value is None:
        return None
    try:
        arr = np.asarray(value, dtype=np.float32).reshape(-1, 3)
    except Exception:
        return None
    return arr if arr.shape[0] == n else None


# --------------------------------------------------------------------------
# Point-domain attribute helpers
# --------------------------------------------------------------------------

def _writePointAttr(points, name: str, blType: str, values: np.ndarray):
    attr = points.attributes.new(name, blType, 'POINT')
    if blType == 'FLOAT_VECTOR':
        attr.data.foreach_set('vector', np.ascontiguousarray(values, dtype=np.float32).ravel())
    elif blType == 'FLOAT_COLOR':
        attr.data.foreach_set('color', np.ascontiguousarray(values, dtype=np.float32).ravel())
    elif blType == 'QUATERNION':
        attr.data.foreach_set('value', np.ascontiguousarray(values, dtype=np.float32).ravel())
    elif blType == 'INT':
        attr.data.foreach_set('value', np.ascontiguousarray(values, dtype=np.int32).ravel())
    elif blType == 'BOOLEAN':
        attr.data.foreach_set('value', np.ascontiguousarray(values, dtype=bool).ravel())
    else:
        attr.data.foreach_set('value', np.ascontiguousarray(values, dtype=np.float32).ravel())


def _writeCustomPointAttr(points, name: str, values: np.ndarray):
    """ Store an arbitrary per-instance channel as a point attribute, picking the Blender
        type from the array shape/dtype. """
    arr = np.asarray(values)
    if arr.ndim == 2 and arr.shape[1] in (3, 4):
        colors = np.ones((arr.shape[0], 4), dtype=np.float32)
        colors[:, :arr.shape[1]] = arr.astype(np.float32)
        _writePointAttr(points, name, 'FLOAT_COLOR', colors)
    elif arr.dtype.kind in ('i', 'u'):
        _writePointAttr(points, name, 'INT', arr)
    elif arr.dtype.kind == 'b':
        _writePointAttr(points, name, 'BOOLEAN', arr)
    else:
        _writePointAttr(points, name, 'FLOAT', arr)


# --------------------------------------------------------------------------
# Small utilities
# --------------------------------------------------------------------------

def _dedupePrototypes(memberTuples: list):
    """ Map per-instance member tuples to deduped prototypes (first-seen order). None entries
        (unset / unresolved) map to index 0 and are expected to be masked off. """
    prototypes = []
    keyToIndex = {}
    protoIndex = np.zeros(len(memberTuples), dtype=np.int32)
    for i, key in enumerate(memberTuples):
        if key is None:
            continue
        index = keyToIndex.get(key)
        if index is None:
            index = len(prototypes)
            keyToIndex[key] = index
            prototypes.append(key)
        protoIndex[i] = index
    return prototypes, protoIndex


def _popcount(value: int) -> int:
    return bin(value & 0xFFFFFFFF).count('1')


def _channelLen(value) -> int:
    return len(value) if _isSequence(value) else -1


def _isSequence(value) -> bool:
    return isinstance(value, (list, tuple, np.ndarray))
