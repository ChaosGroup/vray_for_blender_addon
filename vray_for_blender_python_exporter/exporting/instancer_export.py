# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
import numpy as np
from collections import defaultdict

from mathutils import Matrix

from vray_blender.exporting import tools
from vray_blender.exporting.mtl_export import getMtlTopologyUpdates
from vray_blender.exporting.node_export import exportNodePlugin
from vray_blender.exporting.plugin_tracker import getObjTrackId
from vray_blender.lib.blender_utils import TestBreak, NonGeometryTypes
from vray_blender.lib.defs import ExporterBase, ExporterContext, AttrPlugin
from vray_blender.lib.names import Names
from vray_blender.plugins.geometry.GeomHair import getGeomHairPluginName

from vray_blender.bin import VRayBlenderLib as vray


# Data holder passed to C++ for GeomInstancer export.
# Fields are separate contiguous numpy arrays for efficient bulk processing.
class InstancerData:
    def __init__(self, name, ids, tms, meshes, indices):
        self.name    = name
        self.count   = len(ids) if isinstance(ids, np.ndarray) else 0
        self.ids     = ids       # np.ndarray int32 (N, 8) — persistent IDs
        self.tms     = tms       # np.ndarray float32 (N, 12) — AttrTransform layout
        self.meshes  = meshes    # list[str] — unique mesh plugin names
        self.indices = indices   # np.ndarray int32 (N,) — per-instance mesh index


# Temp collector for instance data, will be converted to InstanceData before
# passing it to C++
class Instancer:
    _INITIAL_CAPACITY = 512

    def __init__(self, inst, name: str):
        self.obj       = inst.instance_object  # The object being instanced
        self.instancer = inst.parent           # The object whose geometry determines the position and number of instances
        self.name      = name

        cap = self._INITIAL_CAPACITY
        # All three buffers grow together depending on _count. Slice assignment
        # (buf[i] = x) uses the buffer protocol instead of np.array(list_of_objects),
        # which falls back to O(N*16) Python-level float extraction for matrices
        # and O(N*8) for persistent IDs.
        self._tmsBuf     = np.empty((cap, 4, 4), dtype=np.float32)
        self._idsBuf     = np.empty((cap, 8),    dtype=np.int32)
        self._indicesBuf = np.empty((cap,),       dtype=np.int32)
        self._count      = 0

        self._meshes      = []
        self._meshToIndex = {}


    def _grow(self):
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


    def append(self, persistentId, tm, nodePluginName):
        if self._count >= len(self._tmsBuf):
            self._grow()

        n = self._count
        self._tmsBuf[n] = tm
        self._idsBuf[n] = persistentId

        if nodePluginName not in self._meshToIndex:
            self._meshToIndex[nodePluginName] = len(self._meshes)
            self._meshes.append(nodePluginName)
        self._indicesBuf[n] = self._meshToIndex[nodePluginName]

        self._count += 1


    def toData(self):
        count = self._count
        if count == 0:
            empty = np.empty(0, dtype=np.int32)
            return InstancerData(self.name, empty, np.empty((0, 12), dtype=np.float32), [], empty)

        ids = self._idsBuf[:count].copy()  # (N, 8)

        # Transpose to row-major, drop the 4th column to get AttrTransform layout:
        # [row0[0:3], row1[0:3], row2[0:3], row3[0:3]] = 12 floats per transform.
        mats = self._tmsBuf[:count]  # (N, 4, 4) -- already numpy, no conversion
        tms = np.ascontiguousarray(mats.transpose(0, 2, 1)[:, :, :3], dtype=np.float32).reshape(count, 12)

        indices = self._indicesBuf[:count].copy()  # (N,)

        return InstancerData(self.name, ids, tms, self._meshes, indices)


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

    def _addInstance(
        self, 
        inst: bpy.types.DepsgraphObjectInstance, 
        nodePluginName: str, 
        instTrackIdOverride: int = None, 
        nameOverride: str = None, 
        tmOverride: Matrix | None = None
    ):
        instancerObj = inst.parent  # This is the object whose vertices/faces are used for the instantiation
        instancedObj = inst.instance_object     # The object being instanced

        if instancedObj.type not in tools.EXPORTED_OBJECT_TYPES:
            # There are some instanced types we are not interested in, like armature or the empty
            # controller objects for instanced collections
            return

        idInstancer = instTrackIdOverride or getObjTrackId(instancerObj.original)

        nameInstancer = nameOverride if nameOverride is not None else Names.instancer(inst)

        # There could be multiple instancers with the same id, but different names.
        instancer = self.instancers.setdefault(idInstancer, {}).setdefault(
            nameInstancer, Instancer(inst, nameInstancer))

        tm = tmOverride if tmOverride is not None else inst.matrix_world
        instancer.append(inst.persistent_id, tm, nodePluginName)


    def export(self):
        # Export a GeomInstancer plugin together with a wrapping Node so that V-Ray adds
        # it to the scene. Both geometry and light instancers need the `Node` wrapper -
        # the only difference is what the `GeomInstancer.sources` list points at.
        for instancerId, instancers in self.instancers.items():
            for instancer in instancers.values():
                vray.pluginCreate(self.renderer, instancer.name, 'GeomInstancer')
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

        exported = self.geomExporter.exportObject(
            obj,
            exportGeometry=(not nonInstancedObjDataExported) and shouldExportGeometry,
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
        exportedLights = set()

        for inst in self.dg.object_instances:
            if not inst.is_instance or not inst.object:
                continue

            obj = inst.object

            if obj.type in NonGeometryTypes and obj.type != 'LIGHT':
                continue

            if self.isProxyExport and (not tools.isProxyConvertibleGeometryType(inst.object)):
                continue

            instancer = inst.parent

            if self.isProxyExport and self.proxyExportSettings.exportOnlySelected and not instancer.original.select_get():
                continue

            objTrackId       = getObjTrackId(obj)
            instancerTrackId = getObjTrackId(instancer)
            dataID           = id(obj.data)

            instanceChanged  = hasInstanceChanged(objTrackId, instancerTrackId)
            instancerChanged = hasInstancerChanged(objTrackId, instancerTrackId) or hasRecreatedMaterial(objTrackId, obj)
           
            if instanceChanged or instancerChanged:
                if obj.type == 'LIGHT':
                    self._exportLightInstance(inst, exportedLights)
                    continue
                elif dataID not in exportedGeometry:
                    geomNeedsExport = shouldExportGeometry(objTrackId, instancerTrackId)
                    self._exportGeometryInstance(inst, obj, geomNeedsExport, exportedGeometry, exportedNodes)

            if instancerChanged and (nodePluginName := exportedNodes.get(dataID)):
                self._addInstance(inst, nodePluginName)

            isFurInstanceChanged = (instancerTrackId in updatedFurGizmoObjTrackIdSet) or instancerChanged

            # Marking fur nodes to be added to instancer.
            if isFurInstanceChanged and (dataID not in exportedGeomHair):
                exportedGeomHairNodes[dataID] = self.geomExporter.furExporter.exportFursOfObject(obj, inst)
                exportedGeomHair[dataID] = inst.random_id

            # Adding fur nodes to instancer.
            if isFurInstanceChanged and (nodePluginNames := exportedGeomHairNodes.get(dataID)):
                for nodePluginName, furTrackId, furName in nodePluginNames:
                    instancerName = f"instancer@{getGeomHairPluginName(furName, Names.object(instancer))}"
                    self._addInstance(inst, nodePluginName, furTrackId, instancerName)


        # Export the collected instancer data
        self.export()


    def _exportLightInstance(self, inst: bpy.types.DepsgraphObjectInstance, exportedLights: set):
        obj = inst.instance_object

        # Skip mesh lights for now - their geometry-driven export path is more complex.
        if obj.data.vray.light_type == 'MESH':
            return

        objTrackId = getObjTrackId(obj)

        if objTrackId not in exportedLights:
            # If the original light is visible, we don't need to export it again.
            originalIsVisible = obj.original.visible_get() if self.interactive else not obj.original.hide_render
            if not originalIsVisible:
                with self.objectContext.push(obj):
                    self.lightExporter.exportLight(obj)
                
                # Disable the light plugin, so that the original light is not visible in the render result.
                vray.pluginUpdateInt(self.renderer, Names.object(obj), 'enabled', False)

            exportedLights.add(objTrackId)

        # The `LightXxx` plugin already carries the source light's world transform
        # (set by `LightExporter._exportLightPlugin` from `obj.matrix_world`). V-Ray
        # composes the source's transform with the per-instance transform we pass to
        # `GeomInstancer`. Feed in `inst.matrix_world @ source^-1` so the composition
        # `relative @ source` yields `inst.matrix_world` and each instance ends up at
        # its actual world position with its own scale (not the source's).
        sourceTm = obj.matrix_world
        relativeTm = inst.matrix_world @ sourceTm.inverted()

        self._addInstance(inst, Names.object(obj), tmOverride=relativeTm)


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

        def showInstancer(instancerTrackId, show: bool):
            for pluginName in instTracker.getOwnedPlugins(instancerTrackId):
                if pluginName.startswith('node@instancer@'):
                    vray.pluginUpdateInt(exporterCtx.renderer, pluginName, 'visible', show)

        instancers = [o for o in exporterCtx.sceneObjects if (o.is_instancer or o.vray.isVRayFur)]

        for instancer in instancers:
            show = instancer.visible_get() if exporterCtx.interactive else not instancer.hide_render
            showInstancer(getObjTrackId(instancer), show)

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
