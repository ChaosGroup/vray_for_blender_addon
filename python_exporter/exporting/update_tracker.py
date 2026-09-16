# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import bpy
from contextlib import contextmanager
from enum import IntEnum, IntFlag

from vray_blender.exporting.plugin_tracker import getObjTrackId

class UpdateFlags(IntFlag):
    NONE        = 0
    DATA        = 1     # A property value has changed 
    TOPOLOGY    = 2     # The node tree topology has changed
    ALL         = DATA | TOPOLOGY

class UpdateTarget(IntEnum):
    """ The object on which the update was performed """
    MATERIAL    = 0
    OBJECT      = 1
    OBJECT_MTL_OPTIONS = 2
    LIGHT = 3
    WORLD = 4


class UpdateTracker:
    """ This is an alternative to Blender's depsgraph update info mechanism. The problem
        with the depsgraph is that, when tagging objects (specifically materials) for update,
        we don't have sufficient control over the type of update that is being recorded. 
        This makes is difficult or impossible to tell for example whether a material property
        has changed, or the node tree topology.
    """
    # Dictionary {UpdateTarget: {obj.session_uid: UpdateFlags}}
    updates: dict[int, UpdateFlags] = {}

    # Material names whose topology tag is pending, while deferMtlTopology() is active.
    # Names, not datablocks: the deferral spans a whole scene build, over which a held
    # reference can go stale.
    deferredMtlTopology: set[str] | None = None

    @staticmethod
    def clear():
        UpdateTracker.updates = {}

    @staticmethod
    def tagUpdate(obj: bpy.types.ID, target: UpdateTarget, flag: UpdateFlags):
        updatesForTarget = UpdateTracker.updates.setdefault(target, {})
        flags = updatesForTarget.get(getObjTrackId(obj), UpdateFlags.NONE)
        updatesForTarget[getObjTrackId(obj)] = (flags | flag)

    @staticmethod
    def getObjUpdate(target: UpdateTarget, obj):
        return UpdateTracker.updates.get(target, {}).get(getObjTrackId(obj), UpdateFlags.NONE)

    @staticmethod
    def getUpdatesOfType(target: UpdateTarget, flags: UpdateFlags):
        if updatesForTarget := UpdateTracker.updates.get(target, None):
            return [(sid, updatesForTarget[sid]) for sid in updatesForTarget if flags & updatesForTarget[sid]]

        return []

    @staticmethod
    def tagMtlTopology(context: bpy.types.Context, mtl: bpy.types.Material):
        """ A helper method to tag material topology and all scene objects related to this material """

        if UpdateTracker.deferredMtlTopology is not None:
            UpdateTracker.deferredMtlTopology.add(mtl.name)
            return

        # Tag the material proper
        UpdateTracker.tagUpdate(mtl, UpdateTarget.MATERIAL, UpdateFlags.TOPOLOGY)

        # Tag all objects that are using this material
        objects = context.scene.objects

        for obj in objects:
            if any(s.material == mtl for s in obj.material_slots) or (obj.vray.material == mtl):
                UpdateTracker.tagUpdate(obj, UpdateTarget.OBJECT_MTL_OPTIONS, UpdateFlags.TOPOLOGY)

    @staticmethod
    def tagMtlTopologyBatch(context: bpy.types.Context, mtlNames: set[str]):
        """ tagMtlTopology() for a set of materials in one pass over the scene objects.

            tagMtlTopology() walks every object per material, so tagging N materials one by
            one costs N * objectCount slot comparisons - 320 * 42k on an imported
            object-heavy scene. Tagging is a flag OR into a dict keyed by track id, so the
            single inverted pass records exactly the same updates.
        """
        mtls = {mtl for name in mtlNames if (mtl := bpy.data.materials.get(name))}
        if not mtls:
            return

        for mtl in mtls:
            UpdateTracker.tagUpdate(mtl, UpdateTarget.MATERIAL, UpdateFlags.TOPOLOGY)

        for obj in context.scene.objects:
            if any(s.material in mtls for s in obj.material_slots) or (obj.vray.material in mtls):
                UpdateTracker.tagUpdate(obj, UpdateTarget.OBJECT_MTL_OPTIONS, UpdateFlags.TOPOLOGY)

    @staticmethod
    @contextmanager
    def deferMtlTopology():
        """ Collect the materials passed to tagMtlTopology() and tag each of them once on exit.

            tagMtlTopology() walks every object in the scene, and the node-tree update callback
            calls it once per node per topology change - so building a tree of N nodes tags the
            same material O(N^2) times. Under this guard a bulk scene build (import, conversion,
            upgrade) pays for a single walk for all the materials instead.
        """
        if UpdateTracker.deferredMtlTopology is not None:
            yield   # Already deferring; the outermost guard does the flush.
            return

        UpdateTracker.deferredMtlTopology = set()
        try:
            yield
        finally:
            mtlNames = UpdateTracker.deferredMtlTopology
            UpdateTracker.deferredMtlTopology = None
            UpdateTracker.tagMtlTopologyBatch(bpy.context, mtlNames)

    @staticmethod
    def tagCrossObjectUpdates(exporterCtx: ExporterContext, data, updateTarget: UpdateTarget):
        """ Tag for update all properties that depend on objects that have been updated. 

            Args:
                data : the data collection ot scan e.g. bpy.data.materials
                updateTarget: corresonding to the type of data in the collection
        """

        from vray_blender.lib.plugin_utils import CROSS_DEPENDENCIES
        from vray_blender.nodes.utils import getVrayPropGroup
        from vray_blender.exporting.update_tracker import UpdateTracker, UpdateFlags

        # Iterate over all plugin types and their cross dependencies
        updatedObjects = {u.id.original for u in exporterCtx.dg.updates if u.is_updated_transform or u.is_updated_geometry}

        if not updatedObjects:
            return

        for pluginType, propList in CROSS_DEPENDENCIES.items():
            for item in [i for i in data if i.node_tree]:
                for node in [n for n in item.node_tree.nodes if getattr(n, 'vray_plugin', '') == pluginType]:
                    for propName in propList:
                        propGroup = getVrayPropGroup(node)
                        refProp = getattr(propGroup, propName)
                        refObj = exporterCtx.sceneObjects.get(refProp)
                        if refObj in updatedObjects:
                            UpdateTracker.tagUpdate(item, updateTarget, UpdateFlags.DATA)
