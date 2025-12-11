
import bpy
from pathlib import Path
from vray_blender.lib.sys_utils import getDefaultTexturePath
from vray_blender.lib.blender_utils import tagUsersForUpdate
from collections import defaultdict


class _ImageTrack:
    """ Tracks the file path and update status of a packed or edited image. """
    def __init__(self, path: str = "", updated: bool = False):
        self.path = path
        self.originalPath = ""
        self.updated = updated

    def isInitialized(self) -> bool:
        return self.path != ""

    def removeTemporaryData(self):
        if self.isInitialized() and (self.path not in (self.originalPath, getDefaultTexturePath())):
            Path(self.path).unlink(missing_ok=True)
            self.path = ""

    def __del__(self):
        self.removeTemporaryData()


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
            imageTrack.removeTemporaryData()

            imageTrack.path = _saveTemporaryImage(image)
            imageTrack.updated = True

            # Tag V-Ray users of the image for update.
            tagUsersForUpdate(image)

        else:
            if not imageTrack.isInitialized(): # Untracked image
                imageTrack.originalPath = image.filepath
                if image.type == 'RENDER_RESULT':
                    imageTrack.path = getDefaultTexturePath()
                elif (image.source == 'FILE' and image.packed_file) or image.source == "GENERATED":
                    imageTrack.path = _saveTemporaryImage(image)
                else:
                    imageTrack.path = image.filepath

            imageTrack.updated = False


def _getTexturePlaceholderNode(material: bpy.types.Material) -> bpy.types.Node:
    """ Returns the placeholder 'ShaderNodeTexImage' node for a material. """
    IMAGE_PLACEHOLDER_NAME = "V-Ray Texture Placeholder"

    nodeTree = material.node_tree
    if node := nodeTree.nodes.get(IMAGE_PLACEHOLDER_NAME):
        return node

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

    if activeNode and (activeNode.bl_idname == "VRayNodeMetaImageTexture"):
        imagePlaceHolderNode = _getTexturePlaceholderNode(material)
        imagePlaceHolderNode.image = activeNode.texture.image

def _saveTemporaryImage(image: bpy.types.Image):
    """ Saves the given image in a temporary location. """

    filePath = str(Path(bpy.app.tempdir + image.name).resolve())
    image.save(filepath=filePath)

    return filePath

def clearSavedImages():
    """ Removes all tracked images. """
    _trackedImages.clear()