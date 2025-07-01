
import bpy
from pathlib import Path
from typing import Dict


# Dictionary to track file paths of a packed images.
# Maps image names to their corresponding file paths.
_savedImages: Dict[str, str] = {}

def packedImageSavedPath(image: bpy.types.Image) -> str:
    """ Returns the saved file path of a packed image if it exists. """
    return _savedImages.get(image.name, "")

def checkPackedImageForUpdates():
    """ Checks all tracked packed images and removes their saved file if the image has been modified. """
    for imageName, path in _savedImages.items():
        if imageName not in bpy.data.images:
            continue

        image = bpy.data.images[imageName]
        if image.is_dirty and path:
            Path(path).unlink(missing_ok=True)
            _savedImages[imageName] = ""

def saveAndTrackImage(image: bpy.types.Image):
    """ Saves the given image in a temporary location and tracks it in the '_savedImages' dictionary. """
    global _savedImages

    filePath = str(Path(bpy.app.tempdir + image.name).resolve())
    image.save(filepath=filePath)

    _savedImages[image.name] = filePath
    return filePath


def clearSavedImages():
    """ Removes all images from '_savedImages'. """
    for imagePath in _savedImages.values():
        Path(imagePath).unlink(missing_ok=True)

    _savedImages.clear()