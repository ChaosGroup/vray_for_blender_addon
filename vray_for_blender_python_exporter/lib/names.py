from __future__ import annotations
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    # Import only valid for the type-check pass
    from vray_blender.lib.defs import NodeContext

import bpy
import re
from threading import Lock

from vray_blender import debug 

"""
 VRAY EXPORTED PLUGIN NAMING RULES

 Plugins in VRay must have per-scene unique names. However, there is no 1-to-1 correspondence between
 Blender object names and VRay plugin names. What is more, the symbol set accepted for Blender names
 is larger than the one accepted for VRay plugin names. This is why a naming scheme that guarantees
 unique plugin names is necessary.
 
 For each exported Blender object, we are generating a unique name in a pre-pass to the export procedure.
 The names are generated once per Blender session and stored in volatile properties on the objects themselves.
 The names of the Blender objects are converted to VRay compatible symbol set. The uniqueness is guaranteed 
 by disabling the '@' symbol in the converted names and using it to construct a suffix with a unique number
 per scene. The resulting unique name looks like 'ObjectName@123', where 123 is the unique number.
 
 Plugin names are constructed out of one or more such unique object names according to the rules below. 

 1.    NODE TREE NAMES

 Entity                | Naming template                           | Example plugin name
 -------------------------------------------------------------------------
 root object           | objectType_objName                        | mtl_MyMaterial@12, mesh_Cube@13
 blender node          | ntreeObject|blenderNodeName::output_socket| mtl_MyMaterial@12|VRayMtl@35::color
 virtual node          | ntreeObject|virtualNodeName_#             | mtl_MyMaterial@12|TexCombineColor_001

  Deeper levels follow recursively the same rules, each level being separated by a '|' 
   
 NOTES:
  - Root object corresponds to the data object the node tree is attached to. Currently, each node tree can 
        have only one output node, so the name of the root object is used for the output node of the tree.  
  - Blender nodes are the nodes present in the node tree. 
  - Virtual nodes are vray plugins that the exporter creates behind the scenes. 
     The uniqueness of their names in the node tree is guaranteed by the counter suffix.
      These can be:
        - converters (like TexCombineColor) 
        - multuple plugins that correspond to the same node (TexBitmap + BitmapBuffer). In this case, the output' plugin of the group - that is, the one that holds the output socket connected to the previous node in he tree, is designated as main and gets the blenderNode name. All the other plugins are considered virtual odes and use the virtual node naming. 


 2.   OBJECT NAMES
 
 The examples below are based on the following scene: 
   Object 'Cube'
       |-- mesh 'CubeMesh'
   Object 'Circle'
   Instancer object for 'Cube' -> Circle ( e.g. cubes positioned at the vertices of a circle )
 Object type       | Naming template              | Example plugin name
 -------------------------------------------------------------------------
 Object            | objectName                            | Cube@15, Circle@17  
 Data object       | dataObjectName                        | mesh_CubeMesh@21
 Modified object   | dataObjectName|Modifier               | mesh_CubeMesh@21|ParticleSystem   ( for modifiers hat add geometry, like particle systems)   
 VRay node         | node@objectName                       | node@Cube@15
 Instanced         | instancer@instancerName|vrayNodeName  | instancer@Circle@17|node@Cube@15

 Deeper levels follow recursively the same rules, each level being separated by a '|' 

 NOTES:
   - In Blender, object names are unique. Data object names are unique per object type.
   - VRay nodes use the Object rather than data object (ID) name to reference objects. This is
       because the same data object may be referenced by multiple objects. This translates to 
       e.g. a geometry object referenced by multiple Node plugins in VRay
   - Instancers can be nested, in which case an instancer may reference another instancer wrapped 
   in a VRay Node
"""


class Names:

    @staticmethod
    def object(obj: bpy.types.ID):
        """ Return the unique name for an object """
        # Use the original object, not the evaluated one, because the unique ID 
        # has only been set on the original
        return obj.original.vray.unique_id

    @staticmethod
    def struct(obj: bpy.types.Struct):
        """ Return the unique name for an object that is not a subclass of bpy.types.ID, e.g. bpy.types.Node. """
        if not hasattr(obj, "unique_id"):
            raise Exception(f"Attempt to export a non-vray object: {obj.bl_idname}::{obj.name}")
        return obj.unique_id 
    

    @staticmethod
    def node(obj: bpy.types.Node):
        """ Return the unique name of a node. """
        return Names.struct(obj) 

    @staticmethod
    def objectData(obj: bpy.types.Object):
        """ Return the unique name of the .data member of a bpy.types.Object. """
        assert isinstance(obj, bpy.types.Object), "Only concrete data objects accepted." 
        return Names.object(obj.original.data) 
    

    @staticmethod
    def treeNode(nodeCtx: NodeContext):
        """ Return the unique name of a tree node. """
        # For nodetrees that are not attached to a particular scene object - Scene and World -
        # use the nodetree name as root for the node unique name. This will work because there
        # could ever be just 1 node tree of each type.
        if nodeCtx.rootObj:
            ntreeRootName = Names.object(nodeCtx.rootObj)
        elif hasattr(nodeCtx.ntree, 'vray'):
            ntreeRootName = f"{ABBR_NODETREE_NAMES[nodeCtx.ntree.vray.tree_type]}_{nodeCtx.ntree.name}"
        else:
            debug.printError(f"Name requested for non-vray node tree: {nodeCtx.ntree.name}")
                             
        return f"{ntreeRootName}|{Names.struct(nodeCtx.node)}"
        

    @staticmethod
    def nextVirtualNode(nodeCtx: NodeContext, pluginType: str):
        """ Construct a virtual node name and increment the virtual nodes counter in node context. """ 
        nodeCtx.virtualNodes += 1
        virtualNodeName = f"{_changeFirstLetterToLower(pluginType)}_{nodeCtx.virtualNodes}"
        return f"{Names.object(nodeCtx.rootObj)}|{virtualNodeName}"
    
    @staticmethod
    def nextFromCounter(counter: NamedCounter, pluginType: str):
        """ Construct a node name from a named counter. """ 
        virtualNodeName = f"{_changeFirstLetterToLower(pluginType)}_{counter.next()}"
        return f"{counter.name}|{virtualNodeName}"
    
    @staticmethod
    def pluginObject(pluginType: str, *objNames):
        """ Return the name of a plugin which is not exported as a part of a nodeteree but is 
            refernced by a scene object.
        """
        objName = '@'.join(objNames)
        return f"{_changeFirstLetterToLower(pluginType)}@{objName}" 


    # VRAY PER-PLUGIN TYPE NAMES
    @staticmethod
    def vrayNode(geomPluginName: str):
        """ Return the name of the Node plugin for the object. """
        return f"node@{geomPluginName}"


    @staticmethod
    def instancer(inst: bpy.types.DepsgraphObjectInstance):
        if inst.parent.instance_type == 'COLLECTION':
            instancerObj = inst.parent.instance_collection
        else:
            instancerObj = inst.parent
        
        instancedObj = inst.object

        try:
            name =  f"instancer@{Names.object(instancerObj)}|{Names.vrayNode(Names.object(instancedObj))}"
        except Exception as ex:
            debug.printError(f"Failed to construct name for instancer object {instancerObj.name}")
            raise ex

        return name
    
    
    @staticmethod 
    def singletonPlugin(pluginType):
        # Sometimes VRay has issues with plugin names that are the same as the plugin type, 
        # regardless of the letter case. We use an underscore here to avoid this situation.
        return f"_{_changeFirstLetterToLower(pluginType)}"
    
        

#########################  Scene obects ID  generator ##################
class IdGenerator:
    """ Thread safe generator for unique IDs. """
    _id = 0
    _lock = Lock()
    
    @staticmethod
    def getNext():
        """ Return the next unique id in the sequence """
        with IdGenerator._lock:
            IdGenerator._id += 1
            return IdGenerator._id


    @staticmethod
    def reset():
        """ Reset unique sequence """
        with IdGenerator._lock:
           IdGenerator._id = 0



# Abbreviated names for some of the Blender's ID data types. For the types that are not 
# listed here, the original type name is used
ABBR_DATA_TYPES = {
    bpy.types.Camera        : 'cam',
    bpy.types.Collection    : 'coll',
    bpy.types.VectorFont    : 'font',
    bpy.types.Material      : 'mtl',
    bpy.types.PointCloud    : 'ptcloud',
}

ABBR_NODETREE_NAMES = {
    'WORLD': 'world'
}

_reChars = re.compile(r'[^a-zA-Z0-9\|\@]')

def _replaceSpecialChars(string):
        # Convert to charset conforming to VRay plugin name rules. Replace '|' and '@' symbols
        # as well, because we give them special meaning in the constructed names
        return _reChars.sub('_', string)

def _createUniqueName(name: str):
    return f"{_replaceSpecialChars(name)}@{IdGenerator.getNext()}"


def _createTypedUniqueName(type: str, name: str):
    return f"{type}_{_replaceSpecialChars(name)}@{IdGenerator.getNext()}"


def _getDataTypeName(dataObj: bpy.types.ID):
        shortName = ABBR_DATA_TYPES.get(type(dataObj))
        return shortName or type(dataObj).__name__.lower()


def _syncUniqueNamesForColl(coll, reset: bool):
    for item in coll:
        syncObjectUniqueName(item, reset)


def _changeFirstLetterToLower(name: str):
    if name[0].isupper():
        return f"{name[0].lower()}{name[1:]}"
    else:
        return name


def syncUniqueNamesForPreview(dg: bpy.types.Depsgraph):
    """ Set unique names for the objects of a preview scene. 
        Preview objects can only be accessed through the depsgraph, hence the
        need for a separate function.
    """

    for id in [ id for id in dg.ids if hasattr(id, 'vray')]:
        syncObjectUniqueName(id.original, reset=False)

        if isinstance(id, bpy.types.Object):
            # Walk material node trees and set unique ids to the nodes
            for slot in [s for s in id.original.material_slots if hasattr(s.material.node_tree, 'nodes')]:
                for node in slot.material.node_tree.nodes:
                    syncObjectUniqueName(node, reset=False)


def syncUniqueNames(reset: bool = False):
    """ Assign unique IDs to all exportable entities in the scene.

        Names assigned to objects by Blender cannot be used to construct unique names 
        for the VRay plugins because 
            a) the supported symbol set in VRay is narrower and 
            b) Blender names are not immutable 
        
        This function will assign a unique id to the 'vray' property group of each 
        exportable entity in the scene.

        @param reset - force overwrite previous values
    """
    # Objects and data objects
    for obj in bpy.data.objects:
        syncObjectUniqueName(obj, reset)

        if hasattr(obj, "data") and obj.data and hasattr(obj.data, "vray") and obj.data.vray:
            syncObjectUniqueName(obj.data, reset)

    _syncUniqueNamesForColl(bpy.data.collections, reset)
    _syncUniqueNamesForColl(bpy.data.worlds, reset)
    _syncUniqueNamesForColl(bpy.data.materials, reset)
    _syncUniqueNamesForColl(bpy.data.metaballs, reset)



def syncObjectUniqueName(obj: bpy.types.ID | bpy.types.Node, reset: bool):
    """ Sync the uniqe name for a single object. """
    
    # Order of checking is important, because node trees are not of type VRayEntity. 
    # We need to process them before any VRayEntity-speecific fields ar checked.
    if ntree := getattr(obj, 'node_tree', None):
        syncObjectUniqueName(ntree, reset)
    
    if isinstance(obj, bpy.types.NodeTree):
        # Node tree names are not part of any plugin name, only set unique IDs for the nodes in the tree
        # NOTE: The check for the unique_id field is used to filter out obsolete node types
        # which are loaded from .blend scenes but classes for them are not defined in
        # the current implementation.
        for node in [n for n in  obj.nodes if hasattr(n, 'unique_id')]:
            syncObjectUniqueName(node, reset)
        return
        
    if isinstance(obj, bpy.types.Object):
        # When 'obj' is bpy.types.Object, obj.vray.as_pointer() and obj.as_pointer() return
        # different values. And in that case obj.vray.object_ptr
        # is set to obj.as_pointer() instead of obj.vray.as_pointer()
        if reset or obj.vray.isNewlyAdded(obj.as_pointer()):
            obj.vray.setUniqueId(_createUniqueName(obj.name), obj.as_pointer())
        return

    # VRay nodes do not have a 'vray' field, check 'vray_type' instead
    if isinstance(obj, bpy.types.Node) and hasattr(obj, 'vray_type'):
        if reset or obj.isNewlyAdded():
            uniqueId = _createUniqueName(obj.name)
            obj.setUniqueId(uniqueId)

            # If the property group is attached to a node, set the ID of the parent node 
            # to it. It will be needed to reach the node from the property group in event
            # callbacks.
            if propGroup := getattr(obj, obj.vray_plugin, None):
                propGroup.parent_node_id = uniqueId 

            # Some nodes may have more than 1 plugin property group
            for propGroupName in getattr(obj, "vray_plugins_list", []):
                getattr(obj, propGroupName).parent_node_id = uniqueId 

        return

    if isinstance(obj, bpy.types.ID):
        if reset or obj.vray.isNewlyAdded(obj.as_pointer()):
            obj.vray.setUniqueId(_createTypedUniqueName(_getDataTypeName(obj), obj.name), obj.as_pointer())



class NamedCounter:
    """ A counter wich can be used as a standard interface for counting classes of objects """
    def __init__(self, name):
        self.name = name
        self.index = 0

    def next(self):
        self.index += 1
        return self.index