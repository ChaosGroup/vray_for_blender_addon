# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later


import bpy

from vray_blender import plugins
from vray_blender.lib.names import syncObjectUniqueName
from vray_blender.nodes import sockets as SocketUtils, utils as NodeUtils
from vray_blender.nodes.nodes import vrayNodeUpdate
from vray_blender.lib.mixin import VRayNodeBase
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

        NodeUtils.addOutputs(self, plugins.PLUGIN_MODULES['TexBitmap'])
        NodeUtils.addInputs(self, plugins.PLUGIN_MODULES['BitmapBuffer'])
        NodeUtils.addInputs(self, plugins.PLUGIN_MODULES['TexBitmap'])

        syncObjectUniqueName(self, reset=True)

    def copy(self, node):
        def _createTexture():
            hasTexture = bool(self.texture)
            NodeUtils.createBitmapTexture(self)

            if hasTexture:
                if hasattr(node.texture, 'image'):
                    # The node is copied from the current scene. Link the texture to the
                    # original texture image.
                    self.texture.image = node.texture.image
                else:
                    # The node is copied from a diffent scene. The texture image is not copied, so
                    # it will be empty in the copy.
                    pass

            syncObjectUniqueName(self, reset=True)

        # The 'texture' property of the node is still not valid here. Execute the
        # reattachment asynchronously.
        bpy.app.timers.register(_createTexture)


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

    def update(self):
        vrayNodeUpdate(self)

def register():
    for pluginType in VRayNodeMetaImageTexture.vray_plugins_list:
        pluginDesc = plugins.PLUGIN_MODULES[pluginType]

        plugins.addAttributes(pluginDesc, VRayNodeMetaImageTexture)

    NodeUtils.createFakeTextureAttribute(VRayNodeMetaImageTexture)

    bpy.utils.register_class(VRayNodeMetaImageTexture)


def unregister():
    bpy.utils.unregister_class(VRayNodeMetaImageTexture)
