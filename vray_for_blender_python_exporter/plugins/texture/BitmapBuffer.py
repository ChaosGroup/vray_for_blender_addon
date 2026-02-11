# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy

from vray_blender.nodes.utils import getNodeOfPropGroup, createBitmapTexture
from vray_blender.lib import plugin_utils

plugin_utils.loadPluginOnModule(globals(), __name__)


def nodeInit(node: bpy.types.Node):
    createBitmapTexture(node)


def nodeDraw(context, layout, node):
    if not node.texture:
        # This will be the case right after the node has been copied from another node.
        return

    bitmapBuffer = node.BitmapBuffer

    if bitmapBuffer.use_external_image:
        layout.prop(bitmapBuffer, "file")
    elif node.texture.image:
        layout.template_ID_preview(node.texture, "image")
    else:
        layout.template_ID(node.texture, 'image', open='image.open')
    layout.prop(bitmapBuffer, "use_external_image", expand=True)


def widgetDrawFile(context, layout, propGroup, widgetAttr):
    if (node := getNodeOfPropGroup(propGroup)) and node.texture:
        split = layout.split(factor=0.2)

        col1 = split.column()
        col1.label(text="Image")

        col2 = split.column()
        row = col2.row(align=True)
        row.use_property_split = True # Allow property animation

        if propGroup.use_external_image:
            row.prop(propGroup, "file")
        elif img := node.texture.image:
            row.template_ID(node.texture, 'image')

            # Show the pack button for external image files which have not been packed yet
            if (not img.packed_file) and (img.filepath):
                # Only draw the Pack operator. If the image is packed, an Unpack button
                # will automatically be added by Blender to the UI drawn by the call to template_ID().
                op = row.operator("vray.pack_image", text='', icon='UGLYPACKAGE')
                op.nodeID = node.unique_id

                parentNodeTree = node.id_data
                op.nodeTreeType = parentNodeTree.vray.tree_type
        else:
            row.template_ID(node.texture, 'image', open='image.open')

        col2.prop(propGroup, "use_external_image", expand=True)
