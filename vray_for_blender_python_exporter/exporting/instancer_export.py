import bpy
import ctypes
import struct
from mathutils import Matrix
from vray_blender.exporting import tools, marshaller
from vray_blender.exporting.node_export import exportNodePlugin
from vray_blender.exporting.plugin_tracker import getObjTrackId, log as trackerLog
from vray_blender.lib.defs import DataArray, ExporterBase, ExporterContext, AttrPlugin
from vray_blender.lib.names import Names
from vray_blender.external import mmh3

from vray_blender.bin import VRayBlenderLib as vray


# Struct to pass to C++. 
# To avoid the hassle of unpacking Instancer objects in C++ using Python interop, 
# for the time being pack all the data to a single blob here. 
# TODO: Figure out whether doing this in C++ will be more convenient/fast.

class InstancerData:
    def __init__(self, name, frame, data, itemCount):
        self.name   = name
        self.frame  = frame
        self.data   = data

        dataLen = len(self.data)
        ptr = (ctypes.c_char * dataLen).from_buffer(self.data)
        self.arr    = DataArray(ctypes.addressof(ptr), dataLen)
        self.count  = itemCount


# Temp collector for instance data, will be converted to InstanceData before 
# passing it to C++
class Instancer:
    class Instance:
        def __init__(self, persistentId):
            # The index is generated from the instance's persistent_id (an array of ints) by hashing it
            # to create a unique identifier for this instance
            self.index          = mmh3.hash(struct.pack(f'{len(persistentId)}i', *persistentId))

            self.tm: Matrix     = None
            self.velocity       = Matrix()
            self.velocity.zero()
            self.nodePlugin     = ""

        def pack(self):
            m = marshaller.Marshaller()
            
            m.dumpInt32(self.index)
            m.dumpMatrix4(self.tm)
            m.dumpMatrix4(self.velocity)
            m.dumpString(self.nodePlugin)

            return m.pack()


    def __init__(self, inst):
        self.obj    = inst.instance_object  # The object being instanced
        self.instancer = inst.parent        # The object whose geometry determines the position and number of instances
        self.name   = Names.instancer(inst)
        self.frame  = 0
        self._data = bytearray()
        self._items: list[Instancer.Instance]  = []


    def append(self, item):
        self._items.append(item)
        self._data.extend(item.pack())


    def toData(self):
        return InstancerData(self.name, self.frame, self._data, len(self._items))



class InstancerExporter(ExporterBase):

    def __init__(self, ctx: ExporterContext):
        super().__init__(ctx)
        self.instancers       = {}
        self.exporter         = self.ctx.scene.vray.Exporter
        self.objTracker       = ctx.objTrackers['OBJ']
        self.objMtlTracker    = ctx.objTrackers['OBJ_MTL']
        self.instTracker      = ctx.objTrackers['INSTANCER']

    def addInstance(self, inst: bpy.types.DepsgraphObjectInstance, objectName):
        instancerObj = inst.parent  # This is the object whose vertices/faces are used for the instantiation
        instancedObj = inst.instance_object     # The object being instanced

        if instancedObj.type not in tools.EXPORTED_OBJECT_TYPES:
            # There are some instanced types we are not interested in, like armature or the empty
            # controller objects for instanced collections
            return
        
        # The inst.persistent_id is used to uniquely identify each instance across frames
        # This allows V-Ray to track instances between frames for motion blur and other
        # frame-dependent effects. Without it, instances could get randomly reassigned
        # between frames, causing visual artifacts in animations.
        instance = Instancer.Instance(inst.persistent_id)

        # In VRay, instance transformation should be relative to the object being duplicated.
        # Remove its component from the instance's world position.
        instance.tm         = inst.matrix_world
        # Pass the name of the exported node, for geometry nodes this will result in one Instancer2
        # instancing multiple different nodes. But we will still have one Instancer2 per scene object.
        instance.nodePlugin = objectName

        if instancerObj.instance_type == 'COLLECTION':
            instancerObj = instancerObj.instance_collection

        idInstancer = mmh3.hash(f"{id(instancerObj.original)}{id(instancedObj.original)}")
        instancer = self.instancers.setdefault(idInstancer, Instancer(inst))
        instancer.append(instance)

    def export(self):
        # Export an Instancer2 plugin + a wrapper node for it
        for instancer in self.instancers.values():
            vray.pluginCreate(self.renderer, instancer.name, 'Instancer2')
            vray.exportInstancer(self.renderer, instancer.toData())
            
            # Track the Instancer2 plugin in both the instancer and the instanced_object
            self.instTracker.trackPlugin(getObjTrackId(instancer.instancer), instancer.name)
            self.objTracker.trackPlugin(getObjTrackId(instancer.obj), instancer.name)
            
            nodePlugin = exportNodePlugin(self, instancer.instancer, AttrPlugin(instancer.name), instancer.name, True)
            self.objTracker.trackPlugin(getObjTrackId(instancer.instancer), nodePlugin.name) 
            
            # In addition to tracking the Node plugin in the instancer, track it in the instanced object as well
            # so that deleting the object would delete the node
            self.instTracker.trackPlugin(getObjTrackId(instancer.obj), nodePlugin.name)

    @staticmethod
    def pruneInstances(prevActiveInstancers, exporterCtx: ExporterContext):
        """ Remove plugins associated with instanced objects.

            @param prevActiveInstancers - a set of track object IDs, snapshot of the state before the current export
            @param exporterCtx - the exporter context of the current export 
        """
        instTracker = exporterCtx.objTrackers['INSTANCER']
        for objTrackId in prevActiveInstancers.difference(exporterCtx.activeInstancers):
            for pluginName in instTracker.getOwnedPlugins(objTrackId):
                vray.pluginRemove(exporterCtx.renderer, pluginName)
                trackerLog(f"REMOVE: {objTrackId} => {pluginName}")
            instTracker.forget(objTrackId) 

