# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later


from __future__ import annotations

import datetime
import getpass
import os
import re
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


def getScenePath() -> str:
    """ Returns the current scene file path, or a persistent per-user default for unsaved scenes.
        The default is inside Blender's user datafiles directory so it follows Blender conventions.
    """
    if bpy.data.filepath:
        return bpy.data.filepath
    defaultPath = Path(bpy.utils.user_resource('DATAFILES', path='vray_blender'))
    defaultPath.mkdir(parents=True, exist_ok=True)
    return str(defaultPath)


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
    tempPath = os.path.join(bpy.app.tempdir, "vray_blender")
    os.makedirs(tempPath, exist_ok=True)
    return tempPath


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


# Metadata for path placeholders used in output paths and file names. Each entry is
# (placeholder, displayName, description). Kept here so both the expansion logic and
# the UI menu that lists the supported placeholders stay in sync.
PATH_PLACEHOLDERS = (
    # Scene / file
    ('$frame',    "Frame Number",      "Current frame number (zero-padded to 4 digits)"),
    ('$file',     "Blendfile Name",    "Name of the current .blend file"),
    ('$scene',    "Scene Name",        "Name of the current scene"),
    ('$camera',   "Camera Name",       "Name of the active camera in the scene"),
    ('$viewlayer',"View Layer Name",   "Name of the current View Layer"),
    ('$renderer', "Renderer",          "Active rendering engine name"),
    # Resolution / animation
    ('$res',      "Resolution",        "Render resolution, e.g. 800x600"),
    ('$width',    "Width",             "Render output width in pixels"),
    ('$height',   "Height",            "Render output height in pixels"),
    ('$fps',      "FPS",               "Scene frame rate"),
    # System
    ('$username', "Username",          "Operating system user identification"),
    ('$computer', "Computer",          "Operating system machine identifier"),
    # Date / time (evaluated when the render starts)
    ('$YYYY',     "Year (4-digit)",    "Four-digit year, e.g. 2025"),
    ('$YY',       "Year (2-digit)",    "Two-digit year, e.g. 25"),
    ('$MM',       "Month",             "Current month (01-12)"),
    ('$DD',       "Day",               "Current day of the month (01-31)"),
    ('$hh',       "Hours",             "Current hour (00-23)"),
    ('$mm',       "Minutes",           "Current minute (00-59)"),
    ('$ss',       "Seconds",           "Current second (00-59)"),
)


# Subset of PATH_PLACEHOLDERS valid in directory paths. $frame is excluded because
# V-Ray cannot embed frame tokens in folder names — only in the filename portion.
PATH_DIR_PLACEHOLDERS = tuple(p for p in PATH_PLACEHOLDERS if p[0] != '$frame')


def _getCameraForFrame(scene: bpy.types.Scene, frame: int) -> str:
    """ Return the name of the camera active at the given frame.

        Camera markers bind specific camera objects to specific frames. The camera
        in effect at a given frame is the one bound by the most recent marker at or
        before that frame. Falls back to scene.camera when no such marker exists.
    """
    bound_markers = [m for m in scene.timeline_markers if m.camera is not None and m.frame <= frame]
    if bound_markers:
        return max(bound_markers, key=lambda m: m.frame).camera.name
    return scene.camera.name if scene.camera else "no_camera"


class PathExpander:
    """ Pre-computes all static path placeholder values once per render/bake job.
        $camera and $viewlayer are resolved per-call because they vary across frames
        and view layers respectively; everything else is captured once at construction
        time so it stays consistent for the entire job.

        One instance covers all view layers — pass viewLayerName to expand() /
        expandFilename() for each layer rather than constructing separate instances.
    """

    _EXTENSIONS = (".png", ".jpg", ".tiff", ".tga", ".sgi", ".exr", ".vrimg")

    def __init__(self, ctx: bpy.types.Context, allowRelative: bool = False):
        from vray_blender.lib.blender_utils import isDefaultScene

        scene  = ctx.scene
        render = scene.render

        pct    = render.resolution_percentage / 100.0
        width  = int(render.resolution_x * pct)
        height = int(render.resolution_y * pct)

        now = datetime.datetime.now()

        self._scene            = scene
        self._allowRelative    = allowRelative
        self._defaultViewLayer = ctx.view_layer.name if getattr(ctx, "view_layer", None) else ""
        # Order matters: longer/more-specific tokens before their shorter prefixes.
        # $frame is intentionally absent — it is only valid in filenames, not directory
        # paths, and is substituted in expandFilename() rather than here.
        self._static = (
            ("$YYYY",     now.strftime("%Y")),
            ("$YY",       now.strftime("%y")),
            ("$MM",       now.strftime("%m")),
            ("$DD",       now.strftime("%d")),
            ("$hh",       now.strftime("%H")),
            ("$mm",       now.strftime("%M")),
            ("$ss",       now.strftime("%S")),
            ("$renderer", "vray"),
            ("$res",      f"{width}x{height}"),
            ("$width",    str(width)),
            ("$height",   str(height)),
            ("$fps",      str(render.fps)),
            ("$username", getpass.getuser()),
            ("$computer", sys_utils.getHostname()),
            ("$file",     "default" if isDefaultScene() else Path(bpy.data.filepath).stem),
            ("$scene",    scene.name),
        )

    def expand(self, expr: str, frame: int = None, viewLayerName: str = None,
               allowRelative: bool = None) -> str:
        """ Expand all static placeholders + per_frame:
                $camera    — resolved per frame via timeline markers.
                $viewlayer — resolved from viewLayerName; falls back to the context's active
                            view layer captured at construction time when not supplied.
            Args:
            allowRelative — overrides the instance default when provided.
        """
        if frame is None:
            frame = self._scene.frame_current

        result = expr
        for token, value in self._static:
            result = result.replace(token, value)
        result = result.replace("$camera", _getCameraForFrame(self._scene, frame))
        result = result.replace("$viewlayer",
                                viewLayerName if viewLayerName is not None
                                else self._defaultViewLayer)

        effective_allow = self._allowRelative if allowRelative is None else allowRelative
        return formatResourcePath(result, effective_allow)

    def expandFilename(self, imgFile: str, imgFormat: int,
                       frame: int = None, viewLayerName: str = None,
                       allowRelative: bool = None) -> str:
        """ Expand placeholders in imgFile and append the format extension.
            $frame is substituted here (not in expand()) because it is only meaningful
            in filenames; directory paths must not contain frame tokens.
        """
        assert 0 <= imgFormat < len(self._EXTENSIONS), "Unknown output image format"
        result = self.expand(imgFile, frame, viewLayerName=viewLayerName, allowRelative=allowRelative)
        return result.replace("$frame", "<frame04>") + self._EXTENSIONS[imgFormat]


def withLayerSuffix(imgFile: str, viewLayerName: str | None) -> str:
    """ Return imgFile with '_viewLayerName' appended for multi-layer exports,
        unless the template already contains the $viewlayer placeholder (which
        will expand to the layer name, making an extra suffix redundant).
    """
    if viewLayerName and '$viewlayer' not in imgFile:
        return f"{imgFile}_{viewLayerName}"
    return imgFile


# Per-render-job session storage. The pre-render overwrite check (VRAY_OT_render._checkOutputInfo
# and VRAY_OT_batch_bake._checkOutputInfo) stores one PathExpander here so the exporter reuses
# the same instance — and therefore the same datetime — for both the check and the actual export.
_session_expander: 'PathExpander | None' = None


def setSessionExpander(expander: 'PathExpander') -> None:
    global _session_expander
    _session_expander = expander


def getSessionExpander() -> 'PathExpander | None':
    return _session_expander


def clearSessionExpander() -> None:
    global _session_expander
    _session_expander = None



def getOutputFileName(context, imgFile, imgFormat, viewLayerName=None, allowRelative=False, frame=None):
    expander = PathExpander(context, allowRelative=allowRelative)
    return expander.expandFilename(withLayerSuffix(imgFile, viewLayerName), imgFormat,
                                   viewLayerName=viewLayerName, frame=frame)


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


def tryGetRelativePath(path: str):
    """ Return the relative path or None if such cannot be constructed. """
    try:
        return bpy.path.relpath(path)
    except ValueError:
        return None


# Matches V-Ray's <frame##> output-path token, where ## is the optional padding width.
_VRAY_FRAME_RE = re.compile(r"<frame(\d*)>")


def hasFrameToken(imgFile: str) -> bool:
    """ Return True if imgFile contains a frame placeholder — either the $frame
        shorthand or a raw <frame##> V-Ray token (e.g. <frame07>).  Used to decide
        whether V-Ray needs to append '.NNNN' to the filename automatically.
    """
    return '$frame' in imgFile or bool(_VRAY_FRAME_RE.search(imgFile))


def resolveVRayFrameToken(template: str, frame: int) -> str:
    """ Substitute <frame##> tokens in a filename template with the zero-padded
        frame number. The digits after 'frame' set the padding width (default 4).
    """
    def sub(m):
        pad = int(m.group(1)) if m.group(1) else 4
        return f"{frame:0{pad}d}"
    return _VRAY_FRAME_RE.sub(sub, template)


def checkOutputFileExists(existingFiles: set, imgFileName: str, frame: int,
                          needFrameNumber: bool = True) -> bool:
    """ Return True if the rendered output for the given frame would overwrite an
        existing file.

        When imgFileName contains a <frame##> token V-Ray substitutes it in-place —
        this works the same regardless of needFrameNumber.

        Without the token, V-Ray's behaviour depends on SettingsOutput.img_file_needFrameNumber:
        - True:  V-Ray appends '.NNNN' before the extension  →  pattern match needed.
        - False: V-Ray writes the filename as-is             →  direct lookup.

        existingFiles: set of bare filenames already present in the output directory.
    """
    if _VRAY_FRAME_RE.search(imgFileName):
        return resolveVRayFrameToken(imgFileName, frame) in existingFiles

    if needFrameNumber:
        name, extension = os.path.splitext(imgFileName)
        pattern = re.compile(r"^" + re.escape(name) + r"\.0*" + str(frame) + re.escape(extension) + r"$")
        return any(pattern.match(f) for f in existingFiles)

    return imgFileName in existingFiles
