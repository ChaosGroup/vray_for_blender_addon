import ctypes
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
        def __init__(self, index):
            self.index          = index
            self.tm: Matrix     = None
            self.velocity       = Matrix()
            self.velocity.zero()
            self.nodePlugin     = ""

        def id(self):
            return mmh3.hash(f"{id(self)}{str(self.index)}")

        def pack(self):
            m = marshaller.Marshaller()
            
            m.dumpInt32(self.id())
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
        self.instanceIndex    = 0
        self.exporter         = self.ctx.scene.vray.Exporter
        self.objTracker       = ctx.objTrackers['OBJ']
        self.objMtlTracker    = ctx.objTrackers['OBJ_MTL']
        self.instTracker      = ctx.objTrackers['INSTANCER']

    def addInstance(self, inst):
        instancerObj = inst.parent  # This is the object whose vertices/faces are used for the instantiation
        # NOTE: possibly flaky behaviour
        # inst.object and inst.instance_object point to the same object, but for some reason 
        # when it is accesed through inst.object, its matrix_world transform is sometimes incorrect 
        # ( is in fact equal to inst.matrix_world )
        instancedObj = inst.instance_object     # The object being instanced

        if instancedObj.type not in tools.EXPORTED_OBJECT_TYPES:
            # There are some instanced types we are not interested in, like armature or the empty
            # controller objects for instanced collections
            return                 
        
        instance = Instancer.Instance(self.instanceIndex)

        # In VRay, instance transformation should be relative to the object being duplicated.
        # Remove its component from the instance's world position.
        instance.tm         = inst.matrix_world @ instancedObj.matrix_world.inverted() 
        instance.nodePlugin = Names.vrayNode(Names.object(instancedObj))

        if self.exporter.calculate_instancer_velocity:
            # TODO: calculate
            pass

        if instancerObj.instance_type == 'COLLECTION':
            instancerObj = instancerObj.instance_collection

        # TODO: implement lazy defaultdict
        instancer = Instancer(inst)
        idInstancer = mmh3.hash(f"{id(instancerObj.original)}{id(instancedObj.original)}")
        instancer = self.instancers.setdefault(idInstancer, instancer)
        instancer.append(instance)
        
        self.instanceIndex += 1


    def export(self):
        # Export an Instancer2 plugin + a wrapper node for it
        for instancer in self.instancers.values():
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

