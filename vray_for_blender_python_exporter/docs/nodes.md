# Custom V-Ray nodes

## V-Ray node classes

1. Node classes corresponding to V-Ray plugins 
These nodes classes are instantiated from the JSON description of the plugin. Exceptions are listed in plugins/skipped_plugins.py. The code for node class registration can be found in nodes/nodes.py (start from the createDynamicNodeClass() function). The node's 'vray_plugin' property will hold the type (ID) of the V-Ray plugin it represents. The data for the plugin properties is stored in a property with the same name as the name of the plugin, e.g.

node.vray_plugin == 'TexChecker'
node.TexChecker == {plugin data}

2. Utility node classes

We need some utility nodes which do not directly correspond to a V-Ray plugin. They are created in code (mostly in nodes/specials and nodes/meta folders). They have their 'vray_plugin' property set to 'NONE'.


## Custom node function overrides

Some events in the node's lifecycle are exposed as callbacks that can be handled in node's plugin module.


`def nodeInit(node: bpy.types.Node)`

    Custom handler for Node.init() handler

`def nodeFree(node: bpy.types.Node)`

    Custom handler for Node.free() handler

`def nodeCopy(dest: bpy.types.Node, src: bpy.types.Node)`

    Custom handler for Node.copy() handler

`def nodeInsertLink(link: bpy.types.NodeLink)`

    Called in the plugin module of the to_node when a new link is created to an input socket.

`def nodeDraw(context: bpy.types.Context, layout: bpy.types.UILayout, node: bpy.types.Node)`

    Custom handler for Node.draw_buttons() (the non-socket properties drawn on a node)

`def nodeDrawSide(context: bpy.types.Context, layout: bpy.types.UILayout, node: bpy.types.Node)`

    Custom handler for Node.draw_buttons_ext() (node properties on a property page)

`def exportTreeNode(node: bpy.types.Node)`

    Override the whole export of a node, including any incoming links.
    NOTE: Must either return node_export.exportPluginWithStats(nodeCtx, pluginDesc) or handle plugin tracking itself.

`def resolveInternalLink(node: bpy.types.Node, outSock: bpy.types.NodeSocket)`
    Called on muted nodes to resolve an internal link from outSock.