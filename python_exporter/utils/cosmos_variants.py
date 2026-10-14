# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

""" Chaos Cosmos variants: the picker and the swap behind it.

A Cosmos 3D model package can ship several material variants over one geometry, and several
geometry variants, each of which is its own package. The server reports the whole catalogue on
import; it is stored flattened geometry-major on the mesh datablock and drawn as a grid of preview
thumbnails. Clicking one asks the server to re-import that variant, which comes back through the
same queue a normal Cosmos import does.

Registered from utils/__init__.py, which runs after ui/ - the panels here are children of
VRAY_PT_VRayProxy and VRAY_PT_npanel, and Blender resolves bl_parent_id at registration.
"""

from enum import Enum
import bpy, os, time, uuid
import bpy.utils.previews

from vray_blender import debug
from vray_blender.lib import blender_utils
from vray_blender.lib.mixin import VRayOperatorBase
from vray_blender.nodes.operators.import_file import importCosmosAssetLights, importMaterials
from vray_blender.ui import classes, icons

from vray_blender.bin import VRayBlenderLib as vray


# Keep in sync with CosmosVariantStatus in zmq_message.hpp.
class CosmosVariantStatus(Enum):
    Downloading = 0
    Stalled = 1
    Failed = 2


# How long the spinner stays up before it is hidden. Nothing waits on this: the request is sent and
# returns, and the swap is still honoured whenever the reply lands. The only cost of a longer window
# is that the grid keeps repainting at _SWAP_TICK_INTERVAL to animate the ring, so this is set to
# comfortably cover a real download - previews plus the package - and then get out of the way well
# before the server's own stall report at around three minutes.
_SPINNER_TIMEOUT_SECONDS = 120.0


class PendingSwap:
    def __init__(self, objName: str, variantIndex: int):
        self.objName      = objName
        self.variantIndex = variantIndex
        self.startTime    = time.monotonic()


# token -> PendingSwap. Only ever touched from the import timer and from operators, i.e. the main
# thread, so no locking is needed.
_pendingSwaps: dict[str, PendingSwap] = {}

# Swaps whose spinner has been hidden but whose reply is still welcome. Separate from the above so
# that pendingIndexFor(), which drives the spinner, stops seeing them.
_expiredSwaps: dict[str, PendingSwap] = {}


def clearSwaps():
    """ Drop every in-flight swap. Called on addon unregister and before a .blend is replaced -
        a pending swap names an object in the file that is going away.
    """
    _pendingSwaps.clear()
    _expiredSwaps.clear()


def pendingIndexFor(objName: str) -> int:
    """ Index of the variant currently being applied to this object, or -1. Drives the loading
        ring in the grid.
    """
    for swap in _pendingSwaps.values():
        if swap.objName == objName:
            return swap.variantIndex
    return -1


def hasVariants(mesh) -> bool:
    """ True if this mesh datablock has a variant grid worth showing. A single-cell grid is not
        a choice, so it is hidden - same rule the other integrations use.
    """
    vrayData = getattr(mesh, 'vray', None)
    return len(getattr(vrayData, 'cosmos_variants', ())) >= 2


def setVariants(mesh, settings):
    """ Rebuild the mesh's variant grid from an import reply and mark the applied cell.

    The reply carries per-geometry-variant lists plus one preview path per (geometry, material)
    cell, geometry-major. Material counts differ between geometry variants, so the cells are
    walked with a running offset rather than a stride.
    """
    vrayData = mesh.vray
    vrayData.cosmos_variants.clear()

    names = list(settings.variantGeomNames)
    packageIds = list(settings.variantGeomPackageIds)
    revisions = list(settings.variantGeomRevisions)
    mtlCounts = list(settings.variantMtlCounts)
    previews = list(settings.variantPreviewPaths)

    if not (len(packageIds) == len(revisions) == len(mtlCounts)):
        debug.printError("Chaos Cosmos: inconsistent variant catalogue, ignoring it")
        return

    appliedIndex = 0
    cell = 0
    for geomIndex, mtlCount in enumerate(mtlCounts):
        for mtlIndex in range(mtlCount):
            variant = vrayData.cosmos_variants.add()
            variant.package_id  = packageIds[geomIndex]
            variant.revision_id = revisions[geomIndex]
            variant.geom_index  = geomIndex
            variant.geom_name   = names[geomIndex] if geomIndex < len(names) else ""
            variant.mtl_index   = mtlIndex
            variant.preview     = previews[cell] if cell < len(previews) else ""
            # Cosmos recomputes the geometry index per import and does not promise the variant
            # list keeps its order, so match the package rather than trust the position.
            if variant.package_id == settings.packageId and mtlIndex == settings.currentMtlVariant:
                appliedIndex = cell
            cell += 1

    vrayData.cosmos_variant_index = appliedIndex


def startSwap(obj: bpy.types.Object, variantIndex: int):
    """ Ask the server to apply one variant to obj. Returns True if a request was sent. """
    mesh = obj.data
    variants = mesh.vray.cosmos_variants

    if not (0 <= variantIndex < len(variants)):
        debug.printError(f"Chaos Cosmos: variant {variantIndex} is out of range for '{obj.name}'")
        return False

    if pendingIndexFor(obj.name) != -1:
        debug.printInfo(f"Chaos Cosmos: '{obj.name}' is already applying a variant")
        return False

    if variantIndex == mesh.vray.cosmos_variant_index:
        return False

    variant = variants[variantIndex]
    token = uuid.uuid4().hex
    _pendingSwaps[token] = PendingSwap(obj.name, variantIndex)

    vray.cosmosSwapVariant(variant.package_id, variant.revision_id, variant.mtl_index, token)
    _tagRedraw()
    return True


def applySwap(settings, scaleUnit=1.0):
    """ Apply a swap reply to the object that asked for it. Runs on the import timer, so the whole
        rewrite lands in one undo step.
    """
    token = settings.variantSwapToken
    # A swap whose spinner already timed out is still applied - see _SPINNER_TIMEOUT_SECONDS.
    swap = _pendingSwaps.pop(token, None) or _expiredSwaps.pop(token, None)
    if swap is None:
        debug.printWarning("Chaos Cosmos: received a variant swap result nobody is waiting for")
        return

    _tagRedraw()

    obj = bpy.data.objects.get(swap.objName)
    if obj is None:
        debug.printWarning(f"Chaos Cosmos: '{swap.objName}' is gone, dropping its variant swap")
        return

    if not settings.matFile:
        debug.reportError(f"Could not apply the Chaos Cosmos variant to '{obj.name}'")
        return

    mesh = obj.data
    oldPackageId = mesh.vray.cosmos_package_id
    oldMaterials = [slot.material for slot in obj.material_slots if slot.material]

    # The material importer only ever appends slots, so the old ones have to go first.
    mesh.materials.clear()
    importMaterials(settings.matFile,
                    objectForMatAssign=obj,
                    locationsMap=settings.locationsMap,
                    cosmosAssetContext=settings)
    _freeOrphanedCosmosMaterials(oldMaterials)

    # A geometry variant is a different package, so it brings its own proxy file and its own
    # lights. A material-only swap leaves both alone.
    geomMeshFile = mesh.vray.GeomMeshFile
    swappedGeometry = settings.objFile and (settings.objFile != bpy.path.abspath(geomMeshFile.file))
    if swappedGeometry:
        geomMeshFile.file = settings.objFile
        blender_utils.setShadowAttr(geomMeshFile, 'file', geomMeshFile.file)
        _removeCosmosLightChildren(obj, oldPackageId)
        importCosmosAssetLights(settings, obj, scaleUnit)

    mesh.vray.cosmos_package_id = settings.packageId
    mesh.vray.cosmos_revision_id = settings.revisionId
    mesh.vray.cosmos_asset_name = settings.assetName
    setVariants(mesh, settings)

    # A download may have brought previews that were missing when the grid was first drawn.
    retryMissingPreviews(settings.variantPreviewPaths)

    bpy.ops.ed.undo_push(message="Apply Cosmos Variant")


def swapStatusCallback(token: str, status: int, message: str):
    """ Cosmos variant status callback, invoked from a server worker thread. Only Failed is
        terminal; the rest is progress reporting. Deferred to the main thread, which is the only
        one that may touch bpy data.
    """
    def apply():
        if status != CosmosVariantStatus.Failed.value:
            debug.printInfo(f"Chaos Cosmos: downloading a variant asset ({CosmosVariantStatus(status).name})")
            return None

        swap = _pendingSwaps.pop(token, None) or _expiredSwaps.pop(token, None)
        objName = f"'{swap.objName}'" if swap else "an object"
        debug.reportError(f"Could not apply the Chaos Cosmos variant to {objName}: {message}")
        _tagRedraw()
        return None # one-shot timer

    bpy.app.timers.register(apply)


def tickSwaps() -> bool:
    """ Hide the spinner of any swap that has outlived _SPINNER_TIMEOUT_SECONDS and keep the rest
        animating. Returns True while a spinner is still showing. Called from the Cosmos import
        timer.
    """
    now = time.monotonic()
    for token in [t for t, s in _pendingSwaps.items() if (now - s.startTime) > _SPINNER_TIMEOUT_SECONDS]:
        swap = _expiredSwaps[token] = _pendingSwaps.pop(token)
        # Not an error: the server is most likely still downloading. It will be applied on arrival.
        debug.printInfo(f"Chaos Cosmos: still applying a variant to '{swap.objName}', "
                        f"this can take a while for an asset that is not downloaded yet")

    if not _pendingSwaps:
        return False

    _tagRedraw()
    return True


def _freeOrphanedCosmosMaterials(materials):
    """ Remove the Cosmos materials the swap left unused. Anything the user made or assigned
        elsewhere survives, which is the rule the other integrations use too.
    """
    for material in materials:
        if material.users == 0 and not material.use_fake_user and material.vray.cosmos_package_id:
            bpy.data.materials.remove(material)


def _removeCosmosLightChildren(obj: bpy.types.Object, packageId: str):
    """ Delete the light children the previous geometry variant brought in. The new variant's
        package may declare a different set, or none at all.
    """
    for child in list(obj.children):
        if child.type != 'LIGHT':
            continue
        if getattr(child.data.vray, 'cosmos_package_id', "") == packageId:
            bpy.data.objects.remove(child, do_unlink=True)


def _tagRedraw():
    """ Repaint the panels hosting the variant grid. Also what advances the loading ring. """
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type in {'PROPERTIES', 'VIEW_3D'}:
                area.tag_redraw()


######## ##     ## ##     ## ##     ## ########  ##    ##    ###    #### ##        ######
   ##    ##     ## ##     ## ###   ### ##     ## ###   ##   ## ##    ##  ##       ##    ##
   ##    ##     ## ##     ## #### #### ##     ## ####  ##  ##   ##   ##  ##       ##
   ##    ######### ##     ## ## ### ## ########  ## ## ## ##     ##  ##  ##        ######
   ##    ##     ## ##     ## ##     ## ##     ## ##  #### #########  ##  ##             ##
   ##    ##     ## ##     ## ##     ## ##     ## ##   ### ##     ##  ##  ##       ##    ##
   ##    ##     ##  #######  ##     ## ########  ##    ## ##     ## #### ########  ######

# Thumbnail size in UI units. Matches the material lister's grid and Cinema 4D's 100px tiles.
_THUMB_SCALE = 5.0

# Seconds per sweep of the spinner in the cell being applied. Cosmos reports no usable progress
# for a single package - downloadMissingAssetFiles only counts whole packages, so a swap's one
# callback arrives at completion - so this is an indeterminate "still working" sweep, not a
# percentage. Slow enough not to look frantic during a download that can run for minutes.
_SPINNER_PERIOD_SECONDS = 2.5

# Cosmos previews live outside the addon and are discovered at runtime, so they get their own
# collection - icons.py's is loaded eagerly from a fixed list and must survive the whole session.
_PREVIEWS: bpy.utils.previews.ImagePreviewCollection = None

# Absolute preview path -> key in _PREVIEWS. Also keeps load() from ever being called twice for
# the same path, which would raise.
_previewKeys: dict[str, str] = {}

# Paths already found missing. Doubles as the stat cache, so draw() never re-stats.
_missingPreviews: set[str] = set()

# (package id, revision, material index) -> preview path the server resolved this session. Previews
# are cached per user and outside the .blend, so the path an import stored on a cell is only a first
# guess for a scene that was opened somewhere else - this is where the answer for such a scene lands.
_resolvedPreviews: dict[tuple[str, int, int], str] = {}

# (package id, revision) already asked of the server. One attempt per package per session: a package
# that cannot be resolved - Cosmos closed, not logged in, offline - would otherwise be re-asked on
# every redraw of the grid.
_requestedPreviews: set[tuple[str, int]] = set()


def previewIconId(path: str) -> int:
    """ Icon id for a variant preview, or the V-Ray placeholder when it cannot be loaded. """
    if not path or path in _missingPreviews:
        return icons.getIcon('VRAY_PLACEHOLDER')

    if (key := _previewKeys.get(path)) is not None:
        return _PREVIEWS[key].icon_id

    if not os.path.isfile(path):
        _missingPreviews.add(path)
        return icons.getIcon('VRAY_PLACEHOLDER')

    key = f"cosmosvar_{len(_previewKeys)}"
    _previewKeys[path] = key
    # Only icon_id is read from here on. Touching icon_size would decode the file synchronously
    # instead of letting Blender's icon job do it in the background.
    return _PREVIEWS.load(key, path, 'IMAGE').icon_id


def previewPath(variant) -> str:
    """ Where this cell's preview is: what the server resolved for it this session, or the path the
        import stored on the cell.
    """
    return _resolvedPreviews.get((variant.package_id, variant.revision_id, variant.mtl_index)) or variant.preview


def requestMissingPreviews(mesh):
    """ Ask the server to download the previews this grid could not show.

    A cleaned preview cache, or a .blend from a machine whose Cosmos cache never held these assets,
    leaves the cells with paths that resolve to nothing. The server re-downloads a whole package's
    previews at a time and reports back through previewsCallback, which is what puts the images on
    screen - nothing here waits for it.
    """
    if not vray.isRunning():
        # Nothing was sent, so nothing is marked as asked and the next redraw tries again.
        return

    packageIds = []
    revisions = []
    for variant in mesh.vray.cosmos_variants:
        path = previewPath(variant)
        if path and path not in _missingPreviews:
            continue

        key = (variant.package_id, variant.revision_id)
        if not key[0] or key in _requestedPreviews:
            continue

        _requestedPreviews.add(key)
        packageIds.append(key[0])
        revisions.append(key[1])

    if packageIds:
        vray.cosmosFetchVariantPreviews(packageIds, revisions)


def previewsCallback(packageIds, revisions, mtlIndices, paths):
    """ Cosmos variant previews reply, invoked from a server worker thread. The previews are on disk
        by now; deferred to the main thread, which is the only one that may repaint.
    """
    def apply():
        for packageId, revision, mtlIndex, path in zip(packageIds, revisions, mtlIndices, paths):
            _resolvedPreviews[(packageId, revision, mtlIndex)] = path
            # The download may have produced exactly the path the grid already gave up on, which is
            # what a preview deleted from an otherwise intact cache looks like.
            _missingPreviews.discard(path)
        _tagRedraw()
        return None # one-shot timer

    bpy.app.timers.register(apply)


def retryMissingPreviews(paths):
    """ Re-stat the previews that were absent, so a download that has just produced them is picked
        up on the next redraw.

        Only the missing ones: a loaded preview is never dropped. Each variant has its own file, so
        a path that resolved once always resolves to the same image, and re-loading it would make
        the whole grid visibly reload on every variant change.
    """
    for path in paths:
        _missingPreviews.discard(path)


def _hasVariants(context) -> bool:
    obj = context.object
    if obj is None or obj.type != 'MESH':
        return False
    if obj.vray.VRayAsset.assetType != blender_utils.VRAY_ASSET_TYPE["Proxy"]:
        return False
    return hasVariants(obj.data)


def drawVariantGrid(layout: bpy.types.UILayout, context: bpy.types.Context):
    obj = context.object
    mesh = obj.data

    # A swap regenerates the preview mesh, which is not safe from edit mode.
    classes.disableLayoutInEditMode(layout, context)

    applied = mesh.vray.cosmos_variant_index
    pending = pendingIndexFor(obj.name)

    # Say it in words too. The spinner alone cannot convey that the wait is open-ended, and a
    # download of a variant that is not on disk yet can take minutes.
    if pending != -1:
        layout.label(text="Applying variant, downloading if needed...", icon='SORTTIME')

    # Cells are stored geometry-major, so grouping them keeps the geometry variants in order.
    # Each group gets its own grid under a heading, otherwise several geometry variants show up
    # as one long run of identically named cells.
    groups = {}
    for i, variant in enumerate(mesh.vray.cosmos_variants):
        groups.setdefault(variant.geom_index, []).append((i, variant))

    for cells in groups.values():
        if len(groups) > 1:
            layout.label(text=_geometryLabel(cells[0][1]))

        grid = layout.grid_flow(row_major=True, columns=0, even_columns=True, even_rows=False, align=True)
        for i, variant in cells:
            cell = grid.box().column(align=True)
            cell.template_icon(icon_value=previewIconId(previewPath(variant)), scale=_THUMB_SCALE)

            if i == pending:
                # No text - a ring with a label is 8 UI units wide and would stretch the column.
                ring = cell.row(align=True)
                ring.alignment = 'CENTER'
                ring.progress(factor=_spinnerFactor(), type='RING')
            else:
                # template_icon draws a label, not a button, so the thumbnail itself cannot be
                # clicked - this row is the click target. It carries the variant name because an
                # unlabelled bar reads as decoration rather than a button.
                op = cell.operator(VRAY_OT_apply_cosmos_variant.bl_idname,
                                   text=_variantLabel(variant),
                                   icon='CHECKMARK' if i == applied else 'BLANK1',
                                   depress=(i == applied))
                op.object_name = obj.name
                op.variant_index = i

    requestMissingPreviews(mesh)


def _spinnerFactor() -> float:
    """ Sweep position for the applying cell's ring. Indeterminate - see _SPINNER_PERIOD_SECONDS. """
    return (time.monotonic() % _SPINNER_PERIOD_SECONDS) / _SPINNER_PERIOD_SECONDS


def _geometryLabel(variant) -> str:
    """ Heading for one geometry variant's group of cells. """
    return variant.geom_name or f"Geometry {variant.geom_index + 1}"


def _variantLabel(variant) -> str:
    """ Cell caption. 1-based "Variant N", the same labelling Maya and 3ds Max use for the
        material axis, and short enough never to widen the column past the thumbnail.
    """
    return f"Variant {variant.mtl_index + 1}"


class VRAY_OT_apply_cosmos_variant(VRayOperatorBase):
    bl_idname       = "vray.apply_cosmos_variant"
    bl_label        = "Apply Cosmos Variant"
    bl_description  = "Apply this Chaos Cosmos variant to the asset"
    # No 'UNDO': this only sends a request. The swap pushes its own undo step once it arrives.
    bl_options      = {'INTERNAL'}

    object_name: bpy.props.StringProperty()
    variant_index: bpy.props.IntProperty()

    @classmethod
    def description(cls, context, properties):
        obj = bpy.data.objects.get(properties.object_name)
        if obj is None:
            return cls.bl_description

        variants = obj.data.vray.cosmos_variants
        if not (0 <= properties.variant_index < len(variants)):
            return cls.bl_description

        variant = variants[properties.variant_index]
        name = f"material variant {variant.mtl_index + 1}"
        if len({v.geom_index for v in variants}) > 1:
            return f"Apply {name} of '{_geometryLabel(variant)}'"
        return f"Apply {name}"

    def execute(self, context):
        obj = bpy.data.objects.get(self.object_name)
        if obj is None:
            return {'CANCELLED'}
        return {'FINISHED'} if startSwap(obj, self.variant_index) else {'CANCELLED'}


class VRAY_PT_CosmosVariants(classes.VRayGeomPanel):
    bl_label     = "Variants"
    bl_parent_id = "VRAY_PT_VRayProxy"
    vray_icon    = "COSMOS"

    @classmethod
    def poll_custom(cls, context):
        return _hasVariants(context)

    def draw(self, context):
        drawVariantGrid(self.layout, context)


class VRAY_PT_npanel_cosmos_variants(classes.VRayPanel):
    bl_space_type  = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category    = 'V-Ray'
    bl_parent_id   = "VRAY_PT_npanel"
    bl_label       = "Variants"
    vray_icon      = "COSMOS"

    @classmethod
    def poll_custom(cls, context):
        return _hasVariants(context)

    def draw(self, context):
        drawVariantGrid(self.layout, context)


def _getRegClasses():
    return (
        VRAY_OT_apply_cosmos_variant,
        VRAY_PT_CosmosVariants,
        VRAY_PT_npanel_cosmos_variants,
    )


def register():
    from vray_blender.lib.class_utils import registerClass

    global _PREVIEWS
    _PREVIEWS = bpy.utils.previews.new()

    for regClass in _getRegClasses():
        registerClass(regClass)


def unregister():
    for regClass in reversed(_getRegClasses()):
        bpy.utils.unregister_class(regClass)

    global _PREVIEWS
    _previewKeys.clear()
    _missingPreviews.clear()
    _resolvedPreviews.clear()
    _requestedPreviews.clear()
    bpy.utils.previews.remove(_PREVIEWS)
    _PREVIEWS = None
