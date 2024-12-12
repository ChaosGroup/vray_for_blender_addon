import bpy

from vray_blender import plugins
from vray_blender.lib.names import syncObjectUniqueName
from vray_blender.nodes import utils as NodeUtils
from vray_blender.nodes.mixin import VRayNodeBase
from vray_blender.nodes.nodes import vrayNodeUpdate
from vray_blender.nodes.sockets import addInput, addOutput
from vray_blender.ui import classes

##    ##  #######  ########  ########  ######
###   ## ##     ## ##     ## ##       ##    ##
####  ## ##     ## ##     ## ##       ##
## ## ## ##     ## ##     ## ######    ######
##  #### ##     ## ##     ## ##             ##
##   ### ##     ## ##     ## ##       ##    ##
##    ##  #######  ########  ########  ######

class VRayNodeDisplacement(VRayNodeBase):
    bl_idname = 'VRayNodeDisplacement'
    bl_label  = 'V-Ray Displacement'
    bl_icon   = 'SHADING_TEXTURE'

    vray_type  : bpy.props.StringProperty(default='GEOMETRY')
    vray_plugin: bpy.props.StringProperty(default='GeomDisplacedMesh')

    def init(self, context):
        # Create a new unique ID for the node and set the link to it in the property group
        syncObjectUniqueName(self, reset=True)
        addOutput(self, 'VRaySocketGeom', "Displacement")
        NodeUtils.addInputs(self, plugins.PLUGIN_MODULES['GeomDisplacedMesh'])
        
        addInput(self, 'VRaySocketFloatColor', "Texture")
    
    def draw_buttons(self, context, layout):
        layout.row().prop(self.GeomDisplacedMesh, 'type', text="Type")
    
    def draw_buttons_ext(self, context, layout):
        geomDisplacedMeshDesc = plugins.PLUGIN_MODULES['GeomDisplacedMesh']
        classes.drawPluginUI(
            context,
            layout,
            self.GeomDisplacedMesh,
            geomDisplacedMeshDesc,
            self
        )

    def update(self):
        vrayNodeUpdate(self)


########  ########  ######   ####  ######  ######## ########     ###    ######## ####  #######  ##    ##
##     ## ##       ##    ##   ##  ##    ##    ##    ##     ##   ## ##      ##     ##  ##     ## ###   ##
##     ## ##       ##         ##  ##          ##    ##     ##  ##   ##     ##     ##  ##     ## ####  ##
########  ######   ##   ####  ##   ######     ##    ########  ##     ##    ##     ##  ##     ## ## ## ##
##   ##   ##       ##    ##   ##        ##    ##    ##   ##   #########    ##     ##  ##     ## ##  ####
##    ##  ##       ##    ##   ##  ##    ##    ##    ##    ##  ##     ##    ##     ##  ##     ## ##   ###
##     ## ########  ######   ####  ######     ##    ##     ## ##     ##    ##    ####  #######  ##    ##
def register():
    
    plugins.addAttributes(plugins.PLUGIN_MODULES["GeomDisplacedMesh"], VRayNodeDisplacement)
    bpy.utils.register_class(VRayNodeDisplacement)

def unregister():
    bpy.utils.unregister_class(VRayNodeDisplacement)
    