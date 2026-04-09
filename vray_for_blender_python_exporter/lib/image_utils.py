# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later


import bpy
import os
from collections import defaultdict
from pathlib import Path

from vray_blender import debug
from vray_blender.lib.sys_utils import getDefaultTexturePath
from vray_blender.lib.blender_utils import tagUsersForUpdate
from vray_blender.lib.path_utils import getV4BTempDir
from vray_blender.plugins import getPluginModule

VRAY_IMAGE_FORMATS_LIST = ('.png', '.bmp', '.tga', '.hdr', '.sgi', '.rgb', '.rgba',
                         '.jpg', '.jpeg', '.jpe', '.exr', '.pic', '.tif', '.tiff',
                         '.tx', '.tex', '.psd')
def getVRayImageFormatExts():
    # Returns ".png;.bmp;..."
    return ";".join(VRAY_IMAGE_FORMATS_LIST)

def getVRayImageFormatFilter():
    # Returns "*.png;*.bmp;..."
    return ";".join(['*'+ext for ext in VRAY_IMAGE_FORMATS_LIST])

class _ImageTrack:
    """ Tracks the file path and update status of a packed or edited image. """
    def __init__(self, path: str = "", updated: bool = False):
        self.path = path
        self.updated = updated

    def isInitialized(self) -> bool:
        return self.path != ""


# Dictionary to track file paths of a packed or edited images.
# Maps image names to their corresponding file paths.
_trackedImages: dict[str, _ImageTrack] = defaultdict(_ImageTrack)

def getTrackedImagePath(image: bpy.types.Image) -> str:
    """ Returns the saved file path of a packed or edited image if it exists. """
    return _trackedImages[image.name].path

def imageUpdated(image: bpy.types.Image) -> bool:
    """ Returns True if the image has been recently updated. """
    return _trackedImages[image.name].updated

def untrackImage(image: bpy.types.Image):
    """ Removes a tracked image """
    _trackedImages.pop(image.name, None)

def trackImageUpdates():
    """ Tracks V-Ray-related images and saves them if modified. """
    for image in bpy.data.images:
        if image.users <= int(image.use_fake_user):
            continue

        imageTrack: _ImageTrack = _trackedImages[image.name]

        if image.is_dirty:
            imageTrack.path = _saveTemporaryImage(image)
            imageTrack.updated = True

            # Tag V-Ray users of the image for update.
            tagUsersForUpdate(image)

        else:
            if not imageTrack.isInitialized(): # Untracked image
                imageTrack.updated = False
                if image.type == 'RENDER_RESULT':
                    imageTrack.path = getDefaultTexturePath()
                elif (image.source == 'FILE' and image.packed_file) or image.source == "GENERATED":
                    imageTrack.path = _saveTemporaryImage(image)
                else:
                    imageTrack.path = image.filepath
            elif image.source == 'FILE' and not image.packed_file and image.filepath != imageTrack.path:
                imageTrack.path = image.filepath
                imageTrack.updated = True
            else:
                imageTrack.updated = False


def _getTexturePlaceholderNode(material: bpy.types.Material, create: bool) -> bpy.types.Node:
    """ Returns the placeholder 'ShaderNodeTexImage' node for a material. """
    IMAGE_PLACEHOLDER_NAME = "V-Ray Texture Placeholder"

    nodeTree = material.node_tree
    if node := nodeTree.nodes.get(IMAGE_PLACEHOLDER_NAME):
        return node
    if not create:
        return False

    imagePlaceHolderNode = nodeTree.original.nodes.new(type='ShaderNodeTexImage')
    imagePlaceHolderNode.name = IMAGE_PLACEHOLDER_NAME
    imagePlaceHolderNode.location = (-100000, 0)
    imagePlaceHolderNode.select = False

    return imagePlaceHolderNode

def updateTexturePlaceholderNode():
    """ Copy the image from a V-Ray Bitmap node to a placeholder 'ShaderNodeTexImage' node.
        This allows the V-Ray texture to appear in the 3D viewport during texture painting mode,
        since Blender uses this image node for the material's texture paint slot.
    """

    obj = getattr(bpy.context, 'active_object', None)
    if (not obj) or (not obj.active_material) or (not obj.active_material.node_tree):
        return

    material = obj.active_material
    activeNode = material.node_tree.nodes.active

    if activeNode and activeNode.bl_idname == "VRayNodeMetaImageTexture" and activeNode.texture:
        imagePlaceHolderNode = _getTexturePlaceholderNode(material, True)
        if imagePlaceHolderNode.image != activeNode.texture.image:
            imagePlaceHolderNode.image = activeNode.texture.image
    elif (imagePlaceHolderNode := _getTexturePlaceholderNode(material, False)) and imagePlaceHolderNode.image:
        if (pluginModule := getattr(activeNode, 'vray_plugin', '')) and pluginModule != 'NONE' and getPluginModule(pluginModule).Category == 'TEXTURE':
            imagePlaceHolderNode.image = None

def _saveTemporaryImage(image: bpy.types.Image):
    """ Saves the given image to a temporary file. """

    if image.stereo_3d_format is None:
        # This is a rare case with broken Image objects in Blender. The .stereo_3d_format
        # field is populated by default when an image is created. In a user reported
        # scenario however it is None and this causes access violation when Blender dereferences
        # the pointer to it when saving the image.
        debug.printError(f"Image {image.name} contains imvalid data and cannot be saved. Please consider re-creating it.")
        return None

    filePath = str(Path(os.path.join(getV4BTempDir(), image.name)).resolve())
    image.save(filepath=filePath)

    return filePath

def clearSavedImages():
    """ Removes all tracked images. """
    _trackedImages.clear()