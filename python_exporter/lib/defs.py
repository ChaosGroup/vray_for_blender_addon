# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations
from dataclasses import dataclass, field
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

        # Settings values collected from the user interface
        self.commonSettings: CommonSettings = None

        # Track VRay plugins associated with scene objects. The same plugins may be referenced
        # by multiple objects.
        self.objTrackers: Mapping[str, ObjTracker|FakeObjTracker]  = {}

        # Track VRay plugins assosiated with nodes in a nodetree.
        self.nodeTrackers: Mapping[str, ScopedNodeTracker|FakeScopedNodeTracker]  = {}

        # A snapshot of the active instancer objects in the scene
        self.activeInstancers = set() # set of objTrackId

        # All objects in the scene, inlcuding those from linked collections
        self.allObjects = set()

        # State to carry over to the next rendering cycle
        self.persistedState = PersistedState()

        # Depsgraph updates split by type.
        self.dgUpdates: dict[str, set[int]] = {}    #   dict[update_type, set[objTrackId]]

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
        self.allObjects             = other.allObjects
        self.dgUpdates              = other.dgUpdates
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

    def calculateObjectVisibility(self):
        """ Fill the visibility and active instancers info into ExporterContext """

        # Compile a list of the active instancers in the scene. There are two types of instancers:
        #   1. Legacy, set through Data Properties -> Instancing
        #   2. Objects made instancers through e.g. geometry nodes
        #   3. V-Ray Fur Objects (they are also instancers if they have instancers selected)
        # The depsgraph only includes the visible instancers. We need however a list of all instancers
        # in the scene in order to determine which ones to delete and which to only hide.
        legacyInstancers = set((getObjTrackId(o) for o in self.sceneObjects if o.is_instancer))
        instancers = set((getObjTrackId(i.parent) for i in self.dg.object_instances if i.is_instance and (i.parent is not None)))
        furInstancers = set((getObjTrackId(o) for o in self.sceneObjects if o.vray.isVRayFur))

        self.activeInstancers = instancers.union(legacyInstancers).union(furInstancers)

        # Get a list of all objects in the scene, including objects from linked collections. We will
        # need it in order to determine which objects can be deleted from the scene vs only be hidden.
        self.allObjects = set([o for o in self.ctx.scene.objects])

        # Add objects from linked collections as they are not included in scene's depsgraph
        linkedCollections = [c for c in bpy.data.collections if c.library is not None]
        for c in linkedCollections:
            self.allObjects.update(c.all_objects)

        self.dgUpdates = {
            'geometry':  set((u.id.original.session_uid for u in self.dg.updates if u.is_updated_geometry)),
            'transform': set((u.id.original.session_uid for u in self.dg.updates if u.is_updated_transform)),
            'shading':   set((u.id.original.session_uid for u in self.dg.updates if u.is_updated_shading)),
            'all':       set(u.id.original.session_uid for u in self.dg.updates)
        }


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

            Lights are ignored: they are exported by the light exporter, not the geometry force-export
            pass that consumes self.referencedObjects, so registering one would be a no-op.
        """
        if (obj is None) or (obj.type == 'LIGHT'):
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
    """Statistics for a single scene export"""

    def __init__(self):
        self.uniqueMtls     = set()
        self.uniqueObjs     = set()
        self.uniquePlugins  = set()

        self.mtls: int      = 0
        self.objs: int      = 0
        self.plugins: int   = 0
        self.attrs: int     = 0

    def __add__(self, v: TSceneStats):
        result = SceneStats()
        result.uniqueMtls = self.uniqueMtls.union( v.uniqueMtls)
        result.uniquePlugins = self.uniquePlugins.union( v.uniquePlugins)

        result.mtls = self.mtls + v.mtls
        result.objs = self.objs + v.objs
        result.plugins = self.plugins + v.plugins
        result.attrs = self.attrs + v.attrs

        return result


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

    def _cacheKey(self, node: bpy.types.Node) -> tuple:
        return (node, self._groupInstancePath)

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

    def cacheNodePlugin(self, node: bpy.types.Node, attrPlugin: AttrPlugin = AttrPlugin()):
        """ Caches the AttrPlugin of already exported V-Ray node.
            If the node doesn't have corresponding AttrPlugin, an empty one is added.
        """
        self._exportedNodes[self._cacheKey(node)] = attrPlugin

    def getCachedNodePlugin(self, node: bpy.types.Node):
        """ If the given node is cached returns its AttrPlugin, otherwise it returns None
        """
        return self._exportedNodes.get(self._cacheKey(node), None)

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
                debug.reportAsync('WARNING', "Multiple node export warnings")
                break
            errMsg = f"{msg} [{self.rootObj.id_type} {self.rootObj.name}]"
            if self.sceneObj is not None:
                errMsg += f" Object: {self.sceneObj.name}"

            if self.exporterCtx.fullExport and self.exporterCtx.interactive:
                # To avoid pestering the user with status messages on each scene change, only
                # report as status during the first export after switching to viewport/IPR.
                debug.reportAsync('WARNING', errMsg)
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