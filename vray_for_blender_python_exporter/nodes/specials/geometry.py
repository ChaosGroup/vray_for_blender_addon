import bpy

from vray_blender import plugins
from vray_blender.lib import plugin_utils
from vray_blender.lib.draw_utils import UIPainter
from vray_blender.lib.names import syncObjectUniqueName
from vray_blender.nodes import utils as NodeUtils
from vray_blender.lib.mixin import VRayNodeBase
from vray_blender.nodes.nodes import vrayNodeUpdate
from vray_blender.nodes.sockets import addInput, addOutput
from vray_blender.plugins import getPluginModule
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
        addOutput(self, 'VRaySocketGeom', "Displacement", "displacement")
        NodeUtils.addInputs(self, getPluginModule('GeomDisplacedMesh'))

        addInput(self, 'VRaySocketFloatNoValue', "Displacement Texture")

        # Set the new defaults here, changing them in the description breaks old scenes.
        self.GeomDisplacedMesh.keep_continuity = True
        self.GeomDisplacedMesh.water_level = 0.0

    def draw_buttons(self, context, layout):
        pluginModule = getPluginModule('GeomDisplacedMesh')
        painter = UIPainter(context, pluginModule, self.GeomDisplacedMesh, self)
        painter.renderWidgets(layout, pluginModule.Node['widgets'], True)

    def draw_buttons_ext(self, context, layout):
        geomDisplacedMeshDesc = getPluginModule('GeomDisplacedMesh')
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
    plugins.addAttributes(getPluginModule("GeomDisplacedMesh"), VRayNodeDisplacement)
    bpy.utils.register_class(VRayNodeDisplacement)

def unregister():
    bpy.utils.unregister_class(VRayNodeDisplacement)
