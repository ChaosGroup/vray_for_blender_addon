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

import os

import bpy

from vray_blender.lib import lib_utils
from vray_blender.lib.blender_utils import VRAY_ASSET_TYPE
from vray_blender.lib.mixin import VRayOperatorBase
from vray_blender.nodes.tools import isVrayLight
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


def _vrayNodeTrees():
    trees = set()
    for collection in (bpy.data.materials, bpy.data.worlds, bpy.data.lights):
        for block in collection:
            nt = getattr(block, 'node_tree', None)
            if nt is not None:
                trees.add(nt)
    for ng in bpy.data.node_groups:
        trees.add(ng)
    return trees


# Cache of file-existence checks keyed by absolute path. The lister redraws often
# (every mouse move over the window), so without this every visible asset row would
# stat the filesystem on every redraw - unworkable for big scenes / network paths.
# Populated lazily, cleared on file load, manual Refresh and after any relink.
_existsCache: dict = {}


def refreshAssetCache():
    """ Forget cached file-existence results so the next draw re-checks the disk. """
    _existsCache.clear()


def _isMissing(path: str) -> bool:
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
    'CAMERA':  ("Camera Files",      'CAMERA_DATA'),
}

# Node plugin type -> Assets kind (object refs set their kind explicitly).
_NODE_KIND = {
    'BitmapBuffer': 'BITMAP',
    'TexOCIO':      'OCIO',
    'BRDFScanned':  'SCANNED',
    'MtlVRmat':     'VRMAT',
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
    }.get(kind, "")


class AssetRef:
    """ One external-file reference in the scene. Holds either a (propGroup, attr) -
        a V-Ray plugin string attribute - or a Blender Image datablock (default-mode
        bitmaps keep their path on the Image). 'locator' is a stable string used to
        re-resolve the ref from an operator and as the selection key. """
    __slots__ = ('kind', 'element', 'locator', 'propGroup', 'attr', 'image')

    def __init__(self, kind, element, locator, propGroup=None, attr='', image=None):
        self.kind = kind
        self.element = element
        self.locator = locator
        self.propGroup = propGroup
        self.attr = attr
        self.image = image

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
            setattr(self.propGroup, self.attr, newPath)

    @property
    def isMissing(self) -> bool:
        return _isMissing(self.path)


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
        return getattr(bitmapBuffer, 'file', ""), f"n|{ntree.name}|{node.name}|BitmapBuffer|file"
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

    def add(kind, element, locator, propGroup, attr):
        if propGroup is None:
            return
        try:
            if attr not in propGroup.bl_rna.properties:
                return
        except AttributeError:
            return
        key = (id(propGroup), attr)
        if key in seen or not getattr(propGroup, attr, ""):
            return  # already added, or empty path (nothing referenced)
        seen.add(key)
        refs.append(AssetRef(kind, element, locator, propGroup=propGroup, attr=attr))

    def addPlugin(element, locatorPrefix, propGroup, pluginType, kind):
        for attr in fileAttrs.get(pluginType, ()):
            add(kind, element, f"{locatorPrefix}|{pluginType}|{attr}", propGroup, attr)

    # Node-tree references, in a single walk: file-path plugin attrs (bitmaps in external mode,
    # scanned / VRmat materials, OCIO textures, node-mode IES lights, ...) and the default-mode
    # V-Ray bitmaps whose path lives on a Blender Image datablock.
    imgSeen = set()
    for ntree in _vrayNodeTrees():
        for node in ntree.nodes:
            for pluginType, propGroup in _nodePluginGroups(node):
                kind = _NODE_KIND.get(pluginType, pluginType)
                addPlugin(f"{ntree.name} / {node.name}", f"n|{ntree.name}|{node.name}",
                          propGroup, pluginType, kind)

            if getattr(node, 'vray_plugin', '') != 'TexBitmap':
                continue
            bb = getattr(node, 'BitmapBuffer', None)
            if bb is not None and getattr(bb, 'use_external_image', False):
                continue  # external mode: path is on BitmapBuffer.file, added above
            tex = getattr(node, 'texture', None)
            img = getattr(tex, 'image', None) if tex is not None else None
            if (img is None or img.source in ('GENERATED', 'VIEWER')
                    or not img.filepath or id(img) in imgSeen):
                continue
            imgSeen.add(id(img))
            # Carry the BitmapBuffer propgroup too so the color-space column can edit
            # it; the path itself comes from the image (path/setPath prefer .image).
            refs.append(AssetRef('BITMAP', f"{ntree.name} / {node.name}",
                                 f"i|{img.name}", propGroup=bb, image=img))

    # Object / data / light / camera references that do not live on nodes.
    for obj in context.scene.objects:
        vrayObj = getattr(obj, 'vray', None)
        data = obj.data
        dataVray = getattr(data, 'vray', None)
        prefix = f"o|{obj.name}"

        if vrayObj is not None and getattr(vrayObj, 'isVRayGaussian', False):
            addPlugin(obj.name, prefix, getattr(vrayObj, 'GeomGaussians', None), 'GeomGaussians', 'SPLAT')

        if vrayObj is not None and dataVray is not None:
            asset = getattr(vrayObj, 'VRayAsset', None)
            if asset is not None:
                if asset.assetType == VRAY_ASSET_TYPE["Proxy"]:
                    addPlugin(obj.name, prefix, getattr(dataVray, 'GeomMeshFile', None), 'GeomMeshFile', 'PROXY')
                elif asset.assetType == VRAY_ASSET_TYPE["Scene"]:
                    addPlugin(obj.name, prefix, getattr(dataVray, 'VRayScene', None), 'VRayScene', 'SCENE')

        if obj.type == 'LIGHT' and isVrayLight(data) and lib_utils.getLightPluginType(data) == 'LightIES':
            addPlugin(obj.name, prefix, lib_utils.getLightPropGroup(data, 'LightIES'), 'LightIES', 'IES')

        if obj.type == 'CAMERA' and dataVray is not None:
            addPlugin(obj.name, prefix, getattr(dataVray, 'CameraPhysical', None), 'CameraPhysical', 'CAMERA')

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
        return {'FINISHED'}


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
        if self.filepath:
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
        if self.filepath:
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


class VRAY_OT_asset_open(VRayOperatorBase):
    bl_idname = "vray.asset_open"
    bl_label = "Open Asset"

    mode: bpy.props.StringProperty(options={'HIDDEN'})  # 'FOLDER' or 'FILE'
    ref: bpy.props.StringProperty(options={'HIDDEN'})

    @classmethod
    def description(cls, context, properties):
        if properties.mode == 'FOLDER':
            return "Open the folder that contains this file in your system file browser"
        return "Open this file in its default application"

    def execute(self, context):
        rec = next((r for r in collectAssetRefs(context) if r.locator == self.ref), None)
        if rec is None or not rec.path:
            return {'CANCELLED'}
        absPath = os.path.normpath(bpy.path.abspath(rec.path))
        if not os.path.exists(absPath):
            self.report({'WARNING'}, "File not found on disk")
            return {'CANCELLED'}
        target = os.path.dirname(absPath) if self.mode == 'FOLDER' else absPath
        bpy.ops.wm.path_open(filepath=target)
        return {'FINISHED'}


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
        return {'FINISHED'}


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
            VRAY_OT_relink_asset, VRAY_OT_asset_select, VRAY_OT_asset_open,
            VRAY_OT_asset_refresh, VRAY_OT_assets_make_paths,
            VRAY_MT_asset_relink, VRAY_MT_asset_paths)
