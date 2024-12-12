import bpy
from vray_blender import plugins 
from vray_blender.exporting.update_tracker import UpdateTracker
from vray_blender.lib import draw_utils
from vray_blender.ui import classes

from vray_blender.nodes.mixin import VRayNodeBase
from vray_blender.nodes import utils as NodeUtils
from vray_blender.nodes import sockets as SocketUtils

def _getMappingPluginType(self):
    match self.mapping_node_type:
        case 'UV':
            return 'UVWGenMayaPlace2dTexture'
        case 'PROJECTION':
            return 'UVWGenProjection'
        case 'OBJECT':
            return 'UVWGenObject'
        case 'ENVIRONMENT':
            return 'UVWGenEnvironment'
    
    assert False, f"Invalid UVW mapping type: {self.mapping_node_type}"
    

def _addMappingInputSockets(self):
    
    mappingPluginType = _getMappingPluginType(self)
    if mappingPluginType:
        NodeUtils.addInputs(self, plugins.PLUGIN_MODULES[mappingPluginType])
    else:
        SocketUtils.addInput(self, 'VRaySocketCoords', "Mapping")


def _mappingTypeUpdate(self: bpy.types.Node, context):
    
    for sock in self.inputs:
        self.inputs.remove(sock)
    
    # Order is important. Set plugin type first, as sockets will be added for the currently    
    _addMappingInputSockets(self)
    self.vray_plugin = _getMappingPluginType(self)
    
    # Tag the parent nodetree as updated, because the effective topology of the tree has changed
    # when the node's underlying plugin has changed.
    if mtl := NodeUtils.getMaterialFromNode(self):
        # Tag material node tree update
        UpdateTracker.tagMtlTopology(context, mtl)
    else:
        # Tag non-material node tree update
        self.id_data.update_tag()



class VRayNodeUVWMapping(VRayNodeBase):
    bl_idname = 'VRayNodeUVWMapping'
    bl_label  = 'V-Ray UWV Mapping'

    vray_type  : bpy.props.StringProperty(default='UVWGEN')
    vray_plugin: bpy.props.StringProperty(default='UVWGenMayaPlace2dTexture')
    mapping_node_type: bpy.props.EnumProperty(
        items = (
            ('UV',         "UV",         "UV mapping"),
            ('PROJECTION', "Projection", "Generated mapping"),
            ('OBJECT',     "Object",     "Object mapping"),
            ('ENVIRONMENT',"Environment","Environment mapping")
        ),
        update = _mappingTypeUpdate,
        default = 'UV'
    )
    
    def init(self, context):
        SocketUtils.addOutput(self, 'VRaySocketCoords', "Mapping", 'uvwgen')
        _addMappingInputSockets(self)


    def draw_buttons(self, context, layout):
        box = layout.column()
        isOnlyRandomizerConnected = self._isOnlyRandomizerConnected()
        
        if not isOnlyRandomizerConnected:
            box.row().prop(self, 'mapping_node_type', expand=True)
            box.separator()
        
        mappingPluginType = _getMappingPluginType(self)
        mapPluginDesc = plugins.getPluginModule(mappingPluginType)

        if hasattr(mapPluginDesc, 'nodeDraw'):
            mapPluginDesc.nodeDraw(context, box, self)
        elif nodeWidgets := mapPluginDesc.Node.get('widgets'):
            mapPropGroup = getattr(self, mappingPluginType)
            uiPainter = draw_utils.UIPainter(context, mapPluginDesc, mapPropGroup, node = self)
            uiPainter.renderWidgets(box, nodeWidgets)


    def draw_buttons_ext(self, context, layout):
        box = layout.column()
        isOnlyRandomizerConnected = self._isOnlyRandomizerConnected()
        
        if not isOnlyRandomizerConnected:
            box.row().prop(self, 'mapping_node_type', expand=True)
            box.separator()
        
        mappingPluginType = _getMappingPluginType(self)
        mapPluginDesc = plugins.getPluginModule(mappingPluginType)

        if hasattr(mapPluginDesc, 'nodeDraw'):
            mapPluginDesc.nodeDraw(context, layout, self)
        
        classes.drawPluginUI(
            context,
            layout,
            getattr(self, mappingPluginType),
            mapPluginDesc,
            self
        )


    def update(self):
        # Called on node tree topology update
        if self._isOnlyRandomizerConnected() and (self.mapping_node_type != 'UV'):
            self.mapping_node_type = 'UV'
       

    def _isOnlyRandomizerConnected(self):
        # Return True if all links of the otput socket are to UVWGenRandomizer nodes
        outSock = self.outputs[0]
        randomizerLinks = len([l for l in outSock.links if l.to_socket.node.bl_idname == 'VRayNodeUVWGenRandomizer'])

        return outSock.links and (len(outSock.links) == randomizerLinks)
       

def register():
    for pluginType in {
                     'UVWGenMayaPlace2dTexture',
                     'UVWGenObject',
                     'UVWGenEnvironment',
                     'UVWGenProjection'}:
        pluginDesc = plugins.PLUGIN_MODULES[pluginType]

        plugins.addAttributes(pluginDesc, VRayNodeUVWMapping)

    bpy.utils.register_class(VRayNodeUVWMapping)


def unregister():
    bpy.utils.unregister_class(VRayNodeUVWMapping)

