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
from vray_blender.lib import path_utils
from vray_blender.lib.path_utils import getV4BTempDir
from vray_blender.plugins import getPluginModule
from vray_blender.ui.classes import pollEngine, pollTreeType
from vray_blender.nodes.tools import deselectNodes
from vray_blender.nodes.utils import createNode

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
    def __init__(self, path: str = "", updated: bool = False, originalPath: str = ""):
        self.path = path
        self.updated = updated
        self.originalPath = originalPath # original path of the image, used to check if the image path has been modified

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

def _resolveImagePath(image: bpy.types.Image) -> str:
    """ Return the absolute file path for an image.

        For images linked from a library, image.filepath is relative to the
        library file's directory, not the current .blend. Passing image.library
        to bpy.path.abspath resolves the path against the correct anchor.
        For local images, image.library is None and the call is equivalent to
        plain bpy.path.abspath(image.filepath).
    """
    return bpy.path.abspath(image.filepath, library=image.library)

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
                    imageTrack.path = _resolveImagePath(image)
                    imageTrack.originalPath = image.filepath
            elif image.source == 'FILE' and not image.packed_file and image.filepath != imageTrack.originalPath:
                imageTrack.path = _resolveImagePath(image)
                imageTrack.originalPath = image.filepath
                imageTrack.updated = True
                tagUsersForUpdate(image)
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
        debug.printError(f"Image {image.name} contains invalid data and cannot be saved. Please consider re-creating it.")
        return None

    filePath = str(Path(os.path.join(getV4BTempDir(), image.name)).resolve())
    image.save(filepath=filePath)

    return filePath

def clearSavedImages():
    """ Removes all tracked images. """
    _trackedImages.clear()


def _pollImageDragDrop(cls, context: bpy.types.Context):
    return pollEngine(context) and context.space_data and context.space_data.type == 'NODE_EDITOR' and pollTreeType(cls, context)


class VRAY_OT_import_drop_image(bpy.types.Operator):
    bl_idname = "vray.import_drop_image"
    bl_label = "Add V-Ray Bitmap"
    bl_options = { 'INTERNAL', 'UNDO' }

    directory: bpy.props.StringProperty(subtype='DIR_PATH', options={'SKIP_SAVE', 'HIDDEN'})
    files: bpy.props.CollectionProperty(type=bpy.types.OperatorFileListElement, options={'SKIP_SAVE', 'HIDDEN'})

    @classmethod
    def poll(cls, context: bpy.types.Context):
        return _pollImageDragDrop(cls, context)

    def invoke(self, context, event):
        context.space_data.cursor_location_from_region(event.mouse_region_x, event.mouse_region_y)

        return self.execute(context)

    def execute(self, context):
        ntree = context.space_data.edit_tree
        if not ntree:
            return { 'CANCELLED' }

        deselectNodes(ntree)
        for file in self.files:
            # For some reason in newer Blender versions the file name is relative e.g. //img.png
            # and os.path.join(C:\dev\test, //img.png) gives us img.png
            filename = bpy.path.basename(file.name)
            filepath = os.path.join(self.directory, filename)
            filepath = bpy.path.abspath(filepath)
            if not os.path.exists(filepath):
                continue

            imageBlockName = bpy.path.display_name_from_filepath(filepath)
            imageNode = createNode(ntree, "VRayNodeMetaImageTexture")
            relative = path_utils.tryGetRelativePath(filepath)
            filepath = relative if relative is not None else filepath
            imageNode.texture.image = bpy.data.images.load(filepath)
            imageNode.texture.image.name = imageBlockName
            imageNode.select = True
            imageNode.location = context.space_data.cursor_location
            context.space_data.cursor_location.y -= 350.0

        return {'FINISHED'}


class VRAY_FH_image_handler(bpy.types.FileHandler):
    bl_idname = "VRAY_FH_image_handler"
    bl_import_operator = "vray.import_drop_image"
    bl_file_extensions = getVRayImageFormatExts()
    bl_label = "V-Ray Image handler"

    @classmethod
    def poll_drop(cls, context):
        return _pollImageDragDrop(cls, context)


def _getRegClasses():
    return (
        VRAY_OT_import_drop_image,
        VRAY_FH_image_handler,
    )


def register():
    for regClass in _getRegClasses():
        bpy.utils.register_class(regClass)


def unregister():
    for regClass in _getRegClasses():
        bpy.utils.unregister_class(regClass)


def _onBitmapImageUpdate(node: bpy.types.Node):
    ntree = node.id_data
    treeType = getattr(getattr(ntree, 'vray', None), 'tree_type', '')

    if treeType == 'LIGHT':
        # Light node trees are embedded data-blocks not tracked by user_map,
        # so we have to find the owning light and its objects manually.
        light = next((l for l in bpy.data.lights if l.node_tree == ntree), None)
        if light:
            for obj in bpy.data.objects:
                if obj.data == light:
                    obj.update_tag()
    elif treeType == 'WORLD':
        ntree.update_tag()
    else:
        tagUsersForUpdate(ntree)


def subscribeToBitmapImageUpdates(node: bpy.types.Node):
    """Subscribe to texture RNA changes for a V-Ray Bitmap node.

    Blender does not fire node.update() when the user swaps node.texture.image
    via template_ID, because the change is on a sub-property of the texture
    pointer. This msgbus subscription watches the whole texture object
    (which covers .image changes) and tags the node tree dirty so that IPR
    picks up the new image.
    """
    tex = node.texture
    if not tex:
        return

    nodeTree = node.id_data.original
    originalNode = nodeTree.nodes.get(node.name)
    if not originalNode:
        return

    bpy.msgbus.clear_by_owner(tex)
    bpy.msgbus.subscribe_rna(
        key=tex,
        owner=tex,
        args=(originalNode,),
        notify=_onBitmapImageUpdate,
    )


def registerBitmapImageNodes():
    """Re-subscribe all V-Ray Bitmap nodes in the scene.

    msgbus subscriptions are cleared on scene reload, undo, and redo, so
    this function must be called from the corresponding event handlers.
    """
    from vray_blender.nodes.tree import iterVRayNodeTrees
    for ntree in iterVRayNodeTrees():
        for node in ntree.nodes:
            if node.bl_idname == 'VRayNodeMetaImageTexture' and node.texture:
                subscribeToBitmapImageUpdates(node)
