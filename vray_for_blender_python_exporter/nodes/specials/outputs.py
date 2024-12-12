
import bpy

from vray_blender.ui import classes
from vray_blender.lib import class_utils, draw_utils
from vray_blender.nodes.mixin import VRayNodeBase
from vray_blender.nodes.sockets import addInput
from vray_blender.nodes.utils import findDataObjFromNode
from vray_blender.plugins import PLUGINS, getPluginModule

 #######  ########        ## ########  ######  ########
##     ## ##     ##       ## ##       ##    ##    ##
##     ## ##     ##       ## ##       ##          ##
##     ## ########        ## ######   ##          ##
##     ## ##     ## ##    ## ##       ##          ##
##     ## ##     ## ##    ## ##       ##    ##    ##
 #######  ########   ######  ########  ######     ##

class VRayNodeObjectOutput(VRayNodeBase):
    bl_idname = 'VRayNodeObjectOutput'
    bl_label  = 'V-Ray Object Output'
    bl_width_default  = 180
    # bl_icon   = 'VRAY_LOGO'

    vray_type   = 'NONE'
    vray_plugin = 'NONE'

    def init(self, context):
        addInput(self, 'VRaySocketGeom', "Displacement", 'geometry')
        addInput(self, 'VRaySocketGeom', "Subdivision", 'geometry')
        addInput(self, 'VRaySocketObjectProps', "Matte")
        addInput(self, 'VRaySocketObjectProps', "Surface")
        addInput(self, 'VRaySocketObjectProps', "Visibility")
    
    def _drawObjectID(self, context, layout):
        if obj := context.active_object:
            layout.use_property_split = True
            layout.prop(obj.vray.VRayObjectProperties, 'objectID')

    def draw_buttons(self, context, layout):
        self._drawObjectID(context, layout)

    def draw_buttons_ext(self, context, layout):
        self._drawObjectID(context, layout)


##     ##    ###    ######## ######## ########  ####    ###    ##
###   ###   ## ##      ##    ##       ##     ##  ##    ## ##   ##
#### ####  ##   ##     ##    ##       ##     ##  ##   ##   ##  ##
## ### ## ##     ##    ##    ######   ########   ##  ##     ## ##
##     ## #########    ##    ##       ##   ##    ##  ######### ##
##     ## ##     ##    ##    ##       ##    ##   ##  ##     ## ##
##     ## ##     ##    ##    ######## ##     ## #### ##     ## ########

            
class VRayNodeOutputMaterial(VRayNodeBase):
    bl_idname = 'VRayNodeOutputMaterial'
    bl_label  = 'V-Ray Material Output'
    bl_width_default  = 180
    # bl_icon   = 'VRAY_LOGO'

    vray_type   = 'NONE'
    vray_plugin = 'NONE'


    def _updateNode(self, context: bpy.types.Context):
        # For custom nodes, updates to their properties are not automatically tagged by Blender. 
        # The updates to sockets are processed by a dedicated hook attached when the socket is 
        # created. This property however does not have a socket, this is why we need this update hook. 
        if ob := context.active_object:
            if ma := ob.active_material:
                ma.update_tag()

    dontOverride: bpy.props.BoolProperty(
        name        = "Don't Override",
        description = "Don't override material",
        default     = False,
        update      = _updateNode
    )

    MtlMaterialID: bpy.props.PointerProperty(
        type = class_utils.TYPES['VRayMtlMaterialID']
    )

    def draw_buttons(self, context, layout):
        layout.prop(self, 'dontOverride')

    def draw_buttons_ext(self, context, layout):
        layout.prop(self, 'dontOverride')
        
        # Draw material option properties in the sidebar 
        mtl = findDataObjFromNode(bpy.data.materials, self)
        
        self._drawMaterialOption(context, layout, mtl.vray.MtlMaterialID, 'MtlMaterialID', 'Material ID')
        self._drawMaterialOption(context, layout, mtl.vray.MtlRenderStats, 'MtlRenderStats', 'Render Stats')
        self._drawMaterialOption(context, layout, mtl.vray.MtlWrapper, 'MtlWrapper', 'Wrapper')
        self._drawMaterialOption(context, layout, mtl.vray.MtlRoundEdges, 'MtlRoundEdges', 'Round Edges')


    def init(self, context):
        addInput(self, 'VRaySocketMtl', "Material")

    def _drawMaterialOption(self, context, layout, propGroup, pluginType, label):
        panelUniqueId = f'{self.as_pointer()}_{pluginType}'

        if panel := draw_utils.rollout(layout, panelUniqueId, label, usePropDataSrc=propGroup, usePropName='use'):
            
            split = panel.split(factor=0.05, align=True)
            
            enabled = propGroup.use
            split.enabled = enabled
            split.active = enabled 

            split.column()
            col = split.column()
            classes.drawPluginUI(context, col, propGroup, getPluginModule(pluginType), self)

    
########  ########  ######   ####  ######  ######## ########     ###    ######## ####  #######  ##    ##
##     ## ##       ##    ##   ##  ##    ##    ##    ##     ##   ## ##      ##     ##  ##     ## ###   ##
##     ## ##       ##         ##  ##          ##    ##     ##  ##   ##     ##     ##  ##     ## ####  ##
########  ######   ##   ####  ##   ######     ##    ########  ##     ##    ##     ##  ##     ## ## ## ##
##   ##   ##       ##    ##   ##        ##    ##    ##   ##   #########    ##     ##  ##     ## ##  ####
##    ##  ##       ##    ##   ##  ##    ##    ##    ##    ##  ##     ##    ##     ##  ##     ## ##   ###
##     ## ########  ######   ####  ######     ##    ##     ## ##     ##    ##    ####  #######  ##    ##

def getRegClasses():
    return (

        VRayNodeOutputMaterial,
        VRayNodeObjectOutput,
    )


def register():
    for regClass in getRegClasses():
        bpy.utils.register_class(regClass)


def unregister():
    for regClass in getRegClasses():
        bpy.utils.unregister_class(regClass)
