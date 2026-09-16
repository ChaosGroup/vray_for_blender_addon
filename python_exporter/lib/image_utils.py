# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later


import bpy
import os
import re
from collections import defaultdict
from pathlib import Path

from vray_blender import debug
from vray_blender.lib.sys_utils import getDefaultTexturePath
from vray_blender.lib.blender_utils import tagUsersForUpdate
from vray_blender.lib import path_utils
from vray_blender.lib.path_utils import getV4BTempDir
from vray_blender.plugins import getPluginModule
from vray_blender.ui.classes import pollEngine, pollTreeType
from vray_blender.nodes.tools import TEXTURE_PLACEHOLDER_NODE_NAME, deselectNodes
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


def computeSequenceFrame(imageUser: bpy.types.ImageUser, sceneFrame: int) -> int:
    """ Resolve the sequence frame for a scene frame - a compact port of Blender's
        BKE_image_user_frame_get, so V-Ray loads the file Blender/Cycles shows.
    """
    sequenceLength = imageUser.frame_duration
    if sequenceLength == 0:
        return 0

    frameNumber = sceneFrame - imageUser.frame_start + 1
    if imageUser.use_cyclic:
        # Wrap into [1, sequenceLength] (Blender's modulo, matches for negative frames too).
        frameNumber = (frameNumber - 1) % sequenceLength + 1

    frameNumber = max(0, min(frameNumber, sequenceLength))
    return frameNumber + imageUser.frame_offset


def detectSequenceRange(image: bpy.types.Image) -> tuple[int, int] | None:
    """ Return (firstFrame, lastFrame) of a sequence's numbered files on disk, or None. Blender has
        no range detection for image sequences ('Match Movie Length' is movie-only), so scan the
        folder for files matching the loaded frame's name pattern (trailing digits before the ext).
    """
    path = _resolveImagePath(image)
    directory, fileName = os.path.split(path)
    if not os.path.isdir(directory):
        return None

    baseName, extension = os.path.splitext(fileName)
    trailingDigitsMatch = re.search(r'(\d+)$', baseName)
    if not trailingDigitsMatch:
        return None

    prefix = baseName[:trailingDigitsMatch.start()]
    framePattern = re.compile('^' + re.escape(prefix) + r'(\d+)' + re.escape(extension) + '$', re.IGNORECASE)
    frameNumbers = [int(fileMatch.group(1)) for candidate in os.listdir(directory) if (fileMatch := framePattern.match(candidate))]
    return (min(frameNumbers), max(frameNumbers)) if frameNumbers else None


def applyDetectedSequenceRange(imageUser: bpy.types.ImageUser, image: bpy.types.Image) -> bool:
    """ Set frame_duration and frame_offset from the sequence files on disk, matching Blender's own
        load convention (frames = count, offset = firstFrame - 1). Returns False if no files found.
        Start Frame is the timeline placement and isn't derivable from disk, so it's left untouched.
    """
    frameRange = detectSequenceRange(image)
    if not frameRange:
        return False
    firstFrame, lastFrame = frameRange
    imageUser.frame_duration = lastFrame - firstFrame + 1
    imageUser.frame_offset = firstFrame - 1
    return True


def applySequenceFrameAttrs(image: bpy.types.Image, imageUser: bpy.types.ImageUser, currentFrame: float, bitmapBufferDesc) -> bool:
    """ Pin BitmapBuffer to the exact frame Blender resolves for the current frame, returning True
        for a sequence so the caller re-exports the material each frame. computeSequenceFrame
        reproduces Blender's cyclic wrap and non-cyclic clamp, which a static V-Ray frame_offset
        can't. UDIM (<UDIM>/<UVTILE> token in 'file', resolved by V-Ray) and single images are
        static and return False.
    """
    if image.source != 'SEQUENCE':
        return False

    frameNumber = int(round(currentFrame)) if currentFrame else bpy.context.scene.frame_current
    bitmapBufferDesc.setAttribute('frame_sequence', True)
    bitmapBufferDesc.setAttribute('frame_number', computeSequenceFrame(imageUser, frameNumber))
    bitmapBufferDesc.setAttribute('frame_offset', 0)
    return True


def trackImageUpdates():
    """ Tracks V-Ray-related images and saves them if modified. """
    for image in bpy.data.images:
        if image.users <= int(image.use_fake_user):
            continue

        imageTrack: _ImageTrack = _trackedImages[image.name]

        if image.is_dirty:
            if image.source == 'SEQUENCE':
                # A dirty sequence can't be temp-saved as a whole - image.save() would capture only
                # the current frame to a non-numbered path. Render from the on-disk numbered files.
                imageTrack.path = _resolveImagePath(image)
                imageTrack.originalPath = image.filepath
            else:
                imageTrack.path = _saveTemporaryImage(image)
            imageTrack.updated = True

            # Tag V-Ray users of the image for update.
            tagUsersForUpdate(image)

        else:
            if not imageTrack.isInitialized(): # Untracked image
                imageTrack.updated = False
                if image.type == 'RENDER_RESULT':
                    imageTrack.path = getDefaultTexturePath()
                elif image.source == "GENERATED" or (image.source in ('FILE', 'TILED') and len(image.packed_files) > 0):
                    # Packed single image or packed UDIM (all tiles) -> save to the V-Ray temp dir.
                    imageTrack.path = _saveTemporaryImage(image)
                else:
                    imageTrack.path = _resolveImagePath(image)
                    imageTrack.originalPath = image.filepath
            elif image.source in ('FILE', 'SEQUENCE', 'TILED') and len(image.packed_files) == 0 and image.filepath != imageTrack.originalPath:
                imageTrack.path = _resolveImagePath(image)
                imageTrack.originalPath = image.filepath
                imageTrack.updated = True
                tagUsersForUpdate(image)
            else:
                imageTrack.updated = False


def _getTexturePlaceholderNode(material: bpy.types.Material, create: bool) -> bpy.types.Node:
    """ Returns the placeholder 'ShaderNodeTexImage' node for a material. """
    IMAGE_PLACEHOLDER_NAME = TEXTURE_PLACEHOLDER_NODE_NAME

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

    if image.source == 'TILED':
        # A UDIM image holds multiple tiles. image.save() with a <UDIM>/<UVTILE> token path writes
        # every tile (BKE_image_save iterates ima->tiles), without disturbing the pack. Reuse the
        # original token filename so the tile-numbering format (<UDIM> vs <UVTILE>) is preserved.
        tokenName = os.path.basename(image.filepath) or f"{image.name}.<UDIM>.exr"
        filePath = os.path.join(getV4BTempDir(), tokenName)
    else:
        filePath = str(Path(os.path.join(getV4BTempDir(), image.name)).resolve())

    try:
        image.save(filepath=filePath)
    except RuntimeError as ex:
        # A single unsaveable image (e.g. a .tif whose Exif block OpenImageIO refuses to write)
        # must not abort the whole export/scene load. Skip it like the invalid-data case above.
        debug.printError(f"Image {image.name} could not be saved to a temporary file ({ex}); skipping.")
        return None

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


class VRAY_OT_calc_image_sequence_range(bpy.types.Operator):
    bl_idname      = "vray.calc_image_sequence_range"
    bl_label       = "Detect Range"
    bl_description = "Set Frames from the numbered files found in the image sequence's folder"
    bl_options     = { 'INTERNAL', 'UNDO' }

    nodeID: bpy.props.StringProperty()
    nodeTreeType: bpy.props.StringProperty()

    def execute(self, context: bpy.types.Context):
        node = getattr(context, 'active_node', None)
        if not (node and getattr(node, 'unique_id', None) == self.nodeID):
            # Invoked from the property panel - resolve the node by its unique id.
            match self.nodeTreeType:
                case 'MATERIAL': nodes = context.material.node_tree.nodes
                case 'WORLD':    nodes = context.world.node_tree.nodes
                case _:          nodes = []
            node = next((n for n in nodes if getattr(n, "unique_id", None) == self.nodeID), None)

        if not (node and node.texture and (image := node.texture.image)):
            return { 'CANCELLED' }

        if not applyDetectedSequenceRange(node.texture.image_user, image):
            self.report({'WARNING'}, "No image sequence files found on disk")
            return { 'CANCELLED' }

        return { 'FINISHED' }


def _getRegClasses():
    return (
        VRAY_OT_import_drop_image,
        VRAY_FH_image_handler,
        VRAY_OT_calc_image_sequence_range,
    )


def register():
    for regClass in _getRegClasses():
        bpy.utils.register_class(regClass)


def unregister():
    for regClass in _getRegClasses():
        bpy.utils.unregister_class(regClass)


def _findBitmapNodeByTexture(tex: bpy.types.Texture):
    """ Locate the V-Ray Bitmap node that owns 'tex', or None. """
    from vray_blender.nodes.tools import iterVRayNodeTrees
    for ntree in iterVRayNodeTrees():
        for node in ntree.nodes:
            if node.bl_idname == 'VRayNodeMetaImageTexture' and node.texture == tex:
                return node
    return None


def _onBitmapImageMsgbusNotify(tex: bpy.types.Texture):
    """ msgbus notify for a V-Ray Bitmap's texture. Re-resolves the node from the texture
        instead of holding it in the subscription args - see subscribeToBitmapImageUpdates
        for why. A node that no longer exists simply resolves to None, leaving the stale
        subscription harmless.
    """
    if node := _findBitmapNodeByTexture(tex):
        _onBitmapImageUpdate(node)


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


# Per-node fingerprint of the sequence frame settings, so we can detect edits Blender does not
# report as depsgraph updates (those settings live on the node's orphan ImageTexture image_user).
_sequenceBitmapParams: dict[int, tuple] = {}


def tagChangedSequenceBitmaps():
    """ Tag the owning material of any V-Ray Bitmap whose SEQUENCE frame settings (Frames / Start
        Frame / Offset / Cyclic) changed since the last call, so IPR re-exports it. Blender does not
        generate a depsgraph update for the orphan ImageTexture's image_user, so we fingerprint and
        compare here. Driven from depsgraph_update_post (event-driven, no polling timer).
    """
    from vray_blender.nodes.tools import iterVRayNodeTrees
    liveNodes = set()

    for ntree in iterVRayNodeTrees():
        for node in ntree.nodes:
            if node.bl_idname != 'VRayNodeMetaImageTexture':
                continue
            texture = node.texture
            image = texture.image if texture else None
            if not (image and image.source == 'SEQUENCE'):
                continue

            imageUser = texture.image_user
            nodeKey = node.as_pointer()
            liveNodes.add(nodeKey)
            fingerprint = (imageUser.frame_duration, imageUser.frame_start,
                           imageUser.frame_offset, imageUser.use_cyclic)
            # Using the new fingerprint as the default means a node we haven't seen before is just
            # recorded, not tagged - we only re-export on an actual change.
            if _sequenceBitmapParams.get(nodeKey, fingerprint) != fingerprint:
                _onBitmapImageUpdate(node)
            _sequenceBitmapParams[nodeKey] = fingerprint

    # Forget nodes that no longer exist so the dict doesn't grow unbounded.
    for nodeKey in _sequenceBitmapParams.keys() - liveNodes:
        del _sequenceBitmapParams[nodeKey]


def subscribeToBitmapImageUpdates(node: bpy.types.Node):
    """ Tag IPR dirty when the user swaps node.texture.image via template_ID - Blender fires no
        node.update() for that sub-property change. Watches the whole texture (covers .image swaps);
        sequence frame settings (image_user) are handled by tagChangedSequenceBitmaps() instead,
        since msgbus doesn't fire reliably for those nested-struct changes.
    """
    nodeTree = node.id_data.original
    originalNode = nodeTree.nodes.get(node.name)
    if not originalNode:
        return

    tex = originalNode.texture
    if not tex:
        return

    bpy.msgbus.clear_by_owner(tex)
    # Pass the texture, NOT the node. The subscription is owned by the texture, which is a
    # separate datablock that can outlive the node - and Blender only calls Node.free() for
    # nodes.remove(), not when the owning node tree or material is freed, so there is no
    # reliable place to unsubscribe. A node held in args would then be dereferenced after it
    # was freed, crashing in pyrna_struct_get_id_data. The texture cannot dangle the same
    # way: it is the subscription's own key, so msgbus drops the subscription with it.
    #
    # The node is found back by identity rather than by unique_id, because unique_id is
    # re-minted for every node whose pointer changed the next time syncUniqueNames() runs
    # after an undo, which would leave the id captured here matching nothing.
    bpy.msgbus.subscribe_rna(key=tex, owner=tex, args=(tex,),
                             notify=_onBitmapImageMsgbusNotify)


def registerBitmapImageNodes():
    """ Re-subscribe all V-Ray Bitmap nodes (subscriptions are cleared on reload/undo/redo). """
    from vray_blender.nodes.tools import iterVRayNodeTrees
    for ntree in iterVRayNodeTrees():
        for node in ntree.nodes:
            if node.bl_idname == 'VRayNodeMetaImageTexture' and node.texture:
                subscribeToBitmapImageUpdates(node)
