# Custom node function overrides

Some events in node lifecycle are exposed as callbacks that can be handled in node's plugin module.


def nodeInit(node: bpy.types.Node)
    Custom handler for Node.init() handler

def nodeFree(node: bpy.types.Node)
    Custom handler for Node.free() handler

def nodeCopy(dest: bpy.types.Node, src: bpy.types.Node)
    Custom handler for Node.copy() handler

def nodeInsertLink(link: bpy.types.NodeLink)
    Called in the plugin module of the to_node when a new link is created to an input socket.

def nodeDraw(context: bpy.types.Context, layout: bpy.types.UILayout, node: bpy.types.Node)
    Custom handler for Node.draw_buttons()

def nodeDrawSide(context: bpy.types.Context, layout: bpy.types.UILayout, node: bpy.types.Node)
    Custom handler for Node.draw_buttons_ext()


def exportTreeNode(node: bpy.types.Node)
    Override the whole export of a node. 
    NOTE: Must return commonNodesExport.exportPluginWithStats(nodeCtx, pluginDesc)