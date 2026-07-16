# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later


import bpy

from vray_blender import plugins
from vray_blender.lib.names import syncObjectUniqueName
from vray_blender.nodes import sockets as SocketUtils, utils as NodeUtils
from vray_blender.nodes.nodes import vrayNodeUpdate
from vray_blender.lib.image_utils import subscribeToBitmapImageUpdates, applyDetectedSequenceRange
from vray_blender.lib.mixin import VRayNodeBase
from vray_blender.ui import classes


# Our 3-entry Source dropdown. The order is also the value used by the get/set callbacks.
_IMAGE_SOURCE_ITEMS = (
    ('SINGLE',   "Single Image",   "A single image file"),
    ('SEQUENCE', "Image Sequence", "Multiple image files forming an animation, numbered in their names"),
    ('UDIM',     "UDIM Tiles",     "A tiled texture using <UDIM> or <UVTILE> tokens in the file name"),
)

# Maps Blender's Image.source to our dropdown index, and back. Generated/Movie/Viewer images
# are shown as 'Single Image' (we export them as a single resolved/temp file).
_BLENDER_SOURCE_TO_INDEX = {'FILE': 0, 'GENERATED': 0, 'MOVIE': 0, 'VIEWER': 0, 'SEQUENCE': 1, 'TILED': 2}
_INDEX_TO_BLENDER_SOURCE = {0: 'FILE', 1: 'SEQUENCE', 2: 'TILED'}


def _getImageSource(self):
    image = self.texture.image if self.texture else None
    return _BLENDER_SOURCE_TO_INDEX.get(image.source, 0) if image else 0


def _setImageSource(self, value):
    if self.texture and (image := self.texture.image):
        wasSequence = (image.source == 'SEQUENCE')
        image.source = _INDEX_TO_BLENDER_SOURCE.get(value, 'FILE')
        if image.source == 'SEQUENCE' and not wasSequence:
            # Auto-detect the frame range from the files on disk when switching to Image Sequence.
            applyDetectedSequenceRange(self.texture.image_user, image)


class VRayNodeMetaImageTexture(VRayNodeBase):
    bl_idname = 'VRayNodeMetaImageTexture'
    bl_label  = 'V-Ray Bitmap'
    bl_icon   = 'TEXTURE'

    vray_type  : bpy.props.StringProperty(default='TEXTURE')
    vray_plugin: bpy.props.StringProperty(default='TexBitmap')

    # Virtual property backed by node.texture.image.source - never stores its own state, so it
    # always reflects the shared datablock and stays in sync with Blender's image editor.
    image_source: bpy.props.EnumProperty(
        name        = "Source",
        description = "How the image is interpreted",
        items       = _IMAGE_SOURCE_ITEMS,
        get         = _getImageSource,
        set         = _setImageSource,
    )

    vray_plugins_list = ["BitmapBuffer", "TexBitmap"]

    def init(self, context):
        NodeUtils.createBitmapTexture(self)

        NodeUtils.addOutputs(self, plugins.PLUGIN_MODULES['TexBitmap'])
        NodeUtils.addInputs(self, plugins.PLUGIN_MODULES['BitmapBuffer'])
        NodeUtils.addInputs(self, plugins.PLUGIN_MODULES['TexBitmap'])

        syncObjectUniqueName(self, reset=True)
        NodeUtils.autoInitBitmapNode(self)
        subscribeToBitmapImageUpdates(self)

    def copy(self, node):
        # Try to capture the texture ID now (same-scene copies, including group operations
        # where the source node is removed synchronously before the timer fires).
        # This fails for cross-scene copies where the node isn't fully set up yet.
        try:
            srcTexture = node.texture
        except Exception:
            srcTexture = None

        def _createTexture():
            hasTexture = bool(self.texture)
            NodeUtils.createBitmapTexture(self)

            if hasTexture:
                tex = srcTexture
                if tex is None:
                    # srcTexture wasn't captured (cross-scene copy); node is still alive here.
                    try:
                        tex = node.texture
                    except ReferenceError:
                        tex = None
                if tex is not None and hasattr(tex, 'image'):
                    # The node is copied from the current scene. Link the texture to the
                    # original texture image.
                    self.texture.image = tex.image
                # else: The node is copied from a different scene; texture image is not copied.

            syncObjectUniqueName(self, reset=True)
            subscribeToBitmapImageUpdates(self)

        # Pre-5.1: the 'texture' property isn't valid yet, defer to a timer.
        # 5.1+: the timer fires after Blender's cross-instance paste has torn
        # down the source struct, causing a use-after-free — do the work
        # synchronously instead.
        if bpy.app.version < (5, 1, 0):
            bpy.app.timers.register(_createTexture)
        else:
            _createTexture()


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

    def draw_label(self):
        # Mirror Cycles' Image Texture node: show the loaded image's datablock name as the
        # node label, falling back to the default label when no image is assigned. An explicit
        # user-set label still overrides this.
        if self.texture and (image := self.texture.image):
            return image.name
        return self.bl_label

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
