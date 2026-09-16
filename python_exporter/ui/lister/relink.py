# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

# "Find missing files" for V-Ray assets. Blender's own File > External Data tools
# only handle libraries and Image datablocks; V-Ray references many external files
# that Blender does not track - proxies, Gaussian splats, .vrscene files, IES
# profiles, scanned materials (.vrscan), .vrmat libraries, V-Ray bitmaps, LUT/ICC
# profiles, OpenVDB volumes and the physical-camera lens/distortion files. This
# collects all of them and relinks the missing ones by file name from a chosen
# folder, exactly like Blender's "Find Missing Files".

import contextlib
import os

import bpy

from vray_blender.lib import lib_utils
from vray_blender.lib.blender_utils import VRAY_ASSET_TYPE
from vray_blender.lib.mixin import VRayOperatorBase
from vray_blender.nodes.tools import isVrayLight, iterVRayNodeTreesWithOwners
from vray_blender.ui.lister import core


# A few plugins are listed as 'skipped' (no auto-generated node) but are still
# exposed in the UI - bitmaps through the meta V-Ray Bitmap node, V-Ray Scene as
# an object asset - so their file references must remain relinkable.
_EXPOSED_DESPITE_SKIPPED = frozenset({'BitmapBuffer', 'TexBitmap', 'VRayScene'})

# Lazily-built {pluginType: (fileAttr, ...)} map of every V-Ray plugin attribute
# that stores an external file path. Derived from the plugin descriptors so any
# new file-bearing plugin is covered automatically, with no hand-kept list.
_fileAttrsByPlugin = None


def _pluginFileAttrs(pluginModule) -> tuple:
    attrs = []
    for p in getattr(pluginModule, 'Parameters', ()):
        if p.get('type') != 'STRING':
            continue
        # V-Ray marks file paths either with a *_FILE_PATH subtype or by declaring
        # 'file_extensions' in the attribute UI (see lib/attribute_utils.py).
        if ('file_extensions' in p.get('ui', {})) or p.get('subtype', '') in ('FILE_PATH', 'DIR_PATH', 'VRAY_FILE_PATH'):
            attrs.append(p['attr'])
    return tuple(attrs)


def _isExposedPlugin(pluginType: str) -> bool:
    """ Only plugins actually exposed in the V-Ray UI are relinkable - we must not
        offer to relink files of plugins the user can never create (e.g. TexOpenVDB,
        TexLut, MtlOSL, which are listed in SKIPPED_PLUGINS pending support). """
    from vray_blender.plugins.skipped_plugins import SKIPPED_PLUGINS
    return pluginType not in SKIPPED_PLUGINS or pluginType in _EXPOSED_DESPITE_SKIPPED


def _getFileAttrsByPlugin() -> dict:
    global _fileAttrsByPlugin
    if _fileAttrsByPlugin is None:
        from vray_blender.plugins import PLUGIN_MODULES
        _fileAttrsByPlugin = {ptype: attrs for ptype, mod in PLUGIN_MODULES.items()
                              if _isExposedPlugin(ptype) and (attrs := _pluginFileAttrs(mod))}
    return _fileAttrsByPlugin


def _nodePluginGroups(node):
    """ Yield (pluginType, propGroup) for every V-Ray plugin a node carries. Meta
        nodes (e.g. the V-Ray Bitmap, which holds both BitmapBuffer and TexBitmap)
        list them in 'vray_plugins_list'; a plain node has a single 'vray_plugin'. """
    names = list(getattr(node, 'vray_plugins_list', None) or [])
    main = getattr(node, 'vray_plugin', 'NONE')
    if main not in ('', 'NONE') and main not in names:
        names.append(main)
    for name in names:
        propGroup = getattr(node, name, None)
        if propGroup is not None:
            yield name, propGroup


def _nodeLocator(ntree, node) -> str:
    """ Row locator prefix, keyed on session_uid, not the tree name: trees share the name
        'Shader Nodetree' and nodes keep their defaults, so name-based locators collide across
        materials and every action keyed by one (relink, select, open) hits the first row. """
    return f"n|{ntree.session_uid}|{node.name}"


# Cache of file-existence checks keyed by absolute path. The lister redraws often
# (every mouse move over the window), so without this every visible asset row would
# stat the filesystem on every redraw - unworkable for big scenes / network paths.
# Populated lazily, cleared on file load, manual Refresh and after any relink.
_existsCache: dict = {}


def refreshAssetCache():
    """ Forget cached file-existence results so the next draw re-checks the disk. """
    _existsCache.clear()


def isFileMissing(path: str) -> bool:
    if not path:
        return False
    absPath = os.path.normpath(bpy.path.abspath(path))
    exists = _existsCache.get(absPath)
    if exists is None:
        exists = os.path.exists(absPath)
        _existsCache[absPath] = exists
    return not exists


############################################################
# Asset reference model (used by the Assets lister tab)
############################################################

# Assets-tab group kind -> (display label, Blender icon).
_KIND_INFO = {
    'BITMAP':  ("Bitmaps",           'IMAGE_DATA'),
    'PROXY':   ("Proxies",           'MESH_DATA'),
    'SCENE':   ("V-Ray Scenes",      'SCENE_DATA'),
    'SPLAT':   ("Gaussian Splats",   'OUTLINER_OB_POINTCLOUD'),
    'SCANNED': ("Scanned Materials", 'MATERIAL'),
    'VRMAT':   ("VRmat Materials",   'MATERIAL'),
    'OCIO':    ("OCIO",              'FILE'),
    'IES':     ("IES Profiles",      'LIGHT'),
    'LUMINAIRE': ("Luminaire Caches", 'LIGHT'),
    'CAMERA':  ("Camera Files",      'CAMERA_DATA'),
}

# Node plugin type -> Assets kind (object refs set their kind explicitly).
_NODE_KIND = {
    'BitmapBuffer':   'BITMAP',
    'TexOCIO':        'OCIO',
    'BRDFScanned':    'SCANNED',
    'MtlVRmat':       'VRMAT',
    'LightLuminaire': 'LUMINAIRE',
}

_IMAGE_GLOB = "*.png;*.jpg;*.jpeg;*.tif;*.tiff;*.exr;*.hdr;*.tga;*.bmp;*.tx;*.tex;*.gif;*.psd"


def kindLabel(kind: str) -> str:
    info = _KIND_INFO.get(kind)
    return info[0] if info else kind


def kindIcon(kind: str) -> str:
    info = _KIND_INFO.get(kind)
    return info[1] if info else 'FILE'


def _globForKind(kind: str) -> str:
    """ File-browser extension filter for an Assets-tab kind. """
    from vray_blender.proxy import VRAY_PROXY_FILTER_GLOB, VRAY_SCENE_FILTER_GLOB
    return {
        'BITMAP':  _IMAGE_GLOB,
        'CAMERA':  _IMAGE_GLOB,
        'PROXY':   VRAY_PROXY_FILTER_GLOB,
        'SCENE':   VRAY_SCENE_FILTER_GLOB,
        'SPLAT':   "*.ply",
        'SCANNED': "*.vrscan",
        'VRMAT':   "*.vrmat;*.vismat",
        'OCIO':    "*.ocio",
        'IES':     "*.ies",
        'LUMINAIRE': "*.vlw;*.vlg;*.vlsh",
    }.get(kind, "")


def _pathWriteGuard(kind: str):
    """ Repairing a path points at the same scanned material in a new place, so it must not
        re-read the .vrscan preset over the parameters on the node (nor run the preset tool once
        per row). Via a flag the callback checks, not key access - that bypass is unsupported
        since Blender 5.0 and silently loses the write on a get/set property. """
    if kind == 'SCANNED':
        # Via the registry: plugin modules load twice (bare name and package path) and the
        # registered callback reads the PLUGIN_MODULES copy's globals.
        from vray_blender.plugins import findPluginModule
        if module := findPluginModule('BRDFScanned'):
            return module.DisablePresetRead()
    return contextlib.nullcontext()


class AssetRef:
    """ One external-file reference in the scene. Holds either a (propGroup, attr) -
        a V-Ray plugin string attribute - or a Blender Image datablock (default-mode
        bitmaps keep their path on the Image). 'locator' is a stable string used to
        re-resolve the ref from an operator and as the selection key. """
    __slots__ = ('kind', 'element', 'locator', 'propGroup', 'attr', 'image', 'objName')

    def __init__(self, kind, element, locator, propGroup=None, attr='', image=None, objName=''):
        self.kind = kind
        self.element = element
        self.locator = locator
        self.propGroup = propGroup
        self.attr = attr
        self.image = image
        # Name of the scene object that owns this reference (proxy / splat / scene / IES /
        # camera files), or '' for node-based references (bitmaps, materials, world). Lets
        # the Assets tab honour the viewport / render visibility filters for object refs.
        self.objName = objName

    @property
    def path(self) -> str:
        if self.image is not None:
            return self.image.filepath
        if self.propGroup is not None and self.attr:
            return getattr(self.propGroup, self.attr, "")
        return ""

    def setPath(self, newPath: str):
        if self.image is not None:
            self.image.filepath = newPath
            self.image.reload()
        elif self.propGroup is not None and self.attr:
            with _pathWriteGuard(self.kind):
                setattr(self.propGroup, self.attr, newPath)

    @property
    def isMissing(self) -> bool:
        return isFileMissing(self.path)


# Transient (runtime-only) set of selected asset locators for the Assets tab's
# multi-select bulk actions. Not persisted - selection is a momentary UI state.
_selectedAssets: set = set()


def getSelectedAssets() -> set:
    return _selectedAssets


def isAssetSelected(locator: str) -> bool:
    return locator in _selectedAssets


def selectOnlyAsset(locator: str):
    """ Make 'locator' the only selected asset - used when jumping to a texture from
        another tab so it stands out in the Assets list. """
    _selectedAssets.clear()
    _selectedAssets.add(locator)


def bitmapAssetRef(node):
    """ (filePath, assetLocator) for a V-Ray Bitmap texture node, with a locator matching
        the ones collectAssetRefs builds, or None if the node is not a bitmap. Lets other
        tabs show a bitmap's path and jump to its row in the Assets tab. """
    if getattr(node, 'vray_plugin', '') != 'TexBitmap':
        return None
    ntree = node.id_data
    bitmapBuffer = getattr(node, 'BitmapBuffer', None)
    if bitmapBuffer is not None and getattr(bitmapBuffer, 'use_external_image', False):
        return getattr(bitmapBuffer, 'file', ""), f"{_nodeLocator(ntree, node)}|BitmapBuffer|file"
    texture = getattr(node, 'texture', None)
    image = getattr(texture, 'image', None) if texture is not None else None
    if image is not None and image.filepath:
        return image.filepath, f"i|{image.name}"
    return None


def collectAssetRefs(context: bpy.types.Context) -> list:
    """ Return an AssetRef for every external-file reference in the scene that has a
        non-empty path: every file-path attribute of every exposed V-Ray plugin used
        by a node, object, light or camera, plus default-mode V-Ray bitmaps (their
        path lives on a Blender Image datablock). Deduplicated by target.
    """
    refs = []
    seen = set()
    fileAttrs = _getFileAttrsByPlugin()

    def add(kind, element, locator, propGroup, attr, objName=''):
        if propGroup is None:
            return
        try:
            if attr not in propGroup.bl_rna.properties:
                return
        except AttributeError:
            return
        # as_pointer(), not id(): Blender hands out a fresh Python wrapper on every propgroup
        # access, so id() would not recognise the same property reached a second time - e.g. a
        # node-mode light's file attr, found both by the node walk and by the object walk below.
        key = (propGroup.as_pointer(), attr)
        if key in seen or not getattr(propGroup, attr, ""):
            return  # already added, or empty path (nothing referenced)
        seen.add(key)
        refs.append(AssetRef(kind, element, locator, propGroup=propGroup, attr=attr, objName=objName))

    def addPlugin(element, locatorPrefix, propGroup, pluginType, kind, objName=''):
        for attr in fileAttrs.get(pluginType, ()):
            add(kind, element, f"{locatorPrefix}|{pluginType}|{attr}", propGroup, attr, objName=objName)

    # Node-tree references, in a single walk: file-path plugin attrs (bitmaps in external mode,
    # scanned / VRmat materials, OCIO textures, node-mode IES lights, ...) and the default-mode
    # V-Ray bitmaps whose path lives on a Blender Image datablock.
    imgSeen = set()
    for owner, ntree in iterVRayNodeTreesWithOwners():
        for node in ntree.nodes:
            element = f"{owner.name} / {node.name}"
            locator = _nodeLocator(ntree, node)
            isBitmap = getattr(node, 'vray_plugin', '') == 'TexBitmap'
            for pluginType, propGroup in _nodePluginGroups(node):
                # A V-Ray Bitmap's BitmapBuffer is added by the dedicated branch below (one row
                # per bitmap, from either its external file path or its Blender image). Skip it
                # here, or an imported bitmap - whose BitmapBuffer.file the importer fills even in
                # image-datablock mode - would be listed twice.
                if isBitmap and pluginType == 'BitmapBuffer':
                    continue
                kind = _NODE_KIND.get(pluginType, pluginType)
                addPlugin(element, locator, propGroup, pluginType, kind)

            if not isBitmap:
                continue
            bb = getattr(node, 'BitmapBuffer', None)
            if bb is not None and getattr(bb, 'use_external_image', False):
                # External mode: the path lives on BitmapBuffer.file.
                addPlugin(element, locator, bb, 'BitmapBuffer', 'BITMAP')
                continue
            tex = getattr(node, 'texture', None)
            img = getattr(tex, 'image', None) if tex is not None else None
            if (img is None or img.source in ('GENERATED', 'VIEWER')
                    or not img.filepath or id(img) in imgSeen):
                continue
            imgSeen.add(id(img))
            # Carry the BitmapBuffer propgroup too so the color-space column can edit
            # it; the path itself comes from the image (path/setPath prefer .image).
            refs.append(AssetRef('BITMAP', element, f"i|{img.name}", propGroup=bb, image=img))

    # Object / data / light / camera references that do not live on nodes.
    for obj in context.scene.objects:
        vrayObj = getattr(obj, 'vray', None)
        data = obj.data
        dataVray = getattr(data, 'vray', None)
        prefix = f"o|{obj.name}"

        if vrayObj is not None and getattr(vrayObj, 'isVRayGaussian', False):
            addPlugin(obj.name, prefix, getattr(vrayObj, 'GeomGaussians', None), 'GeomGaussians', 'SPLAT', objName=obj.name)

        if vrayObj is not None and dataVray is not None:
            asset = getattr(vrayObj, 'VRayAsset', None)
            if asset is not None:
                if asset.assetType == VRAY_ASSET_TYPE["Proxy"]:
                    addPlugin(obj.name, prefix, getattr(dataVray, 'GeomMeshFile', None), 'GeomMeshFile', 'PROXY', objName=obj.name)
                elif asset.assetType == VRAY_ASSET_TYPE["Scene"]:
                    addPlugin(obj.name, prefix, getattr(dataVray, 'VRayScene', None), 'VRayScene', 'SCENE', objName=obj.name)

        if obj.type == 'LIGHT' and isVrayLight(data):
            # Lights whose external file lives on the light itself rather than on a node.
            match lib_utils.getLightPluginType(data):
                case 'LightIES':
                    addPlugin(obj.name, prefix, lib_utils.getLightPropGroup(data, 'LightIES'), 'LightIES', 'IES', objName=obj.name)
                case 'LightLuminaire':
                    addPlugin(obj.name, prefix, lib_utils.getLightPropGroup(data, 'LightLuminaire'), 'LightLuminaire', 'LUMINAIRE', objName=obj.name)

        if obj.type == 'CAMERA' and dataVray is not None:
            addPlugin(obj.name, prefix, getattr(dataVray, 'CameraPhysical', None), 'CameraPhysical', 'CAMERA', objName=obj.name)

    return refs


class VRAY_OT_relink_assets(VRayOperatorBase):
    bl_idname = "vray.relink_assets"
    bl_label = "Find Missing V-Ray Files"
    bl_description = ("Search a folder recursively and relink missing V-Ray asset files "
                      "(proxies, Gaussian splats, scanned / VRmat materials, IES, V-Ray "
                      "bitmaps, scenes, lens files, ...) by file name")
    bl_options = {'UNDO'}

    directory: bpy.props.StringProperty(subtype='DIR_PATH', options={'HIDDEN'})
    max_depth: bpy.props.IntProperty(
        name="Max Search Depth",
        description="How many folder levels below the chosen folder to search "
                    "(the chosen folder itself is level 1). Keeps the search from "
                    "walking a huge tree, e.g. a whole drive, by accident",
        default=4, min=1, max=32,
    )
    # When set, relink only the assets currently selected in the Assets tab.
    selected_only: bpy.props.BoolProperty(default=False, options={'HIDDEN'})

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        if not self.directory or not os.path.isdir(self.directory):
            self.report({'WARNING'}, "Choose a folder to search")
            return {'CANCELLED'}

        # Index every file under the folder by lower-case base name, but only down to
        # 'max_depth' levels (the chosen folder is level 1) so a huge tree - e.g. a
        # whole drive - is never walked by accident.
        searchRoot = os.path.normpath(self.directory)
        index = {}
        for dirPath, subDirs, files in os.walk(searchRoot):
            for f in files:
                index.setdefault(f.lower(), os.path.join(dirPath, f))
            rel = os.path.relpath(dirPath, searchRoot)
            depth = 0 if rel == os.curdir else rel.count(os.sep) + 1
            if depth >= self.max_depth - 1:
                subDirs[:] = []  # do not descend any deeper

        def lookup(path):
            base = os.path.basename(path.replace("\\", "/"))
            return index.get(base.lower())

        refs = collectAssetRefs(context)
        if self.selected_only:
            refs = [r for r in refs if r.locator in _selectedAssets]

        missing = 0
        relinked = 0
        for rec in refs:
            if not rec.isMissing:
                continue
            missing += 1
            if candidate := lookup(rec.path):
                rec.setPath(candidate)
                relinked += 1

        if missing == 0:
            self.report({'INFO'}, "No missing V-Ray files found")
        else:
            self.report({'INFO'}, f"Relinked {relinked} of {missing} missing V-Ray file(s)")

        refreshAssetCache()
        core.tagListerRedraw(context)
        return {'FINISHED'} if relinked else {'CANCELLED'}


def _resolveOneAsset(obj: bpy.types.Object, kind: str):
    """ Return (propGroup, attr) for a single object's external-file reference. """
    if kind == 'PROXY':
        dataVray = getattr(obj.data, 'vray', None)
        return (getattr(dataVray, 'GeomMeshFile', None), 'file') if dataVray else (None, '')
    if kind == 'SCENE':
        dataVray = getattr(obj.data, 'vray', None)
        return (getattr(dataVray, 'VRayScene', None), 'filepath') if dataVray else (None, '')
    if kind == 'GAUSSIAN':
        vrayObj = getattr(obj, 'vray', None)
        return (getattr(vrayObj, 'GeomGaussians', None), 'file') if vrayObj else (None, '')
    if kind == 'IES':
        return (lib_utils.getLightPropGroup(obj.data, 'LightIES'), 'ies_file')
    if kind == 'LUMINAIRE':
        return (lib_utils.getLightPropGroup(obj.data, 'LightLuminaire'), 'file')
    if kind == 'LENS':
        dataVray = getattr(obj.data, 'vray', None)
        return (getattr(dataVray, 'CameraPhysical', None), 'lens_file') if dataVray else (None, '')
    return (None, '')


def _filterGlobForKind(kind: str) -> str:
    """ File-browser extension filter per asset kind - the same globs the V-Ray
        proxy / scene browsers use, so the picker only shows compatible files. """
    from vray_blender.proxy import VRAY_PROXY_FILTER_GLOB, VRAY_SCENE_FILTER_GLOB
    return {
        'PROXY':    VRAY_PROXY_FILTER_GLOB,
        'SCENE':    VRAY_SCENE_FILTER_GLOB,
        'GAUSSIAN': "*.ply",
        'IES':      "*.ies",
        'LUMINAIRE': "*.vlw;*.vlg;*.vlsh",
    }.get(kind, "")


class VRAY_OT_relink_one(VRayOperatorBase):
    bl_idname = "vray.relink_one"
    bl_label = "Select File"
    bl_description = "Pick the file for this asset (filtered to its compatible extensions)"
    bl_options = {'UNDO'}

    object_name: bpy.props.StringProperty(options={'HIDDEN'})
    kind: bpy.props.StringProperty(options={'HIDDEN'})
    filepath: bpy.props.StringProperty(subtype='FILE_PATH', options={'HIDDEN'})
    filter_glob: bpy.props.StringProperty(default="", options={'HIDDEN'})

    def invoke(self, context, event):
        self.filter_glob = _filterGlobForKind(self.kind)
        obj = context.scene.objects.get(self.object_name)
        if obj is not None:
            propGroup, attr = _resolveOneAsset(obj, self.kind)
            current = getattr(propGroup, attr, "") if (propGroup is not None and attr) else ""
            absPath = bpy.path.abspath(current) if current else ""
            # Seeding the browser with an invalid path can crash Blender, so only
            # set it when the file actually exists.
            if absPath and os.path.exists(absPath):
                self.filepath = absPath
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        obj = context.scene.objects.get(self.object_name)
        if obj is None:
            return {'CANCELLED'}
        propGroup, attr = _resolveOneAsset(obj, self.kind)
        if propGroup is None or not attr:
            return {'CANCELLED'}
        if not self.filepath:
            return {'CANCELLED'}
        setattr(propGroup, attr, self.filepath)
        core.tagListerRedraw(context)
        return {'FINISHED'}


class VRAY_OT_relink_asset(VRayOperatorBase):
    bl_idname = "vray.relink_asset"
    bl_label = "Relink Asset"
    bl_description = "Pick a replacement file for this asset (filtered to its compatible extensions)"
    bl_options = {'UNDO'}

    ref: bpy.props.StringProperty(options={'HIDDEN'})
    kind: bpy.props.StringProperty(options={'HIDDEN'})
    filepath: bpy.props.StringProperty(subtype='FILE_PATH', options={'HIDDEN'})
    filter_glob: bpy.props.StringProperty(default="", options={'HIDDEN'})

    def _findRef(self, context):
        return next((r for r in collectAssetRefs(context) if r.locator == self.ref), None)

    def invoke(self, context, event):
        self.filter_glob = _globForKind(self.kind)
        rec = self._findRef(context)
        if rec is not None and rec.path:
            absPath = bpy.path.abspath(rec.path)
            # Seeding the browser with an invalid path can crash Blender.
            if os.path.exists(absPath):
                self.filepath = absPath
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        rec = self._findRef(context)
        if rec is None:
            return {'CANCELLED'}
        if not self.filepath:
            return {'CANCELLED'}
        rec.setPath(self.filepath)
        refreshAssetCache()
        core.tagListerRedraw(context)
        return {'FINISHED'}


class VRAY_OT_asset_select(VRayOperatorBase):
    bl_idname = "vray.asset_select"
    bl_label = "Select Assets"
    bl_description = "Change which assets are selected for bulk relinking"

    # 'TOGGLE' (one row), 'ALL', 'NONE' or 'MISSING'.
    mode: bpy.props.StringProperty(options={'HIDDEN'})
    ref: bpy.props.StringProperty(options={'HIDDEN'})

    def execute(self, context):
        if self.mode == 'TOGGLE':
            _selectedAssets.discard(self.ref) if self.ref in _selectedAssets else _selectedAssets.add(self.ref)
        elif self.mode == 'NONE':
            _selectedAssets.clear()
        elif self.mode == 'ALL':
            _selectedAssets.clear()
            _selectedAssets.update(r.locator for r in collectAssetRefs(context))
        elif self.mode == 'MISSING':
            _selectedAssets.clear()
            _selectedAssets.update(r.locator for r in collectAssetRefs(context) if r.isMissing)
        core.tagListerRedraw(context)
        return {'FINISHED'}


class _AssetOpenBase(VRayOperatorBase):
    """ Shared body of the two Assets-tab open actions. They are two operators rather
        than one with a 'mode' property because an icon-only button takes its tooltip
        title from bl_label, which is fixed per class - a single operator would title
        both buttons the same. """
    openFolder = False

    ref: bpy.props.StringProperty(options={'HIDDEN'})

    def execute(self, context):
        rec = next((r for r in collectAssetRefs(context) if r.locator == self.ref), None)
        if rec is None or not rec.path:
            return {'CANCELLED'}
        absPath = os.path.normpath(bpy.path.abspath(rec.path))
        if not os.path.exists(absPath):
            self.report({'WARNING'}, "File not found on disk")
            return {'CANCELLED'}
        target = os.path.dirname(absPath) if self.openFolder else absPath
        try:
            bpy.ops.wm.path_open(filepath=target)
        except RuntimeError:
            # OS has no app for this file type (e.g. .tx/.vrmesh on macOS) - VBLD-2615.
            self.report({'WARNING'}, f"No application is associated with '{os.path.basename(target)}'")
            return {'CANCELLED'}
        return {'FINISHED'}


class VRAY_OT_asset_open_folder(_AssetOpenBase):
    bl_idname = "vray.asset_open_folder"
    bl_label = "Open Containing Folder"
    bl_description = "Open the folder that contains this file in your system file browser"

    openFolder = True


class VRAY_OT_asset_open_file(_AssetOpenBase):
    bl_idname = "vray.asset_open_file"
    bl_label = "Open in Default Application"
    bl_description = "Open this file in the application your system associates with its file type"


class VRAY_OT_asset_refresh(VRayOperatorBase):
    bl_idname = "vray.asset_refresh"
    bl_label = "Refresh"
    bl_description = "Re-check which asset files currently exist on disk"

    def execute(self, context):
        refreshAssetCache()
        core.tagListerRedraw(context)
        return {'FINISHED'}


class VRAY_OT_assets_make_paths(VRayOperatorBase):
    bl_idname = "vray.assets_make_paths"
    bl_label = "Make Asset Paths"
    bl_options = {'UNDO'}

    mode: bpy.props.StringProperty(options={'HIDDEN'})            # 'RELATIVE' or 'ABSOLUTE'
    selected_only: bpy.props.BoolProperty(default=False, options={'HIDDEN'})

    @classmethod
    def description(cls, context, properties):
        scope = "selected" if properties.selected_only else "all"
        target = "relative to the .blend" if properties.mode == 'RELATIVE' else "absolute"
        return (f"Make {scope} V-Ray asset file paths {target}. Blender's own External "
                f"Data tools do not cover V-Ray's file references")

    def execute(self, context):
        if self.mode == 'RELATIVE' and not bpy.data.filepath:
            self.report({'WARNING'}, "Save the .blend first to make paths relative to it")
            return {'CANCELLED'}

        refs = collectAssetRefs(context)
        if self.selected_only:
            refs = [r for r in refs if r.locator in _selectedAssets]

        changed = 0
        skipped = 0
        for rec in refs:
            path = rec.path
            if not path:
                continue
            try:
                newPath = bpy.path.relpath(path) if self.mode == 'RELATIVE' else bpy.path.abspath(path)
            except (ValueError, RuntimeError):
                skipped += 1   # e.g. a path on a different drive cannot be made relative
                continue
            if newPath and newPath != path:
                rec.setPath(newPath)
                changed += 1

        refreshAssetCache()
        core.tagListerRedraw(context)
        kind = "relative" if self.mode == 'RELATIVE' else "absolute"
        msg = f"Made {changed} path(s) {kind}"
        if skipped:
            msg += f" ({skipped} skipped - different drive)"
        self.report({'INFO'}, msg)
        return {'FINISHED'} if changed else {'CANCELLED'}


class VRAY_MT_asset_paths(bpy.types.Menu):
    bl_idname = "VRAY_MT_asset_paths"
    bl_label = "Asset Paths"

    def draw(self, context):
        layout = self.layout
        selected = len(_selectedAssets)

        op = layout.operator("vray.assets_make_paths", text="Make All Relative")
        op.mode = 'RELATIVE'
        op.selected_only = False
        op = layout.operator("vray.assets_make_paths", text="Make All Absolute")
        op.mode = 'ABSOLUTE'
        op.selected_only = False

        layout.separator()
        col = layout.column()
        col.enabled = selected > 0
        op = col.operator("vray.assets_make_paths", text=f"Make Selected Relative ({selected})")
        op.mode = 'RELATIVE'
        op.selected_only = True
        op = col.operator("vray.assets_make_paths", text=f"Make Selected Absolute ({selected})")
        op.mode = 'ABSOLUTE'
        op.selected_only = True


class VRAY_MT_asset_relink(bpy.types.Menu):
    bl_idname = "VRAY_MT_asset_relink"
    bl_label = "Relink"

    def draw(self, context):
        layout = self.layout
        selected = len(_selectedAssets)

        col = layout.column()
        col.enabled = selected > 0
        op = col.operator("vray.relink_assets", text=f"Relink Selected ({selected})...", icon='FILE_FOLDER')
        op.selected_only = True

        op = layout.operator("vray.relink_assets", text="Relink Missing...", icon='FILE_FOLDER')
        op.selected_only = False


def getRegClasses():
    return (VRAY_OT_relink_assets, VRAY_OT_relink_one,
            VRAY_OT_relink_asset, VRAY_OT_asset_select,
            VRAY_OT_asset_open_folder, VRAY_OT_asset_open_file,
            VRAY_OT_asset_refresh, VRAY_OT_assets_make_paths,
            VRAY_MT_asset_relink, VRAY_MT_asset_paths)
