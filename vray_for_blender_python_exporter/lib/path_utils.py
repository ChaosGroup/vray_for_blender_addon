
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
import filecmp
import shutil
import bpy

from vray_blender import debug

from vray_blender.lib import sys_utils

def getRootFolder():
    import vray_blender
    return os.path.dirname(vray_blender.__file__)

def getFilename(filepath, ext=True):
    filename = os.path.basename(bpy.path.abspath(filepath))
    if not ext:
        filename, fileext = os.path.splitext(filename)
    return filename


def getTmpDirectory():
    return tempfile.gettempdir()

# Convert slashes to unix style
#
def unifyPath(filepath, relative=False):
    if relative:
        if filepath.startswith('//'):
            return filepath[2:].replace('\\', '/')

    filepath = os.path.normpath(bpy.path.abspath(filepath))
    filepath = filepath.replace('\\', '/')

    return filepath


def path_sep_to_unix(filepath):
    # if sys.platform != 'win32':
    filepath = filepath.replace('\\\\', '/')
    filepath = filepath.replace('\\', '/')

    return filepath


def quotes(path, force=False):
    if not force and sys.platform == 'win32':
        return path
    return '"%s"' % (path)


def isRelativePath(path):
    return path.startswith("//")


def getPreviewDir():
    previewRoot   = tempfile.gettempdir()
    previewSubdir = "vrayblender_preview_%s" % sys_utils.getUsername()
    if sys.platform == 'linux':
        previewRoot = "/dev/shm"
    previewDir = os.path.join(previewRoot, previewSubdir)
    return createDirectory(previewDir)


def getV4BTempDir():
    return os.path.join(tempfile.gettempdir(), "vray_blender")


def getIconsDir():
    return os.path.join(getRootFolder(), "resources/icons")
                        

def getUpgradeScriptsDir():
    return os.path.join(getRootFolder(), "resources/upgrade_scripts")


def getBinTool(executableName: str):
    """ Get the full path to a tool located in the 'bin' folder """
    return os.path.join(sys_utils.getExporterPath(), "bin", executableName)


def copyTree(src, dst, symlinks=False, ignore=None):
    if sys.platform == 'win32':
        os.system('robocopy /E "%s" "%s"' % (src, dst))
    else:
        if not os.path.exists(dst):
            os.makedirs(dst)
        for item in os.listdir(src):
            s = os.path.join(src, item)
            d = os.path.join(dst, item)
            if os.path.isdir(s):
                shutil.copytree(s, d, symlinks, ignore)
            else:
                shutil.copy2(s, d)


def createDirectory(directory):
    directory = path_sep_to_unix(directory)
    if not os.path.exists(directory):
        debug.printDebug('Creating directory "%s"...' % directory)
        try:
            os.makedirs(directory)
        except OSError:
            debug.printError('Error creating directory: "%s"' % directory)
            directory = tempfile.gettempdir()
            debug.printDebug("Using TMP path: %s" % directory)
    return os.path.expanduser(directory)


def createDirectoryFromFilepath(filepath):
    dirPath, fileName = os.path.split(bpy.path.abspath(filepath))
    dirPath = createDirectory(dirPath)
    return os.path.join(dirPath, fileName)


# @srcFilepath - full absolute path
# Used for copying assets accros DR machines. Currently unused. 
def copyDRAsset(scene, srcFilepath):
    VRayScene = scene.vray
    VRayDR    = VRayScene.VRayDR

    srcFilepath = os.path.normpath(srcFilepath)
    dstRoot     = createDirectory(bpy.path.abspath(VRayDR.shared_dir))

    ExtToSubdir = {
        'ies'    : "ies",
        'lens'   : "misc",
        'vrmesh' : "proxy",
        'vrmap'  : "lightmaps",
        'vrst'   : "lightmaps",
        'vrsm'   : "lightmaps",
    }

    srcFilename = os.path.basename(srcFilepath)

    srcFiletype = os.path.splitext(srcFilename)[1]

    assetSubdir = ExtToSubdir.get(srcFiletype.lower(), "textures")

    if assetSubdir:
        dstRoot = createDirectory(os.path.join(dstRoot, assetSubdir))

    dstFilepath = os.path.join(dstRoot, srcFilename)

    if not os.path.exists(srcFilepath):
        # debug.printError('"%s" file does not exists!' % srcFilepath)
        return srcFilepath

    if not os.path.isfile(srcFilepath):
        debug.printError('"%s" is not a file!' % srcFilepath)
        return srcFilepath

    else:
        if os.path.exists(dstFilepath):
            if not filecmp.cmp(srcFilepath, dstFilepath):
                debug.printDebug('Copying "%s" to "%s"'% (debug.Color(srcFilename, 'magenta'), dstRoot))

                shutil.copyfile(srcFilepath, dstFilepath)

            else:
                debug.printDebug('File "%s" exists and not modified.'% debug.Color(srcFilename, 'magenta'))

        else:
            debug.printDebug('Copying "%s" to "%s"' % (debug.Color(srcFilename, 'magenta'), dstRoot))

            shutil.copyfile(srcFilepath, dstFilepath)

    if VRayDR.networkType == 'WW':
        return Path(r'\\') / sys_utils.getHostname() / VRayDR.share_name / assetSubdir / srcFilename

    return dstFilepath


def expandPathVariables(ctx: bpy.types.Context, expr: str, allowRelative = False):
    """ Expand special placeholders in the supplied path expression:
        - $F - the name of the .blend file
        - $C - the name of the current camera
        - $S - the name of the scene

    Args:
        ctx (bpy.types.Context): context
        expr (str): the path expression to expand
        allowRelative (bool, optional): True to format relative Blender paths as relative in V-Ray.

    Returns:
        _type_: _description_
    """
    scene = ctx.scene
    cameraName = scene.camera.name if scene.camera else "no_camera"

    expandedPath = expr.replace("$F", Path(bpy.data.filepath).name)\
                        .replace("$C", cameraName)\
                        .replace("$S", scene.name)
    
    return formatResourcePath(expandedPath, allowRelative)


def getOutputFileName(context, imgFile, imgFormat, viewLayerName="", allowRelative = False):
    imgFile = f"{imgFile}_{viewLayerName}" if viewLayerName else imgFile

    imgFile = expandPathVariables(context, imgFile)
    
    extensions = (".png", ".jpg", ".tiff", ".tga", ".sgi", ".exr", ".vrimg")

    assert 0 <= imgFormat < len(extensions), "Unknown output image format"     
    imgFile += extensions[imgFormat]

    return imgFile


def formatResourcePath(path: str, allowRelative: bool):
    """ Format absolute or relative path suitable for usage as path property in a 
        .vrscene file.

    Args:
        path (str):             the path to the resource file as supplied by Blender
        allowRelative(bool) :   if True, relative Blender paths will be formatted as 
                                relative V-Ray paths, otherwise they will be converted
                                to absolute
    """

    if allowRelative and isRelativePath(path):
        # Path is relative to the currently open .blend file. Remove the leading '//'
        # to change into the relative path format expected by V-Ray.
        return path[2:]
    
    return bpy.path.abspath(path)
