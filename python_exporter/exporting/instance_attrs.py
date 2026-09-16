# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

""" Per-instance (geometry-nodes instance domain) attribute resolution.

    Blender does not expose DupliObject.instance_data/instance_idx to Python, so this
    module re-implements the dupli-list traversal of blenkernel/intern/object_dupli.cc
    on top of the GeometrySet Python API (Object.evaluated_geometry(),
    GeometrySet.instances_pointcloud(), GeometrySet.instance_references()).

    For a root instancer object it produces a map from DepsgraphObjectInstance.persistent_id
    to the chain of (instances geometry, index) pairs recorded innermost to outermost,
    exactly like DupliObject.instance_data/instance_idx. Attribute lookup then mirrors
    find_geonode_attribute_rgba(): the innermost level that carries the attribute wins.
"""

import bpy
import numpy as np

from vray_blender import debug

_INT_MAX = 2**31 - 1

# Matches DupliObject.persistent_id length (MAX_DUPLI_RECUR).
_MAX_DUPLI_RECUR = 8

# Matches ARRAY_SIZE(DupliObject.instance_data): at most 4 geometry levels are
# recorded per instance for attribute lookup.
_MAX_ATTR_LEVELS = 4

# Object types that can carry an evaluated geometry set
_GEOMETRY_TYPES = ('MESH', 'CURVE', 'SURFACE', 'META', 'FONT', 'CURVES', 'POINTCLOUD', 'VOLUME', 'GREASEPENCIL')

# Realized geometry-set components in dupli emission order, with the object types
# whose own primary data they represent (skipped at the root level).
_COMPONENTS = (('mesh', ('MESH',)), ('volume', ('VOLUME',)),
               ('curves', ('CURVE', 'FONT', 'CURVES')),
               ('pointcloud', ('POINTCLOUD',)), ('grease_pencil', ('GREASEPENCIL',)))

# Per-instance user attribute payload kinds.
# Mirrors Interop::InstancerUserAttrKind in blender_lib/api/interop/types.h.
KIND_INT   = 0  # int list
KIND_FLOAT = 1  # float list
KIND_COLOR = 2  # vector list (3 floats)

# Instance-domain attributes that are never exported as user attributes.
_SKIP_ATTRS = ('position', 'instance_transform', 'id')

# Sentinel for instances whose duplicate 'id' was randomized by Blender (unmatchable)
_COLLIDED_ID = int(np.iinfo(np.int64).min)

# InstanceReference kinds, pre-classified per reference to keep the hot loop cheap
_REF_NONE, _REF_OBJECT, _REF_COLLECTION, _REF_GEOSET = range(4)

# data_type -> (foreach_get field, numpy dtype, components, KIND_*).
# Color alpha is dropped: V-Ray vector user attributes are 3 floats.
_ATTR_FORMATS = {
    'FLOAT':        ('value',  np.float32, 1, KIND_FLOAT),
    'INT':          ('value',  np.int32,   1, KIND_INT),
    'BOOLEAN':      ('value',  np.bool_,   1, KIND_INT),
    'FLOAT_COLOR':  ('color',  np.float32, 4, KIND_COLOR),
    'BYTE_COLOR':   ('color',  np.float32, 4, KIND_COLOR),
    'FLOAT_VECTOR': ('vector', np.float32, 3, KIND_COLOR),
    'FLOAT2':       ('vector', np.float32, 2, KIND_COLOR),
}


class _GeoLevel:
    """ Cached instance-domain data of one GeometrySet's Instances component.

        Mirrors what object_dupli.cc reads per level: unique ids, reference handles,
        references, plus the public instance attributes converted to numpy columns.
    """
    __slots__ = ('count', 'ids', 'refIndices', 'refs', 'refKinds', 'realized', 'attrs')

    def __init__(self, gs, wantedAttrs):
        # Realized (non-instance) components present in this geometry set, as the object
        # types they represent, in dupli emission order.
        self.realized = tuple(obTypes for propName, obTypes in _COMPONENTS
                              if getattr(gs, propName, None) is not None)

        pc = gs.instances_pointcloud()
        self.count = len(pc.points) if pc is not None else 0
        self.refs = gs.instance_references() if self.count else []
        self.attrs = {}  # name -> (KIND_*, np.ndarray (N,) or (N,3))

        if not self.count:
            self.ids = []
            self.refIndices = []
            self.refKinds = []
            return

        # ids/refIndices are plain Python lists: the traversal indexes them once per
        # instance and numpy scalar extraction is much slower than list access.
        n = self.count
        refIndices = np.zeros(n, dtype=np.int32)
        pc.attributes['.reference_index'].data.foreach_get('value', refIndices)
        self.refIndices = refIndices.tolist()

        self.refKinds = [_REF_NONE if r is None else
                         _REF_OBJECT if isinstance(r, bpy.types.Object) else
                         _REF_COLLECTION if isinstance(r, bpy.types.Collection) else
                         _REF_GEOSET
                         for r in self.refs]

        # Instances::unique_ids(): the 'id' attribute if present (first occurrence of a
        # duplicate keeps its value, later duplicates get randomized ids we cannot
        # reproduce), otherwise the positional index.
        if (idAttr := pc.attributes.get('id')) and idAttr.domain == 'POINT' and idAttr.data_type == 'INT':
            ids = np.zeros(n, dtype=np.int64)
            idAttr.data.foreach_get('value', ids)
            _, firstIdx = np.unique(ids, return_index=True)
            if len(firstIdx) != n:
                # Mark instances whose id collided: their real unique id is random.
                collided = np.ones(n, dtype=bool)
                collided[firstIdx] = False
                ids[collided] = _COLLIDED_ID
                debug.printWarning("Duplicate instance 'id' values; per-instance attributes"
                                   " of the colliding instances will use defaults")
            self.ids = ids.tolist()
        else:
            self.ids = list(range(n))

        # Blender propagates the source mesh's own attributes onto the instance domain (a
        # UV-mapped grid gives every instance a 'UVMap'), so keep only what the scene reads -
        # otherwise a plain scatter looks like it has attributes and pays for the walk below.
        for attr in pc.attributes:
            name = attr.name
            if name.startswith('.') or name in _SKIP_ATTRS or name not in wantedAttrs:
                continue
            if (fmt := _ATTR_FORMATS.get(attr.data_type)) is None:
                continue
            field, dtype, comps, kind = fmt
            data = np.zeros(n * comps, dtype=dtype)
            attr.data.foreach_get(field, data)
            if comps == 1:
                col = data.astype(np.int32) if kind == KIND_INT else data.astype(np.float32)
            else:
                data = data.reshape(n, comps).astype(np.float32)
                col = np.zeros((n, 3), dtype=np.float32)
                col[:, :min(comps, 3)] = data[:, :3] if comps >= 3 else data
            self.attrs[name] = (kind, col)


class InstanceAttrResolver:
    """ Maps persistent_id tuples of one root instancer's instances to resolved
        per-instance attribute values.
    """

    def __init__(self, instancerEval: bpy.types.Object, isViewport: bool, wantedAttrs: set):
        self._isViewport = isViewport
        self._wantedAttrs = wantedAttrs # attribute names referenced somewhere in the scene
        self._chains = {}           # pid 8-tuple -> tuple[(_GeoLevel, int), ...] innermost first
        self._levelCache = {}       # id(GeometrySet) -> _GeoLevel
        self._gsKeepAlive = []      # GeometrySet wrappers must stay alive while their id() keys the cache
        self._objGenCache = {}      # original obj pointer -> resolved generator tuple
        self._visibleCollCache = {} # (collection ptr, excluded obj ptr) -> [(baseId, obj), ...]
        # Emulated DupliContext state
        self._pidStack = []         # ints, outermost first
        self._attrStack = []        # (levelOrNone, instIdx), parallel to _pidStack
        self._objStack = [instancerEval.original.as_pointer()]  # instance_stack: self-instancing guard

        self._instancerEval = instancerEval
        self._built = False
        # Visit the unique references only: enough for hasAttributes() without paying
        # the per-instance walk for instancers that carry no attributes at all.
        self._discoverLevels(instancerEval)

    def hasAttributes(self):
        return any(level.attrs for level in self._levelCache.values())

    def resolve(self, persistentId) -> dict:
        """ Resolve the attribute values for one depsgraph instance.

            @param persistentId - DepsgraphObjectInstance.persistent_id (sequence of 8 ints)
            @return dict name -> (KIND_*, scalar or np row), innermost level wins per name
        """
        if not self._built:
            self._built = True
            self._walkObject(self._instancerEval)

        chain = self._chains.get(tuple(persistentId))
        if not chain:
            return {}
        result = {}
        for level, idx in chain:
            for name, (kind, col) in level.attrs.items():
                if name not in result:
                    result[name] = (kind, col[idx])
        return result

    # ----- Level discovery (unique references only, no per-instance work) -----

    def _discoverLevels(self, obj: bpy.types.Object, originalPtr=None):
        key = originalPtr if originalPtr is not None else obj.original.as_pointer()
        if (gen := self._objGenCache.get(key)) is None:
            gen = self._resolveGenerator(obj)
            self._objGenCache[key] = gen

        if gen[0] == 'gs':
            self._discoverGeometrySet(gen[1])
        elif gen[0] == 'coll':
            for _, cobj in self._visibleCollectionObjects(obj.instance_collection, obj):
                self._discoverInto(cobj)

    def _discoverInto(self, obj):
        originalPtr = obj.original.as_pointer()
        if originalPtr in self._objStack:
            return
        self._objStack.append(originalPtr)
        self._discoverLevels(obj, originalPtr)
        self._objStack.pop()

    def _discoverGeometrySet(self, gs):
        level = self._getLevel(gs)
        for ref, refKind in zip(level.refs, level.refKinds):
            if refKind == _REF_GEOSET:
                self._discoverGeometrySet(ref)
            elif refKind == _REF_OBJECT:
                self._discoverInto(ref)
            elif refKind == _REF_COLLECTION:
                for _, cobj in self._visibleCollectionObjects(ref, None):
                    self._discoverInto(cobj)

    # ----- Traversal (mirrors object_dupli.cc) -----

    def _isHidden(self, obj):
        # get_dupli_generator(): respect restrict flags for the evaluation mode
        return obj.hide_viewport if self._isViewport else obj.hide_render

    def _emit(self, index, geometry=None, instIdx=0):
        """ make_dupli(): record persistent_id and the innermost-first attribute chain. """
        chain = []
        if geometry is not None:
            chain.append((geometry, instIdx))
        for entry in reversed(self._attrStack):
            if len(chain) >= _MAX_ATTR_LEVELS:
                break
            if entry is not None:
                chain.append(entry)

        # Only instances whose chain can actually deliver attribute values are recorded;
        # resolve() misses yield the same result ({}) without the storage cost.
        if not any(level.attrs for level, _ in chain):
            return

        pid = [index] + self._pidStack[::-1]
        pid += [_INT_MAX] * (_MAX_DUPLI_RECUR - len(pid))
        self._chains[tuple(pid[:_MAX_DUPLI_RECUR])] = tuple(chain)

    def _push(self, index, geometry=None, instIdx=0):
        """ copy_dupli_context(): returns False when the recursion limit is reached. """
        self._pidStack.append(index)
        self._attrStack.append((geometry, instIdx) if geometry is not None else None)
        return len(self._pidStack) < _MAX_DUPLI_RECUR - 1

    def _pop(self):
        self._pidStack.pop()
        self._attrStack.pop()

    def _getLevel(self, gs) -> _GeoLevel:
        key = id(gs)
        if (level := self._levelCache.get(key)) is None:
            level = _GeoLevel(gs, self._wantedAttrs)
            self._levelCache[key] = level
            self._gsKeepAlive.append(gs)
        return level

    def _resolveGenerator(self, obj):
        """ get_dupli_generator(): pick the dupli generator for an object. The resolution
            is stack-independent, so it is cached per original object.
        """
        if self._isHidden(obj):
            return ('none',)

        # Geometry-set instances take priority over the legacy generators
        if obj.type in _GEOMETRY_TYPES:
            try:
                gs = obj.evaluated_geometry()
            except (RuntimeError, TypeError):
                gs = None
            # object_has_geometry_set_instances(): instances, or realized components
            # of a type different from the object's own
            if gs is not None:
                level = self._getLevel(gs)
                if level.count or any(obj.type not in obTypes for obTypes in level.realized):
                    return ('gs', gs)

        # Legacy generators: only collection instancing can lead to nested GN attribute
        # levels; particles/verts/faces subtrees carry no instance attributes.
        if obj.instance_type == 'COLLECTION' and obj.instance_collection is not None:
            return ('coll',)

        return ('none',)

    def _walkObject(self, obj: bpy.types.Object, originalPtr=None):
        """ Generator dispatch for one (possibly nested) object. """
        key = originalPtr if originalPtr is not None else obj.original.as_pointer()
        if (gen := self._objGenCache.get(key)) is None:
            gen = self._resolveGenerator(obj)
            self._objGenCache[key] = gen

        if gen[0] == 'gs':
            self._walkGeometrySet(gen[1], obj, geometrySetIsInstance=False)
        elif gen[0] == 'coll':
            self._walkCollectionInstancer(obj)

    def _walkGeometrySet(self, gs, ctxObj, geometrySetIsInstance):
        """ make_duplis_geometry_set_impl() """
        level = self._getLevel(gs)

        # Realized components each consume one dupli id (mesh, volume, curve, pointcloud,
        # grease pencil, in this order), except the component matching the object's own type
        # at the root level.
        componentIndex = 0
        ctxObjType = ctxObj.type
        for obTypes in level.realized:
            if ctxObjType not in obTypes or geometrySetIsInstance:
                self._emit(componentIndex)
                componentIndex += 1

        if not level.count:
            return

        # Sub-context so instance ids don't collide with the component duplis above
        pushedComponentCtx = False
        if componentIndex >= 1:
            if not self._push(componentIndex):
                self._pop()
                return
            pushedComponentCtx = True

        try:
            ids, refIndices, refs, refKinds = level.ids, level.refIndices, level.refs, level.refKinds
            for i in range(level.count):
                instId = ids[i]
                if instId == _COLLIDED_ID:
                    continue  # unmatchable duplicate-id instance
                refIdx = refIndices[i]
                refKind = refKinds[refIdx]

                if refKind == _REF_OBJECT:
                    self._emit(instId, level, i)
                    self._recurseInto(refs[refIdx], instId, level, i)
                elif refKind == _REF_GEOSET:
                    if self._push(instId, level, i):
                        self._walkGeometrySet(refs[refIdx], ctxObj, geometrySetIsInstance=True)
                    self._pop()
                elif refKind == _REF_COLLECTION:
                    if self._push(instId, level, i):
                        self._walkCollectionObjects(refs[refIdx], ctxObj)
                    self._pop()
        finally:
            if pushedComponentCtx:
                self._pop()

    def _recurseInto(self, obj, index, geometry=None, instIdx=0):
        """ make_recursive_duplis() """
        originalPtr = obj.original.as_pointer()
        if originalPtr in self._objStack:
            return  # object is trying to instance itself
        if self._push(index, geometry, instIdx):
            self._objStack.append(originalPtr)
            self._walkObject(obj, originalPtr)
            self._objStack.pop()
        self._pop()

    def _collectionBases(self, collection):
        """ BKE_collection_object_cache_get() order: own objects first, then child
            collections depth-first; duplicates keep their first position but OR in
            the enabled flags of every occurrence.
        """
        bases = []      # [obj, enabled]
        baseIndex = {}  # object pointer -> index in bases

        def fill(coll, parentRestricted):
            restricted = parentRestricted or (coll.hide_viewport if self._isViewport else coll.hide_render)
            for obj in coll.objects:
                key = obj.as_pointer()
                if (existing := baseIndex.get(key)) is None:
                    baseIndex[key] = len(bases)
                    bases.append([obj, not restricted])
                else:
                    bases[existing][1] = bases[existing][1] or not restricted
            for child in coll.children:
                fill(child, restricted)

        fill(collection, False)
        return bases

    def _visibleCollectionObjects(self, collection, excludeObj):
        """ FOREACH_COLLECTION_VISIBLE_OBJECT_RECURSIVE: (baseId, obj) pairs; baseId
            counts every base, visibility only filters emission. Cached per
            (collection, excluded object) since collections repeat across instances.
        """
        excludeKey = excludeObj.original.as_pointer() if excludeObj is not None else 0
        cacheKey = (collection.as_pointer(), excludeKey)
        if (cached := self._visibleCollCache.get(cacheKey)) is None:
            cached = []
            for baseId, (obj, enabled) in enumerate(self._collectionBases(collection)):
                if not enabled or self._isHidden(obj):
                    continue
                if excludeKey and obj.original.as_pointer() == excludeKey:
                    continue
                cached.append((baseId, obj))
            self._visibleCollCache[cacheKey] = cached
        return cached

    def _walkCollectionObjects(self, collection, ctxObj):
        """ The Collection branch of make_duplis_geometry_set_impl(): a visible-only
            counter incremented twice per object (dupli, then recursion).
        """
        objectId = 0
        for _, obj in self._visibleCollectionObjects(collection, ctxObj):
            self._emit(objectId, None)
            objectId += 1
            self._recurseInto(obj, objectId)
            objectId += 1

    def _walkCollectionInstancer(self, obj):
        """ make_duplis_collection() (Empty with instance_collection): _base_id indexes
            the full base cache and is shared by the dupli and its recursion.
        """
        for baseId, cobj in self._visibleCollectionObjects(obj.instance_collection, obj):
            self._emit(baseId, None)
            self._recurseInto(cobj, baseId)
