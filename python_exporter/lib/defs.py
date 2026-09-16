# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Mapping, Optional, TypeVar, Dict
import bpy, mathutils
from numpy import ndarray

from vray_blender import debug
from vray_blender.exporting.tools import TimeStats, FakeTimeStats
from vray_blender.exporting.plugin_tracker import getObjTrackId, ObjTracker, ObjDataTracker, FakeObjTracker, ScopedNodeTracker, FakeScopedNodeTracker
from vray_blender.lib.motion_blur import MotionBlurBuilder


class RendererMode:
    Invalid     = 0
    Interactive = 1
    Production  = 2
    Preview     = 3
    Bake        = 4
    Viewport    = 5
    Vantage     = 6
    ProxyExport = 7


class ExporterType:
    IPR_VIEWPORT = 0
    IPR_VFB = 1
    PROD = 2
    PREVIEW = 3
    ANIMATION = 4
    VANTAGE_LIVE_LINK = 5

class ProdRenderMode:
    EXPORT_VRSCENE = 0
    RENDER = 1
    CLOUD_SUBMIT = 2
    EXPORT_PROXY = 3


class RenderMaskState:
    def __init__(self, mode: str, clearMask: bool,  renderMaskData: list):
        self.mode = mode
        self.clearMask = clearMask
        self.renderMaskData = renderMaskData

    def __eq__(self, other: RenderMaskState):
        assert isinstance(self.renderMaskData, list)

        if (self.mode != other.mode) or (self.clearMask != other.clearMask):
            return False
        if len(self.renderMaskData) != len(other.renderMaskData):
            return False
        return all(obj in other.renderMaskData for obj in self.renderMaskData) and all(obj in self.renderMaskData for obj in other.renderMaskData)

class PersistedState:
    def __init__(self):

        # A snapshot of scene objects' visibility. Blender does not provide updates when the visibility
        # of an object changes, so we need to track it ourselves.
        self.visibleObjects = set() # set of objTrackId

        # A snapshot of scene objects' Holdout / Indirect Only state. Like visibility, Blender does
        # not tag a usable depsgraph update when these change, so we track it ourselves.
        self.holdoutState: dict[int, tuple[bool, bool]] = {} # objTrackId -> (holdout, indirectOnly)

        self.activeInstancers = set()
        self.activeGizmos     = set()

        # Instancer `Node` plugins which are tracked under a track id other than that of the
        # instancer object driving them, mapped from that instancer object. V-Ray Fur is the case:
        # its instancer is keyed by the fur object so that the fur pruning can find it. One fur
        # object drives one instancer per instancer object, and all of them are tracked under the
        # same fur track id, so plugin names are the only granularity at which they can be told
        # apart - pruneInstances() would otherwise hide every one of them at once.
        self.derivedInstancerNodes: dict[int, set[str]] = {}

        # Per instancer, a signature of the set of instances it produced during the previous export:
        # (count, sum, xor) over the instanced objects' track ids. Blender reports a collection
        # membership change by tagging the *collection*, not the instancer object, and not with any
        # of the update flags the change predicates consult - so the only reliable way to notice that
        # an instancer has gained or lost an instance is to compare the sets. Order independent on
        # purpose, the instance iteration order is not guaranteed to be stable.
        self.instancerMembership: dict[int, tuple[int, int, int]] = {}

        # Cached scene-wide sets, rebuilt only when ExporterContext.structuralUpdate says the scene
        # itself changed (object added / removed / re-linked / hidden). See the incremental passes in
        # ExporterContext.syncSceneState().
        #
        # allObjectsTrackIds: the track ids of every object in the scene plus those from linked
        # collections, i.e. what ExporterContext.allObjects holds, reduced to ints. Only the ids are
        # persisted, never the bpy.types.Object wrappers: a wrapper outlives the object it points to,
        # and once the object is freed the wrapper raises ReferenceError on any attribute access and
        # its hash silently changes to 0 (measured on Blender 5.0.1), which quietly corrupts any set
        # still holding it. Ids compare exactly for the same memory as the object set they replace,
        # so this is a strict improvement over diffing the wrappers. None = not built.
        self.allObjectsTrackIds: set[int] | None = None

        # objTrackIds of the objects that could possibly be instancers, i.e. that need the expensive
        # dg.object_instances walk re-run when one of them changes. An object qualifies if it has a
        # NODES (geometry nodes) modifier, a particle system, is_instancer, or is a V-Ray Fur object.
        # NOTE: geometry-nodes instancers are NOT reported by Object.is_instancer, on the original or
        # on the evaluated object (measured on Blender 5.0.1), so the NODES modifier is the only way
        # to spot them without walking every instance. None = not built.
        self.instancerCandidates: set | None = None

        # What syncActiveInstancers() computed on its last real rescan, so that a skipped cycle can
        # reuse it. Written by that pass on every rescan, alongside instancerCandidates.
        # NOT the same thing as activeInstancers above, and deliberately a separate field: that one
        # is a snapshot of the *previous export cycle*, written only by the renderers'
        # _persistState() and diffed against the current set by pruneInstances() / purgeFurInfo().
        # Two different lifecycles, and only one of them is guaranteed to be written - the .vrscene
        # animation export (renderer_prod._exportFullScene) never calls _persistState() at all, so
        # reading the snapshot here would hand back an empty set from frame 2 onwards.
        # None = not built.
        self.syncedActiveInstancers: set | None = None

        # The instanced objects seen under each instancer parent during the last real instance walk:
        # {parentObjTrackId: set(instancedObjTrackId)}. Lets the instance pass decide whether anything
        # it would export has changed without iterating the instances first.
        self.instancedObjectsByParent: dict[int, set[int]] = {}

        # Local view is a per-viewport state that Blender does not tag the depsgraph for at all, so
        # entering or leaving it has to be detected by comparing this token.
        self.localViewToken = None

        # Cache mapping for exported materials:
        # Each key is a session_uid of a Blender material that has been processed for export,
        # and the corresponding value is its AttrPlugin representation.
        # This cache is used to avoid re-exporting materials that have already been exported.
        self.exportedMtls: Dict[int, AttrPlugin] = {}

        # Track ids (session_uids) of materials whose node tree contains an animated image-sequence
        # bitmap. Filled as bitmaps are exported and read by syncMtlExportCache, so those materials
        # are re-exported on frame change without scanning every node in the scene.
        self.animatedBitmapMaterials: set = set()

        # The frame of the previous export, so syncMtlExportCache re-exports animated bitmaps only
        # when the frame actually changed (not on every IPR edit). None until the first export.
        self.lastExportedFrame = None

        # The names the previous export filtered the exported attributes by, see
        # reference_collector.syncReferencedAttrNames(). None until the first export.
        self.referencedAttrNames: dict | None = None

        self.activeFurInfo = set()
        self.activeMeshLightsInfo = set()

        # Tracks objects that have been processed during any of the export procedures.
        # Its use is to distinguish between objects already handled on previous update cycles and those newly added to the depsgraph.
        self.processedObjects = set()

        # This variable holds the value of view3D.region_3d.view_matrix before calling of self.exportViewport()
        # It is used to ensure that there is a change in the 3D viewport, indicating that the scene should be redrawn.
        self.prevRegion3dViewMatrix = None

        # Points Names.object(obj, instance) to geom plugin name.
        # It is used for easy access to the geom plugin name of already exported objects.
        # it is set during the export of node plugins.
        self.objDataTracker = ObjDataTracker()

        # Stores the render mask state for the current export.
        self.renderMaskState = RenderMaskState(-1, True, [])

        # Stores the prevous state of the material override settings to ensure
        # we know when to re-export all materials in the scene when it changes.
        self.materialOverrideMode = '-1'
        self.overrideMaterial = None

class UIRegionContext:
    """ UI context in which a render job is started. Blender does not provide 
        UI context for IPR and production rendering so we capture the relevant 
        data when a render request is made and use it for the duration
        of the render job.
    """
    def __init__(self, view3d: bpy.types.SpaceView3D, window: bpy.types.Window):
            assert window is not None
            assert view3d is not None and view3d.type == 'VIEW_3D'
            assert view3d.region_3d is not None

            self.window:   bpy.types.Window = window
            self.view3d:   bpy.types.SpaceView3D  = view3d
            self.region3d: bpy.types.RegionView3D = view3d.region_3d


@dataclass
class ProxyExportSettings:
    """Settings and mutable state used only for RendererMode.ProxyExport."""

    exportOnlySelected: bool = False
    proxyMaterialSlots: list[tuple[int, str | None]] = field(default_factory=list)
    proxyMaterialSlotOffsets: dict[str, int] = field(default_factory=dict)


@dataclass
class ReferencedPluginParam:
    """ A plugin parameter that references other plugins and is therefore exported only after the
        whole scene has been exported, once all the referenced plugins are guaranteed to exist. """

    targetPluginName: str
    attrName: str
    value: object         # AttrPlugin | list[AttrPlugin] | scalar accepted by plugin_utils.updateValue
    append: bool = False  # True => append into a per-key list; False => last-writer-wins


class ExporterContext:

    class _CurrentObjectContext:
        # This class manages a stack of the currently exported objects so that the exporting
        # code could obtain the last pushed object. This is necessary e.g. when custom code
        # is exporting V-Ray properties created directly on the object.

        def __init__(self):
            self._objectsStack: list[bpy.types.Object] = []

        def get(self):
            return self._objectsStack[-1] if self._objectsStack else None

        def push(self, obj: bpy.types.Object):
            self._objectsStack.append(obj)
            return self

        def __enter__(self):
            # Object has already been pushed in the push() method
            pass

        def __exit__(self, *args):
            self._objectsStack.pop()

    class _ExportingProgress:
        """ Tracks and updates the progress of scene export based on the total number of objects and frames.
        
        Notes:
            - Only unique objects from the scene are counted, object instances are excluded.
              This may cause reported progress to be less accurate for scenes with many instances.

            - The update() method works only when exporting a .vrscene file.
              During rendering, the progress is reported directly by V-Ray and it does nothing.
        """
        def __init__(self):
            self.totalObjects = 0
            self.totalFrames = 0
            self.progress = 0.0

        def setTotalObjectsAndFrames(self, exporterCtx: ExporterContext):
            self.totalObjects = len(exporterCtx.dg.view_layer_eval.objects)
            self.totalFrames = len(exporterCtx.commonSettings.animation.frames) if exporterCtx.isAnimation else 1

        def update(self, engine: bpy.types.RenderEngine):
            """ Update the progress of the scene export if the total frames and objects counts are set """
            
            # Skip updating progress if totalFrames or totalObjects are unset,
            # which typically indicates we are in the rendering phase rather than exporting.
            if self.totalFrames == 0 or self.totalObjects == 0: 
                return

            self.progress += (1 / self.totalFrames) * (1 / self.totalObjects)
            engine.update_progress(self.progress)


    ##### Properties accessible in any context #####

    # A list of plugins to remove before the next export.
    # This list is meant to be filled in from contexts outside the export sequence where immediate changes to V-Ray state
    # are not possible or convenient.
    pluginsToRecreate = set()

    # Name of the material whose quick caustics parameter was last changed from a UI update
    # callback (outside the export sequence). Quick caustics beam generators are registered at
    # geometry-compile time in the core, so the geometry of objects using this material must be
    # recompiled. Transferred into the per-export updatedMtlWithQuickCaustics field at the start
    # of each export sync. 
    pendingQuickCausticsMtl = ""


    def __init__(self, exporterCtx: ExporterContext = None):
        from vray_blender.lib.common_settings import CommonSettings

        if exporterCtx:
            self._copyConstruct(exporterCtx)
            return

        # A raw pointer to the VRayBlenderLib's exporter(renderer) object associated with the current export
        self.renderer = None
        self.rendererMode: RendererMode  = RendererMode.Invalid

        # If true, export everything regardless of the depsgraph update status. Otherwise,
        # only export changes. This flag has meaning for interactive renderer mode only.
        # For all other modes, the value should always be True
        self.fullExport: bool = True

        # True if this is an export-only job.
        self.exportOnly: bool = False

        # The number of the frame being currently exported. This may be outside the animation
        # range if motion blur is active.
        self.currentFrame: float = 0.0

        # Camera to export as the active one, overriding the scene's own. Set while a production
        # animation render re-exports the camera of a frame whose motion blur interval spans a
        # camera switch, so that the frame's camera - and not the one the scene has switched to
        # at that subframe - is the one whose values land inside its interval.
        self.cameraOverride: bpy.types.Object = None

        # Settings values collected from the user interface
        self.commonSettings: CommonSettings = None

        # Track VRay plugins associated with scene objects. The same plugins may be referenced
        # by multiple objects.
        self.objTrackers: Mapping[str, ObjTracker|FakeObjTracker]  = {}

        # Track VRay plugins assosiated with nodes in a nodetree.
        self.nodeTrackers: Mapping[str, ScopedNodeTracker|FakeScopedNodeTracker]  = {}

        # A snapshot of the active instancer objects in the scene
        self.activeInstancers = set() # set of objTrackId

        # Backing store for the allObjects property. None = not collected on this cycle.
        self._allObjects: set | None = None

        # Backing store for the referencedAttrNames properties. None = not collected on this cycle.
        self._referencedAttrNames: dict | None = None

        # State to carry over to the next rendering cycle
        self.persistedState = PersistedState()

        # Depsgraph updates split by type. Initialised with all the keys present and empty because
        # not every caller reaches syncSceneState() before the first read - the production path runs
        # fur_export.syncFurInfo() first (it only consults these on incremental exports, and
        # production is always a full export).
        self.dgUpdates: dict[str, set[int]] = {   #   dict[update_type, set[objTrackId]]
            'geometry':  set(),
            'transform': set(),
            'shading':   set(),
            'all':       set(),
        }

        # The originals of the Object IDs reported in dg.updates. The incremental passes use this
        # instead of walking the whole scene.
        self.dgUpdatedObjects: set[bpy.types.Object] = set()

        # True when dg.updates contains a Scene / ViewLayer / Collection entry, i.e. a change Blender
        # cannot attribute to an individual object. Blender reports visibility changes ONLY this way -
        # hiding an object with hide_set() produces a single flagless Scene update and does not
        # mention the object at all - so this is what forces the full visibility rescan. Measured on
        # Blender 5.0.1: raised by every hide/exclude/holdout/collection toggle and by object
        # add/delete/relink, and NOT raised by object transforms or renames.
        #
        # IMPORTANT: selecting an object raises it too, with a byte-identical signature (one flagless
        # Scene entry), and there is no way to tell the two apart from dg.updates. Since every click
        # selects something, this flag is true on most interactive updates - so it must NOT be used to
        # gate expensive work. Use objectsWithUpdatedVisibility (computed by syncObjVisibility, which
        # is the pass that resolves the ambiguity) or allObjectsChanged instead.
        self.structuralUpdate: bool = False

        # True when the set of objects in the scene actually changed since the last export (added,
        # deleted or re-linked). Unlike structuralUpdate this is a real topology signal, so it is safe
        # to gate expensive work on.
        self.allObjectsChanged: bool = True

        # True when dg.updates contains a Collection entry. A subset of structuralUpdate, and the
        # useful part of it: a selection change reports a Scene entry, never a Collection one, so this
        # is NOT raised on every click and may be used to gate expensive work.
        # Needed because changing the membership of an instanced collection is reported by tagging the
        # collection alone - the instancer object and the instanced objects are not mentioned, and
        # members of a collection that is not itself linked to the view layer do not even reach
        # allObjects. See PersistedState.instancerMembership and InstancerExporter's
        # canSkipInstanceWalk().
        self.collectionUpdate: bool = False

        self.ctx: bpy.types.Context      = None
        self.dg: bpy.types.Depsgraph     = None
        self.ts: TimeStats|FakeTimeStats = None
        self.engine: bpy.types.RenderEngine = None

        # LightMesh and target meshes are exported by geometry and light exporters respectively.
        # Both need to know about any updates to the other. In updatedMeshLightsInfo, all pairs of
        # LightMesh and Gizmo Object for which either object has been updated will be stored
        # at the beginning of each update cycle for later reference.
        self.updatedMeshLightsInfo = set() # set[LightMesh.UpdatedMeshLightInfo]

        # Currently active LightMesh:gizmo pairs
        self.activeMeshLightsInfo = set()  # set[LightMesh.ActiveMeshLightInfo]

        # VRayFur:gizmo pairs that have been updated during the current update cycle
        self.updatedFurInfo = set()

        # Currently active VRayFur:gizmo pairs
        self.activeFurInfo = set()

         # A snapshot of the active environment fog gizmo objects in the scene
        self.activeGizmos = set() # set of objTrackId

        # Any stats that exporters wish to publish, e.g. number of entities processed
        self.stats: list[str] = []

        # Counts for the whole export cycle, filled as the export runs and reported at the end.
        # It has to be a single accumulator rather than one per pass: materials are exported from
        # the object pass (node_export._exportObjectMaterial) long before mtl_export.run() gets to
        # them, so a per-pass count attributes them to the wrong pass - and, because they are cached
        # in exportedMtls by then, counts zero of them in the pass that is named after them.
        # Shared by every exporter through _copyConstruct. Off until resetSceneStats() decides
        # otherwise, so a context that never starts an export collects nothing.
        self.sceneStats: SceneStats = FakeSceneStats()

        # There are some default plugins which may be referenced by multiple other plugins,
        # e.g. mapping etc. We only need one copy of those.
        self.defaultPlugins = {}  # plugin_type: attrPlugin

        # A list of all objects for which temp meshes have beem created with the to_mesh method.
        # to_mesh_clear() must be called on these objects after the export is complete.
        # Currently, temp meshes are only created for objects in edit mode.
        self.objectsWithTempMeshes = []

        self.objectsWithUpdatedVisibility: dict[int, bool] = {}

        # objTrackIds of objects whose Holdout / Indirect Only state changed since the last export.
        self.objectsWithUpdatedHoldout: set[int] = set()

        # Plugin parameters that reference other plugins, exported only after every referenceable
        # plugin exists so the references resolve. Keyed by (targetPluginName, attrName), drained by
        # export_utils.exportReferencedPluginParams(). Currently used for render-channel linking (see
        # linkPluginToRenderChannel); object selectors use self.referencedObjects instead.
        self.referencedPluginParams: dict[tuple[str, str], ReferencedPluginParam] = {}

        # Objects referenced by a plugin parameter (e.g. a selector); must export even when hidden,
        # else the reference points to an empty plugin. Filled by reference_collector, exported by
        # GeometryExporter._exportObjects. Maps objTrackId -> original object (render-hidden objects
        # are absent from the depsgraph, so the original cannot be recovered from it).
        self.referencedObjects: dict[int, bpy.types.Object] = {}

        # A dictionary of collection => list of lights. For lights outside a collection, the collection
        # name is an empty string. This info is used for exporting LightSelect and LightMix render channels.
        self.lightCollections = {}

        # A list of the emissive materials in the scene. Used to create LightSelects for the emissive materials
        # when 'Separate emissive materials' is active in the LighMix options.
        # The data type is tuple(emissivePluginName, attrName, nodeName, materialName)
        self.emissiveMaterials = []

        # A stack of the objects that are being currently exported.
        self.objectContext = __class__._CurrentObjectContext()


        self.motionBlurBuilder = MotionBlurBuilder()

        # The active LightMix node in the scene or None
        self.activeLightMixNode = None

        # The name of currently updated material that has a displacement node in its node tree
        self.updatedMtlWithDisplacement = ""

        # Name of the material whose quick caustics parameters changed for this export. Objects
        # using this material get their geometry recompiled so beam generators re-register.
        self.updatedMtlWithQuickCaustics = ""

        # UI context data
        self.uiRegionContext: UIRegionContext = None

        # Per-job override of the scene's animation mode. 'AUTO' means "use Exporter.animation_mode";
        # 'ANIMATION' forces animation; 'FRAME' forces single frame.
        self.forceAnimationMode: str = 'AUTO'

        # Tracks and updates the progress of the scene export. It is used only when exporting a .vrscene file.
        self.exportProgress = ExporterContext._ExportingProgress()

        # Lazily-built map of {Object: collection_name} for cryptomatte layer name export.
        # Built once per export pass via _buildObjectCollectionMap() and shared with child exporters.
        self.objCollectionMap: dict | None = None
        self.proxyExportSettings = ProxyExportSettings()


    def _copyConstruct(self, other: ExporterContext):
        self.renderer               = other.renderer
        self.rendererMode           = other.rendererMode
        self.objTrackers            = other.objTrackers
        self.nodeTrackers           = other.nodeTrackers
        self.activeInstancers       = other.activeInstancers
        self.persistedState         = other.persistedState
        self._allObjects            = other._allObjects
        self._referencedAttrNames   = other._referencedAttrNames
        self.dgUpdates              = other.dgUpdates
        self.dgUpdatedObjects       = other.dgUpdatedObjects
        self.structuralUpdate       = other.structuralUpdate
        self.allObjectsChanged      = other.allObjectsChanged
        self.collectionUpdate       = other.collectionUpdate
        self.sceneStats             = other.sceneStats
        self.objectsWithUpdatedVisibility  = other.objectsWithUpdatedVisibility
        self.objectsWithUpdatedHoldout     = other.objectsWithUpdatedHoldout
        self.commonSettings         = other.commonSettings
        self.ctx                    = other.ctx
        self.dg                     = other.dg
        self.ts                     = other.ts
        self.engine                 = other.engine
        self.updatedMeshLightsInfo  = other.updatedMeshLightsInfo
        self.activeMeshLightsInfo   = other.activeMeshLightsInfo
        self.updatedFurInfo         = other.updatedFurInfo
        self.activeFurInfo          = other.activeFurInfo
        self.activeGizmos           = other.activeGizmos
        self.fullExport             = other.fullExport
        self.exportOnly             = other.exportOnly
        self.currentFrame           = other.currentFrame
        self.cameraOverride         = other.cameraOverride
        self.stats                  = other.stats
        self.defaultPlugins         = other.defaultPlugins
        self.objectsWithTempMeshes  = other.objectsWithTempMeshes
        self.referencedPluginParams = other.referencedPluginParams
        self.referencedObjects      = other.referencedObjects
        self.lightCollections       = other.lightCollections
        self.emissiveMaterials      = other.emissiveMaterials
        self.objectContext          = other.objectContext
        self.motionBlurBuilder      = other.motionBlurBuilder
        self.activeLightMixNode     = other.activeLightMixNode
        self.updatedMtlWithDisplacement = other.updatedMtlWithDisplacement
        self.updatedMtlWithQuickCaustics = other.updatedMtlWithQuickCaustics
        self.uiRegionContext             = other.uiRegionContext
        self.forceAnimationMode     = other.forceAnimationMode
        self.exportProgress         = other.exportProgress
        self.objCollectionMap       = other.objCollectionMap
        self.exportProgress     = other.exportProgress
        self.proxyExportSettings = other.proxyExportSettings

    @property
    def viewport(self):
        return self.rendererMode == RendererMode.Viewport

    @property
    def iprVFB(self):
        return self.rendererMode == RendererMode.Interactive

    @property
    def vantage(self):
        return self.rendererMode == RendererMode.Vantage

    @property
    def interactive(self):
        return self.viewport or self.iprVFB or self.vantage

    @property
    def production(self):
        return self.rendererMode == RendererMode.Production

    @property
    def preview(self):
        return self.rendererMode == RendererMode.Preview

    @property
    def bake(self):
        return self.rendererMode == RendererMode.Bake

    @property
    def isProxyExport(self):
        return self.rendererMode == RendererMode.ProxyExport

    @property
    def isAnimation(self):
        return self.production and self.commonSettings.animation.use

    @property
    def sceneObjects(self):
        return self.ctx.scene.objects

    @property
    def allObjects(self):
        """ Every object in the scene, including those from linked collections (which are not in the
            scene's depsgraph).

            Collected by _syncAllObjects() when the scene topology changed, and on demand otherwise.
            Never cached across export cycles - see PersistedState.allObjectsTrackIds - so it can
            never hand back a wrapper whose object has since been deleted.
        """
        if self._allObjects is None:
            self._allObjects = self._collectAllObjects()
        return self._allObjects

    @property
    def instancedObjectTrackIds(self) -> set[int]:
        """ The track ids of the objects the last instance walk found instanced.

            An instance source does not have to be in the scene: the collection it lives in is often
            not linked, whether it is instanced by an empty, a particle system or a geometry-nodes
            tree. The instance pass still exports a plugin for each source and tracks it under the
            source's own track id, so the prune passes have to be told that these ids are alive -
            allObjects cannot see them. Read from the previous pass's snapshot, which is what the
            trackers were filled from; pruning runs before this cycle's instance pass.
        """
        instanced = self.persistedState.instancedObjectsByParent
        return set().union(*instanced.values()) if instanced else set()

    @property
    def collectedAttrNames(self):
        """ The attribute names the scene refers to, so mesh and per-instance attributes nothing can
            read are not exported. Both sets, see collectReferencedAttrNames(). Collected on demand,
            once per exporter.
        """
        if self._referencedAttrNames is None:
            # Imported here because reference_collector imports this module.
            from vray_blender.exporting.reference_collector import collectReferencedAttrNames
            self._referencedAttrNames = collectReferencedAttrNames(self)
        return self._referencedAttrNames

    @property
    def referencedAttrNames(self):
        """ Every referenced name. Gates the mesh attribute export. """
        return self.collectedAttrNames['all']

    @property
    def referencedUserAttrNames(self):
        """ Only the names read through a V-Ray user attribute. Gates the per-instance export,
            since GeomInstancer.user_attributes is the only thing that can serve those.
        """
        return self.collectedAttrNames['userAttrs']

    @property
    def visibleObjects(self):
        return self.persistedState.visibleObjects

    @property
    def allowRelativePaths(self):
        # For cloud we always want absolute paths since the .vrscene is saved in %temp%/vray_blender
        return self.exportOnly and not self.commonSettings.isCloudSubmit()

    @property
    def exportedMtls(self):
        return self.persistedState.exportedMtls

    @property
    def animatedBitmapMaterials(self):
        return self.persistedState.animatedBitmapMaterials

    def registerAnimatedBitmapMaterial(self, material):
        """ Record a material that contains an animated image-sequence bitmap, so the incremental
            exporter re-exports it next frame without scanning every node tree. """
        if material is not None:
            self.persistedState.animatedBitmapMaterials.add(material.original.session_uid)

    def syncSceneState(self):
        """ Collect the scene-wide state the export passes depend on: the depsgraph update index,
            the active instancers and the list of all scene objects.

            Everything here used to be recomputed by walking the whole scene (and every instance)
            on each interactive update. Now only the update index is built unconditionally; the
            rest is cached in PersistedState and refreshed only when the scene really changed.
            Full exports always rebuild.

            NOTE: the active-instancer snapshot is deliberately NOT part of this call -
            syncActiveInstancers() has to run after GeometryExporter.syncObjVisibility(). See there.
        """
        self._buildUpdateIndex()
        self._syncAllObjects()


    def resetSceneStats(self):
        """ Start a new set of export counts, or switch collection off entirely.

            Must be called at the top of every export. In production the ExporterContext is created
            once per job and reused for every animation frame (engine/renderer_prod.py:256), so
            without this the counts - and the sets backing them - would grow for the whole job.

            Collection is gated on the same debug_log_times property that decides whether the
            numbers are ever printed (engine/renderer_ipr_viewport.py:150) and whether the C++ side
            collects its own stats. With it off, every counting site becomes a no-op call.
        """
        collect = (self.dg is not None) and self.dg.scene.vray.Exporter.debug_log_times
        self.sceneStats = SceneStats() if collect else FakeSceneStats()


    def _buildUpdateIndex(self):
        """ Index dg.updates once into the per-type sets the exporters query, and derive the
            signals the incremental passes are driven by. """
        geometry, transform, shading, allIds = set(), set(), set(), set()
        updatedObjects = set()
        structural = False
        collection = False

        for u in self.dg.updates:
            original = u.id.original
            allIds.add(original.session_uid)

            if u.is_updated_geometry:
                geometry.add(original.session_uid)
            if u.is_updated_transform:
                transform.add(original.session_uid)
            if u.is_updated_shading:
                shading.add(original.session_uid)

            if isinstance(original, bpy.types.Object):
                updatedObjects.add(original)
            elif isinstance(original, (bpy.types.Scene, bpy.types.ViewLayer, bpy.types.Collection)):
                # See the comment on self.structuralUpdate.
                structural = True
                collection = collection or isinstance(original, bpy.types.Collection)

        self.dgUpdates = {
            'geometry':  geometry,
            'transform': transform,
            'shading':   shading,
            'all':       allIds,
        }
        self.dgUpdatedObjects = updatedObjects
        self.structuralUpdate = structural
        self.collectionUpdate = collection


    def _collectAllObjects(self):
        """ Walk the scene for the set backing the allObjects property. """
        allObjects = set(self.ctx.scene.objects)

        for coll in [c for c in bpy.data.collections if c.library is not None]:
            allObjects.update(coll.all_objects)

        return allObjects


    def _syncAllObjects(self):
        """ Decide whether the set of objects in the scene changed since the last export, and
            collect it when it did. Used to tell objects that can be deleted from V-Ray from those
            that should only be hidden. """
        cached = self.persistedState.allObjectsTrackIds

        if (cached is not None) and (not self.fullExport) and (not self.structuralUpdate):
            # Objects can only be added, removed or re-linked through a change Blender reports as a
            # structural update, so the cached ids are still accurate here and the scene walk can be
            # skipped altogether. _allObjects is left unset rather than carried over from the
            # previous cycle: in production the same ExporterContext is reused for every animation
            # frame, so carrying it would be exactly the stale-wrapper hazard the id cache avoids.
            self._allObjects = None
            self.allObjectsChanged = False
            return

        allObjects = self._collectAllObjects()
        trackIds = set(getObjTrackId(obj) for obj in allObjects)

        # structuralUpdate cannot distinguish a selection change from a real one, so diff the result:
        # this is the signal the expensive passes are allowed to trust.
        self.allObjectsChanged = (cached is None) or (trackIds != cached)
        self.persistedState.allObjectsTrackIds = trackIds
        self._allObjects = allObjects


    def syncActiveInstancers(self):
        """ The track ids of every instancer in the scene. Three kinds exist:
              1. Legacy, set through Data Properties -> Instancing
              2. Objects made instancers through e.g. geometry nodes
              3. V-Ray Fur objects (also instancers when they have instancers selected)
            The depsgraph only yields the visible instancers, but we need all of them in order to
            tell which ones to delete from V-Ray and which to only hide.

            Only kind 2 requires walking dg.object_instances, and that walk is the single most
            expensive thing the interactive exporter does (~1 us per instance, so ~0.9 s for a
            500k-instance scene) - so it is re-run only when an object that could plausibly be an
            instancer changed. Everything else reuses the previous cycle's result.

            MUST be called after GeometryExporter.syncObjVisibility(): an instancer enters or leaves
            the depsgraph's instance list when its visibility changes, and objectsWithUpdatedVisibility
            is the only precise signal for that. structuralUpdate cannot be used - Blender reports a
            selection change identically to a hide, so it is set on nearly every click.
        """
        candidates = self.persistedState.instancerCandidates
        cached = self.persistedState.syncedActiveInstancers
        rescan = self.fullExport or self.allObjectsChanged \
                    or (candidates is None) or (cached is None)

        if not rescan:
            # An object can only start or stop instancing if it was reported as updated. Testing the
            # updated objects against both the known candidates and a fresh check also catches an
            # object that has just become one (GN modifier added, show_instancer_* toggled) and one
            # that has just stopped being one.
            rescan = any(
                (getObjTrackId(obj) in candidates) or __class__._isInstancerCandidate(obj)
                for obj in self.dgUpdatedObjects
            ) or bool(candidates & set(self.objectsWithUpdatedVisibility))

        if not rescan:
            # Copy rather than alias, so that a caller mutating ctx.activeInstancers cannot corrupt
            # the cache the next skipped cycle will read.
            self.activeInstancers = set(cached)
            return

        candidates = set()
        legacyInstancers = set()
        furInstancers = set()

        for obj in self.sceneObjects:
            if obj.is_instancer:
                legacyInstancers.add(getObjTrackId(obj))
            if obj.vray.isVRayFur:
                furInstancers.add(getObjTrackId(obj))
            if __class__._isInstancerCandidate(obj):
                candidates.add(getObjTrackId(obj))

        # Geometry-nodes instancers are invisible to Object.is_instancer, so they can only be found
        # by walking the instances.
        gnInstancers = set(
            getObjTrackId(i.parent) for i in self.dg.object_instances
            if i.is_instance and (i.parent is not None)
        )

        self.activeInstancers = gnInstancers | legacyInstancers | furInstancers

        # Both caches are written here and nowhere else, so they cannot fall out of step with each
        # other or depend on a caller remembering to persist anything.
        self.persistedState.instancerCandidates = candidates
        self.persistedState.syncedActiveInstancers = set(self.activeInstancers)


    @staticmethod
    def _isInstancerCandidate(obj: bpy.types.Object):
        """ True if obj could be (or become) an instancer. See PersistedState.instancerCandidates. """
        # isVRayFur is a plain BoolProperty on VRayObject (plugins/__init__.py), so it is present on
        # every Object - read directly, the same way the loop above and every other caller does.
        return obj.is_instancer \
            or bool(obj.particle_systems) \
            or any(m.type == 'NODES' for m in obj.modifiers) \
            or obj.vray.isVRayFur


    def registerReferencedPluginParam(self, targetPluginName: str, attrName: str, value, append=False):
        """ Register a plugin parameter that references other plugins, to be exported only after the
            whole scene has been exported (the referenced plugins may not have been created yet).

        Args:
            targetPluginName (str): The plugin on which the parameter is set.
            attrName (str): The parameter name.
            value: AttrPlugin | list[AttrPlugin] | scalar accepted by plugin_utils.updateValue.
            append (bool): If True, accumulate values into a list per (plugin, attr) - used e.g. when
                several render channels reference the same light. If False, the last registration wins.
        """
        key = (targetPluginName, attrName)

        if not append:
            self.referencedPluginParams[key] = ReferencedPluginParam(targetPluginName, attrName, value, append=False)
            return

        existing = self.referencedPluginParams.get(key)
        if existing is None:
            seed = list(value) if isinstance(value, list) else [value]
            self.referencedPluginParams[key] = ReferencedPluginParam(targetPluginName, attrName, seed, append=True)
        else:
            assert existing.append, f"Mixing appended and single-valued referenced plugin params for {key}"
            existing.value.extend(value) if isinstance(value, list) else existing.value.append(value)


    def registerReferencedObject(self, obj: bpy.types.Object):
        """ Mark a scene object as referenced by a plugin parameter so that it is exported even when
            it is invisible / disabled in renders. See the comment on self.referencedObjects.

            Two passes force-export from this: GeometryExporter._exportObjects and, for lights,
            LightExporter._exportScene. Each skips what the other owns.
        """
        if obj is None:
            return
        objTrackId = getObjTrackId(obj)
        self.referencedObjects.setdefault(objTrackId, obj)


    def linkPluginToRenderChannel(self, pluginName: str, channelLinkAttr: str, renderChannelPlugin: AttrPlugin):
        """ Store information about a link from a plugin to a render channel, i.e. that the plugin
            output should be visible in a render channel.

        Args:
            pluginName (str): The name of the plugin to show in the render channel
            channelLinkAttr (str): Attribute of type PLUGIN_LIST to which the render channel name should be added.
            renderChannelPlugin (AttrPlugin): the render channel plugin
        """
        self.registerReferencedPluginParam(pluginName, channelLinkAttr, renderChannelPlugin, append=True)


class ExporterBase(ExporterContext):
    """ This class serves only to skip copying ExporterContext's members in each
        exporter's constructor. The alternative is to have ExporterContext as a member,
        but this leads to confusion of the names with the Blender's context object and
        also makes accessing ExporterContext's properties unnecessarily verbose ( one more
        indirection )
    """
    def __init__(self, exporterCtx: ExporterContext):
        super().__init__(exporterCtx)

class DataArray:
    def __init__(self, ptr = 0, count = 0, name = ""):
        self.ptr = ptr
        self.count = count
        self.name = name

    @staticmethod
    def fromAttribute(data, attributeName: str):
        """ Creates DataArray from attribute data """
        if attribute := data.attributes.get(attributeName, None):
            return DataArray(attribute.data[0].as_pointer(), len(attribute.data))
        return DataArray(0, 0) # Return an empty DataArray if the attribute is missing

class AttrDataLayer:
    def __init__(self, ptr, count: int, name: str, dataType: str, domain: str):
        self.ptr = ptr
        self.count = count
        self.name = name
        self.dataType = dataType
        self.domain = domain

class MeshData:
    """ Zero-copy description of a Blender mesh for vray.exportGeometry.

        Every geometry field is a raw pointer into Blender's arrays, so the mesh must outlive
        the export: immediately for asyncExport=False, until finishExport() otherwise. Here
        rather than in obj_export.py because the Chaos Scatter preview builds one too.
    """
    NORMALS_FACE    = 0
    NORMALS_POINT   = 1
    NORMALS_CORNER  = 2

    def __init__(self, name = ""):
        from vray_blender.bin import VRayBlenderLib as vray

        self.name           = name
        self.normalsDomain  = MeshData.NORMALS_FACE
        self.options        = vray.MeshExportOptions()
        self.vertices       : DataArray = None
        self.loopTris       : DataArray = None
        self.loops          : DataArray = None
        self.normals        : DataArray = None
        self.loopTriPolys   : DataArray = None
        self.cornerEdges    : DataArray = None
        self.edgeCreases    : DataArray = DataArray()
        self.edgeVertices   : DataArray = DataArray()
        self.vertexCreases  : DataArray = DataArray()
        self.polyMtlIndices : DataArray = None
        self.mtlIdOffset = 0
        self.loopUVs        : list[DataArray] = []
        self.loopColors     : list[AttrDataLayer] = []

        self.subdiv = SimpleNamespace(enabled=False, level=0, type=0, useCreases=False)

        self.options.mergeChannelVerts = False

    @staticmethod
    def fromMesh(mesh, name: str, attrNames: set | None = None):
        """ Fill the geometry pointers from an evaluated mesh. attrNames limits the exported mesh
            attributes, None exports all. Callers set .options themselves.
        """
        # Function-level: blender_utils reaches back into lib.defs via ui.preferences
        from vray_blender.lib.blender_utils import iterExportedMeshAttributes

        mesh.calc_loop_triangles()

        meshData = MeshData(name)
        meshData.vertices     = DataArray(mesh.vertices[0].as_pointer(), len(mesh.vertices))
        meshData.loops        = DataArray(mesh.loops[0].as_pointer(), len(mesh.loops))
        meshData.loopTris     = DataArray(mesh.loop_triangles[0].as_pointer(), len(mesh.loop_triangles))
        meshData.loopTriPolys = DataArray(mesh.loop_triangle_polygons[0].as_pointer(),
                                          len(mesh.loop_triangle_polygons))
        meshData.cornerEdges  = DataArray.fromAttribute(mesh, '.corner_edge')

        meshData.edgeCreases = DataArray.fromAttribute(mesh, "crease_edge")
        if meshData.edgeCreases.count > 0:
            meshData.edgeVertices = DataArray(mesh.edges[0].as_pointer(), len(mesh.edges))
        meshData.vertexCreases = DataArray.fromAttribute(mesh, "crease_vert")

        # Blender adds 'material_index' to the mesh when extra material slots are created.
        meshData.polyMtlIndices = DataArray.fromAttribute(mesh, 'material_index')

        match mesh.normals_domain:
            case 'FACE':
                meshData.normals = DataArray(mesh.polygon_normals[0].as_pointer(), len(mesh.polygon_normals))
                meshData.normalsDomain = MeshData.NORMALS_FACE
            case 'POINT':
                meshData.normals = DataArray(mesh.vertex_normals[0].as_pointer(), len(mesh.vertex_normals))
                meshData.normalsDomain = MeshData.NORMALS_POINT
            case 'CORNER':
                meshData.normals = DataArray(mesh.corner_normals[0].as_pointer(), len(mesh.corner_normals))
                meshData.normalsDomain = MeshData.NORMALS_CORNER

        for layer in mesh.uv_layers:
            # layer.data may be empty while the object's mesh is in edit mode
            if len(layer.data) > 0:
                meshData.loopUVs.append(DataArray(layer.data[0].as_pointer(), len(layer.data), layer.name))

        for layer in iterExportedMeshAttributes(mesh):
            if (attrNames is None) or (layer.name in attrNames):
                meshData.loopColors.append(AttrDataLayer(layer.data[0].as_pointer(), len(layer.data),
                                                         layer.name, layer.data_type, layer.domain))
        return meshData


@dataclass
class NdDataArray:
    buffer: ndarray
    name: str = ""


class AColor:
    """ Representation of Color with alpha channel
    """

    def __init__(self, chanelsInput):
        """ Gets a list (chanelsInput) of channels where the firs element in it is R,
            the second G, the third B, and the last is Alpha.
            The argument chanelsInput can take a list with less than 4 elements
        """
        self.channels = [0, 0, 0, 1]
        for i in range(0, min(4, len(chanelsInput))):
            self.channels[i] = chanelsInput[i]

    @property
    def r(self):
        return self.channels[0]
    @r.setter
    def r(self, value):
        self.channels[0] = value

    @property
    def g(self):
        return self.channels[1]
    @g.setter
    def g(self, value):
        self.channels[1] = value

    @property
    def b(self):
        return self.channels[2]
    @b.setter
    def b(self, value):
        self.channels[2] = value

    @property
    def a(self):
        return self.channels[3]
    @a.setter
    def a(self, value):
        self.channels[3] = value

    def __getitem__(self, i):
        return self.channels[i]


class PluginDesc:
    """ This is the main carrier of plugin property data. Fill-in the properties
        and pass the object to the export procedure.
    """
    def __init__(self, name, type):
        self.name = name    # unique name of the plugin
        self.type = type    # type of the plugin
        self.attrs = {}

        # VRay extension of the Blender's underlying object for a plugin.
        # It will have the state set through the UI
        self.vrayPropGroup = {}

        # 'node' should be set if the plugin is being exported as part of
        # a nodetree. It will be used by the final export procedure to determine
        # how to export meta properties.
        self.node: bpy.types.Node = None


    def resetAttribute(self, name):
        """ Reset a single attribute to its default (not set) state.
            This is achieved by setting it to empty Plugin.
        """
        self.attrs[name] = AttrPlugin()

    def setAttribute(self, name, value, overwriteExisting = True):
        """ Add/update a single attribute """
        if overwriteExisting or (name not in self.attrs):
            self.attrs[name] = value

    def appendToListAttribute(self, name, value):
        """ Appends value to list attribute """
        if (name not in self.attrs) or \
                ((attr := self.attrs[name]) and (type(attr) is AttrPlugin) and attr.isEmpty()):
            self.attrs[name] = [value]
        else:
            assert type(self.attrs[name]) is list
            self.attrs[name].append(value)



    def setAttributes(self, dict):
        """ Add/update multiple values at once """
        self.attrs.update(dict)


    def getAttribute(self, name):
        return self.attrs.get(name)


    def removeAttribute(self, name):
        try:
            self.attrs.pop(name)
        except KeyError:
            pass

    def isSetAttribute(self, name):
        return name in self.attrs


class AttrPlugin:
    OUTPUT_UNDEFINED = None
    OUTPUT_DEFAULT   = ''

    def __init__(self, name = "", output = OUTPUT_UNDEFINED, forceUpdate = False, pluginType = ""):
        self.name = name        # The name of the plugin

        # The name of the output socket to connect. We distinguish between 3 states:
        # 1. None - use only to tell the generic code for exporting sockets that it should
        #           set the value, i.e. to differentiate with the default ('') value.
        # 2. ''   - use plugin's default output (i.e. do not append ::sock_name when exporting)
        # 3. non-empty - use the output name set to this field
        self.output = output

        # 'True' to bypass change tracker cache on the server
        self.forceUpdate = forceUpdate

        # Some exporters need additional data about the exported plugin besides its V-Ray name.
        # The actual plugin exporter may add arbitrary data as auxData to be consumed by its caller.
        self.auxData = {}

        # The plugin type being exported. Used for type conversions.
        self.pluginType = pluginType

    def isEmpty(self):
        return self.name == ''

    def isOutputSet(self):
        return self.output is not __class__.OUTPUT_UNDEFINED

    def useDefaultOutput(self):
        self.output = __class__.OUTPUT_DEFAULT


class AttrListValue:
    """ Python representation of VRayBaseTypes::AttrListValue """

    def __init__(self):
        self.attrList = [] # members of AttrListValue instance
        self.attrType = "" # string representing the types in self.attrList

    def _getTypeAsChar(self, val):
        """ returns the type of value. If it is a list the next len(list) character are its member types """
        if type(val) is bool or type(val) is int:
            return 'i'
        elif type(val) is float:
            return 'f'
        elif type(val) is str:
            return 's'
        elif type(val) is AttrPlugin:
            return 'p'
        elif type(val) is mathutils.Vector:
            return 'v'
        elif type(val) is mathutils.Color:
            return 'c'
        elif type(val) is list:
            chType = 'l'
            for v in val:
                chType += self._getTypeAsChar(v)
            return chType

    def append(self, val):
        if type(val) is AttrPlugin:
            self.attrList.append(val.name)
        elif type(val) is list:
            # Handling of plugin list members
            self.attrList.append([v.name if type(v) is AttrPlugin else v  for v in val])
        else:
            self.attrList.append(val)
        self.attrType += self._getTypeAsChar(val)

    def isEmpty(self):
        return len(self.attrList) == 0

    def __eq__(self, other):
        return self.attrList == other.attrList


# This definition lets the class use type hints for its own type
TSceneStats = TypeVar("TSceneStats", bound="SceneStats")


class SceneStats:
    """Statistics for a single scene export.

        The 'unique*' sets and the plain counters answer different questions and both are kept: an
        object re-exported once as a scene object and again as an instance source is two exports of
        one object.
    """

    def __init__(self):
        self.uniqueMtls     = set()
        self.uniqueObjs     = set()
        self.uniquePlugins  = set()

        self.mtls: int      = 0
        self.objs: int      = 0
        self.plugins: int   = 0
        self.attrs: int     = 0

        # Plugins created empty only so that a reference to them resolves, see
        # plugin_utils.forwardDeclarePlugin(). Deliberately NOT part of 'plugins': the same plugin is
        # normally exported for real elsewhere, so adding these would count it twice. Their names do
        # join 'uniquePlugins'.
        self.forwardDeclared: int = 0

    # Collection goes through these rather than through '+=' on the fields, so that FakeSceneStats
    # can make the whole thing disappear. See resetSceneStats().
    enabled = True

    def addPlugin(self, pluginName: str):
        self.plugins += 1
        self.uniquePlugins.add(pluginName)

    def addForwardDeclaredPlugin(self, pluginName: str):
        self.forwardDeclared += 1
        self.uniquePlugins.add(pluginName)

    def addAttrs(self, count: int):
        self.attrs += count

    def addObject(self, objTrackId: int):
        self.objs += 1
        self.uniqueObjs.add(objTrackId)

    def addMaterial(self, mtlName: str):
        self.mtls += 1
        self.uniqueMtls.add(mtlName)

    def __add__(self, v: TSceneStats):
        result = SceneStats()
        result.uniqueMtls = self.uniqueMtls.union( v.uniqueMtls)
        result.uniqueObjs = self.uniqueObjs.union( v.uniqueObjs)
        result.uniquePlugins = self.uniquePlugins.union( v.uniquePlugins)

        result.mtls = self.mtls + v.mtls
        result.objs = self.objs + v.objs
        result.plugins = self.plugins + v.plugins
        result.attrs = self.attrs + v.attrs
        result.forwardDeclared = self.forwardDeclared + v.forwardDeclared

        return result

    def __sub__(self, v: TSceneStats):
        """ The delta between two snapshots, so a single pass can report just its own share of the
            cycle-wide accumulator. Only the counters are subtractable; the 'unique*' sets are
            difference-of-sets. """
        result = SceneStats()
        result.uniqueMtls = self.uniqueMtls.difference(v.uniqueMtls)
        result.uniqueObjs = self.uniqueObjs.difference(v.uniqueObjs)
        result.uniquePlugins = self.uniquePlugins.difference(v.uniquePlugins)

        result.mtls = self.mtls - v.mtls
        result.objs = self.objs - v.objs
        result.plugins = self.plugins - v.plugins
        result.attrs = self.attrs - v.attrs
        result.forwardDeclared = self.forwardDeclared - v.forwardDeclared

        return result

    def snapshot(self) -> SceneStats:
        """ A copy that will not move when the export continues. """
        result = SceneStats()
        result.uniqueMtls = set(self.uniqueMtls)
        result.uniqueObjs = set(self.uniqueObjs)
        result.uniquePlugins = set(self.uniquePlugins)
        result.mtls, result.objs = self.mtls, self.objs
        result.plugins, result.attrs = self.plugins, self.attrs
        result.forwardDeclared = self.forwardDeclared
        return result


class FakeSceneStats(SceneStats):
    """ A no-op implementation, in the same spirit as FakeTimeStats and FakeObjTracker.

        Used unless debug_log_times is on. The counters are touched once per plugin and once per
        object, and 'uniquePlugins' would otherwise hold a name for every plugin in the scene - not
        something to pay for, in time or in memory, when nobody is going to read the result. The
        fields are still there and still read as zero, so the reporting code needs no guard of its
        own beyond 'enabled'.
    """
    enabled = False

    def addPlugin(self, pluginName: str):
        pass

    def addForwardDeclaredPlugin(self, pluginName: str):
        pass

    def addAttrs(self, count: int):
        pass

    def addObject(self, objTrackId: int):
        pass

    def addMaterial(self, mtlName: str):
        pass

    def snapshot(self):
        # Nothing ever moves, so there is nothing to copy.
        return self


class NodeContext:
    """ Context for a single node tree export """

    class _NodeItem:
        """ Context manager for the export of a single node """
        def __init__(self, ctx: NodeContext, node: bpy.types.Node):
            self.ctx = ctx
            self.node = node

        def __enter__(self):
            self.ctx._pushNode(self.node)

        def __exit__(self, *args):
            self.ctx._popNode()

    # A list of errors that have occurred during the export of the node tree.
    # It is inconvenient to pass the context to all utility functions that might need it just to
    # be able to log errors. This facility provides a globally accessible place to store error messages.
    # The implementation relies on the fact that all export actions will be carried out sequentiallu.
    # NOTE:The list should be updated through the registerError() function. This is a
    # provision for a possible future need to store the information per-thread if the export procedure
    # gets parallelized when Python 3.12+ is adopted.
    _errorList = set()

    def __init__(self, exporterCtx: ExporterContext, dataObj = None, scene = None, renderer = None):
        self.exporterCtx    = exporterCtx
        self.rootObj        = dataObj # bpy.types.ID to which the node tree is attached
        self.scene          = scene
        self.renderer       = renderer

        self.material       = None
        self.sceneObj       = None # [optional] The bpy.types.Object which holds dataObj
        self.nodeTracker: ScopedNodeTracker = None

        self.stats = SceneStats()

        self.object_context = None

        # Cache of exported (reachable) nodes to prevent duplicate exports.
        # Key: (node, group_instance_path). The node object (not id()) is used so the
        # dict keeps it alive -- preventing wrapper GC and address reuse causing false hits.
        self._exportedNodes: Dict[tuple, AttrPlugin] = {}

        # The group instance path currently active during export.
        # Tuple of VRayNodeGroup nodes entered from the root tree to reach the node
        # being exported.  Set by NodeContext.pushGroupPath().
        self._groupInstancePath: tuple = ()

        # The nodetree being exported
        self.ntree: bpy.types.NodeTree

        # A stack of the parent nodes of the node that is being curently exported.
        # The current node is at the top of the stack.
        self.nodes: list[bpy.types.Node] = []

        # Virtual node counter. Used to append a unique suffix to vitual nodes
        self.virtualNodes: int = 0

        # A custom handler to be invoked for each node being exported in addition to the
        # regular export procedure. The handled is invoked right before export_utils.exportPlugin()
        # is exported for the node
        # The signature of the handler is customHandler(nodeCtx: NodeContext, plDesc: PluginDesc)
        self.customHandler = None

        # Stack of composed transforms. The last transform is the active one (if there is one).
        self.transformStack = []

        # Reset the error list for each node tree that is to be exported
        __class__._errorList = set()

    @property
    def node(self):
        assert self.nodes, "Nodes stack is empty"
        return self.nodes[-1]

    def _pushNode(self, node: bpy.types.Node):
        self.nodes.append(node)

    def _cacheKey(self, node: bpy.types.Node, outputId = None) -> tuple:
        uvwKey = tuple(round(v, 6) for row in self.transformStack[-1] for v in row) if self.transformStack else None
        return (node, self._groupInstancePath, outputId, uvwKey)

    def _popNode(self):
        assert self.nodes, "Nodes stack is empty"
        self.nodes.pop()

    def push(self, node: bpy.types.Node):
        return __class__._NodeItem(self, node)

    def pushUVWTransform(self, matrix: mathutils.Matrix):
        if len(self.transformStack) == 0:
            self.transformStack.append(matrix)
        else:
            self.transformStack.append(matrix @ self.transformStack[-1])

    def popUVWTransform(self):
        if len(self.transformStack)>0:
            self.transformStack.pop()

    def getUVWTransform(self):
        if len(self.transformStack)>0:
            return self.transformStack[-1]
        return mathutils.Matrix()

    def __enter__(self):
        pass

    def __exit__(self, *args):
        assert len(self.nodes) == 0, "Incorrectly unwound nodes stack"
        self._reportErrors()

    def getTreeType(self):
        return self.ntree.vray.tree_type

    def cacheNodePlugin(self, node: bpy.types.Node, attrPlugin: AttrPlugin = AttrPlugin(), outputId = None):
        """ Caches the AttrPlugin of already exported V-Ray node.
            If the node doesn't have corresponding AttrPlugin, an empty one is added.
        """
        self._exportedNodes[self._cacheKey(node, outputId)] = attrPlugin

    def getCachedNodePlugin(self, node: bpy.types.Node, outputId = None):
        """ If the given node is cached returns its AttrPlugin, otherwise it returns None
        """
        return self._exportedNodes.get(self._cacheKey(node, outputId), None)

    def pushGroupPath(self, groupPath: tuple):
        """ Context manager: set the group instance path for the duration of exporting
            a node that lives inside the given group nodes.  Also pushes to the module-level
            stack in tools.py so that resolveNodeSocket can use it for fresh calls.
        """
        from vray_blender.exporting.tools import _groupExportStack

        class _GroupPathCtx:
            def __init__(ctx):
                ctx._old = self._groupInstancePath

            def __enter__(ctx):
                self._groupInstancePath = tuple(groupPath)
                _groupExportStack.append(self._groupInstancePath)
                return ctx

            def __exit__(ctx, *_):
                if _groupExportStack:
                    _groupExportStack.pop()
                self._groupInstancePath = ctx._old

        return _GroupPathCtx()

    @staticmethod
    def registerError(msg: str):
        __class__._errorList.add(msg)

    @staticmethod
    def getErrors():
        return __class__._errorList

    def _reportErrors(self):
        for i, msg in enumerate(__class__._errorList):
            if i > 1:
                debug.report('WARNING', "Multiple node export warnings")
                break
            errMsg = f"{msg} [{self.rootObj.id_type} {self.rootObj.name}]"
            if self.sceneObj is not None:
                errMsg += f" Object: {self.sceneObj.name}"

            if self.exporterCtx.fullExport and self.exporterCtx.interactive:
                # To avoid pestering the user with status messages on each scene change, only
                # report as status during the first export after switching to viewport/IPR.
                debug.report('WARNING', errMsg)
            elif not self.exporterCtx.preview:
                # In production or subsequnt changes to the scene while viewport render is running,
                # only log the issues to the console. In preview mode, we don't want to print anything
                # at all as it could easily flood the console with messages.
                debug.printWarning(errMsg)

@dataclass
class LinkInfo:
    """ This class represents the information defined in the options:link_info field
        of the plugin parameter description.
    """
    OBJECTS     = 'OBJECTS'
    OBJECT_DATA = 'OBJECT_DATA'

    linkType: str = OBJECTS
    fnFilter: function = lambda obj: True