## How animation export works in V4B

We rely entirely on Blender to provide the correct values for the animated properties that are being exported. For each animation (sub)frame, the frame number is advanced through Blender's API at which point the scene depsgraph is automatically recalculated. The V4B add-on then sets the same frame as current in V-Ray and exports the current values of the animated properties.


There is no mechanism exposed through Blender's Python API which allows to determine whether a property had its value changed from the previous frame. The implementation of this functionality is split between the V4B add-on and ZmqServer. The add-on determines the frame ranges, per object, where object's properties are animated, so that export is done only for these ranges. ZmqServer, on the other hand, keeps track of the last frame a property value has been changed alongside a hash of the value. When a value is exported, it will only replace it in V-Ray if it has indeed changed. 

**NOTE 1**: This does not cover all types of data. Some of the properties may still be exported for each frame in the animated range even if their value has not changed.

**NOTE 2**: The current workflow has the known inefficiency that the data for the property has to be transferred to ZmqServer before it can be compared to the previously exported value. This might introduce some slowdown for large geometry data objects compared to computing the hash and comparing on the add-on side. 
To implement this in the add-on however mihght be complicated as it would require making the operation asynchronous so as not to block the exporting Python thead while computing the hash. So far no measurements of the relative performance of the two approaches has been done. We might reconsider the solution in the future if necessary.

## TRIVIA:

### Animation data locations
Animation data is stored in different locations for different classes of animated properties:

* object property changes:  object.animation_data
* geometry shape changes
    There are two sources of geometry shape changes - direct mesh edits and modifiers applied to an object. For direct edits animation data is stored in object.data.shape_keys.animation_data. The modifiers' animation data is stored in Objects.mofidiers.node_group.animation_data for geometry nodes and in Object.animation_data for the rest of the modifiers 

The same data may be accessed through bpy.data shape_keys/actions collections.

The animation data blocks can be shared between objects, this is why there are no links to the animated objects in the proper data items.

### Negative frames
Blender allows setting negative frames in the Timeline if the Preferences->Animation->Allow Negative Frames option is on. The rendered animation however cannot start at a frame less than 0. The negative frames can only be visualized in previews.
