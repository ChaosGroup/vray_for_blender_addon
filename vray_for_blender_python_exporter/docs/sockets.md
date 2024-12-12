# Custom sockets for V-Ray nodes

## General description
TBD


## Socket link information
Input node sockets аccepting plugins or objects/object ilists can provide some information as to what type of data they expect to be provided by the connected socket(s) and how it should be exported. This information is stored in the _Parameters::options::link_info_ section in the plugin description file. The supported options are:
* 'link_type' - the type of data the socket accepts. Can be:
  * "OBJECTS" - Blender objects that exported as nodes
  * "OBJECT_DATA" - Blender objects that will be exported as object data (meshes, materials etc.)
* 'filter_function' - the function to use for filtering the data. This may be used e.g. by a connected selector node to show only the relevant objects to pick from.