# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy

from vray_blender.lib import plugin_utils, image_utils
from vray_blender.lib.draw_utils import UIPainter
from vray_blender.nodes.utils import getNodeOfPropGroup, createBitmapTexture, autoInitBitmapNode
from vray_blender.plugins import getPluginModule


plugin_utils.loadPluginOnModule(globals(), __name__)


def nodeInit(node: bpy.types.Node):
    createBitmapTexture(node)


def nodeDraw(context, layout, node):
    if not node.texture:
        # This will be the case right after the node has been copied from another node.
        return

    bitmapBuffer = node.BitmapBuffer

    if bitmapBuffer.use_external_image:
        drawFile(context, layout, node)
        _drawIFL(layout, bitmapBuffer)
    elif image := node.texture.image:
        layout.template_ID_preview(node.texture, "image")
        # Match the property layout used by the rest of the V-Ray node widgets.
        col = layout.column()
        col.use_property_split = True
        col.use_property_decorate = False
        # 'image_source' is a virtual enum backed by image.source, so it stays in sync.
        col.prop(node, "image_source", text="Source")
        _drawImageSourceOptions(context, col, node, image)
    else:
        layout.template_ID(node.texture, 'image', open='image.open')
    layout.prop(bitmapBuffer, "use_external_image", expand=True)


def _drawImageSourceOptions(context, layout, node, image):
    """ Draw the per-source controls for a Blender image: Blender's own sequence frame
        settings, or UDIM tile info. Single Image needs nothing extra.
    """
    if image.source == 'SEQUENCE':
        imageUser = node.texture.image_user
        # The resolved frame V-Ray will load for the current scene frame (same value Blender's
        # own image panel shows), computed live with Blender's formula.
        currentFrame = image_utils.computeSequenceFrame(imageUser, context.scene.frame_current)
        layout.label(text=f"Frame: {currentFrame}")

        col = layout.column(align=True)
        row = col.row(align=True)
        row.prop(imageUser, "frame_duration", text="Frames")
        detectRangeOperator = row.operator("vray.calc_image_sequence_range", text="", icon='FILE_REFRESH')
        detectRangeOperator.nodeID = node.unique_id
        detectRangeOperator.nodeTreeType = node.id_data.vray.tree_type
        col.prop(imageUser, "frame_start", text="Start Frame")
        col.prop(imageUser, "frame_offset", text="Offset")
        col.prop(imageUser, "use_cyclic", text="Cyclic")
    elif image.source == 'TILED':
        layout.label(text=f"{len(image.tiles)} UDIM tile(s)")


def _drawIFL(layout, bitmapBuffer):
    """ Draw the Image File List options when the external file path points at a .ifl. """
    if bitmapBuffer.file.lower().endswith('.ifl'):
        col = layout.column(align=True)
        col.use_property_split = True
        col.use_property_decorate = False
        col.prop(bitmapBuffer, "ifl_start_frame", text="IFL Start Frame")
        col.prop(bitmapBuffer, "ifl_playback_rate", text="IFL Playback Rate")
        col.prop(bitmapBuffer, "ifl_end_condition", text="IFL End Condition")


def widgetDrawFile(context, layout, propGroup, widgetAttr):
    if (node := getNodeOfPropGroup(propGroup)) and node.texture:
        split = layout.split(factor=0.2)

        col1 = split.column()
        col1.label(text="Image")

        col2 = split.column()
        row = col2.row(align=True)
        row.use_property_split = True # Allow property animation

        if propGroup.use_external_image:
           drawFile(context, layout, node)
        elif img := node.texture.image:
            row.template_ID(node.texture, 'image')

            # Show the pack button for external image files which have not been packed yet.
            # Sequences/movies are excluded: Blender packs them using frame 0 (BKE_image_packfiles),
            # which fails for non-zero-based sequences and can't capture a whole animation anyway.
            if (not img.packed_file) and img.filepath and (img.source not in ('SEQUENCE', 'MOVIE')):
                # Only draw the Pack operator. If the image is packed, an Unpack button
                # will automatically be added by Blender to the UI drawn by the call to template_ID().
                op = row.operator("vray.pack_image", text='', icon='UGLYPACKAGE')
                op.nodeID = node.unique_id

                parentNodeTree = node.id_data
                op.nodeTreeType = parentNodeTree.vray.tree_type
        else:
            row.template_ID(node.texture, 'image', open='image.open')

        col2.prop(propGroup, "use_external_image", expand=True)

def drawFile(context, layout, node: bpy.types.Node):
    pluginModule = getPluginModule('BitmapBuffer')
    painter = UIPainter(context, pluginModule, node.BitmapBuffer, node)

    # This is an unusual way to draw the widget but drawing a whole widget section
    # here breaks the alignment for some reason.
    fileSelectorWidget = next((w for w in pluginModule.Widget['widgets'][0]['attrs'] if w['name'] == 'file_selector'), None)
    assert fileSelectorWidget, "File selector widget not found for V-Ray Bitmap"                          
    painter.drawTemplate(layout, fileSelectorWidget)