
import bpy

from vray_blender import plugins 
from vray_blender.lib.names import syncObjectUniqueName
from vray_blender.nodes import sockets as SocketUtils, utils as NodeUtils
from vray_blender.nodes.mixin import VRayNodeBase
from vray_blender.ui import classes


class VRayNodeMetaImageTexture(VRayNodeBase):
    bl_idname = 'VRayNodeMetaImageTexture'
    bl_label  = 'V-Ray Bitmap'
    bl_icon   = 'TEXTURE'

    vray_type  : bpy.props.StringProperty(default='TEXTURE')
    vray_plugin: bpy.props.StringProperty(default='TexBitmap')
    
    vray_plugins_list = ["BitmapBuffer", "TexBitmap"]
        
    def init(self, context):
        NodeUtils.createBitmapTexture(self)

        SocketUtils.addInput(self, 'VRaySocketCoords', "Mapping")
        NodeUtils.addOutputs(self, plugins.PLUGIN_MODULES['TexBitmap'])
        
        syncObjectUniqueName(self, reset=True)


    def copy(self, node):
        tex = NodeUtils.createBitmapTexture(self)
        
        def _reattachTexture():
            self.texture = tex

        
        # Set the same image file
        if node.texture.image:
            self.texture.image = node.texture.image

        # For some reason Blender will reset the 'texture' property of the node.
        # Schedule a reattach.
        bpy.app.timers.register(_reattachTexture)
        
        syncObjectUniqueName(self, reset=True)


    def draw_buttons(self, context, layout):
        box = layout.box()
        bitmapPluginDesc = plugins.PLUGIN_MODULES['BitmapBuffer']
        bitmapPluginDesc.nodeDraw(context, box, self)

    def draw_buttons_ext(self, context, layout):
        bitmapPluginDesc = plugins.PLUGIN_MODULES['BitmapBuffer']
        classes.drawPluginUI(
            context,
            layout,
            self.BitmapBuffer,
            bitmapPluginDesc,
            self
        )

        texPluginDesc = plugins.PLUGIN_MODULES['TexBitmap']
        classes.drawPluginUI(
            context,
            layout,
            self.TexBitmap,
            texPluginDesc,
            self
        )

   
def register():
    for pluginType in VRayNodeMetaImageTexture.vray_plugins_list:
        pluginDesc = plugins.PLUGIN_MODULES[pluginType]

        plugins.addAttributes(pluginDesc, VRayNodeMetaImageTexture)

    NodeUtils.createFakeTextureAttribute(VRayNodeMetaImageTexture)

    bpy.utils.register_class(VRayNodeMetaImageTexture)


def unregister():
    bpy.utils.unregister_class(VRayNodeMetaImageTexture)
