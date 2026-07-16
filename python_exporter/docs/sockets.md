# Custom sockets for V-Ray nodes

## General description

Most of the V-Ray socket classes are defined in the nodes.sockets module. There are also some plugin-specific socket classes defined in the respective plugin module.

We use 2 categories of sockets classes in V-Ray nodes - automatically created from the JSON plugin descriptions, and 'manually' defined in code.

The classes for the first category are dynamically defined and registered in lib.attribute_utiils. A separate class is registered for each plugin parameter even if the underlying data type is the same because some of socket properties, e.g. min/max and default value, are part of the socket's RNA. A socket is created for each non-excluded property of the V-Ray plugin, with only some of the sockets actually visible on the node. The sockets proxy their get/set methods to a plugin-specific property group object attached to the node. This design allows us to access the property values in a uniform way regardless of whether a Blender object's properties are exposed as sockets on a node or not (see lights). Sockets backed by V-Ray properties have a valid vray_attr property which holds the name of the backing V-Ray plugin property.

## Socket names and identifiers

Sockets are uniquely identified by Blender using their 'identifier' property. They also have a 'name' property which is their display name. For legacy reasons, the V4B addon is using the 'name' property as the identifier ensuring it is always unique. Sockets backed by V-Ray plugin properties can also be identified using their 'vray_attr' property. 

The name of the socket is determined by looking at the following in turn (in the JSON plugin description file):
 - the 'label' property on the socket entry in Node.input_sockets or Node.output_sockets
 - the ui.display_name of the item in the Parameters section
 - the name of the V-Ray property (beautified)

The label of the socket that is visible on the node is always its 'name' property.


## Socket link information
Input node sockets аccepting plugins or objects/object lists can provide some information as to what type of data they expect to be provided by the connected socket(s) and how it should be exported. This information is stored in the _Parameters::options::link_info_ section in the plugin description file. The supported options are:
* 'link_type' - the type of data the socket accepts. Can be:
  * "OBJECTS" - Blender objects that exported as nodes
  * "OBJECT_DATA" - Blender objects that will be exported as object data (meshes, materials etc.)
* 'filter_function' - the function to use for filtering the data. This may be used e.g. by a connected selector node to show only the relevant objects to pick from.