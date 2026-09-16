# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Drag-and-drop support for Cosmos assets.
#
# Flow
# ----
# The V-Ray server process augments HTML5 drags originating in the Cosmos
# browser webview with a `text/uri-list` entry pointing at a small stub file
# (extension ".vrayasset") containing the asset's JSON descriptor. This
# turns the drag into something Blender's file-drop machinery recognises.
#
# The descriptor covers one asset or several: the Cosmos browser lets the
# user select multiple assets and drag them out in one go, and describes
# such a drag with a `packages` array instead of a single `id`. Each asset
# of the drop is requested separately, all with the same drop context, so
# they all land on the drop point - the same placement a regular import
# through the browser's Import button gives its assets at the 3D cursor.
#
# This module provides the Blender-side pieces:
#
#   VRAY_FH_cosmos_asset   A FileHandler registered on ".vrayasset" that
#                          accepts drops into VIEW_3D and OUTLINER areas
#                          and routes them to the operator below.
#
#   VRAY_OT_cosmos_drop    Invoked by the FileHandler on drop. Two flavours:
#                          - 3D viewport: raycasts the scene to find a
#                            placement (with a Z=0 ground-plane fallback),
#                            the hit object, the surface normal, and the
#                            hit face's material_index (so material drops
#                            land in the slot that face uses).
#                          - Outliner: target is context.object and the
#                            target slot is target_obj.active_material_index.
#                            Clicking an Outliner row makes it active and
#                            clicking a material-slot row sets the active
#                            slot index, so the natural workflow (click
#                            slot, drag, drop) puts the material in the
#                            chosen slot without any picker dialog.
#                          The drop request is forwarded to the server
#                          via vray.cosmosDropImport(), once per asset in
#                          the drag payload; the server kicks
#                          off GalaxyClient::importPackage() and the normal
#                          Cosmos import pipeline runs with the drop
#                          context attached, so cosmos_handler.py can
#                          place the asset / assign the material at the
#                          right spot.

import json
import os

import bpy
from bpy_extras import view3d_utils
from mathutils import Vector

from vray_blender import debug
from vray_blender.lib.mixin import VRayOperatorBase
from vray_blender.utils import cosmos_drop_batch
from vray_blender.bin import VRayBlenderLib as vray


# Secondary fallback distance: only used when even the Z=0 ground plane can't
# be intersected (e.g. looking straight along the horizon). Places the asset
# this many units along the view ray.
_CAMERA_FALLBACK_DISTANCE = 5.0

# Consider the ray parallel to the ground plane when |dir.z| is below this
# threshold, and skip the ground-plane intersection.
_GROUND_PLANE_PARALLEL_EPS = 1e-4


def _asRevision(value) -> int:
    """Revision number from a payload field. 0 - which the server reads as "use the
    latest revision" - for anything missing or not a number."""
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _packagesFromPayload(payload: dict) -> list[tuple[str, int]]:
    """(packageId, revisionId) for every asset in a drag payload, in payload order.

        Cosmos describes a single-asset drag with a top-level 'id' and a multi-asset drag -
        the user selected several assets in the browser before dragging them out - with a
        'packages' array of per-asset objects. 
        Entries with no usable id are skipped rather than sent as empty ids, so a payload
        that is partly malformed still imports the assets it does describe.
    """
    packages = payload.get('packages')
    if isinstance(packages, list) and packages:
        resolved = []
        for entry in packages:
            if isinstance(entry, dict):
                pkgId, revision = entry.get('id'), entry.get('revision')
            else:
                continue
            if pkgId:
                resolved.append((str(pkgId), _asRevision(revision)))
        return resolved

    if pkgId := payload.get('id'):
        return [(str(pkgId), _asRevision(payload.get('revision')))]
    return []


def _findWindowRegion(area: bpy.types.Area):
    """Return the WINDOW-type region of an area. FileHandler-invoked operators
    have context.region set to the drop-target region already, but we iterate
    anyway to be defensive (some areas have multiple regions)."""
    if area is None:
        return None
    if context_region := getattr(bpy.context, 'region', None):
        if context_region.type == 'WINDOW' and context_region in list(area.regions):
            return context_region
    for r in area.regions:
        if r.type == 'WINDOW':
            return r
    return None


class DropHit:
    """Outcome of a viewport drop raycast.

    world_pos   Where the dropped asset should be placed.
    obj_name    Name of the Blender object the ray hit (empty string on miss).
                This - not context.object - is the reliable drop target: a
                user may have a selected object elsewhere, but what they
                visually dropped on is whatever sits under the cursor.
    normal      Surface normal at the hit point in world space, or None
                when there was no geometry hit (ground-plane / camera
                fallback).
    slot_index  Material slot index of the hit polygon (mesh.polygons[i].
                material_index). -1 when there was no geometry hit, when
                the hit object is not a mesh, or when the polygon index
                is out of range. Used so that dropping a material on a
                multi-material mesh's face assigns to that face's slot
                without showing a picker dialog.
    """

    __slots__ = ('world_pos', 'obj_name', 'normal', 'slot_index')

    def __init__(self, world_pos: Vector, obj_name: str = '', normal=None,
                 slot_index: int = -1):
        self.world_pos = world_pos
        self.obj_name = obj_name
        self.normal = normal
        self.slot_index = slot_index


def _worldPosFromDrop(context: bpy.types.Context, mouse_x: int, mouse_y: int) -> DropHit:
    """Convert viewport-local mouse coords to a DropHit.

    Priority order:
      1. Raycast against scene geometry - if the drop ray hits anything,
         return that hit point, the hit object's name, and the surface
         normal.
      2. Intersect the ray with the Z=0 ground plane so assets dropped
         over empty space land on the floor instead of floating in the
         air at an arbitrary camera-relative distance (no obj / normal).
      3. Camera-distance fallback (ray origin + _CAMERA_FALLBACK_DISTANCE
         * ray_dir) when the ray is parallel to the ground or the ground
         intersection would be behind the camera (no obj / normal).

    Returns None if the viewport context is unusable (operator invoked
    outside a VIEW_3D area, or region/region_3d missing) - there is no
    placement to be had, and the caller cancels rather than dropping the
    asset on the world origin. That also keeps the origin meaning exactly
    one thing downstream: an Outliner drop, which has no position at all.
    See _invokeOutlinerDrop.
    """
    area = context.area
    if area is None or area.type != 'VIEW_3D':
        debug.printDebug(f"Cosmos drop: area is not VIEW_3D (got {area})")
        return None

    region = _findWindowRegion(area)
    space = context.space_data
    rv3d = getattr(space, 'region_3d', None)
    if region is None or rv3d is None:
        debug.printDebug(f"Cosmos drop: missing region ({region}) or region_3d ({rv3d})")
        return None

    coord = (mouse_x, mouse_y)
    ray_origin = view3d_utils.region_2d_to_origin_3d(region, rv3d, coord)
    ray_dir    = view3d_utils.region_2d_to_vector_3d(region, rv3d, coord)

    depsgraph = context.evaluated_depsgraph_get()
    hit, location, normal, hit_poly_index, obj, _matrix = context.scene.ray_cast(
        depsgraph, ray_origin, ray_dir)

    if hit:
        # ray_cast returns the depsgraph-evaluated copy; .original is the
        # scene datablock whose name we use to refer to it from the addon.
        hit_name = obj.original.name if (obj and obj.original) else (obj.name if obj else '')

        # Read the slot index of the hit polygon. polygons[i].material_index
        # is preserved across the original/evaluated mesh, so reading it on
        # the evaluated obj.data is fine. Defensive bounds-check: ray_cast
        # can return -1 / out-of-range indices for non-mesh hits.
        #
        # Also validate against the object's material_slots: a mesh with
        # zero materials still has polygons whose material_index is 0,
        # which is NOT a valid slot. In that case we leave slot_index at
        # -1 and let the addon's default policy (clear + append) create
        # slot 0 from scratch.
        slot_index = -1
        try:
            mesh = getattr(obj, 'data', None) if obj else None
            polys = getattr(mesh, 'polygons', None) if mesh else None
            if polys is not None and 0 <= hit_poly_index < len(polys):
                candidate = int(polys[hit_poly_index].material_index)
                slots = getattr(obj, 'material_slots', None)
                if slots is not None and 0 <= candidate < len(slots):
                    slot_index = candidate
        except Exception:
            pass

        debug.printDebug(
            f"Cosmos drop: raycast hit '{hit_name}' at "
            f"({location.x:.3f}, {location.y:.3f}, {location.z:.3f}) "
            f"normal=({normal.x:.3f}, {normal.y:.3f}, {normal.z:.3f}) "
            f"face_slot={slot_index}"
        )
        return DropHit(location.copy(), hit_name, normal.copy(), slot_index)

    # No geometry under the cursor. Project the ray onto the Z=0 ground
    # plane so assets land on the floor instead of floating in the air at
    # an arbitrary camera-relative distance. Parametric form:
    #   origin.z + t * dir.z = 0   =>   t = -origin.z / dir.z
    # t must be positive (ground in front of camera). If the ray is nearly
    # parallel to the ground, or the intersection is behind the camera,
    # fall back to a fixed distance along the view ray.
    if abs(ray_dir.z) > _GROUND_PLANE_PARALLEL_EPS:
        t = -ray_origin.z / ray_dir.z
        if t > 0:
            ground_hit = ray_origin + ray_dir * t
            debug.printDebug(
                f"Cosmos drop: raycast miss, projected to ground plane at "
                f"({ground_hit.x:.3f}, {ground_hit.y:.3f}, {ground_hit.z:.3f})"
            )
            return DropHit(ground_hit)

    fallback = ray_origin + ray_dir * _CAMERA_FALLBACK_DISTANCE
    debug.printDebug(
        f"Cosmos drop: raycast miss and ray parallel/behind ground, "
        f"camera-ahead fallback at "
        f"({fallback.x:.3f}, {fallback.y:.3f}, {fallback.z:.3f})"
    )
    return DropHit(fallback)


# ---------------------------------------------------------------------------
# Outliner drop helpers
# ---------------------------------------------------------------------------

def _resolveOutlinerSlotIndex(target_obj: bpy.types.Object) -> int:
    """Pick the material-slot index for an Outliner drop on target_obj.

    Blender does not expose which Outliner row a FileHandler drop landed
    on, so we cannot tell a slot-row drop from an object-row drop
    programmatically. We instead use target_obj.active_material_index:
    clicking a slot row in the Outliner sets the active index to that
    slot, so the user's natural workflow (click slot, drag, drop)
    deposits the material in the right place. For object-row drops
    without any prior slot click the index is whatever was last active -
    typically 0 - which is a sensible default.

    Returns -1 if the object has no slots; the addon's default policy
    then creates slot 0 from scratch.
    """
    print("Resolve outpliner slot")
    slots = getattr(target_obj, 'material_slots', None)
    if not slots:
        return -1
    idx = getattr(target_obj, 'active_material_index', 0)
    if not (0 <= idx < len(slots)):
        idx = 0
    return idx


class VRAY_OT_cosmos_drop(VRayOperatorBase):
    """Operator invoked by the VRAY_FH_cosmos_asset FileHandler when the
    user drops a Cosmos stub file onto a Blender area.

    Drop targets (set per area.type):
      VIEW_3D  - Resolves to a world-space point via raycast (with a Z=0
                 ground-plane fallback), the hit object, the hit face's
                 surface normal, and the face's material_index. VRMesh,
                 ParallaxInterior, Decal etc. are placed at the cursor;
                 materials assign to the slot of the hit face.
      OUTLINER - Target object is context.object. The slot is taken from
                 target_obj.active_material_index, which Blender updates
                 when the user clicks a slot row - so the natural flow
                 (click slot row, drag material, drop) puts the material
                 in the chosen slot. For object-row drops without a prior
                 slot click the active index is typically 0.

    The actual import runs asynchronously on the server: this operator
    only assembles the request and fires vray.cosmosDropImport() once per
    asset in the drag payload; each response comes back as the usual
    MsgControlOnImportAsset and runs through assetImportTimerFunction.
    """

    bl_idname = "vray.cosmos_drop"
    bl_label = "Import Cosmos Asset (Drop)"
    bl_options = {'REGISTER', 'INTERNAL'}

    # Populated by Blender's FileHandler machinery with the path of the
    # dropped stub file in the OS temp dir.
    filepath: bpy.props.StringProperty(subtype='FILE_PATH', options={'HIDDEN'})

    @classmethod
    def poll(cls, context):
        # Accept invocation in any area we support drops from. FileHandler's
        # poll_drop is the authoritative gate at drop time, but Blender also
        # invokes poll() on the operator itself.
        return context.area is not None and context.area.type in {'VIEW_3D', 'OUTLINER'}

    def invoke(self, context, event):
        payload = self._loadStub()
        if payload is None:
            return {'CANCELLED'}

        # Resolved once here and threaded through. Both the debug lines below and
        # the dispatch want the same list, and debug.printDebug is handed an
        # already-formatted string - printMsg only filters by level afterwards - so
        # re-parsing inside an f-string would cost on every drop whether or not
        # debug logging is switched on.
        packages = _packagesFromPayload(payload)
        if not packages:
            self.report({'WARNING'},
                        f"Cosmos drop: payload carries no asset id: {payload!r}")
            return {'CANCELLED'}

        area_type = context.area.type if context.area else ''
        if area_type == 'OUTLINER':
            return self._invokeOutlinerDrop(context, payload, packages)
        elif area_type == 'VIEW_3D':
            return self._invokeViewportDrop(context, event, payload, packages)
        self.report({'WARNING'},
                    f"Cosmos drop: unsupported drop area {area_type!r}")
        return {'CANCELLED'}

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _loadStub(self):
        """Read and validate the stub file. Reports on failure and returns
        None; returns the parsed JSON payload dict on success."""
        if not self.filepath or not os.path.isfile(self.filepath):
            self.report({'ERROR'},
                        f"Cosmos drop: stub file missing: {self.filepath!r}")
            return None
        try:
            with open(self.filepath, 'r', encoding='utf-8') as f:
                payload = json.load(f)
        except Exception as e:
            self.report({'ERROR'}, f"Cosmos drop: failed to parse stub file: {e}")
            return None
        # A multi-asset drag carries its assets in a 'packages' array; accept it on
        # the strength of that array alone, without insisting on the type marker, the
        # same way V-Ray for Maya's handler does.
        if not isinstance(payload, dict):
            self.report({'WARNING'},
                        f"Cosmos drop: unexpected payload: {payload!r}")
            return None
        if payload.get('type') != 'cosmos-asset' and not isinstance(payload.get('packages'), list):
            self.report({'WARNING'},
                        f"Cosmos drop: unexpected payload type: {payload!r}")
            return None
        return payload

    def _invokeViewportDrop(self, context, event, payload, packages):
        """Drop handling for the 3D viewport. Resolves a world-space
        placement + drop target via raycast and fires the import.

        For Material drops the raycast also yields the hit face's
        material_index, so a drop on a multi-material mesh face assigns to
        that face's slot directly (matches the Outliner direct-slot drop
        behaviour). When the ray missed all geometry slot_index is -1 and
        the addon falls back to its default policy.

        Holding Ctrl while dropping is a modifier that asks the addon to
        align the asset's local +Z to the hit surface normal even for
        assets that wouldn't otherwise be re-oriented (anything not tagged
        wall / not a Decal). Useful for placing things like furniture on
        sloped terrain or a hammock on a slanted ceiling beam. Wall and
        Decal drops already align by default, so the modifier is a no-op
        for them.
        """
        hit = _worldPosFromDrop(
            context, event.mouse_region_x, event.mouse_region_y)
        if hit is None:
            self.report({'WARNING'},
                        "Cosmos drop: the 3D viewport context is unusable, "
                        "nothing was imported")
            return {'CANCELLED'}

        force_align = bool(getattr(event, 'ctrl', False))
        debug.printDebug(
            "Cosmos drop (viewport) -> server: "
            f"assets={[pkgId for pkgId, _ in packages]} "
            f"mouse_region=({event.mouse_region_x}, {event.mouse_region_y}) "
            f"world=({hit.world_pos.x:.3f}, {hit.world_pos.y:.3f}, {hit.world_pos.z:.3f}) "
            f"target={hit.obj_name!r} face_slot={hit.slot_index} "
            f"force_align={force_align}"
        )
        return self._sendDrop(
            payload, packages,
            target_name=hit.obj_name,
            target_slot=hit.slot_index,
            world_pos=hit.world_pos,
            normal=hit.normal,
            force_normal_align=force_align,
        )

    def _invokeOutlinerDrop(self, context, payload, packages):
        """Drop handling for the Outliner.

        Target object is context.object (set by Blender to the active
        item; clicking an Outliner row makes it active, so the natural
        click-then-drop flow puts the right object here).

        Target slot is target_obj.active_material_index. Clicking a
        slot row in the Outliner sets that index to that slot, so the
        natural workflow (click slot, drag, drop) lands the material
        directly in the chosen slot - no picker dialog needed.
        """
        target_obj = context.object
        if target_obj is None:
            self.report({'WARNING'},
                        "Cosmos drop: no object under the cursor in the Outliner")
            return {'CANCELLED'}

        slot = _resolveOutlinerSlotIndex(target_obj)
        debug.printDebug(
            f"Cosmos drop (outliner) -> server: "
            f"assets={[pkgId for pkgId, _ in packages]} "
            f"target={target_obj.name!r} slot={slot}"
        )
        # No world position: an Outliner drop names a slot, not a place. The default
        # world_pos of (0,0,0) is what cosmos_drop_batch keys this drop's batch on, and
        # it is unambiguous because _worldPosFromDrop no longer returns the origin.
        return self._sendDrop(payload, packages, target_obj.name, target_slot=slot)

    def _sendDrop(self, payload, packages, target_name: str, target_slot: int,
                  world_pos: Vector = Vector((0.0, 0.0, 0.0)),
                  normal=None,
                  force_normal_align: bool = False):
        """Dispatch a cosmosDropImport call per entry of 'packages', which invoke()
        resolved from the payload. Common to all drop flavours. Returns the
        operator return-set. 'payload' is still read for the drag's options.

        A multi-asset drag is requested one asset at a time - there is no batch
        form of the call, and the Cosmos client imports one package per request
        anyway - with the same drop context attached to each, so every asset of
        the drop lands on the drop point. The drop is announced to
        cosmos_drop_batch first: an import comes back with no indication of how
        many assets were dropped with it, which the material assignment needs
        to know.
        """
        options = payload.get('options') or {}
        apply_triplanar  = bool(options.get('triplanarMapping', False))
        apply_real_world = bool(options.get('realWorldScale', False))

        has_normal = normal is not None
        nx, ny, nz = (normal.x, normal.y, normal.z) if has_normal else (0.0, 0.0, 0.0)

        cosmos_drop_batch.openBatch(world_pos, len(packages))

        for pkgId, revision in packages:
            vray.cosmosDropImport(
                packageId           = pkgId,
                revisionId          = revision,
                worldX              = float(world_pos.x),
                worldY              = float(world_pos.y),
                worldZ              = float(world_pos.z),
                dropTargetObject    = target_name,
                dropTargetSlot      = int(target_slot),
                hasHitNormal        = has_normal,
                normalX             = float(nx),
                normalY             = float(ny),
                normalZ             = float(nz),
                applyTriplanar      = apply_triplanar,
                applyRealWorldScale = apply_real_world,
                forceNormalAlign    = bool(force_normal_align),
            )

        # Surface what we actually targeted in the info bar so the user can
        # spot mismatches (e.g. a different selected object intercepting an
        # Outliner drop). The actual import is async and the final material
        # appears after the assetImportTimerFunction tick.
        what = "1 asset" if len(packages) == 1 else f"{len(packages)} assets"
        if target_name:
            slot_label = f"slot {target_slot}" if target_slot >= 0 else "default slot"
            self.report({'INFO'},
                        f"Cosmos drop: importing {what} into {target_name} ({slot_label})")
        elif len(packages) > 1:
            self.report({'INFO'}, f"Cosmos drop: importing {what}")
        return {'FINISHED'}


class VRAY_FH_cosmos_asset(bpy.types.FileHandler):
    """Registers ".vrayasset" stub files as a drop target for the V-Ray
    Cosmos drop operator. Blender uses this to decide which areas accept
    the drop (greying out other regions during the drag) and to route the
    dropped file path to the operator. We accept VIEW_3D for placement
    drops and OUTLINER for material-on-object drops."""

    bl_idname = "VRAY_FH_cosmos_asset"
    bl_label = "V-Ray Cosmos Drop"
    bl_import_operator = "vray.cosmos_drop"
    bl_file_extensions = ".vrayasset"

    @classmethod
    def poll_drop(cls, context):
        return context.area is not None and context.area.type in {'VIEW_3D', 'OUTLINER'}


def getRegClasses():
    return (
        VRAY_OT_cosmos_drop,
        VRAY_FH_cosmos_asset,
    )


def register():
    for cls in getRegClasses():
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(getRegClasses()):
        bpy.utils.unregister_class(cls)
