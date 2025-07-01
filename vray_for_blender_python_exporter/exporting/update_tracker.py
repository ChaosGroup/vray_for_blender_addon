from __future__ import annotations

import bpy
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
           
    @staticmethod
    def clear():
        UpdateTracker.updates = {}

    @staticmethod
    def tagUpdate(obj: bpy.types.ID, target: UpdateTarget, flag: UpdateFlags):
        updatesForTarget = UpdateTracker.updates.setdefault(target, {})
        flags = updatesForTarget.get(target, UpdateFlags.NONE)
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

        # Tag the material proper
        UpdateTracker.tagUpdate(mtl, UpdateTarget.MATERIAL, UpdateFlags.TOPOLOGY)

        # Tag all objects that are using this material
        objects = context.scene.objects

        for obj in objects:
            if any([s for s in obj.material_slots if s.material == mtl]):
                UpdateTracker.tagUpdate(obj, UpdateTarget.OBJECT_MTL_OPTIONS, UpdateFlags.TOPOLOGY)

    @staticmethod
    def tagCrossObjectUpdates(exporterCtx: ExporterContext, data, updateTarget: UpdateTarget):
        """ Tag for update all properties that depend on objects that have been updated. 

            Args:
                data : the data collection ot scan e.g. bpy.data.materials
                updateTarget: corresonding to the type of data in the collection
        """

        from vray_blender.lib.plugin_utils import CROSS_DEPENDENCIES
        from vray_blender.nodes.utils import getVrayPropGroup
        from vray_blender.exporting.update_tracker import UpdateTracker, UpdateTarget, UpdateFlags

        # Iterate over all plugin types and their cross dependencies
        updatedObjects = [u.id.original for u in exporterCtx.dg.updates if u.is_updated_transform or u.is_updated_geometry]

        if not updatedObjects:
            return

        for pluginType, propList in CROSS_DEPENDENCIES.items():
            for item in [i for i in data if i.node_tree]:
                for node in [n for n in item.node_tree.nodes if n.get('vray_plugin', '') == pluginType]:
                    for propName in propList:
                        propGroup = getVrayPropGroup(node)
                        refProp = getattr(propGroup, propName)
                        refObj = exporterCtx.sceneObjects.get(refProp)
                        if refObj in updatedObjects:
                            UpdateTracker.tagUpdate(item, updateTarget, UpdateFlags.DATA)
