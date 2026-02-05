import bpy

from vray_blender.lib import class_utils
from vray_blender.lib.mixin import VRayNodeBase, VRayOperatorBase
from vray_blender.nodes.nodes import vrayNodeUpdate
from vray_blender.nodes.sockets import addInput, addOutput, VRaySocket, removeInputs, getHiddenInput
from vray_blender.nodes.utils import selectedObjectTagUpdate
from vray_blender.plugins import PLUGINS, getPluginModule
from vray_blender.ui import classes


 ######   #######   ######  ##    ## ######## ########  ######
##    ## ##     ## ##    ## ##   ##  ##          ##    ##    ##
##       ##     ## ##       ##  ##   ##          ##    ##
 ######  ##     ## ##       #####    ######      ##     ######
      ## ##     ## ##       ##  ##   ##          ##          ##
##    ## ##     ## ##    ## ##   ##  ##          ##    ##    ##
 ######   #######   ######  ##    ## ########    ##     ######

class VRaySocketTexLayeredBlendMode(VRaySocket):
    bl_idname = 'VRaySocketTexLayeredBlendMode'
    bl_label  = 'Blend Mode'

    value: bpy.props.EnumProperty(
        name = "Blend Mode",
        description = "Blend mode",
        items = (
            ("0", "Normal", "Normal"),
            ("1", "Average", "Average"),
            ("2", "Add", "Add"),
            ("3", "Subtract", "Subtract"),
            ("4", "Darken", "Darken"),
            ("5", "Multiply", "Multiply"),
            ("6", "Color burn", "Color burn"),
            ("7", "Linear burn", "Linear burn"),
            ("8", "Lighten", "Lighten"),
            ("9", "Screen", "Screen"),
            ("10", "Color Dodge", "Color Dodge"),
            ("11", "Linear Dodge", "Linear Dodge"),
            ("12", "Spotlight", "Spotlight"),
            ("13", "Spotlight blend", "Spotlight blend"),
            ("14", "Overlay", "Overlay"),
            ("15", "Softlight", "Softlight"),
            ("16", "Hardlight", "Hardlight"),
            ("17", "Pinlight", "Pinlight"),
            ("18", "Hardmix", "Hardmix"),
            ("19", "Difference", "Difference"),
            ("20", "Exclusion", "Exclusion"),
            ("21", "Hue", "Hue"),
            ("22", "Saturation", "Saturation"),
            ("23", "Color", "Color"),
            ("24", "Value", "Value")
        ),
        default = '0',
        update = selectedObjectTagUpdate
    )

    def draw(self, context, layout, node, text):
        row = layout.row(align=True)
        row.prop(self, 'value', text="")


class VRaySocketTexLayeredOpacity(VRaySocket):
    """ A Float socket with limits 0..1 """

    bl_idname = 'VRaySocketTexLayeredOpacity'
    bl_label  = 'Opacity'

    value: bpy.props.FloatProperty(
        name = "Value",
        description = "Value",
        precision = 3,
        min = 0.0,
        max = 1.0,
        soft_min = 0.0,
        soft_max =  1.0,
        default = 1.0,
        update = selectedObjectTagUpdate
    )

    def draw(self, context, layout, node, text):
        row = layout.row(align=True)
        row.prop(self, 'value', text="")


 #######  ########  ######## ########     ###    ########  #######  ########   ######
##     ## ##     ## ##       ##     ##   ## ##      ##    ##     ## ##     ## ##    ##
##     ## ##     ## ##       ##     ##  ##   ##     ##    ##     ## ##     ## ##
##     ## ########  ######   ########  ##     ##    ##    ##     ## ########   ######
##     ## ##        ##       ##   ##   #########    ##    ##     ## ##   ##         ##
##     ## ##        ##       ##    ##  ##     ##    ##    ##     ## ##    ##  ##    ##
 #######  ##        ######## ##     ## ##     ##    ##     #######  ##     ##  ######

class VRAY_OT_node_texlayered_layer_add(VRayOperatorBase):
    bl_idname      = 'vray.node_texlayered_layer_add'
    bl_label       = "Add Texture Layer"
    bl_description = "Adds Texture Layer to V-Ray Layered texture"
    bl_options     = {'INTERNAL', 'UNDO'}

    def execute(self, context):
        node = context.node

        humanIndex = node.layers + 1
        VRayNodeTexLayeredMax.addLayer(node, humanIndex)

        return {'FINISHED'}


class VRAY_OT_node_texlayered_layer_del(VRayOperatorBase):
    bl_idname      = 'vray.node_texlayered_layer_del'
    bl_label       = "Remove Texture Layer"
    bl_description = "Removes Texture layer from V-Ray Layered texture"
    bl_options     = {'INTERNAL', 'UNDO'}

    def execute(self, context):
        node = context.node

        if node.layers < 2:
            # Do not allow the user to remove the last remaining layer as
            # the node would cease to be functional.
            self.report({'WARNING'}, "Cannot remove the last texture layer.")
            return {'CANCELLED'}

        humanIndex = node.layers

        sockNameTexture = f"Texture {humanIndex}"
        sockNameMask = f"Mask {humanIndex}"

        if removeInputs(node, [sockNameTexture, sockNameMask], removeLinked=False):
            node.layers -= 1
            return {'FINISHED'}

        self.report({'WARNING'}, "Cannot remove linked layers. Unlink and try again")
        return {'CANCELLED'}


######## ######## ##     ##    ##          ###    ##    ## ######## ########  ######## ########  
   ##    ##        ##   ##     ##         ## ##    ##  ##  ##       ##     ## ##       ##     ## 
   ##    ##         ## ##      ##        ##   ##    ####   ##       ##     ## ##       ##     ## 
   ##    ######      ###       ##       ##     ##    ##    ######   ########  ######   ##     ## 
   ##    ##         ## ##      ##       #########    ##    ##       ##   ##   ##       ##     ## 
   ##    ##        ##   ##     ##       ##     ##    ##    ##       ##    ##  ##       ##     ## 
   ##    ######## ##     ##    ######## ##     ##    ##    ######## ##     ## ######## ########  

class VRayNodeTexLayeredMax(VRayNodeBase):
    """ Custom node representation for the TexLayeredMax plugin """

    bl_idname = 'VRayNodeTexLayeredMax'
    bl_label  = 'V-Ray Layered'
    bl_icon   = 'TEXTURE'

    vray_type   = 'TEXTURE'
    vray_plugin = 'TexLayeredMax'

    layers: bpy.props.IntProperty()

    @classmethod
    def poll(cls, context):
        return cls.bl_idname.startswith('VRayNode') and bpy.context.engine == 'VRAY_RENDER_RT'

    def init(self, context):
        for i in range(2):
            VRayNodeTexLayeredMax.addLayer(self, humanIndex = i + 1)

        addOutput(self, 'VRaySocketColor',      "Color")
        addOutput(self, 'VRaySocketFloatColor', "Out Transparency", 'out_transparency')
        addOutput(self, 'VRaySocketFloatColor', "Out Alpha",        'out_alpha')
        addOutput(self, 'VRaySocketFloatColor', "Out Intensity",    'out_intensity')


    def draw_buttons(self, context, layout):
        """ Draw node """

        split = layout.split()
        row = split.row(align=True)
        row.operator('vray.node_texlayered_layer_add', icon="ADD", text="Layers")
        row.operator('vray.node_texlayered_layer_del', icon="REMOVE", text="")


    def draw_buttons_ext(self, context, layout):
        """ Draw node sidebar """

        classes.drawPluginUI(context, layout, self.TexLayeredMax, getPluginModule('TexLayeredMax'), self)

        split = layout.split()
        row = split.row(align=True)
        row.operator('vray.node_texlayered_layer_add', icon="ADD", text="Layers")
        row.operator('vray.node_texlayered_layer_del', icon="REMOVE", text="")

        node = self

        for l in range(node.layers):
            humanIndex = l + 1
            layout.label(text=f'Layer {humanIndex}')

            split = layout.split(factor=0.1)
            split.column()

            subpanel = split.column()

            row  = subpanel.row()
            sockBlendMode = getHiddenInput(self, f'Blend Mode {humanIndex}')
            row.label(text="Blend Mode")
            sockBlendMode.draw(context, row, node, text="")

            row  = subpanel.row()
            sockOpacity = getHiddenInput(self, f'Opacity {humanIndex}')
            sockOpacity.draw(context, row, node, text="Opacity")


    def update(self):
        vrayNodeUpdate(self)

        
    @staticmethod
    def addLayer(node, humanIndex):
        """ Add the inputs for a texture layer """

        addInput(node, 'VRaySocketColor', f"Texture {humanIndex}").setValue((1.0, 1.0, 1.0))
        addInput(node, 'VRaySocketColor', f"Mask {humanIndex}").setValue((1.0, 1.0, 1.0))
        addInput(node, "VRaySocketTexLayeredOpacity", f"Opacity {humanIndex}", visible = False)
        addInput(node, "VRaySocketTexLayeredBlendMode", f"Blend Mode {humanIndex}", visible = False)

        node.layers += 1


class VRAY_OT_pack_image(VRayOperatorBase):
    bl_idname      = 'vray.pack_image'
    bl_label       = "Pack image"
    bl_description = "Packs the image into the .blend file."
    bl_options     = {'INTERNAL', 'UNDO'}

    nodeID : bpy.props.StringProperty()
    nodeTreeType : bpy.props.StringProperty()

    def execute(self, context: bpy.types.Context):
        if hasattr(context, 'active_node'):
            # Operator is called from node side bar
            node = context.active_node
        else:
            # Operator is called from material property panel

            nodes = None
            match self.nodeTreeType:
                case 'MATERIAL':
                    nodes = context.material.node_tree.nodes
                case 'WORLD':
                    nodes = context.world.node_tree.nodes
                case _:
                    # Properties of nodes from Object and Light trees are not visible in the property panel,
                    # so there is no need to handle the case where "self.nodeTreeType in ('OBJECT', 'LIGHT')"
                    assert False, f"Calling the operator 'vray.pack_image' from the property panel is unsupported for '{self.nodeTreeType}' nodes"


            # The check for 'unique_id' ensures we only process V-Ray nodes.
            node = next((n for n in nodes if getattr(n, "unique_id", None) == self.nodeID), None)

        if node and (image := node.texture.image):
            if not image.packed_file:
                node.texture.image.pack()

        return {'FINISHED'}


########  ########  ######   ####  ######  ######## ########     ###    ######## ####  #######  ##    ##
##     ## ##       ##    ##   ##  ##    ##    ##    ##     ##   ## ##      ##     ##  ##     ## ###   ##
##     ## ##       ##         ##  ##          ##    ##     ##  ##   ##     ##     ##  ##     ## ####  ##
########  ######   ##   ####  ##   ######     ##    ########  ##     ##    ##     ##  ##     ## ## ## ##
##   ##   ##       ##    ##   ##        ##    ##    ##   ##   #########    ##     ##  ##     ## ##  ####
##    ##  ##       ##    ##   ##  ##    ##    ##    ##    ##  ##     ##    ##     ##  ##     ## ##   ###
##     ## ########  ######   ####  ######     ##    ##     ## ##     ##    ##    ####  #######  ##    ##

def getRegClasses():
    return (
        VRaySocketTexLayeredBlendMode,
        VRaySocketTexLayeredOpacity,
        VRAY_OT_node_texlayered_layer_add,
        VRAY_OT_node_texlayered_layer_del,
        VRayNodeTexLayeredMax,

        VRAY_OT_pack_image
    )


def register():
    class_utils.registerPluginPropertyGroup(VRayNodeTexLayeredMax, PLUGINS['TEXTURE']['TexLayeredMax'])

    for regClass in getRegClasses():
        bpy.utils.register_class(regClass)


def unregister():
    for regClass in getRegClasses():
        bpy.utils.unregister_class(regClass)
