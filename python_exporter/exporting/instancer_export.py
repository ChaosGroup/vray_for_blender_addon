# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
import numpy as np
from collections import defaultdict

from mathutils import Matrix

from vray_blender import debug
from vray_blender.exporting import tools
from vray_blender.exporting.instance_attrs import InstanceAttrResolver, KIND_INT, KIND_COLOR
from vray_blender.exporting.mtl_export import getMtlTopologyUpdates
from vray_blender.exporting.node_export import exportNodePlugin
from vray_blender.exporting.plugin_tracker import getObjTrackId
from vray_blender.lib.blender_utils import TestBreak, NonGeometryTypes
from vray_blender.lib.defs import ExporterBase, ExporterContext, AttrPlugin
from vray_blender.lib.names import Names
from vray_blender.plugins.geometry.GeomHair import getGeomHairPluginName
from vray_blender.plugins.geometry.VRayDecal import isPluginVRayDecal

from vray_blender.lib import plugin_utils
from vray_blender.bin import VRayBlenderLib as vray


# mathutils gained the buffer protocol in Blender 5.0.
_MATRIX_HAS_BUFFER = bpy.app.version >= (5, 0, 0)

# Data holder passed to C++ for GeomInstancer export.
# Fields are separate contiguous numpy arrays for efficient bulk processing.
class InstancerData:
    def __init__(self, name, ids, tms, meshes, indices, userAttrs):
        self.name      = name
        self.count     = len(ids) if isinstance(ids, np.ndarray) else 0
        self.ids       = ids       # np.ndarray int32 (N, 8) — persistent IDs
        self.tms       = tms       # np.ndarray float32 (N, 12) — AttrTransform layout
        self.meshes    = meshes    # list[str] — unique mesh plugin names
        self.indices   = indices   # np.ndarray int32 (N,) — per-instance mesh index
        self.userAttrs = userAttrs # list[tuple[str, KIND_*, np.ndarray (N,) or (N,3)]]


# Temp collector for instance data, will be converted to InstanceData before
# passing it to C++
class Instancer:
    _INITIAL_CAPACITY = 512
    # Instances staged before converting a batch into the numpy buffers.
    _FLUSH_CHUNK = 4096

    def __init__(self, inst, name: str):
        self.obj       = inst.instance_object  # The object being instanced
        self.instancer = inst.parent           # The object whose geometry determines the position and number of instances
        self.name      = name

        cap = self._INITIAL_CAPACITY
        # All three buffers grow together depending on _count.
        self._tmsBuf     = np.empty((cap, 4, 4), dtype=np.float32)
        self._idsBuf     = np.empty((cap, 8),    dtype=np.int32)
        self._indicesBuf = np.empty((cap,),       dtype=np.int32)
        self._count      = 0

        # Rows staged flat and converted a chunk at a time; a row assignment per instance
        # goes through the sequence protocol element by element. Chunked to bound memory.
        self._tmRows       = []
        self._idRows       = []
        self._pending      = 0
        self._flushedCount = 0

        self._meshes      = []
        self._meshToIndex = {}
        self._attrCols    = {}  # name -> [KIND_*, np buffer (cap,) or (cap, 3)], zero-filled defaults


    def _flushPending(self):
        """ Convert the staged rows into the numpy buffers. """
        pending = self._pending
        if pending == 0:
            return

        start = self._flushedCount
        self._idsBuf[start:start + pending] = np.array(self._idRows, np.int32).reshape(pending, 8)
        self._idRows.clear()

        if not _MATRIX_HAS_BUFFER:
            self._tmsBuf[start:start + pending] = \
                np.array(self._tmRows, np.float32).reshape(pending, 4, 4)
            self._tmRows.clear()

        self._flushedCount = start + pending
        self._pending = 0


    def _grow(self):
        self._flushPending()

        n                = self._count
        cap              = len(self._tmsBuf) * 2
        newTms           = np.empty((cap, 4, 4), dtype=np.float32)
        newIds           = np.empty((cap, 8),    dtype=np.int32)
        newIndices       = np.empty((cap,),       dtype=np.int32)
        newTms[:n]       = self._tmsBuf[:n]
        newIds[:n]       = self._idsBuf[:n]
        newIndices[:n]   = self._indicesBuf[:n]
        self._tmsBuf     = newTms
        self._idsBuf     = newIds
        self._indicesBuf = newIndices

        for col in self._attrCols.values():
            buf = col[1]
            newBuf = np.zeros((cap,) + buf.shape[1:], dtype=buf.dtype)
            newBuf[:n] = buf[:n]
            col[1] = newBuf


    def _attrColumn(self, name, kind):
        if (col := self._attrCols.get(name)) is None:
            cap = len(self._tmsBuf)
            shape = (cap, 3) if kind == KIND_COLOR else (cap,)
            dtype = np.int32 if kind == KIND_INT else np.float32
            col = [kind, np.zeros(shape, dtype=dtype)]
            self._attrCols[name] = col
        return col


    def append(self, persistentId, tm, nodePluginName, instAttrs=None):
        if self._count >= len(self._tmsBuf):
            self._grow()

        n = self._count
        if _MATRIX_HAS_BUFFER:
            self._tmsBuf[n] = tm        # buffer protocol, a straight memcpy
        else:
            # Without it, assigning a Matrix walks 4 Vectors and 16 PyFloats.
            appendRow = self._tmRows.extend
            appendRow(tm[0])
            appendRow(tm[1])
            appendRow(tm[2])
            appendRow(tm[3])

        # No buffer protocol either, and indexing re-reads the whole 8-int array per element.
        self._idRows.extend(persistentId)

        self._pending += 1
        if self._pending >= self._FLUSH_CHUNK:
            self._flushPending()

        if nodePluginName not in self._meshToIndex:
            self._meshToIndex[nodePluginName] = len(self._meshes)
            self._meshes.append(nodePluginName)
        self._indicesBuf[n] = self._meshToIndex[nodePluginName]

        if instAttrs:
            for name, (kind, value) in instAttrs.items():
                colKind, buf = self._attrColumn(name, kind)
                if colKind == kind:
                    buf[n] = value
                elif colKind == KIND_COLOR:  # scalar value into a color column
                    buf[n] = float(value)
                else:                        # mismatched kind into a scalar column
                    buf[n] = value[0] if np.ndim(value) else value

        self._count += 1


    def toData(self):
        self._flushPending()

        count = self._count
        if count == 0:
            empty = np.empty(0, dtype=np.int32)
            return InstancerData(self.name, empty, np.empty((0, 12), dtype=np.float32), [], empty, [])

        ids = self._idsBuf[:count].copy()  # (N, 8)

        # Transpose to row-major, drop the 4th column to get AttrTransform layout:
        # [row0[0:3], row1[0:3], row2[0:3], row3[0:3]] = 12 floats per transform.
        mats = self._tmsBuf[:count]  # (N, 4, 4) -- already numpy, no conversion
        tms = np.ascontiguousarray(mats.transpose(0, 2, 1)[:, :, :3], dtype=np.float32).reshape(count, 12)

        indices = self._indicesBuf[:count].copy()  # (N,)

        userAttrs = [(name, kind, np.ascontiguousarray(buf[:count]))
                     for name, (kind, buf) in self._attrCols.items()]

        return InstancerData(self.name, ids, tms, self._meshes, indices, userAttrs)


class InstancerExporter(ExporterBase):

    def __init__(self, ctx: ExporterContext, geomExporter, lightExporter):
        super().__init__(ctx)
        self.instancers       = defaultdict(dict)
        self.exporter         = self.ctx.scene.vray.Exporter
        self.geomExporter     = geomExporter
        self.lightExporter    = lightExporter
        self.objTracker       = ctx.objTrackers['OBJ']
        self.lightTracker     = ctx.objTrackers['LIGHT']
        self.objMtlTracker    = ctx.objTrackers['OBJ_MTL']
        self.instTracker      = ctx.objTrackers['INSTANCER']
        self._attrResolvers   = {}  # instancer track id -> InstanceAttrResolver | False
        self._instancerNames  = {}  # instancer track id -> Names.instancer() result
        self._exportedDecals  = defaultdict(set)  # instancer track id -> per-instance decal plugin names


    def _resolveInstanceAttrs(self, inst: bpy.types.DepsgraphObjectInstance, parentTrackId: int):
        """ Resolve the geometry-nodes instance-domain attribute values for one instance.

            One resolver is built per root instancer object and shared by all its instances.
            Returns dict name -> (KIND_*, value) or None when the instancer carries no attributes.
        """
        if (resolver := self._attrResolvers.get(parentTrackId)) is None:
            wantedAttrs = self.referencedUserAttrNames

            instancerObj = inst.parent
            try:
                resolver = InstanceAttrResolver(instancerObj, self.interactive, wantedAttrs) \
                            if wantedAttrs else False
                if resolver and not resolver.hasAttributes():
                    resolver = False
            except Exception as exc:
                debug.printError(f"Per-instance attribute resolution failed for '{instancerObj.name}': {exc}")
                resolver = False
            self._attrResolvers[parentTrackId] = resolver

        return resolver.resolve(inst.persistent_id) if resolver else None

    def _addInstance(
        self,
        inst: bpy.types.DepsgraphObjectInstance,
        nodePluginName: str,
        instTrackIdOverride: int = None,
        nameOverride: str = None,
        tmOverride: Matrix | None = None,
        parentTrackId: int = None
    ):
        instancedObj = inst.instance_object     # The object being instanced

        if instancedObj.type not in tools.EXPORTED_OBJECT_TYPES:
            # There are some instanced types we are not interested in, like armature or the empty
            # controller objects for instanced collections
            return

        if parentTrackId is None:
            # inst.parent is the object whose vertices/faces are used for the instantiation
            parentTrackId = getObjTrackId(inst.parent)
        idInstancer = instTrackIdOverride or parentTrackId

        if nameOverride is not None:
            nameInstancer = nameOverride
        elif (nameInstancer := self._instancerNames.get(parentTrackId)) is None:
            nameInstancer = self._instancerNames[parentTrackId] = Names.instancer(inst)

        # There could be multiple instancers with the same id, but different names.
        instancersByName = self.instancers[idInstancer]
        if (instancer := instancersByName.get(nameInstancer)) is None:
            instancer = instancersByName[nameInstancer] = Instancer(inst, nameInstancer)

        tm = tmOverride if tmOverride is not None else inst.matrix_world
        instancer.append(inst.persistent_id, tm, nodePluginName,
                         self._resolveInstanceAttrs(inst, parentTrackId))


    def export(self):
        # Export a GeomInstancer plugin together with a wrapping Node so that V-Ray adds
        # it to the scene. Both geometry and light instancers need the `Node` wrapper -
        # the only difference is what the `GeomInstancer.sources` list points at.
        for instancerId, instancers in self.instancers.items():
            for instancer in instancers.values():
                plugin_utils.createPlugin(self, instancer.name, 'GeomInstancer')
                vray.exportInstancer(self.renderer, instancer.toData())

                # Track the GeomInstancer plugin in both the instancer and the instanced_object
                self.instTracker.trackPlugin(instancerId, instancer.name)
                if instancer.obj.type == 'LIGHT':
                    self.lightTracker.trackPlugin(getObjTrackId(instancer.obj), instancer.name)
                else:
                    self.objTracker.trackPlugin(getObjTrackId(instancer.obj), instancer.name)

                nodePlugin = exportNodePlugin(self, instancer.instancer, AttrPlugin(instancer.name),
                                                instancer.name, self.objTracker, isInstancer=True)
                self.instTracker.trackPlugin(instancerId, nodePlugin.name)

                # A fur instancer is keyed by the fur object rather than by the instancer object
                # driving it, so record the association by plugin name - see
                # PersistedState.derivedInstancerNodes.
                driverTrackId = getObjTrackId(instancer.instancer)
                if instancerId != driverTrackId:
                    self.persistedState.derivedInstancerNodes.setdefault(driverTrackId, set()).add(nodePlugin.name)

                TestBreak.check(self)


    def _exportGeometryInstance(
        self,
        inst: bpy.types.DepsgraphObjectInstance,
        obj: bpy.types.Object,
        shouldExportGeometry: bool,
        exportedGeometry: dict,
        exportedNodes: dict,
    ):
        dataID = id(obj.data)

        nonInstDataName = Names.objectData(obj.original)
        instDataName = Names.objectData(obj, inst)
        # If the non-instanced object has already exported this data, skip re-export.
        nonInstancedObjDataExported = (nonInstDataName == instDataName) \
            and self.persistedState.objDataTracker.dataExported(nonInstDataName)

        # 'shouldExportGeometry' only reflects what the depsgraph reported as updated, which misses
        # instances whose geometry was never exported in the first place. Pointing an instancer at a
        # different collection is such a case: Blender tags only the instancer object, so none of the
        # newly instanced objects look "changed". Exporting just the Node then leaves it pointing at a
        # geometry plugin that does not exist and the whole instancer renders empty.
        instDataMissing = not self.persistedState.objDataTracker.dataExported(instDataName)

        exported = self.geomExporter.exportObject(
            obj,
            exportGeometry=(not nonInstancedObjDataExported) and (shouldExportGeometry or instDataMissing),
            isVisible=False,
            instance=inst,
            asyncExport=False
        )

        if exported:
            exportedNodes[dataID] = Names.vrayNode(Names.object(obj, inst))
            exportedGeometry[dataID] = inst.random_id

        # Clear temp meshes created while the instance iterator is valid.
        for tempMeshObject in self.objectsWithTempMeshes:
            tempMeshObject.to_mesh_clear()
        self.objectsWithTempMeshes.clear()


    def exportInstances(self):
        """ Iterate the depsgraph instances and emit the per-instancer `GeomInstancer` plugins.

            Geometry instances reuse the `GeometryExporter`'s `_exportObject` / `furExporter`
            for their data export. Light instances skip mesh lights and lazily ensure their
            source `LightXxx` plugin exists via `LightExporter._exportLight` before being
            collected into the same per-parent `instancer@<parent>` `GeomInstancer` as
            geometry from that parent.

            Should be called after both geometries (`obj_export.run`) and lights
            (`LightExporter.export`) have finished, since the instance pass references the
            plugins they produce.
        """

        instanceChanges = {}
        instancerChanges = {}
        scatterCache = {}
        # `self` is an `ExporterBase`, so it copy-inherits all ExporterContext fields
        # (`activeInstancers`, `dgUpdates`, `dg`, ...). `self.ctx` is the *Blender* context,
        # not the exporter context.
        newInstancers = self.activeInstancers.difference(self.persistedState.activeInstancers)
        mtlTopologyUpdates = getMtlTopologyUpdates()

        def hasInstanceChanged(objTrackId, instancerTrackId):
            """ Returns True if the instance has to be exported because either the instanced object or the
                instancer have changed.
            """
            if (result := instanceChanges.get((objTrackId, instancerTrackId), None)) is not None:
                return result

            # Blender does not create instanced objects when the scene is rendered for the first time if
            # the instancer is invisible. This is why we need to check if the instancer has become visible
            # since the last export and process the instances if this is so.
            changed = self.objectsWithUpdatedVisibility.get(instancerTrackId, False) \
                        or (objTrackId in self.dgUpdates['geometry']) \
                        or (instancerTrackId in self.dgUpdates['geometry'])

            instanceChanges[(objTrackId, instancerTrackId)] = changed
            return changed

        def shouldExportGeometry(objTrackId, instancerTrackId):
            return self.fullExport \
                or self.objectsWithUpdatedVisibility.get(instancerTrackId, False) \
                or (objTrackId in self.dgUpdates['geometry']) \
                or (instancerTrackId in newInstancers)

        def hasInstancerChanged(objTrackId, instancerTrackId):
            if (result := instancerChanges.get((objTrackId, instancerTrackId), None)) is not None:
                return result

            changed = self.fullExport \
                        or self.objectsWithUpdatedVisibility.get(instancerTrackId, False) \
                        or objTrackId in self.dgUpdates['transform'] \
                        or instancerTrackId in self.dgUpdates['geometry'] \
                        or instancerTrackId in self.dgUpdates['transform'] \
                        or (instancerTrackId not in self.persistedState.activeInstancers) and (instancerTrackId in self.activeInstancers)

            instancerChanges[(objTrackId, instancerTrackId)] = changed
            return changed

        def canSkipInstanceWalk():
            """ True when nothing this pass would export can have changed, so both
                dg.object_instances walks - the rebuild prepass and the export loop - can be skipped.

                A walk costs ~1 us per instance (~0.5 s for a 500k-instance scene) even when every
                instance turns out to be unchanged, because the loop still has to resolve each
                instance's object and parent before the predicates above can reject it. All the
                predicate inputs are known up front except which objects are instanced under which
                parent, and that is what persistedState.instancedObjectsByParent remembers from the
                previous real walk.

                Deliberately conservative: any signal that could feed a predicate in a way this
                pre-check does not model forces the walk.
            """
            if self.fullExport or self.allObjectsChanged:
                return False

            if self.collectionUpdate:
                # The instancerMembership comparison in the prepass below is the only thing that
                # notices an instanced collection gaining or losing a member, and it can only run if
                # the walk runs. Blender reports such a change by tagging the collection alone, so
                # neither the pair predicates nor allObjectsChanged see it - a member of a collection
                # that is not itself linked to the view layer is not even in allObjects.
                return False

            if not (known := self.persistedState.instancedObjectsByParent):
                # Nothing recorded yet - the first pass has to walk.
                return False

            if newInstancers or mtlTopologyUpdates or updatedFurGizmoObjTrackIdSet:
                return False

            if set(known) & set(self.objectsWithUpdatedVisibility):
                # Both predicates consult objectsWithUpdatedVisibility, but only ever keyed by the
                # *instancer's* track id - hiding an unrelated object cannot affect this pass.
                return False

            if set(known) != self.activeInstancers & set(known):
                # An instancer we recorded is no longer active.
                return False

            # An instanced object or its parent appearing ANYWHERE in dg.updates can change which
            # instances exist, not merely where they are - and the pair predicates below only look at
            # the geometry and transform sets. Measured case: hiding a member of a collection that is
            # collection-instanced but not itself linked to the view layer removes its instance, while
            # reporting only a flagless entry for that object. It reaches neither allObjects nor
            # objectsWithUpdatedVisibility, so this is the only guard that catches it.
            allUpdated = self.dgUpdates['all']
            for parentTrackId, objTrackIds in known.items():
                if (parentTrackId in allUpdated) or (objTrackIds & allUpdated):
                    return False

            # Same predicates the loop uses, so the two cannot drift.
            return not any(
                hasInstanceChanged(objTrackId, parentTrackId)
                or hasInstancerChanged(objTrackId, parentTrackId)
                for parentTrackId, objTrackIds in known.items()
                for objTrackId in objTrackIds
            )

        recreatedMaterialCache = {}
        def hasRecreatedMaterial(objTrackId: int, obj: bpy.types.Object):
            if (result := recreatedMaterialCache.get(objTrackId)) is not None:
                return result
            result = any(getObjTrackId(s.material) in mtlTopologyUpdates for s in obj.material_slots if s.material is not None)
            recreatedMaterialCache[objTrackId] = result
            return result

        # If object meshes are generated from a GN tree, they won't be assigned a unique vray IDs.
        # Track the first exported instance of each object, matching the rest of the instances by
        # the object's data pointer.
        # NOTE: We rely on the order of the instances being always the same;
        # if not, an additional map rendom_id to id should be used.
        # NOTE: instance.random_id is stable but is not persisted to the scene.

        exportedGeometry = {} # id(obj.data) => list[instance.random_id]
        exportedNodes    = {} # id(obj.data) => node_plugin_name

        exportedGeomHair = {} # mark fur objects that have been exported for

        # Pair of nodePluginName, furTrackId, furName for fur objects that have been exported for each instance
        exportedGeomHairNodes : dict[int, list[tuple[str, int, str]]] = {}

        updatedFurGizmoObjTrackIdSet = set(p.gizmoObjTrackId for p in self.updatedFurInfo) # Objects selected by fur objects that have been updated.

        # Tracks source light objects whose `LightXxx` plugin we have ensured during this pass.
        # In 'instanced' LightMix mode the entries are (objTrackId, instancerTrackId) tuples, since a
        # dedicated light plugin is exported per (instancer, light) pair; otherwise plain objTrackId.
        exportedLights = set()

        if canSkipInstanceWalk():
            return

        # Which objects are instanced under which parent, for the next pass's skip check. Recorded
        # only when the walk actually runs, so a skipped pass leaves the previous map in place.
        instancedByParent = defaultdict(set)

        def iterInstances():
            """ Yield the instances this pass is interested in, along with their track ids.

                Shared by the rebuild prepass and the export loop below, so that the two can never
                disagree about which instances belong to an instancer.
            """
            for inst in self.dg.object_instances:
                if not inst.is_instance or not inst.object:
                    continue

                obj = inst.object

                if obj.type in NonGeometryTypes and obj.type != 'LIGHT':
                    continue

                if tools.isRedundantGeometryInstance(inst):
                    continue

                if self.isProxyExport and (not tools.isProxyConvertibleGeometryType(inst.object)):
                    continue

                instancer = inst.parent

                # The answer depends only on the instancer object, which is the same for every
                # instance it emits, so cache it instead of asking once per instance.
                instancerKey = instancer.as_pointer()
                isScatter = scatterCache.get(instancerKey)
                if isScatter is None:
                    isScatter = tools.isObjectChaosScatter(instancer)
                    scatterCache[instancerKey] = isScatter

                if isScatter:
                    # Chaos Scatter preview instances; the render-time placement comes from the
                    # GeomScatter plugin exported by scatter_export, not from baked transforms.
                    continue

                if self.isProxyExport and self.proxyExportSettings.exportOnlySelected and not instancer.original.select_get():
                    continue

                yield inst, obj, instancer, getObjTrackId(obj), getObjTrackId(instancer)

        # A `GeomInstancer` is rebuilt from scratch out of the instances collected during this pass,
        # so it has to be rebuilt from *all* of them or not at all. Two of the change predicates are
        # per instanced object though - a changed transform and a recreated material - so deciding
        # per instance would drop every instance which did not itself change. 
        # Decide once per instancer instead. The short circuit keeps this to one predicate
        # evaluation per instancer plus a set lookup per instance.
        instancersToRebuild = set()

        # Signature of the instances each instancer produces, see PersistedState.instancerMembership.
        # Accumulated for every instance, which is why the short circuit below only skips the
        # predicate evaluation.
        membership: dict[int, tuple[int, int, int]] = {}

        for _, obj, _, objTrackId, instancerTrackId in iterInstances():
            count, total, checksum = membership.get(instancerTrackId, (0, 0, 0))
            membership[instancerTrackId] = (count + 1, total + objTrackId, checksum ^ objTrackId)

            if instancerTrackId in instancersToRebuild:
                continue

            # 'updatedFurGizmoObjTrackIdSet' holds growth mesh objects, so it is matched against the
            # instanced object. Folding it in here rather than gating the fur block on it separately
            # keeps the fur instancers subject to the same all-or-nothing rule as the others - they
            # are populated from this same loop, so a per object trigger would rebuild one out of a
            # single instance.
            if hasInstancerChanged(objTrackId, instancerTrackId) \
                    or hasRecreatedMaterial(objTrackId, obj) \
                    or objTrackId in updatedFurGizmoObjTrackIdSet:
                instancersToRebuild.add(instancerTrackId)

        # Moving an object in or out of an instanced collection changes what the instancer produces
        # without anything reporting the instancer itself as changed, so compare the sets.
        for instancerTrackId, signature in membership.items():
            if self.persistedState.instancerMembership.get(instancerTrackId) != signature:
                instancersToRebuild.add(instancerTrackId)

        for inst, obj, instancer, objTrackId, instancerTrackId in iterInstances():
            dataID = id(obj.data)

            instancedByParent[instancerTrackId].add(objTrackId)

            instanceChanged  = hasInstanceChanged(objTrackId, instancerTrackId)
            instancerChanged = instancerTrackId in instancersToRebuild

            # Appending an instance is what commits its instancer to being rebuilt from scratch, so
            # the light branch below must only append when the instancer is actually being rebuilt -
            # otherwise a member which changed on its own (editing a light property) would rebuild
            # the instancer out of just itself and drop all the other instances. Its source plugin
            # still has to be re-exported either way, which is why the two decisions are passed
            # separately.
            if tools.isObjectVRayDecal(obj):
                # Decals are handled outside the GeomInstancer, see _exportDecalInstance().
                if instanceChanged or instancerChanged:
                    self._exportDecalInstance(inst, instancerTrackId)
                continue

            if instanceChanged or instancerChanged:
                if obj.type == 'LIGHT':
                    self._exportLightInstance(inst, exportedLights, instancerChanged)
                    continue
                elif dataID not in exportedGeometry:
                    geomNeedsExport = shouldExportGeometry(objTrackId, instancerTrackId)
                    self._exportGeometryInstance(inst, obj, geomNeedsExport, exportedGeometry, exportedNodes)

            if instancerChanged and (nodePluginName := exportedNodes.get(dataID)):
                self._addInstance(inst, nodePluginName, parentTrackId=instancerTrackId)

            # An updated fur gizmo already marked this instancer for rebuild in the prepass.
            isFurInstanceChanged = instancerChanged

            # Marking fur nodes to be added to instancer.
            if isFurInstanceChanged and (dataID not in exportedGeomHair):
                exportedGeomHairNodes[dataID] = self.geomExporter.furExporter.exportFursOfObject(obj, inst)
                exportedGeomHair[dataID] = inst.random_id

            # Adding fur nodes to instancer.
            if isFurInstanceChanged and (nodePluginNames := exportedGeomHairNodes.get(dataID)):
                for nodePluginName, furTrackId, furName in nodePluginNames:
                    instancerName = f"instancer@{getGeomHairPluginName(furName, Names.object(instancer))}"
                    self._addInstance(inst, nodePluginName, furTrackId, instancerName, parentTrackId=instancerTrackId)


        self._disableStaleDecals(instancersToRebuild)

        self.persistedState.instancedObjectsByParent = dict(instancedByParent)

        # Export the collected instancer data
        self.export()

        # Only after a successful export - if it raises, the old snapshot has to survive so that
        # the next pass still sees these instancers as changed. Replaced wholesale rather than
        # updated, so that instancers which no longer exist drop out on their own.
        self.persistedState.instancerMembership = membership


    def _disableStaleDecals(self, instancersToRebuild: set):
        """ Disable the per-instance decals which this pass did not re-export.

            Instanced geometry that goes out of date is harmless - the source `Node` plugins are
            exported invisible and simply stop being referenced by the `GeomInstancer`. A decal has
            no such wrapper, it is a scene level plugin which keeps projecting until it is disabled.
            Pointing `instance_collection` at another collection is the typical case: the decals of
            the previous collection are left behind with no instance to match them.

            Only instancers which were rebuilt during this pass are considered, otherwise the still
            valid decals of the untouched ones would be disabled as well.
        """
        for instancerTrackId in instancersToRebuild:
            live = self._exportedDecals.get(instancerTrackId, set())

            for pluginName in self.instTracker.getPlugins(instancerTrackId):
                if isPluginVRayDecal(pluginName) and (pluginName not in live):
                    vray.pluginUpdateInt(self.renderer, pluginName, 'enabled', False)


    def _exportDecalInstance(self, inst: bpy.types.DepsgraphObjectInstance, instancerTrackId: int):
        """ Export a dedicated `VRayDecal` plugin for a single instance of a decal object.

            `GeomInstancer` does not instance `VRayDecal` correctly, so a decal cannot be collected
            into the parent's instancer the way meshes and lights are. A decal also carries its own
            transform, so instead of one prototype driven by the instancer we export one decal per
            instance, placed at the instance's world transform.
        """
        obj = inst.object
        instancerObj = inst.parent.original
        isVisible = instancerObj.visible_get() if self.interactive else not instancerObj.hide_render

        with self.objectContext.push(obj):
            pluginName = self.geomExporter.exportVRayDecal(obj, isVisible, inst)

        # Owned by the instancer as well, so that the per-instance decals are removed
        # together with it.
        self.instTracker.trackPlugin(instancerTrackId, pluginName)
        self._exportedDecals[instancerTrackId].add(pluginName)


    def _useInstancedLightMix(self):
        """ True when the active LightMix render channel is in 'instanced' mode and we are producing
            LightMix render channels (production or IPR-VFB export).
        """
        return (self.activeLightMixNode is not None) \
            and (self.activeLightMixNode.RenderChannelLightMix.mode == 'instanced') \
            and (self.production or self.iprVFB)

    def _exportLightInstance(self, inst: bpy.types.DepsgraphObjectInstance, exportedLights: set, addToInstancer: bool):
        """ Ensure the `LightXxx` plugin for an instanced light object, and optionally collect the
            instance into the parent's `GeomInstancer`.

            'addToInstancer' must be False unless the instancer is being rebuilt this pass, see the
            call site.
        """
        obj = inst.instance_object

        useLightMix = self._useInstancedLightMix()
        instancerObj = inst.parent
        pluginNameBase = Names.instancedLight(obj, instancerObj) if useLightMix else Names.object(obj)
        pluginNames = [pluginNameBase]

        if obj.data.vray.light_type == 'MESH':
            from vray_blender.plugins.light.LightMesh import getLightMeshGeometryObjects, getLightMeshPluginName
            
            if not (geomObjs := getLightMeshGeometryObjects(self, obj)):
                # Skip instancer export when the LightMesh has no geometry selected.
                # Any previously created instancer is now stale, so remove it.
                instTracker = self.objTrackers['INSTANCER']
                instTrackerId = getObjTrackId(instancerObj.original)
                for pluginName in instTracker.getOwnedPlugins(instTrackerId):
                    vray.pluginRemove(self.renderer, pluginName)
                instTracker.forget(instTrackerId)
                return
            
            pluginNames = [getLightMeshPluginName(pluginNameBase, getObjTrackId(o)) for o in geomObjs]

        # In 'instanced' LightMix mode, each object that instances lights gets its own LightSelect
        # render channel, and each instanced light is exported as a dedicated plugin (separate from
        # the source light) so its contribution is attributable to that channel. This only applies
        # to full production/IPR-VFB exports, where LightMix render channels are produced.
        # For that reason, we need to use the instancer object track id to identify the light as there could be
        # multiple instancers that instance the same light.
        exportedLightKey = (getObjTrackId(obj), getObjTrackId(instancerObj)) if useLightMix else getObjTrackId(obj)

        if exportedLightKey not in exportedLights:
            # If the original is visible the regular light pass exports the plugin. Both modes
            # must agree on "visible": an instancing source is normally hidden by EXCLUDING its
            # collection, which hide_render does not reflect but the depsgraph does.
            if self.interactive:
                originalIsVisible = obj.original.visible_get()
            else:
                originalIsVisible = (not obj.original.hide_render) \
                                    and (obj.original.name in self.dg.objects)
            exportLightPlugin = useLightMix or (not originalIsVisible)

            if exportLightPlugin:
                with self.objectContext.push(obj):
                    # Don't export the LightSelect render channel if we are in 'instanced' LightMix mode.
                    self.lightExporter.exportLight(obj, pluginNameOverride=pluginNameBase, lightSelectExportEnabled=not useLightMix)

                
                for pluginName in pluginNames:
                    # Disable the light plugin, so that the original light is not visible in the render result.
                    vray.pluginUpdateInt(self.renderer, pluginName, 'enabled', False)
                
                if useLightMix: # Export the LightSelect render channel for the instancer object.
                    self.lightExporter.exportLightSelectChannel(instancerObj.name, pluginNames, obj.name)

            exportedLights.add(exportedLightKey)

        if not addToInstancer:
            return

        if obj.data.vray.light_type == 'MESH':
            for pluginName in pluginNames:
                self._addInstance(inst, pluginName)
        else:
            # The `LightXxx` plugin already carries the source light's world transform
            # (set by `LightExporter._exportLightPlugin` from `obj.matrix_world`). V-Ray
            # composes the source's transform with the per-instance transform we pass to
            # `GeomInstancer`. Feed in `inst.matrix_world @ source^-1` so the composition
            # `relative @ source` yields `inst.matrix_world` and each instance ends up at
            # its actual world position with its own scale (not the source's).
            sourceTm = obj.matrix_world
            relativeTm = inst.matrix_world @ sourceTm.inverted()

            self._addInstance(inst, pluginNames[0], tmOverride=relativeTm)


    @staticmethod
    def pruneInstances(exporterCtx: ExporterContext):
        """ Remove plugins associated with instanced objects.

            @param prevActiveInstancers - a set of track object IDs, snapshot of the state before the current export
            @param exporterCtx - the exporter context of the current export
        """
        instTracker = exporterCtx.objTrackers['INSTANCER']

        def removeInstancer(instancerTrackId):
            for pluginName in instTracker.getOwnedPlugins(instancerTrackId):
                vray.pluginRemove(exporterCtx.renderer, pluginName)

            instTracker.forget(instancerTrackId)

        # Instancer nodes which are tracked under something other than the instancer object driving
        # them, see PersistedState.derivedInstancerNodes. Their visibility follows the driving
        # instancer object and nothing else, so they are excluded from the pass over the object they
        # are keyed under - a fur instancer must not react to the fur object being hidden, in the
        # same way that hiding any other member of a collection does not affect its instances.
        derivedInstancerNodes = exporterCtx.persistedState.derivedInstancerNodes
        allDerivedNodes = set().union(*derivedInstancerNodes.values()) if derivedInstancerNodes else set()

        def showInstancer(instancerTrackId, show: bool):
            for pluginName in instTracker.getOwnedPlugins(instancerTrackId):
                if pluginName in allDerivedNodes:
                    continue

                if pluginName.startswith('node@instancer@'):
                    vray.pluginUpdateInt(exporterCtx.renderer, pluginName, 'visible', show)
                elif isPluginVRayDecal(pluginName):
                    # Decals instanced by this object are separate plugins, not instancer sources.
                    vray.pluginUpdateInt(exporterCtx.renderer, pluginName, 'enabled', show)

        instancers = [o for o in exporterCtx.sceneObjects if (o.is_instancer or o.vray.isVRayFur)]

        instancerVisibility = {}

        for instancer in instancers:
            show = instancer.visible_get() if exporterCtx.interactive else not instancer.hide_render
            showInstancer(getObjTrackId(instancer), show)
            instancerVisibility[getObjTrackId(instancer)] = show

        # Apply the driving instancer object's visibility to the nodes excluded above. By plugin name
        # rather than by track id, because one fur object drives one instancer per instancer object
        # and all of them are tracked under that single fur track id - going through the track id
        # would hide the fur of every instancer at once.
        for driverTrackId, pluginNames in derivedInstancerNodes.items():
            if (show := instancerVisibility.get(driverTrackId)) is None:
                # The driving object is no longer an instancer or is gone from the scene. Leave these
                # plugins alone, removeInstancer() below deals with the deletion case.
                continue

            for pluginName in pluginNames:
                vray.pluginUpdateInt(exporterCtx.renderer, pluginName, 'visible', show)

        for objTrackId in exporterCtx.persistedState.activeInstancers.difference(exporterCtx.activeInstancers):
            # The instancer has been deleted, remove all plugins associated with it.
            removeInstancer(objTrackId)


def run(ctx: ExporterContext, geomExporter, lightExporter):
    """ Module-level entry point for the instance export pass.

        Mirrors `obj_export.run` / `light_export.LightExporter(...).export()` so renderers
        can hook the instancer export into their pipeline with a single call. Must be invoked
        after both geometries and lights have been exported.
    """
    if ctx.preview:
        # Preview scenes don't have instances and lack the depsgraph state that the
        # iteration relies on.
        return

    InstancerExporter(ctx, geomExporter, lightExporter).exportInstances()
