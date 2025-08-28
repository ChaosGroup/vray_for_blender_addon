
import bpy

from vray_blender.lib.names import Names
from vray_blender.lib.defs import NodeContext, PluginDesc, ProdRenderMode
from vray_blender.lib import plugin_utils, image_utils, sys_utils, path_utils
from vray_blender.exporting import node_export as commonNodesExport
from vray_blender.exporting.node_exporters.uvw_node_export import exportDefaultUVWGenChannel, exportDefaultUVWGenEnvironment
from vray_blender.exporting.tools import getInputSocketByName
from vray_blender.nodes.tools import isInputSocketLinked

plugin_utils.loadPluginOnModule(globals(), __name__)


def exportTreeNode(nodeCtx: NodeContext):
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
    
    allowRelativePaths = nodeCtx.exporterCtx.exportOnly

    if node.BitmapBuffer.use_external_image:
        bitmapBufferPluginDesc.setAttribute('file', _getExternalImagePath(node.BitmapBuffer.file, allowRelativePaths))
    else:
        bitmapBufferPluginDesc.setAttribute('file', _getImagePathFromTexture(node.texture, allowRelativePaths))

    pluginBitmapBuffer = commonNodesExport.exportPluginWithStats(nodeCtx, bitmapBufferPluginDesc)

    mappingSock = getInputSocketByName(nodeCtx.node, "Mapping")
    if mappingSock and isInputSocketLinked(mappingSock):
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
    for link in outSock.links:
        toNode = link.to_socket.node
        if (toNode.bl_idname in ('VRayNodeLightDome', 'VRayNodeEnvironment')) or \
            (toNode.bl_idname == "NodeReroute" and _shouldExportEnvironmentUVW(toNode)): # Handling Reroute nodes
            return True
       
    return False

def getImageFilePath(image: bpy.types.Image):
    if (image.source == 'FILE' and image.packed_file) or image.source == "GENERATED":
        if savedPath := image_utils.packedImageSavedPath(image): # Checking if the packed image is already saved
            filePath = savedPath
        else:
            filePath = image_utils.saveAndTrackImage(image)
    elif not image.filepath:
        # The image has been created in Blender but not saved to disk.
        filePath = image_utils.saveAndTrackImage(image)
    else:
        filePath = image.filepath
    return filePath

def _getImagePathFromTexture(texture: bpy.types.Texture, allowRelativePaths: bool):
    filePath = sys_utils.getDefaultTexturePath()

    if texture and (image := texture.image):
        filePath = getImageFilePath(image)

    return path_utils.formatResourcePath(filePath, allowRelativePaths)


def _getExternalImagePath(filePath: str, allowRelativePaths: bool):
    if not filePath: # Return the default texture path if there is no file path set.
        return sys_utils.getDefaultTexturePath()

    formattedPath = ''

    if filePath[:2] in ("//", "\\\\"):
        formattedPath = filePath[2:]
        if not allowRelativePaths:
            # We want any relative paths to be absolute, but there might be user attributes in the path
            # which will break abspath(). 
            formattedPath =  bpy.path.abspath('//') + formattedPath
    else:
        formattedPath = bpy.path.abspath(filePath)

    assert formattedPath != ''
    return formattedPath
    