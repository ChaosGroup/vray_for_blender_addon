import bpy

############################################################################
#  Mix-ins for adding base functionality to custom registered classes.
#  This is the preferred approach because using registered base classes
#  might sometimes interfere with the registration process.
############################################################################

class VRayEntity:
    
    # The unique_id and object_ptr properties are used in tandem to implement Blender object 
    # identification. In Python API, the objects are referred to using their names. However the
    # names can be change, either from code or from the UI. There is no way built into Blender 
    # for identifying renamed objects. The only thing we can work with is the state of the scene 
    # before and after the change. The granularity of the scene updates is not enough to tell which
    # names have changed, e.g. we can have two objects with swapped names in a singel update
    

    # A human-readable unique object name. It is set at specific points in our code, 
    # prior to any exports that the object is part of.
    unique_id: bpy.props.StringProperty(
        name = "V-Ray Unique ID",
        description = "Unique ID",
        default = "",
        options = {'SKIP_SAVE'}
    )

    # The position in memory of the 'vray' Blender object property. It is set together
    # with unique_id and can be used to tell whether the object with non-empty unique_id is a
    # copy of another object for which we need to generate a new unique_id.
    object_ptr : bpy.props.StringProperty(
        name = "V-Ray Object Ptr",
        description = "The pointer in memory to the current object. Used to track newly created objects.",
        default = "",
        options = {'SKIP_SAVE', 'HIDDEN'}
    )

    # Unique id that remains the same no matter of the changes of the scene.
    # It has to be explicitly assigned using assignStaticId().
    static_id: bpy.props.IntProperty(
        name = "V-Ray Static ID",
        description = "Static ID",
        min     = 0,
        default = -1 # -1 means that the id is unset
    )

    def isNewlyAdded(self, objectPtr=None):
        """ Return True if the object's unique_id has not been set yet """
        return str(objectPtr if objectPtr else self.as_pointer()) != self.object_ptr
    
    def setUniqueId(self, id: str, objectPtr=None):
        self.unique_id = id
        
        # Save the pointer to self or to provided pointer 'objectPtr'(used for bpy.types.Object,
        # because obj.vray.as_pointer() and obj.as_pointer() return different values).
        # This will definitely change if the object gets copied, so we
        # can unambiguously identify copies
        self.object_ptr = str(objectPtr if objectPtr else self.as_pointer())

    def assignStaticId(self):
        # Initialization of VRayEntity.static_id
        self.static_id = bpy.context.scene.vray.StaticIdCounter
        bpy.context.scene.vray.StaticIdCounter += 1


class VRayDirtyState:
    """ This class is an alternative to the Blender depsgraph updates mechanism 
        for classes that do not currently get update notifications through 
        the depsgraph, e.g. custom node trees 
    """

    # Mark the object as having been updated
    is_dirty: bpy.props.BoolProperty(
        name = "V-Ray Dirty Tag",
        description = "V-Ray dirty Tag",
        default = False,
        options = {'SKIP_SAVE'}
    )


class VRayNodeBase(VRayEntity, bpy.types.Node):
    """ Base class for all V-Ray nodes """
    bl_width_default = 230

    def insert_link(node: bpy.types.Node, link: bpy.types.NodeLink):
        from vray_blender.nodes.nodes import vrayNodeInsertLink
        vrayNodeInsertLink(node, link)