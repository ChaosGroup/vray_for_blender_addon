import mathutils

from vray_blender.exporting import node_export as commonNodesExport
from vray_blender.exporting.node_exporters.uvw_node_export import exportDefaultUVWGenChannel, exportDefaultUVWGenEnvironment
from vray_blender.lib import export_utils
from vray_blender.lib.defs import *
from vray_blender.lib.names import Names
from vray_blender.exporting.tools import *


################ SPECIALIZED TEXTURE NODE EXPORTERS ####################

def exportVRayNodeMetaImageTexture(nodeCtx: NodeContext, nodeLink: bpy.types.NodeLink):
    """ V-Ray Bitmap node translates to 3 VRay plugins: TexBitmap, BitmapBuffer and a UVW Gen plugin
        depending on the selected UV mapping mode. This function returns a TexBitmap plugin with 
        attached to it the other two plugins.
    """
    node = nodeCtx.node

    texBitmapPluginName = Names.treeNode(nodeCtx)
    texBitmapPluginDesc = PluginDesc(texBitmapPluginName, 'TexBitmap')
    texBitmapPluginDesc.vrayPropGroup = node.TexBitmap

    bitmapBufferPluginName = Names.nextVirtualNode(nodeCtx, "BitmapBuffer")
    bitmapBufferPluginDesc = PluginDesc(bitmapBufferPluginName, "BitmapBuffer")
    bitmapBufferPluginDesc.vrayPropGroup = node.BitmapBuffer

    commonNodesExport.exportNodeTree(nodeCtx, bitmapBufferPluginDesc)
    _fillBitmapBufferPluginFromNode(node.texture, bitmapBufferPluginDesc)

    pluginBitmapBuffer = commonNodesExport.exportPluginWithStats(nodeCtx, bitmapBufferPluginDesc)

    mappingSock = getInputSocketByName(nodeCtx.node, "Mapping")
    if mappingSock and mappingSock.is_linked:
        # Node has links, continue exporting the node tree
        uvwPlugin = commonNodesExport.exportLinkedSocket(nodeCtx, mappingSock)
        texBitmapPluginDesc.setAttribute("uvwgen", uvwPlugin)
    else:
        # Add a default UVW Mapping because many consumers of the bitmap will not work without it
        if _shouldExportEnvironmentUVW(node):
            uvwPlugin = exportDefaultUVWGenEnvironment(nodeCtx)
        else:
            uvwPlugin = exportDefaultUVWGenChannel(nodeCtx)

        texBitmapPluginDesc.setAttribute("uvwgen", uvwPlugin)

    commonNodesExport.exportNodeTree(nodeCtx, texBitmapPluginDesc)
    texBitmapPluginDesc.setAttribute("bitmap", pluginBitmapBuffer)

    return commonNodesExport.exportPluginWithStats(nodeCtx, texBitmapPluginDesc)


def _shouldExportEnvironmentUVW(node: bpy.types.Node):
    # Return True if all links of the otput socket are to a LightDome or Environment nodes
    outSock = node.outputs[0]
    envLinks = len([l for l in outSock.links if l.to_socket.node.bl_idname in ('VRayNodeLightDome', 'VRayNodeEnvironment')])

    return outSock.links and (len(outSock.links) == envLinks)


def _fillBitmapBufferPluginFromNode(texture, pluginDesc):
    if not texture:
        return

    imageTexture = bpy.types.ImageTexture(texture)
    if not imageTexture:
        return

    image = imageTexture.image

    if image:
        if (image.source == 'FILE' and image.packed_file) or image.source == "GENERATED":
            fileName = image.name
            fileExt = IMAGE_FILE_FORMAT_TO_EXT[image.file_format]

            if not (fileName.endswith(fileExt) or fileName.endswith(fileExt.upper())):
                fileName += fileExt

            # this is needed because sometimes when the filepath gets changed
            # blender triggers engine.view_update(), then the path is changed again and the plugin enters an endless loop
            newFilePath = bpy.app.tempdir + fileName
            if newFilePath != image.filepath:
                image.filepath = newFilePath

            image.save()

        pluginDesc.setAttribute("file", bpy.path.abspath(image.filepath))


def exportVRayNodeTexLayered(nodeCtx: NodeContext):
    node = nodeCtx.node

    textures    = []
    masks       = []
    blendModes  = []
    opacities   = []

    for l in range(node.layers):
        humanIndex = l + 1

        sockTexture = node.inputs[f'Texture {humanIndex}']
        if sockTexture.is_linked:
            linkedPlugin = commonNodesExport.exportLinkedSocket(nodeCtx, sockTexture)
            textures.append(_wrapAsTexture(nodeCtx, linkedPlugin))
        else:
            textures.append(_wrapAsTexture(nodeCtx, sockTexture.value))  

        sockMask = node.inputs[f'Mask {humanIndex}']
        if sockMask.is_linked:
            linkedPlugin = commonNodesExport.exportLinkedSocket(nodeCtx, sockMask)
            masks.append(linkedPlugin)
        else:
            masks.append(_wrapAsTexture(nodeCtx, sockMask.value))    

        blendModes.append(int(node.inputs[f'Blend Mode {humanIndex}'].value))
        opacities.append(node.inputs[f'Opacity {humanIndex}'].value)

    pluginDesc = PluginDesc(Names.treeNode(nodeCtx), "TexLayeredMax")
    pluginDesc.setAttributes({
        "textures": textures,
        "masks":  masks,
        "blend_modes": blendModes, 
        "opacities": opacities
    })

    return commonNodesExport.exportPluginWithStats(nodeCtx, pluginDesc)
    

def _wrapAsTexture(nodeCtx: NodeContext, listItem: mathutils.Color | AttrPlugin):
    """ Wrap a texture or color in a TexAColor plugin which can be added to a plugin list.

        The reason for wrapping the texture plugins is that non-default output sockets are not supported
        for the items set to a TEXTURE_LIST parameter, so we need to route the socket through a convetrer.
    """
    texAColor = PluginDesc(Names.nextVirtualNode(nodeCtx, "TexAColor"), "TexAColor")
    texAColor.setAttribute("texture", listItem)
    return export_utils.exportPlugin(nodeCtx.exporterCtx, texAColor)

