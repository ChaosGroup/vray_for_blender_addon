# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

from contextlib import contextmanager
from enum import Enum
import bpy, math, os, time, queue

from mathutils import Quaternion, Vector

from vray_blender import debug
from vray_blender.utils import cosmos_asset_set, cosmos_scatter_preset, cosmos_drop_batch
from vray_blender.exporting.tools import isObjectVrayProxy, isObjectVRayDecal
from vray_blender.lib.lib_utils import getCosmosAssetName, getLightPropGroup
from vray_blender.lib.blender_utils import selectObject
from vray_blender.lib.image_utils import untrackImage
from vray_blender.lib.mixin import VRayOperatorBase
from vray_blender.nodes.operators.import_file import  importDecal, importHDRI, importMaterials, importParallaxInterior, importCosmosCompositeAsset
from vray_blender.nodes.utils import getNodeByType, treeHasNodes, DisableAutoConnect

from vray_blender.bin import VRayBlenderLib as vray

# Light types whose external file lives directly on the light's plugin property group, as
# opposed to DOME, whose bitmap is a node in the light's tree.
# V-Ray light type -> (plugin type, name of the file attribute).
LIGHT_FILE_ASSETS = {
    'IES':       ('LightIES',       'ies_file'),
    'LUMINAIRE': ('LightLuminaire', 'file'),      # the luminaire cache (.vlw)
}

# Cosmos relinking states, keep in sync with the enum in zmq_messages.hpp.
class CosmosRelinkStatus(Enum):
    CheckingIntegrity  = -1 # Python only state to block while checking integrity in c++.
    AllAssetsValid = 0 # All assets are valid and properly linked. In theory we should never end up getting it.
    NotLoggedIn = 1 # The user is not logged in so relinking is not at all possible.
    RelinkOnly = 2 # Only relinking i.e. changing paths on some assets.
    DownloadAndRelink = 3 # Download the missing assets and then relink them.
    Aborted = 4 # Only used if something goes wrong with the zmq server.

# Cosmos download states, keep in sync with the enum in zmq_messages.hpp.
class CosmosDownloadStatus(Enum):
    Downloading = -1  # Python only state to block while downloading assets.
    Cancelled = 0 # The download was cancelled in the python process.
    Timeout = 1 # The download of an asset timed out and was cancelled.
    Done = 2 # Download and relinking was successful.
    Aborted = 3 # Only used if something goes wrong with the zmq server.

# Enum used to select which browser page to open. Keep in sync with CosmosBrowserPage in cosmos_importer.cpp.
class CosmosBrowserPage(Enum):
    HomePage = 0
    AIGenerator = 1

"""
When relinking is started first all materials, proxies and (IES/Dome)lights in the scene are checked. If any
of them have missing assets they are added to a list and sent back to the Zmq server. Blender UI is locked
and we make a request to cosmos to check the integrity of the assets and also return back the calculated asset
download size. The list of assets is stored in the server and it will wait for a download missing request. When
the request is sent and a download is necessary Blender will also be locked and wait until the download is
completed. BlenderCosmosImporter::downloadMissingAssets(...) will send back the newly relinked asset paths which
are then directly changed in the materials and objects.
"""

class VRAY_OT_dummy(VRayOperatorBase):
    bl_idname = "vray.dummy_operator"
    bl_label = "Does Nothing"

    def execute(self, context):
        return {'FINISHED'}

class VRAY_OT_show_cosmos_info_popup(VRayOperatorBase):
    bl_idname       = "vray.cosmos_info_popup"
    bl_label        = "Chaos Cosmos"
    bl_description  = "Chaos Cosmos Info Message"

    message: bpy.props.StringProperty()
    messageRow2: bpy.props.StringProperty()

    def execute(self, context):
        return { 'FINISHED' }

    def draw(self, context):
        layout = self.layout
        layout.label(text=self.message, icon='ERROR')

        # Splitting the message with \n does not work for labels since this operator behaves
        # a bit different than invoke_confirm(...)
        if self.messageRow2:
            layout.label(text=self.messageRow2)
        layout.template_popup_confirm("vray.dummy_operator", cancel_text="")

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=400)

class CosmosHandler:
    def __init__(self):
        self.relinked = False
        self.unresolvedCallbacks = []
        self.missingAssetsStatus: CosmosRelinkStatus = CosmosRelinkStatus.AllAssetsValid
        self.downloadStatus: CosmosDownloadStatus = CosmosDownloadStatus.Done
        self.downloadSizeMB = 0
        self.relinkedAssets = []

    def _setCosmosRelinkState(self, status, downloadSize):
        self.missingAssetsStatus = status
        self.downloadSizeMB = downloadSize

    def _setCosmosDownloadedAssets(self, downloadStatus, relinkedAssets):
        self.downloadStatus = downloadStatus
        self.relinkedAssets = relinkedAssets

    def _getPathIfAssetMissing(self, path):
        resolvedPath = bpy.path.abspath(path)
        return resolvedPath if not os.path.exists(resolvedPath) else None

    def startDownload(self) -> bool:
        """ Kick off the asset download. Returns False if there is nothing to download. """
        if not self.relinked:
            return False
        self.downloadStatus = CosmosDownloadStatus.Downloading
        vray.downloadMissingAssets()
        return True

    def applyDownloadResult(self):
        """ Apply the download result once downloadStatus leaves Downloading. Returns an operator result dict. """
        if self.downloadStatus == CosmosDownloadStatus.Cancelled:
            return {'CANCELLED'}

        if self.downloadStatus == CosmosDownloadStatus.Aborted:
            bpy.ops.vray.cosmos_info_popup('INVOKE_DEFAULT', message="Download has failed. No assets have been relinked.")
            return {'CANCELLED'}

        if self.downloadStatus == CosmosDownloadStatus.Timeout:
            bpy.ops.vray.cosmos_info_popup('INVOKE_DEFAULT', message="Asset relinking has timed out.")
            return {'CANCELLED'}

        for i, assetPath in enumerate(self.relinkedAssets):
            if assetPath:
                self.unresolvedCallbacks[i](assetPath)

        debug.printInfo(f'{len(self.unresolvedCallbacks)} assets have been successfully relinked')
        return {'FINISHED'}

    def abortDownload(self):
        self.downloadStatus = CosmosDownloadStatus.Aborted
        self.missingAssetsStatus = CosmosRelinkStatus.Aborted

    def checkMissingAssets(self, operator: bpy.types.Operator, context: bpy.types.Context, event: bpy.types.Event):
        self.unresolvedCallbacks = []
        self.missingAssetsStatus = CosmosRelinkStatus.CheckingIntegrity

        unresolvedPackageIds, unresolvedRevisionIds, unresolvedPaths = [], [], []

        def checkNodeTreeAssets(dataObject, ntree: bpy.types.NodeTree):
            # Register unresolved callbacks for the nodes in a  node tree. The object passed in
            # should have valid cosmos_package_id and cosmos_revision_id properties.
            for node in ntree.nodes:
                if (texture := getattr(node, 'texture', None)) and (image := getattr(texture, 'image', None)):
                    if unresolvedPath := self._getPathIfAssetMissing(image.filepath):
                        unresolvedPackageIds.append(dataObject.vray.cosmos_package_id)
                        unresolvedRevisionIds.append(dataObject.vray.cosmos_revision_id)
                        unresolvedPaths.append(unresolvedPath)
                        self.unresolvedCallbacks.append(lambda x, img=image: (setattr(img, 'filepath', x), untrackImage(img)))
                elif (brdfScanned := getattr(node, 'BRDFScanned', None)):
                    if unresolvedPath := self._getPathIfAssetMissing(brdfScanned.file):
                        unresolvedPackageIds.append(dataObject.vray.cosmos_package_id)
                        unresolvedRevisionIds.append(dataObject.vray.cosmos_revision_id)
                        unresolvedPaths.append(unresolvedPath)
                        self.unresolvedCallbacks.append(lambda x, brdf=brdfScanned: setattr(brdf, 'file', x))

        for material in bpy.data.materials:
            if not hasattr(material, 'vray') or not material.node_tree or not material.use_nodes:
                continue
            if not material.vray.cosmos_package_id:
                continue
            checkNodeTreeAssets(material, material.node_tree)

        for obj in bpy.data.objects:
            if obj.type != 'MESH':
                continue
            mesh = obj.data
            if not hasattr(mesh, 'vray') or not mesh.vray.cosmos_package_id:
                continue
            if (isObjectVrayProxy(obj) and (vrayProxy := mesh.vray.GeomMeshFile)):
                if unresolvedPath := self._getPathIfAssetMissing(vrayProxy.file):
                    unresolvedPackageIds.append(mesh.vray.cosmos_package_id)
                    unresolvedRevisionIds.append(mesh.vray.cosmos_revision_id)
                    unresolvedPaths.append(unresolvedPath)
                    self.unresolvedCallbacks.append(lambda x, proxyObj=obj, proxy=vrayProxy: (selectObject(proxyObj), setattr(proxy, 'file', x)))
            elif isObjectVRayDecal(obj) and treeHasNodes(obj.vray.ntree) and (getNodeByType(obj.vray.ntree, 'VRayNodeDecalOutput') is not None):
                checkNodeTreeAssets(obj.data, obj.vray.ntree)

        for light in bpy.data.lights:
            if not hasattr(light, 'vray') or not light.use_nodes or not light.node_tree or not light.vray.cosmos_package_id:
                continue
            if lightAsset := LIGHT_FILE_ASSETS.get(light.vray.light_type):
                pluginType, attrName = lightAsset
                propGroup = getLightPropGroup(light, pluginType)
                if unresolvedPath := self._getPathIfAssetMissing(getattr(propGroup, attrName)):
                    unresolvedPackageIds.append(light.vray.cosmos_package_id)
                    unresolvedRevisionIds.append(light.vray.cosmos_revision_id)
                    unresolvedPaths.append(unresolvedPath)
                    self.unresolvedCallbacks.append(lambda x, pg=propGroup, attr=attrName: setattr(pg, attr, x))
            elif light.vray.light_type == 'DOME':
                checkNodeTreeAssets(light, light.node_tree)

        if len(unresolvedPackageIds) == 0:
            return bpy.ops.vray.cosmos_info_popup('INVOKE_DEFAULT', message='There are no Cosmos assets that require relinking.',
                messageRow2='Only assets imported after hotfix 7.00.24 can be relinked.')

        vray.calculateDownloadSize(unresolvedPackageIds, unresolvedRevisionIds, unresolvedPaths)

        timeout = 0
        MAX_DOWNLOAD_SIZE_TIMEOUT = 30 # Hard timeout in case something goes wrong with the zmq server.
        # Block and wait until the total asset size is calculated... Should not be slower than a 2-3s
        while self.missingAssetsStatus == CosmosRelinkStatus.CheckingIntegrity:
            time.sleep(0.1)
            timeout += 0.1

            if timeout > MAX_DOWNLOAD_SIZE_TIMEOUT:
                return bpy.ops.vray.cosmos_info_popup('INVOKE_DEFAULT', message='Cosmos asset integrity check has timed out')

        self.relinked = False
        # The user is not logged in or the cosmos service is not running.
        if self.missingAssetsStatus == CosmosRelinkStatus.NotLoggedIn:
            return bpy.ops.vray.cosmos_info_popup('INVOKE_DEFAULT', message="Please open Cosmos and log in first before relinking Cosmos assets.")

        if self.missingAssetsStatus == CosmosRelinkStatus.Aborted:
            return bpy.ops.vray.cosmos_info_popup('INVOKE_DEFAULT', message="Checking for asset integrity has failed. No assets have been relinked.")

        if self.missingAssetsStatus == CosmosRelinkStatus.AllAssetsValid:
            return bpy.ops.vray.cosmos_info_popup('INVOKE_DEFAULT', message="There are no Cosmos assets that require relinking.",
                messageRow2="Only assets imported after hotfix 7.00.24 can be relinked.")

        self.relinked = True
        if self.missingAssetsStatus == CosmosRelinkStatus.RelinkOnly:
            return context.window_manager.invoke_confirm(
                operator, event, title="", icon='WARNING',
                message="Would you like to relink your Cosmos assets?"
            )
        else:
            return context.window_manager.invoke_confirm(
                operator, event, title="", icon='WARNING',
                message=f'The scene has {self.downloadSizeMB}MB of missing cosmos assets.\nWould you like to download them and recover the asset file references?'
            )

cosmosHandler = CosmosHandler()


def importCosmosAssets(assetNames, applyTriplanarMapping: bool = False, applyRealWorldScale: bool = False):
    """ Import one or more Chaos Cosmos assets by asset name or package id.

    Sends a request to the Cosmos client (through the ZMQ server) to resolve each
    entry (asset name -> package id, falling back to treating it as a raw id),
    download it if missing, and import it. The import is asynchronous: when the
    Cosmos client responds, the assets are built on the main thread by
    'assetImportTimerFunction'. This mirrors what the Cosmos browser does when the
    user picks an asset - the only difference is that the asset is chosen by name
    from code instead of clicked in the browser.

    Requires the Chaos Cosmos client to be logged in; the resolution/download happens
    through the Cosmos service. Works in headless Blender too - the Cosmos client has
    no GUI dependency (only the interactive asset browser is GUI-only).

    Args:
        assetNames: A single asset name/package id, or a list of them.
        applyTriplanarMapping: Wrap material textures in a TexTriPlanar node on import.
        applyRealWorldScale: Map textures using their real-world dimensions.
    """
    from vray_blender import engine

    if isinstance(assetNames, str):
        assetNames = [assetNames]
    names = [n for n in assetNames if n]
    if not names:
        debug.printError("importCosmosAssets: no asset name/id provided")
        return

    if not hasattr(vray, "importCosmosAsset"):
        debug.printError("importCosmosAssets: the native library does not expose 'importCosmosAsset'. "
                         "A rebuild/reinstall of VRayBlenderLib is required.")
        return

    # Ensure the ZMQ server + control connection (which hosts the Cosmos client) are up
    # and that the asset-import timer that applies the result is registered.
    engine.ensureRunning()

    vray.importCosmosAsset(names, applyTriplanarMapping, applyRealWorldScale)
    debug.printInfo(f"Requested Cosmos import of {len(names)} asset(s): {', '.join(names)}")


class VRAY_OT_import_cosmos_asset(VRayOperatorBase):
    bl_idname       = "vray.import_cosmos_asset"
    bl_label        = "Import Cosmos Asset"
    bl_description  = "Import one or more Chaos Cosmos assets by name or package id"
    bl_options      = {'INTERNAL'}

    # Comma-separated list of asset names or package ids to import.
    asset_names: bpy.props.StringProperty(
        name        = "Asset Names",
        description = "Asset name(s) or package id(s) to import; separate multiple entries with commas",
        default     = ""
    )
    apply_triplanar_mapping: bpy.props.BoolProperty(
        name        = "Triplanar Mapping",
        description = "Wrap material textures in a TexTriPlanar node on import",
        default     = False
    )
    apply_real_world_scale: bpy.props.BoolProperty(
        name        = "Real-World Scale",
        description = "Map textures using their real-world dimensions",
        default     = False
    )

    def execute(self, context):
        names = [n.strip() for n in self.asset_names.split(",") if n.strip()]
        if not names:
            self.report({'WARNING'}, "No Cosmos asset name/id provided")
            return {'CANCELLED'}
        importCosmosAssets(names, self.apply_triplanar_mapping, self.apply_real_world_scale)
        return {'FINISHED'}

# Modifying bpy data structures from another thread could crash Blender.
# This means that vrmat and vrmesh file importing can be done only from the main thread.
# For that reason 'assetImportTimerFunction' is registered as timer and waits until asset import data is
# added to the 'assetImportQueue' from 'assetImportCallback' which is safe for execution from another thread
# This is the recommended by the  blender community way for dealing with this problem:
# https://docs.blender.org/api/current/bpy.app.timers.html#use-a-timer-to-react-to-events-in-another-thread
assetImportQueue = queue.Queue()

# Scale applied when importing Cosmos Assets
COSMOS_SCALE_UNIT = 0.01 # centimeters

# The import timer runs on the main thread and Blender cannot redraw while it does, so an
# Asset Set (hundreds of members, all queued at once) has to be drained a few at a time.
_MAX_IMPORTS_PER_TICK = 4
_BUSY_TICK_INTERVAL = 0.1
_IDLE_TICK_INTERVAL = 1.0


@contextmanager
def _dropPlacement(settings):
    """Temporarily move the bpy.context.scene.cursor to the drop world position while an
    imported asset is being created, then restore it. The existing Cosmos
    importers (importProxyFromMeshFile, importParallaxInterior, importDecal)
    all place new objects at bpy.context.scene.cursor.location, so overriding
    it is the cheapest way to steer their placement to the drop
    point without touching each importer's signature.

    When settings.hasDropCoords is False (i.e. this import came from the
    Cosmos browser's Import button rather than drag-and-drop) this is a
    no-op and the cursor is left alone.

    Note that bpy.context.scene.cursor is just a collection of a few vectors used by Blender,
    it is not related to the actual OS mouse cursor.
    """
    if not getattr(settings, 'hasDropCoords', False):
        yield
        return

    cursor = bpy.context.scene.cursor
    saved = cursor.location.copy()
    try:
        drop_loc = Vector((settings.worldX, settings.worldY, settings.worldZ))
        debug.printDebug(
            f"Cosmos drop: overriding 3D cursor "
            f"{tuple(saved)} -> {tuple(drop_loc)} "
            f"for import of {settings.assetType} '{settings.packageId}'"
        )
        cursor.location = drop_loc
        yield
    finally:
        cursor.location = saved


def _resolveDropTargetObject(settings):
    """If the drop was directly over a scene object, return it so material
    imports can target its material slots. Returns None for non-drop imports
    or when the ray missed all geometry."""
    name = getattr(settings, 'dropTargetObject', '')
    if not name:
        return None
    obj = bpy.data.objects.get(name)
    if obj is None:
        debug.printDebug(f"Cosmos drop: target object {name!r} no longer in scene")
    return obj


def _isCosmosAssetObject(obj):
    """True if obj's data block is tagged as a Cosmos asset.

    Guards every step of the obj.data.vray.cosmos_package_id chain: obj may
    be None, its data may be None (e.g. an Empty) and non-mesh data blocks
    may not carry a 'vray' property group, so a bare chained read can raise.
    """
    data = getattr(obj, 'data', None)
    vrayData = getattr(data, 'vray', None)
    return bool(getattr(vrayData, 'cosmos_package_id', ''))


def _importDroppedMaterial(settings):
    """Import a Cosmos material and give it to the object it was dropped on, if any.

    Which slot it lands in follows Blender's own material drop
    (OBJECT_OT_drop_named_material): the slot under the cursor is replaced, an
    unresolved slot falls back to the first one, a slot is created when the object has
    none, and nothing else on the object is touched. Blender clamps the picked slot with
    max_ii(mat_slot, 1) and BKE_object_material_assign() then writes that one slot only -
    it never clears the others, so neither do we.

      no target object    Imported via the Cosmos browser's Import button, or dropped
                          over empty space. The datablock is created, nothing is
                          assigned.
      dropTargetSlot >= 0 Slot resolved from the hit face's material_index (viewport
                          drops) or from active_material_index (Outliner drops).
                          Replace that slot.
      dropTargetSlot < 0  No slot was resolved - the target has none, or the hit face
                          carries a material_index past the end of the slot array,
                          which Blender allows and only clamps on read. Replace slot 0,
                          creating it when the object has no slots at all.

    A drop of several assets assigns nothing - see isMultiAssetDrop() for why.

    importMaterials() is called WITHOUT an objectForMatAssign so the new datablock can be
    slotted in manually afterwards; left to itself it would append a slot of its own. The
    freshly-created material is located by diffing bpy.data.materials around the call.

    forceDefaultUVChannel is True when the target mesh is NOT a Cosmos asset (so the
    Cosmos-style channel indices in the dropped material would otherwise sample at (0,0)
    and render flat). For a Cosmos-on-Cosmos drop we leave the indices alone, since the
    target mesh's UV layout already matches them.
    """
    targetObj = _resolveDropTargetObject(settings)

    # Snapshot existing materials so we can identify the new one.
    matsBefore = set(bpy.data.materials.keys())

    forceDefaultUV = not _isCosmosAssetObject(targetObj)
    importMaterials(filePath=settings.matFile,
                    objectForMatAssign=None,
                    locationsMap=settings.locationsMap,
                    forceDefaultUVChannel=forceDefaultUV,
                    cosmosAssetContext=settings)

    if targetObj is None:
        return

    if cosmos_drop_batch.isMultiAssetDrop(settings):
        # Several assets were dropped together and they all name the same target and the
        # same slot. Assigning each of them would leave whichever arrived last as the
        # winner - not the one the user pointed at, and not even the same one twice,
        # since the arrival order is whatever Cosmos delivers first. V-Ray for 3ds Max
        # draws the line in the same place and Cinema 4D never assigns at all.
        if cosmos_drop_batch.noteMultiMaterialNotice(settings):
            row1 = "The dropped materials were imported but not assigned."
            row2 = "Drop a material on its own to put it in a material slot."
            debug.printInfo(f"Cosmos drop: {row1} {row2}")
            if bpy.context.window:
                # Show a dialog to the user as the info message cannot make it to
                # Blender's status line from this point
                bpy.ops.vray.cosmos_info_popup('INVOKE_DEFAULT',
                                               message=row1, messageRow2=row2)
        return

    # Find the material(s) created by this call. The vrmat usually contains one top-level
    # material, which is named after the asset; prefer that one so the pick does not depend
    # on how the names happen to sort. Alphabetical order is the fallback.
    newNames = sorted(set(bpy.data.materials.keys()) - matsBefore)
    if not newNames:
        debug.printError(
            f"Cosmos drop: importMaterials produced no new material datablock "
            f"for package {settings.packageId!r}"
        )
        return

    assetName = getCosmosAssetName(settings)
    newMat = bpy.data.materials[assetName if assetName in newNames else newNames[0]]

    slots = getattr(targetObj, 'material_slots', None)
    if slots is None:
        debug.printError(
            f"Cosmos drop: target {targetObj.name!r} has no material_slots; "
            f"cannot assign {newMat.name!r}"
        )
        return

    if not len(slots):
        # Nothing to replace - create the slot the material goes into. Appending to the
        # mesh links the material to the object data, Blender's default for a new slot.
        materials = getattr(getattr(targetObj, 'data', None), 'materials', None)
        if materials is None:
            debug.printError(
                f"Cosmos drop: target {targetObj.name!r} has no material list; "
                f"cannot assign {newMat.name!r}"
            )
            return
        materials.append(newMat)
        debug.printDebug(
            f"Cosmos drop: created slot 0 on {targetObj.name!r} "
            f"for material {newMat.name!r}"
        )
        return

    # An index that is not a slot of this object falls back to the first slot - the same
    # clamp Blender applies to its own material drop with max_ii(mat_slot, 1). The drop
    # operator leaves the index at -1 whenever it could not resolve one: the ray hit a
    # face whose material_index is past the end of the slot array, which Blender allows
    # and clamps only on read (see _worldPosFromDrop in cosmos_drag_drop.py), or an
    # Outliner drop found no slots to pick from. Returning here instead would import the
    # material and then silently never assign it.
    slotIndex = int(getattr(settings, 'dropTargetSlot', -1))
    if not (0 <= slotIndex < len(slots)):
        debug.printDebug(
            f"Cosmos drop: slot {slotIndex} is not a slot of {targetObj.name!r} "
            f"({len(slots)} slot(s)), assigning to the first one instead"
        )
        slotIndex = 0
    # Assigning through the slot keeps that slot's existing object/data link, which is
    # what BKE_object_material_assign(..., BKE_MAT_ASSIGN_EXISTING) does for a drop.
    slots[slotIndex].material = newMat
    debug.printDebug(
        f"Cosmos drop: assigned material {newMat.name!r} to "
        f"slot {slotIndex} of {targetObj.name!r}"
    )


def _dropHitNormal(settings):
    """Return the drop-ray hit normal as a world-space Vector, or None when
    the import wasn't from drag-drop or the drop ray didn't hit geometry."""
    if not getattr(settings, 'hasDropCoords', False):
        return None
    if not getattr(settings, 'hasHitNormal', False):
        return None
    return Vector((settings.normalX, settings.normalY, settings.normalZ))


# Cosmos surface-attachment tags (kept in sync with the C++ resolver in
# cosmos_importer.cpp). Maps each handled tag to the rotation applied
# around the asset's local X axis before aligning the asset's local +Z
# with the drop-surface normal.
#
# Only "wall" is handled here. 3dsmax also pre-rotates "ceiling" assets
# but in V-Ray for Blender we deliberately leave them in their authored
# orientation: items hanging from a ceiling (hammocks, chandeliers, fans)
# should hang along world up regardless of the ceiling's local slope, and
# Cosmos ceiling assets already ship in that pose. Aligning to a tilted
# ceiling's surface normal looks wrong - so for ceiling we do nothing by
# default. The user can opt in per-drop by holding Ctrl (forceNormalAlign).
_SURFACE_ATTACHMENT_PRE_ROTATION_X = {
    'wall': -0.5 * math.pi,
}


def _alignAssetToHitNormal(obj: bpy.types.Object, hitNormal: Vector,
                           preRotXAngle: float = 0.0):
    """Set obj's rotation so its local +Z (after an optional pre-rotation
    around local X by preRotXAngle radians) points along the world-space
    hitNormal.

    preRotXAngle=0.0 is "just align +Z to the normal". Used by the Ctrl-
    held override on arbitrary assets.

    preRotXAngle=-pi/2 is the wall-tag default: the asset's BACK first
    becomes its new bottom (Cosmos wall assets are authored standing on
    the floor with the attachment face on the back), then alignment
    presses the back into the surface.

    The combined quaternion is `align @ pre`: pre-rotation is applied in
    the asset's local frame first, then alignment in world space.

    No-op for degenerate normals.
    """
    if hitNormal.length_squared < 1e-10:
        return
    qFinal = hitNormal.to_track_quat('Z', 'Y')
    if abs(preRotXAngle) > 1e-10:
        qFinal = qFinal @ Quaternion((1.0, 0.0, 0.0), preRotXAngle)
    obj.rotation_mode = 'QUATERNION'
    obj.rotation_quaternion = qFinal


def _orientDecalToNormal(decalObj: bpy.types.Object, hitNormal: Vector):
    """Rotate a newly-created decal so its projection direction points
    INTO the surface along -hitNormal. VRayDecal projects from its local
    +Z toward -Z (Length=Z, Width=X, Height=Y), so aligning local +Z with
    the world-space surface normal makes the projection travel from the
    decal down into the surface it was dropped on.

    The up-hint 'Y' keeps the decal's Height axis as close to world +Z as
    possible, which yields a predictable "upright" orientation for the
    decal's rectangular footprint. Degenerate cases (e.g. dropping on a
    ceiling where hitNormal is ~ world -Z) are handled by Blender's
    to_track_quat.
    """
    if hitNormal.length_squared < 1e-10:
        return  # Defensive; an invalid normal shouldn't reach us.
    # track_axis='Z' means rotate so the object's local +Z points along the
    # input vector (hitNormal). up_axis='Y' is the fallback alignment used
    # when the track axis under-determines the rotation.
    rot_quat = hitNormal.to_track_quat('Z', 'Y')
    decalObj.rotation_mode = 'XYZ'
    decalObj.rotation_euler = rot_quat.to_euler()


def assetImportTimerFunction():
    try:
        imported = 0
        while (imported < _MAX_IMPORTS_PER_TICK) and not assetImportQueue.empty():
            imported += 1
            settings = assetImportQueue.get()
            # True when this asset is one of the models a Chaos Scatter preset references. Such a
            # model is parked where the preset asks and added to the scatter's model list, and is
            # counted towards the preset's completion. See utils/cosmos_scatter_preset.py.
            isPresetModel = cosmos_scatter_preset.isPresetModel(settings)
            # True when this asset is one member of a Chaos Cosmos Asset Set. Such a member is
            # placed from the set's settings.json instead of at the drop point, and is counted
            # towards the set's completion. See utils/cosmos_asset_set.py. Both features tag their
            # members through setInstanceToken, so the preset's own namespace is checked first.
            isSetMember = (not isPresetModel) and cosmos_asset_set.isSetInstance(settings)
            # Set to True once the member has been counted towards its set / preset. Stays False
            # for a plain import, and for a member whose owner has already been finalized.
            setHandled = False
            with DisableAutoConnect(), _dropPlacement(settings):
                match(settings.assetType):
                    case "Material":
                        if not os.path.exists(settings.matFile):
                            debug.printError(f"VRmat file {settings.matFile} does not exist")
                        else:
                            _importDroppedMaterial(settings)

                    case "VRMesh":
                        ob, err = importCosmosCompositeAsset(cosmosAssetContext=settings, scaleUnit=COSMOS_SCALE_UNIT)
                        if err:
                            debug.printError(err)

                        # Re-orient the imported asset to the drop surface when
                        # we have a hit normal AND either:
                        #  - the package is wall-tagged (default behavior, with
                        #    a pre-rotation around X so the back becomes the
                        #    new bottom; matches the 3dsmax VMAX-12393 importer)
                        #  - the user held Ctrl during the drop (the explicit
                        #    "force align to normal" override - applies even to
                        #    untagged or ceiling-tagged assets that wouldn't
                        #    otherwise be re-oriented)
                        # On empty-space drops (no hit normal) the asset stays
                        # in its default upright pose.
                        #
                        # A set member is exempt: its orientation is whatever the
                        # manifest says, so it is placed by cosmos_asset_set and
                        # never re-aligned to the surface the set was dropped on.
                        # A preset's model is exempt for the same reason - it is a
                        # geometry source parked beside the scatter, not a dropped asset.
                        if (not isSetMember) and (not isPresetModel) and (ob is not None):
                            hitNormal = _dropHitNormal(settings)
                            if hitNormal is not None:
                                attachment = getattr(settings, 'surfaceAttachment', '')
                                forceAlign = bool(getattr(settings, 'forceNormalAlign', False))
                                preAngle = _SURFACE_ATTACHMENT_PRE_ROTATION_X.get(attachment)
                                if preAngle is not None:
                                    angle = preAngle
                                elif forceAlign:
                                    angle = 0.0
                                else:
                                    angle = None
                                if angle is not None:
                                    try:
                                        _alignAssetToHitNormal(ob, hitNormal, angle)
                                    except Exception as e:
                                        debug.printExceptionInfo(
                                            e, "cosmos_handler: failed to align dropped asset to surface")

                        # Animated cosmos models need the attribute below
                        # to function properly when the scene containing them is exported and rendered through Vantage.
                        if ob and settings.isAnimated:
                            userAttrs = ob.vray.UserAttributes
                            userAttrs.user_attributes.add()

                            animAttr = userAttrs.user_attributes[-1]
                            animAttr.name = "lavina_fast_morph_mesh"
                            animAttr.value_type = "0"
                            animAttr.value_int = 2

                        # Counted last, so the object is completely built before the set
                        # or the preset is allowed to reach completion.
                        if isSetMember:
                            setHandled = cosmos_asset_set.noteSetInstance(settings, ob)
                        elif isPresetModel:
                            setHandled = cosmos_scatter_preset.notePresetModel(settings, ob)

                    case "HDRI":
                        importHDRI(settings.matFile, settings.lightFile, settings.packageId, settings.revisionId,
                                   locationsMap=settings.locationsMap, assetName=settings.assetName)

                    case "Extras":
                        # importDecal() creates VRayDecal objects at the 3D
                        # cursor (which _dropPlacement has moved to the drop
                        # point). If the drop hit a surface, re-orient those
                        # decals afterwards so their projection points INTO
                        # that surface. We identify the newly-created decals
                        # by diffing bpy.data.objects before/after the call.
                        #
                        # Note: bpy.data.objects.keys() returns a list (not a
                        # dict-view), so we wrap both sides in set() for the
                        # set-subtraction. Reorientation is wrapped in its
                        # own try/except so a failure there doesn't mark the
                        # whole import as failed - the decal is already in
                        # the scene at that point.
                        _objsBefore = set(bpy.data.objects.keys())
                        importDecal(settings)

                        hitNormal = _dropHitNormal(settings)
                        if hitNormal is not None:
                            try:
                                _objsAfter = set(bpy.data.objects.keys())
                                newDecals = [
                                    obj for obj in (bpy.data.objects.get(n) for n in _objsAfter - _objsBefore)
                                    if obj is not None
                                    and hasattr(obj, 'vray')
                                    and getattr(obj.vray, 'isVRayDecal', False)
                                ]
                                for decalObj in newDecals:
                                    _orientDecalToNormal(decalObj, hitNormal)
                            except Exception as e:
                                debug.printExceptionInfo(
                                    e, "cosmos_handler: failed to orient dropped decal")

                    case "ParallaxInterior":
                        if not os.path.exists(settings.matFile):
                            debug.printError(f"VRmat file {settings.matFile} does not exist")
                        else:
                            importParallaxInterior(settings)

                    case "AssetSet":
                        # A set carries no geometry of its own - only a settings.json
                        # listing the packages to import and how to arrange them.
                        cosmos_asset_set.startAssetSetImport(settings)

                    case "ScatterPreset":
                        # A preset carries no geometry either - only a .mbc config with the
                        # scatter settings and the ids of the models to scatter.
                        cosmos_scatter_preset.startScatterPresetImport(settings)

                # Every member of a set has to be accounted for, including the ones that
                # produce no object of their own (a material) and the ones whose import
                # failed - otherwise the set never reaches completion and never gets
                # parented. VRMesh members are counted above, once their object is built.
                if isSetMember and settings.assetType != "VRMesh":
                    if not settings.assetType:
                        # The server reports a member it could not import with no asset type,
                        # so the set can stop waiting for it right away.
                        debug.printError(
                            f"Chaos Cosmos Asset Set: a member could not be imported and was "
                            f"skipped. See the log above for the reason."
                        )
                    elif settings.assetType != "AssetSet":
                        debug.printWarning(
                            f"Chaos Cosmos Asset Set: member '{settings.assetName}' is of type "
                            f"'{settings.assetType}', which is not placed from the manifest"
                        )
                    setHandled = cosmos_asset_set.noteSetInstance(settings, None)

                # Same for a preset's models: a failure report arrives with no asset type at all,
                # and it still has to be counted or the preset waits out its whole idle timeout.
                if isPresetModel and settings.assetType != "VRMesh":
                    if not settings.assetType:
                        debug.printError(
                            f"Chaos Scatter preset: a model could not be imported and was "
                            f"skipped. See the log above for the reason."
                        )
                    else:
                        debug.printWarning(
                            f"Chaos Scatter preset: model '{settings.assetName}' is of type "
                            f"'{settings.assetType}', which cannot be scattered"
                        )
                    setHandled = cosmos_scatter_preset.notePresetModel(settings, None)

                # A member is collapsed into the single undo step its set pushes when it
                # finalizes, so that undoing a set import removes it in one go. Anything
                # else pushes its own step - including a member that arrived after its set
                # gave up waiting, which is now just a loose object in the scene.
                # An AssetSet, a ScatterPreset and a failure report all add nothing of their own
                # (the preset pushes its step when it finalizes, like a set).
                if (not setHandled) and settings.assetType \
                        and (settings.assetType not in ("AssetSet", "ScatterPreset")):
                    bpy.ops.ed.undo_push(message="Import Cosmos " + settings.assetType)

            # Counted here, after the asset has been placed, and once per import
            # regardless of type: a drop of several assets is done with when all of
            # them have been reported, and only then may it stop claiming the assets
            # that arrive at its drop point.
            cosmos_drop_batch.noteArrival(settings)

        cosmos_asset_set.tickSessions()
        cosmos_scatter_preset.tickSessions()
    except Exception as e:
        debug.printExceptionInfo(e, "cosmos_handler.assetImportTimerFunction")
        debug.reportError("Import of Cosmos asset failed")

    # Come back promptly while there is a backlog to drain - a large Asset Set delivers
    # hundreds of members and importing them all in one tick would freeze the UI.
    return _BUSY_TICK_INTERVAL if not assetImportQueue.empty() else _IDLE_TICK_INTERVAL

def assetImportCallback(assetSettings):
    assetImportQueue.put(assetSettings)

def registerAssetImportTimerFunction():
    if not bpy.app.timers.is_registered(assetImportTimerFunction):
        bpy.app.timers.register(assetImportTimerFunction)

def _getRegClasses():
    return (
        VRAY_OT_dummy,
        VRAY_OT_show_cosmos_info_popup,
        VRAY_OT_import_cosmos_asset,
    )

def register():
    for regClass in _getRegClasses():
        bpy.utils.register_class(regClass)
    registerAssetImportTimerFunction()


def unregister():
    for regClass in reversed(_getRegClasses()):
        bpy.utils.unregister_class(regClass)

    if bpy.app.timers.is_registered(assetImportTimerFunction):
        bpy.app.timers.unregister(assetImportTimerFunction)

    # Nothing will drain them once the timer is gone, and they hold object references.
    cosmos_asset_set.clearSessions()
    cosmos_scatter_preset.clearSessions()
    cosmos_drop_batch.clearBatches()
