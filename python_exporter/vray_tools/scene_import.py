# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

""" Assembly of a Blender scene from an imported .vrscene plugin dict.

    The dict is produced by vrscene_import.buildVrsceneDict() from data read by the
    server through the V-Ray SDK. SceneImporter materializes it: meshes through the
    fast numpy/foreach_set path, materials through the existing node import machinery
    (nodes/importing/), objects/lights/camera/settings onto the corresponding
    Blender datablocks and property groups.
"""

import math
import os
from collections import Counter
from dataclasses import dataclass, field
from types import GeneratorType
from pathlib import Path

import bpy
import mathutils
import numpy as np

from vray_blender import debug
from vray_blender.bin import VRayBlenderLib as vray
from vray_blender.engine.vfb_event_handler import VfbEventHandler
from vray_blender.lib import attribute_utils, blender_utils, camera_utils, lib_utils, path_utils
from vray_blender.lib.defs import LinkInfo
from vray_blender.lib.hair_build_utils import buildHairCurvesBase, _DEFAULT_HAIR_RADIUS
from vray_blender.lib.lib_utils import LightTypeToPlugin
from vray_blender.lib.mesh_build_utils import buildTriMeshBase
from vray_blender.lib.settings_defs import LightSelectMode
from vray_blender.nodes import importing as NodesImport, tools as NodesTools, tree_defaults, utils as NodesUtils
from vray_blender.nodes.customRenderChannelNodes import customRenderChannelNodesDesc
from vray_blender.vray_tools import anim_import
from vray_blender.nodes.operators.import_file import importMaterialsFromDict, _importVRayProxy, _assignMaterialsToSlots
from vray_blender.nodes.sockets import addInput, getSpecialChannelSocket, moveExtendSocketToBottom
from vray_blender.nodes.specials.object_properties import VRayObjectMatteProps, VRayObjectSurfaceProps, VRayObjectVisibilityProps
from vray_blender.nodes.tree_defaults import createNodeTreeForLightObject, addWorldNodeTree, addDecalNodeTree
from vray_blender.plugins import PLUGINS, findPluginModule
from vray_blender.plugins.geometry.VRayDecal import createDecalObject, generateDecalPreviewMesh
from vray_blender.plugins.settings.SettingsImageSampler import _maxSubdivsGPUToSamplesLimit, _minSubdivsToRaysPerPixel
from vray_blender.utils import splat_preview
from vray_blender.vray_tools import import_common, instancer_import
from vray_blender.vray_tools.import_common import refName as _refName, sceneName as _sceneName
from vray_blender.vray_tools.vray_proxy import loadVRayScenePreviewMesh
from vray_blender.vray_tools.vrscene_import import normalizeUnits


# The maximum number of UV channels supported by Blender
_MAX_UV_CHANNELS = 8

# Light attribute -> the light_select_mode it implies.
# Keep in sync with _CHANNELS_PROPERTY_MAP in plugins/channel/RenderChannelLightSelect.py.
_LIGHT_SELECT_CHANNEL_ATTRS = {
    'channels':          '0',   # direct diffuse and specular
    'channels_raw':      '1',
    'channels_diffuse':  '2',
    'channels_specular': '3',
    'channels_full':     '4',
}

# Default alias -> the render channel plugin that derives from RenderChannelColor.
# RenderChannelSheen/RenderChannelCoat are deliberately absent.
_LEGACY_COLOR_ALIAS_TO_PLUGIN = {
    154: 'RenderChannelToon',
    163: 'RenderChannelLightSelect',
    194: 'RenderChannelSheenReflection',
    195: 'RenderChannelCoatReflection',
}

# LightSelect modes that exist only to drive Light Mix.
_LIGHTMIX_ONLY_SELECT_MODES = (LightSelectMode.Environment, LightSelectMode.SelfIllumination)

# Render channels the exporter emits on its own.
_EXPORTER_MANAGED_CHANNELS = ('RenderChannelBackToBeauty',)

# Geometry plugins built as a point cloud by the instancer phase instead of as an object.
# 'Instancer' is the legacy plugin and the only one 3ds Max writes.
_INSTANCER_TYPES = ('GeomInstancer', 'Instancer2', 'Instancer')

# pluginType -> the attribute its Color socket is bound to. Both exclude 'color_tex'.
_LIGHT_COLOR_TEX_ALIAS = {
    'LightRectangle': 'rect_tex',
    'LightDome':      'dome_tex',
}

# Only lights whose illumination falls off with distance belong here.
_INVERSE_SQUARE_LIGHTS = ('LightOmni', 'LightSpot')

# The attr SettingsUnitsInfo.photometric_scale multiplies, per plugin, and whether only for a
# photometric 'units' (1-4). Measured with V-Ray standalone: doubling photometric_scale doubles
# these and nothing else - default-unit lights, LightDome and MayaLightDirect do not change.
_PHOTOMETRIC_ATTRS = {
    'LightRectangle': ('intensity', True),
    'LightOmni': ('intensity', True),
    'LightSphere': ('intensity', True),
    'LightSpot': ('intensity', True),
    'LightMesh': ('intensity', True),
    'LightIES': ('power', False),
    'SunLight': ('intensity_multiplier', False),
    'TexSky': ('intensity_multiplier', False),
}

# What V-Ray uses when a scene sets no photometric_scale (PhotoLight::frameBegin).
_DEFAULT_PHOTOMETRIC_SCALE = 0.001

# Below this, a water level is the "disabled" sentinel rather than a real level.
_WATER_LEVEL_DISABLED = -1e17

# At or above this, RenderView.clipping_far is V-Ray's "no far plane" default (1e18).
_VRAY_NO_FAR_CLIP = 1e17


@dataclass
class SceneImportOptions:
    filePath: str = ""
    importGeometry: bool = True
    importMaterials: bool = True
    importLights: bool = True
    importCamera: bool = True
    importEnvironment: bool = True
    importAnimation: bool = False
    importRenderSettings: bool = False
    importRenderChannels: bool = False
    importVfbLayers: bool = False
    importInstances: bool = True
    importHair: bool = True
    createCollection: bool = True
    # Store the imported image paths relative to the .blend. Ignored while it is unsaved.
    relativePaths: bool = False
    # Also apply the Settings* plugins that no panel draws (see
    # SceneImporter._UNEXPOSED_SETTINGS_TYPES). Deliberately absent from the operator's UI.
    importUnexposedSettings: bool = False


@dataclass
class SceneImportStats:
    objects: int = 0
    meshes: int = 0
    hairCurves: int = 0
    materials: int = 0
    lights: int = 0
    cameras: int = 0
    settings: int = 0
    extras: int = 0
    animatedTransforms: int = 0
    skipped: Counter = field(default_factory=Counter)
    errors: list = field(default_factory=list)   # (pluginName, phase, message)

    def summary(self) -> str:
        parts = [f"{self.objects} objects", f"{self.meshes} meshes", f"{self.materials} materials"]
        if self.hairCurves:
            parts.append(f"{self.hairCurves} hair curves")
        if self.lights:
            parts.append(f"{self.lights} lights")
        if self.cameras:
            parts.append(f"{self.cameras} cameras")
        if self.settings:
            parts.append(f"{self.settings} settings plugins")
        if self.extras:
            parts.append(f"{self.extras} extras")
        if self.animatedTransforms:
            parts.append(f"{self.animatedTransforms} animated transforms")
        text = "imported " + ", ".join(parts)

        if self.skipped:
            skippedText = ", ".join(f"{name} x{count}" for name, count in self.skipped.most_common(8))
            text += f"; skipped: {skippedText}"
        if self.errors:
            text += f"; {len(self.errors)} errors (see console log)"
        return text


class _RollbackLedger:
    """ Datablocks created by an import, removed in reverse creation order on cancel. """

    def __init__(self):
        self._entries = []

    def track(self, datablock):
        self._entries.append(datablock)
        return datablock

    def byType(self, bpyType):
        return (datablock for datablock in self._entries if isinstance(datablock, bpyType))

    def rollback(self):
        removers = (
            (bpy.types.Object, bpy.data.objects),
            (bpy.types.Mesh, bpy.data.meshes),
            (bpy.types.Light, bpy.data.lights),
            (bpy.types.Camera, bpy.data.cameras),
            (bpy.types.Material, bpy.data.materials),
            (bpy.types.World, bpy.data.worlds),
            (bpy.types.PointCloud, bpy.data.pointclouds),
            (bpy.types.Curves, bpy.data.hair_curves),
            (bpy.types.NodeTree, bpy.data.node_groups),
            (bpy.types.Collection, bpy.data.collections),
            (bpy.types.Image, bpy.data.images),
            (bpy.types.Action, bpy.data.actions),
        )
        for datablock in reversed(self._entries):
            with _suppressAndLog(f"rollback {type(datablock).__name__}"):
                if (coll := next((c for t, c in removers if isinstance(datablock, t)), None)) is not None:
                    coll.remove(datablock)
        self._entries.clear()


# Estimated wall-clock cost of each phase, in seconds per unit of work. Only the ratios matter,
# because runPhases normalises them. Calibrated on two ~350 MB scenes of opposite shape, one
# node-heavy and one mesh-heavy; the dominant phases land within ~20% on both. 'creating
# objects' is the loosest fit, 3x low on the mesh-heavy one where it is 5% of the total.
_PHASE_COST = {
    'preparing':           lambda c: 2.0e-6 * c['plugins'],
    'building geometry':   lambda c: 0.3e-6 * (c['verts'] + c['faces']),
    'creating objects':    lambda c: 200e-6 * c['nodes'],
    # Node creation scales with the shading plugin count; the second term is img.pack()
    # PNG-encoding embedded textures, which nothing else in the scene predicts.
    'importing materials': lambda c: 5.2e-3 * c['shadingPlugins'] + 1.0e-8 * c['rawPixels'],
    'assigning materials': lambda c: 50e-6 * c['nodes'],
    'importing lights':    lambda c: 5.0e-3 * c['lights'],
}

# Iterations between progress yields. A yield is the driver's only chance to see ESC
# (import_vrscene._advanceApply) and costs a generator resume, so one stride fits every phase.
_YIELD_EVERY = 8

# Floor for the phases that came in under 0.1 s on both reference scenes (cameras, hierarchy,
# environment, render settings, extras, instancers, VFB layers). They need a nonzero weight so
# the bar does not reach 100% while they are still running.
_PHASE_COST_MINOR = 0.05


class SceneImporter:
    """ Builds Blender data from a vrscene dict, one phase at a time.

        Drive through runPhases(), a generator yielding (phase label, progress fraction):
        once before each phase runs, again as a long phase reports its own progress, and
        finally ("done", 1.0). On cancellation, rollback() removes everything created so far.
    """

    def __init__(self, context: bpy.types.Context, vrsceneDict: list[dict], options: SceneImportOptions):
        self.context = context
        self.options = options
        self.stats = SceneImportStats()

        self.vrsceneDict = NodesImport.IndexedVrsceneDict(vrsceneDict)

        # Source scene directory for asset path resolution, the .vrscene the dict was built
        # from. Relative references and the same-tail search resolve against it.
        self.importBaseDir = os.path.dirname(options.filePath)

        # --- unit / coordinate conversion, set by _prepare ---
        self.metersScale = 1.0     # 1 source scene unit, in meters (SettingsUnitsInfo)
        self.framesPerSecond = 1.0 # SettingsOutput.frames_per_second: key time -> frame
        self.vertexScale = 1.0     # source scene unit -> Blender local coordinates
        self.coordAdjust = None    # source coord-system -> Blender Z-up RH (None = identity)

        # --- target collection ---
        self.collection: bpy.types.Collection = None
        # True only when we created self.collection ourselves (createCollection).
        self.ownImportCollection = False

        # --- registries: plugin name -> created datablock / object ---
        self.byType: dict[str, list[dict]] = {}         # pluginType -> [pluginDesc]
        self.meshesByPlugin: dict[str, bpy.types.Mesh] = {}
        self.hairByPlugin: dict[str, bpy.types.Curves] = {}   # GeomMayaHair plugin name -> hair Curves
        # GeomMeshFile plugin name -> shared proxy preview-mesh datablock.
        self.proxyDataByPlugin: dict[str, bpy.types.Mesh] = {}
        self.geomAliases: dict[str, str] = {}           # wrapper geometry name -> inner geometry name
        self.geomWrappers: dict[str, dict] = {}         # wrapper geometry name -> wrapper pluginDesc (displacement/subdiv)
        self.importedMaterials: dict[str, str] = {}     # material plugin name -> bpy material name
        self.meshMaterialKey: dict[str, str] = {}       # mesh plugin name -> material ref of the first user
        # (mesh name, material ref) -> the mesh copy carrying that material's slots.
        self.meshVariants: dict[tuple[str, str], bpy.types.Mesh] = {}

        # Registries resolving object-referencing attrs (TexDistance.objects, LightMesh.geometry).
        self.objectByNodePlugin: dict[str, bpy.types.Object] = {}   # Node plugin name -> object
        self.objectByGeomPlugin: dict[str, bpy.types.Object] = {}   # geometry plugin name -> object

        # scene_name path (element [1], "scene/" stripped) -> object (see _buildHierarchy).
        self.objectsByPath: dict[str, bpy.types.Object] = {}
        # Path -> (plugin name, world matrix) of a Node with a transform and no geometry.
        self.transformOnlyPaths: dict[str, tuple[str, mathutils.Matrix]] = {}

        # --- animation, only when importAnimation is on ---
        self.animTargets = anim_import.AnimTargets() if options.importAnimation else None
        # Object -> the plugin and parameter its world matrix came from.
        self.transformSources: dict[bpy.types.Object, anim_import.TransformSource] = {}
        # Object -> its world matrix before _buildHierarchy parented it.
        self.staticWorlds: dict[bpy.types.Object, mathutils.Matrix] = {}

        # LightSelect channel plugin name -> the light objects feeding it.
        self.lightsByChannelPlugin: dict[str, list] = {}

        # LightSelect channel plugin name -> the light_select_mode implied by the light attribute.
        self.selectModeByChannelPlugin: dict[str, str] = {}

        # Built on demand by _nodeOwnersByGeometry, once per import.
        self._nodeOwners: dict | None = None

        # --- pending work resolved in later phases ---
        # (obj, mesh, materialPluginName), applied after the materials are imported.
        self.pendingAssignments: list = []

        # Reflect/refract exclude lists, resolved after all objects exist.
        self.pendingObjectSelectors: list = []

        # --- rollback + instancer state ---
        self.ledger = _RollbackLedger()

        # Scene pointers the import overwrites, snapshotted by _prepare.
        self._sceneStateSaved = False
        self._prevWorld: bpy.types.World = None
        self._prevCamera: bpy.types.Object = None
        self._prevSettings: list = []   # (owner, attr, value), see _saveSceneSettings()

        # Instancer import state (see vray_tools/instancer_import.py).
        self.instancesNodeGroup: bpy.types.NodeTree = None        # shared "Instance on Points" group
        self.instancerPrototypesRoot: bpy.types.Collection = None  # hidden "prototypes" root
        self.instancerMovedSources: set[str] = set()              # source objects moved to prototypes
        # geometry plugin name -> the wrapping Node's descriptor (see _nodeOwnersByGeometry).
        self._nodeOwnerDescs: dict[str, dict] = {}

    def runPhases(self):
        # Objects must be created before materials, and material slots assigned after both.
        phases = [
            ("preparing", self._prepare),
        ]
        if self.options.importGeometry:
            phases.append(("building geometry", self._importGeometry))
            phases.append(("creating objects", self._importObjects))
        if self.options.importMaterials:
            phases.append(("importing materials", self._importMaterials))
        if self.options.importGeometry and self.options.importMaterials:
            phases.append(("assigning materials", self._assignMaterials))
        if self.options.importLights:
            phases.append(("importing lights", self._importLightsPhase))
        if self.options.importCamera:
            phases.append(("importing cameras", self._importCameras))
        if self.options.importEnvironment:
            phases.append(("importing environment", self._importEnvironment))
        if self.options.importRenderSettings:
            phases.append(("applying render settings", self._importSettings))
        phases.append(("importing extras", self._importExtras))
        if self.options.importGeometry and self.options.importInstances:
            phases.append(("importing instancers", self._importInstancers))
        # Keep after extras and instancers.
        if self.options.importLights:
            phases.append(("linking lights", self._importLightLinking))
        # Keep after every phase that creates objects.
        phases.append(("building hierarchy", self._buildHierarchy))
        if self.options.importAnimation:
            # Keep after the hierarchy. Transform keys are local to the parent.
            phases.append(("importing animation", self._importAnimation))
        if self.options.importVfbLayers:
            phases.append(("importing VFB layers", self._importVfbLayers))
        if self.options.relativePaths and bpy.data.filepath:
            # Last, because the UDIM and sequence branches of the bitmap import rewrite
            # image.filepath after the image is loaded.
            phases.append(("making paths relative", self._makePathsRelative))

        # A critical-phase failure cancels the import. Other phases log the error and continue.
        criticalPhases = ("preparing", "building geometry", "creating objects")

        # Give each phase a slice of the reported fraction proportional to its estimated cost,
        # rather than 1/len(phases). With equal slices the bar crossed 'importing materials',
        # more than half the work, in a single step, then covered its last third in 0.15 s.
        drivers = self._costDrivers()
        weights = [max(_PHASE_COST[label](drivers) if label in _PHASE_COST else 0.0,
                       _PHASE_COST_MINOR)
                   for label, _ in phases]
        totalWeight = sum(weights)
        starts, accumulated = [], 0.0
        for weight in weights:
            starts.append(accumulated / totalWeight)
            accumulated += weight

        for i, (label, phaseFn) in enumerate(phases):
            start, span = starts[i], weights[i] / totalWeight
            yield (label, start)
            try:
                result = phaseFn()
                if isinstance(result, GeneratorType):
                    for frac in result:
                        yield (label, start + span * min(max(frac, 0.0), 1.0))
            except Exception as e:
                if label in criticalPhases:
                    raise
                self._recordError('<phase>', label, e)
        yield ("done", 1.0)

    def _costDrivers(self) -> dict:
        """ The scene counts _PHASE_COST weights the phases by.

            Read off IndexedVrsceneDict.typeIndex, which the constructor already built, so they
            are available before the first phase runs. byType is not; _prepare() fills it.
            Costs ~5 ms on a 400 MB scene.
        """
        typeIndex = self.vrsceneDict.typeIndex
        verts = faces = rawPixels = 0

        for meshDesc in typeIndex.get('GeomStaticMesh', ()):
            attrs = meshDesc['Attributes']
            # Zero-copy ndarray for a server import (see vrscene_import.LARGE_ATTRS), a flat
            # list of components for a .vrmat or a plain-python source.
            if (vertices := attrs.get('vertices')) is not None:
                verts += vertices.shape[0] if hasattr(vertices, 'shape') else len(vertices) // 3
            if (meshFaces := attrs.get('faces')) is not None:
                faces += (meshFaces.shape[0] if hasattr(meshFaces, 'shape')
                          else len(meshFaces)) // 3

        for bufDesc in typeIndex.get('RawBitmapBuffer', ()):
            attrs = bufDesc['Attributes']
            rawPixels += int(attrs.get('width', 0) or 0) * int(attrs.get('height', 0) or 0)

        return {
            'plugins': len(self.vrsceneDict),
            'nodes':   len(typeIndex.get('Node', ())),
            'verts':   verts,
            'faces':   faces,
            'lights':  sum(len(v) for k, v in typeIndex.items() if k.startswith('Light')),
            'shadingPlugins': sum(len(v) for k, v in typeIndex.items()
                                  if k.startswith(('Mtl', 'BRDF', 'Tex'))),
            'rawPixels': rawPixels,
        }

    def resolveObjectPlugin(self, pluginName: str, linkType: str):
        """ Resolve a referenced Node/geometry plugin name to a created Blender object. """
        if linkType == LinkInfo.OBJECT_DATA:
            return self.objectByGeomPlugin.get(pluginName)
        # Some attrs declared OBJECTS carry a geometry-data ref (e.g. LightMesh.geometry).
        return self.objectByNodePlugin.get(pluginName) or self.objectByGeomPlugin.get(pluginName)

    def rollback(self):
        """ Remove everything created by this import after a cancellation. """
        try:
            self.ledger.rollback()
            if self._sceneStateSaved:
                # Removing the datablocks left these pointers cleared, not restored.
                self.context.scene.world = self._prevWorld
                self.context.scene.camera = self._prevCamera
                # Only the changed values, so that their update callbacks run as little as possible.
                # Twice, because a callback can rewrite a value restored before it (samples_limit
                # sets dmc_maxSubdivs).
                for _ in range(2):
                    for owner, attr, value in self._prevSettings:
                        if _toPlainValue(getattr(owner, attr)) != value:
                            with _suppressAndLog(f"rollback {attr}"):
                                setattr(owner, attr, value)
            if self.collection is not None and self.ownImportCollection:
                with _suppressAndLog("rollback collection"):
                    bpy.data.collections.remove(self.collection)
        except Exception as e:
            debug.printExceptionInfo(e, "scene_import.rollback")

    # --------------------------------------------------------------------------
    # Phases
    # --------------------------------------------------------------------------

    def _foldPhotometricScale(self, unitsInfo):
        """ Scale what photometric_scale multiplies by source / scene, so the imported lights render
            as in the source while the scene keeps its own photometric_scale, which the lights
            already in it also use. A physical camera's exposure divides by it too and is not
            compensated, nor is an IES light that takes its intensity from the file (power 0).
        """
        from vray_blender.plugins import findPluginModule

        sourceScale = (unitsInfo['Attributes'].get('photometric_scale') if unitsInfo else None)
        sourceScale = sourceScale or _DEFAULT_PHOTOMETRIC_SCALE
        ratio = float(sourceScale) / self.context.scene.vray.SettingsUnitsInfo.photometric_scale
        if abs(ratio - 1.0) < 1e-6:
            return

        for pluginDesc in self.vrsceneDict:
            if (entry := _PHOTOMETRIC_ATTRS.get(pluginDesc['ID'])) is None:
                continue
            attr, photometricUnitsOnly = entry
            attrs = pluginDesc['Attributes']
            if photometricUnitsOnly and int(attrs.get('units', 0)) == 0:
                continue
            value = attrs.get(attr)
            if value is None:
                value = attribute_utils.getAttrDesc(findPluginModule(pluginDesc['ID']), attr)['default']
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                continue    # a texture-driven value
            attrs[attr] = value * ratio
            if (keys := (pluginDesc.get('AnimatedKeys') or {}).get(attr)) is not None:
                pluginDesc['AnimatedKeys'][attr] = [(time, v * ratio) for time, v in keys]

    def _prepare(self):
        self._prevWorld = self.context.scene.world
        self._prevCamera = self.context.scene.camera
        self._prevSettings = _saveSceneSettings(self.context.scene)
        self._sceneStateSaved = True

        NodesImport.fixPluginParams(self.vrsceneDict, forceDefaultUVChannel=False)

        unitsInfo = NodesImport.getPluginByType(self.vrsceneDict, 'SettingsUnitsInfo')
        self.metersScale = import_common.metersScaleOf(unitsInfo)

        self.coordAdjust = self._buildCoordAdjust(unitsInfo)

        normalizeUnits(self.vrsceneDict, self.metersScale)

        self.vertexScale = self.metersScale / blender_utils.getSceneMetersPerUnit(self.context)

        self._foldPhotometricScale(unitsInfo)

        # Frames per V-Ray time unit, not the scene fps (that is SettingsUnitsInfo.frames_scale).
        settingsOutput = NodesImport.getPluginByType(self.vrsceneDict, 'SettingsOutput')
        self.framesPerSecond = 1.0
        if settingsOutput is not None:
            fps = settingsOutput['Attributes'].get('frames_per_second')
            if isinstance(fps, (int, float)) and fps > 0.0:
                self.framesPerSecond = float(fps)

        # Not typeIndex. fixPluginParams rewrites pluginDesc['ID'] in place for the Max
        # light aliases, which an index built at construction cannot follow.
        for pluginDesc in self.vrsceneDict:
            self.byType.setdefault(pluginDesc['ID'], []).append(pluginDesc)

        # Remap world-space direction params the coordinate flip affects.
        self._applyCoordAdjustToVectors()

        if self.options.createCollection:
            self.collection = bpy.data.collections.new(Path(self.options.filePath).stem)
            self.context.scene.collection.children.link(self.collection)
            self.ownImportCollection = True
        else:
            # Import into the active collection (where new objects normally go).
            self.collection = self.context.view_layer.active_layer_collection.collection

    def _applyCoordAdjustToVectors(self):
        """ When a coordinate flip is active, remap the world-space direction params it
            affects: TexFalloff.direction_type (world axis), GeomHair.gravity_vector and
            EnvironmentFog.yup. """
        if self.coordAdjust is None:
            return
        coordAdjust3x3 = self.coordAdjust.to_3x3()

        axisEnum = {7: mathutils.Vector((1, 0, 0)), 8: mathutils.Vector((0, 1, 0)),
                    9: mathutils.Vector((0, 0, 1))}   # TexFalloff world X/Y/Z direction_type

        def mappedWorldAxis(directionType: int):
            src = axisEnum.get(directionType)
            if src is None:
                return None
            mapped = coordAdjust3x3 @ src
            for enumVal, axis in axisEnum.items():
                if abs(abs(mapped.dot(axis)) - 1.0) < 1e-4:
                    return enumVal
            return None

        for falloff in self.byType.get('TexFalloff', []):
            directionType = falloff['Attributes'].get('direction_type')
            if directionType is None:
                continue
            if (newDt := mappedWorldAxis(int(directionType))) is not None and newDt != int(directionType):
                falloff['Attributes']['direction_type'] = str(newDt) if isinstance(directionType, str) else newDt

        for hair in self.byType.get('GeomHair', []):
            gravityVector = hair['Attributes'].get('gravity_vector')
            if isinstance(gravityVector, (list, tuple)) and len(gravityVector) >= 3:
                v = coordAdjust3x3 @ mathutils.Vector(gravityVector[:3])
                hair['Attributes']['gravity_vector'] = (v.x, v.y, v.z)

        # yup picks the axis the fog height is measured along.
        for fog in self.byType.get('EnvironmentFog', []):
            attrs = fog['Attributes']
            up = coordAdjust3x3 @ mathutils.Vector((0, 1, 0) if int(attrs.get('yup', 0)) else (0, 0, 1))
            if abs(abs(up.z) - 1.0) < 1e-4:
                attrs['yup'] = 0
            elif abs(abs(up.y) - 1.0) < 1e-4:
                attrs['yup'] = 1

    def _importGeometry(self):
        meshDescs = self.byType.get('GeomStaticMesh', [])
        # GeomMayaHair (Maya/XGen hair strands) -> Blender hair Curves, keyed by plugin name.
        hairDescs = self.byType.get('GeomMayaHair', []) if self.options.importHair else []

        # Both loops report against one running count, so the fraction stays monotonic.
        total = (len(meshDescs) + len(hairDescs)) or 1
        done = 0

        for meshDesc in meshDescs:
            try:
                if mesh := self._buildMeshFromGeomStaticMesh(meshDesc):
                    self.meshesByPlugin[meshDesc['Name']] = mesh
                    self.ledger.track(mesh)
                    self.stats.meshes += 1
            except Exception as e:
                self._recordError(meshDesc['Name'], 'geometry', e)
            done += 1
            if done % _YIELD_EVERY == 0:
                yield done / total

        for hairDesc in hairDescs:
            try:
                if curves := self._buildHairFromGeomMayaHair(hairDesc):
                    self.hairByPlugin[hairDesc['Name']] = curves
                    self.ledger.track(curves)
                    self.stats.hairCurves += 1
            except Exception as e:
                self._recordError(hairDesc['Name'], 'geometry', e)
            done += 1
            if done % _YIELD_EVERY == 0:
                yield done / total

        # Displacement/subdivision wrappers are aliased to the mesh they wrap.
        for wrapperType in ('GeomDisplacedMesh', 'GeomStaticSmoothedMesh'):
            for wrapperDesc in self.byType.get(wrapperType, []):
                if innerRef := wrapperDesc['Attributes'].get('mesh'):
                    self.geomAliases[wrapperDesc['Name']] = _refName(innerRef)
                self.geomWrappers[wrapperDesc['Name']] = wrapperDesc

    def _importMaterials(self):

        roots = set()
        # Materials referenced by objects, decals and clippers.
        for hostType in ('Node', 'VRayDecal', 'VRayClipper'):
            for hostDesc in self.byType.get(hostType, []):
                if mtlRef := hostDesc['Attributes'].get('material'):
                    roots.update(self._expandMaterialRoots(_refName(mtlRef)))

        # Also find and add unassigned materials.
        # Both tests are needed. The registry misses the types with no descriptor
        # (MtlUVWSelect, MtlWrapperMaya), the prefix misses MayaMtlMatte. Missing one here
        # leaves what it wraps looking unreferenced, so it imports a second time.
        materialTypes = PLUGINS['MATERIAL']
        allMtlPlugins = {p['Name']: p for p in self.vrsceneDict
                         if p['ID'].startswith('Mtl') or p['ID'] in materialTypes}
        referencedMtls = set()
        for p in allMtlPlugins.values():
            for attrValue in p.get('Attributes', {}).values():
                values = attrValue if isinstance(attrValue, list) else [attrValue]
                for value in values:
                    if isinstance(value, str):
                        refName = _refName(value)
                        if refName in allMtlPlugins:
                            referencedMtls.add(refName)

        topLevelMtls = {name for name in allMtlPlugins if name not in referencedMtls}
        for mtlName in topLevelMtls:
            roots.update(self._expandMaterialRoots(mtlName))

        if not roots:
            return

        # The per-root yield is the modal operator's only cancellation point.
        rootList = sorted(roots)
        total = len(rootList)
        for idx, root in enumerate(rootList):
            with _suppressAndLog(f"material '{root}'"):
                self.importedMaterials.update(
                    importMaterialsFromDict(self.vrsceneDict, materialRoots=[root],
                                            sceneBaseDir=self.importBaseDir,
                                            objectResolver=self.resolveObjectPlugin,
                                            stats=self.stats, ledger=self.ledger,
                                            animTargets=self.animTargets,
                                            sourceMetersScale=self.metersScale))
            yield idx / total

        self.stats.materials = len(self.importedMaterials)
        for mtlName in set(rootList).difference(self.importedMaterials):
            self.stats.skipped['material'] += 1
            debug.printWarning(f"Material '{mtlName}' was not imported")

    @staticmethod
    def _isSwitchMtlMulti(mtlDesc: dict) -> bool:
        """ A MtlMulti is a switch material (chosen at render by a generator) if it has an
            mtlid_gen / mtlid_gen_float plugin link; otherwise it's a per-face-ID
            multi-material applied as object material slots. """
        if mtlDesc is None or mtlDesc['ID'] != 'MtlMulti':
            return False
        attrs = mtlDesc['Attributes']
        for gen in ('mtlid_gen', 'mtlid_gen_float'):
            if _refName(attrs.get(gen)):
                return True
        return False

    def _peelUVWSelect(self, mtlDesc: dict):
        """ Follow MtlUVWSelect.base_mtl to the wrapped material. Returns the innermost
            non-MtlUVWSelect desc (or None). """
        seen = set()
        while (mtlDesc is not None and mtlDesc['ID'] == 'MtlUVWSelect'
               and mtlDesc['Name'] not in seen):
            seen.add(mtlDesc['Name'])
            baseRef = mtlDesc['Attributes'].get('base_mtl')
            mtlDesc = NodesImport.getPluginByName(self.vrsceneDict, _refName(baseRef)) if baseRef else None
        return mtlDesc

    def _expandMaterialRoots(self, mtlName: str, seen: set = None) -> set[str]:
        """ Return the set of importable material roots for a Node.material reference:
            a per-face MtlMulti is expanded into its sub-materials (each a bpy material,
            for slot assignment); a switch MtlMulti (has a generator) imports as one root
            switch node; everything else imports as one root.
        """
        # A cyclic MtlMulti-in-MtlMulti reference would recurse forever.
        if seen is None:
            seen = set()
        if mtlName in seen:
            return set()
        seen.add(mtlName)

        mtlDesc = NodesImport.getPluginByName(self.vrsceneDict, mtlName)
        if mtlDesc is None:
            return set()

        # A per-face MtlMulti is often wrapped in MtlUVWSelect (C4D/Corona).
        innerDesc = self._peelUVWSelect(mtlDesc)
        if innerDesc is not None and innerDesc['ID'] == 'MtlMulti' and not self._isSwitchMtlMulti(innerDesc):
            roots = set()
            for subRef in innerDesc['Attributes'].get('mtls_list', []):
                if isinstance(subRef, str):
                    roots.update(self._expandMaterialRoots(_refName(subRef), seen))
            return roots

        return {mtlName}

    def _importObjects(self):
        nodeDescs = self.byType.get('Node', [])
        total = len(nodeDescs) or 1
        for idx, nodeDesc in enumerate(nodeDescs):
            try:
                self._importNodeObject(nodeDesc)
            except Exception as e:
                self._recordError(nodeDesc['Name'], 'objects', e)
            if idx % _YIELD_EVERY == 0:
                yield idx / total

        # Keep before the environment phase, which resolves EnvironmentFog.gizmos.
        if self.options.importEnvironment:
            self._importFogGizmos()

        # Resolve deferred object-selector lists now that every Node object exists.
        self._resolveObjectSelectors()

    def _importFogGizmos(self):
        """ Create a visible mesh object for each EnvFogMeshGizmo, the volume that confines an
            EnvironmentFog. No Node references gizmo geometry, so nothing else creates it. """
        for desc in self.byType.get('EnvFogMeshGizmo', []):
            with _suppressAndLog(f"fog gizmo '{desc['Name']}'"):
                attrs = desc['Attributes']
                geomRef = _refName(attrs.get('geometry'))
                mesh = self.meshesByPlugin.get(self.geomAliases.get(geomRef, geomRef))
                if mesh is None:
                    self.stats.skipped['EnvFogMeshGizmo (no geometry)'] += 1
                    continue

                world = self.worldMatrix(attrs.get('transform'))
                # V-Ray reads a gizmo's inside from its world-space winding. Move a mirror into the mesh.
                if world.to_3x3().determinant() < 0:
                    reflect = mathutils.Matrix.Scale(-1.0, 4, (0.0, 0.0, 1.0))
                    if mesh.users:
                        mesh = mesh.copy()
                        self.ledger.track(mesh)
                    mesh.transform(reflect)
                    mesh.flip_normals()
                    world = world @ reflect

                obj = self._newObject(_sceneName(attrs, desc['Name']), mesh)
                self._setMeshWorldMatrix(obj, world)
                # EnvironmentFog.gizmos resolves the gizmo plugin name through this map.
                self.objectByNodePlugin[desc['Name']] = obj
                self.stats.extras += 1

    def _importLightLinking(self):
        """ Apply SettingsLightLinker to the lights' include/exclude lists.

            `ignored_lights` holds one [light, obj, obj, ...] list per entry and
            `include_exclude_light_flags` a flag per entry (0 exclude, 1 include). The shadow pair
            works the same way. Mirrors plugins/settings/SettingsLightLinker.py.
        """
        linkerDesc = NodesImport.getPluginByType(self.vrsceneDict, 'SettingsLightLinker')
        if linkerDesc is None:
            return
        attrs = linkerDesc['Attributes']

        # light object -> {mode, targets, illum, shadow}
        perLight: dict = {}

        def collect(listAttr: str, flagAttr: str, isShadow: bool):
            entries = attrs.get(listAttr) or []
            flags = attrs.get(flagAttr) or []
            for index, entry in enumerate(entries):
                if not isinstance(entry, (list, tuple)) or not entry:
                    continue
                lightObj = self.objectByNodePlugin.get(_refName(entry[0]))
                if lightObj is None or lightObj.type != 'LIGHT':
                    self.stats.skipped['light linking (light not imported)'] += 1
                    continue
                targets = [obj for ref in entry[1:]
                           if (obj := self.objectByNodePlugin.get(_refName(ref))) is not None]
                if unresolved := len(entry) - 1 - len(targets):
                    self.stats.skipped['light linking (object not imported)'] += unresolved
                # include_exclude is the V-Ray flag plus one.
                mode = str(int(flags[index]) + 1) if index < len(flags) else '1'
                record = perLight.setdefault(lightObj, {'mode': mode, 'targets': [],
                                                        'illum': False, 'shadow': False})
                record['shadow' if isShadow else 'illum'] = True
                for obj in targets:
                    if obj not in record['targets']:
                        record['targets'].append(obj)

        collect('ignored_lights', 'include_exclude_light_flags', False)
        collect('ignored_shadow_lights', 'include_exclude_shadow_flags', True)

        for lightObj, record in perLight.items():
            # An empty exclude list is a no-op. An empty include list still applies.
            if not record['targets'] and record['mode'] == '1':
                continue
            with _suppressAndLog(f"light linking for '{lightObj.name}'"):
                vrayLight = lightObj.data.vray
                vrayLight.include_exclude = record['mode']
                vrayLight.illumination_shadow = ('2' if record['illum'] and record['shadow']
                                                 else ('1' if record['shadow'] else '0'))
                for obj in record['targets']:
                    vrayLight.objectList.addListItem(self.context, obj)
                self.stats.extras += 1

    def _resolveObjectSelectors(self):
        """ Fill the queued reflect/refract exclude selectors, resolving each Node ref to its
            object. Enables use_visibility. """
        for op, selectorAttr, refNames, isInclusive in self.pendingObjectSelectors:
            selector = getattr(op, selectorAttr)
            added = False
            for refName in refNames:
                if obj := self.objectByNodePlugin.get(refName):
                    selector.addListItem(self.context, obj)
                    added = True
            if added:
                selector.inclusionMode = '1' if isInclusive else '0'
                if not op.use_visibility:
                    op.use_visibility = True

    def _importNodeObject(self, nodeDesc: dict):
        attrs = nodeDesc['Attributes']

        geomRef = attrs.get('geometry')
        if not geomRef:
            # A group transform (Maya writes one per DAG ancestor). _getOrCreateHierParent places its Empty.
            if pathKey := _scenePathKey(attrs):
                self.transformOnlyPaths.setdefault(
                    pathKey, (nodeDesc['Name'], self.worldMatrix(attrs.get('transform'))))
            else:
                self.stats.skipped['Node without geometry'] += 1
            return

        geomRefName = _refName(geomRef)
        geomName = self.geomAliases.get(geomRefName, geomRefName)
        objName = _sceneName(attrs, nodeDesc['Name'])

        obj = None
        if mesh := self.meshesByPlugin.get(geomName):
            obj = self._newObject(objName, mesh)
        elif curves := self.hairByPlugin.get(geomName):
            obj = self._newObject(objName, curves)   # object type -> 'CURVES'
        else:
            geomDesc = NodesImport.getPluginByName(self.vrsceneDict, geomName)
            if geomDesc is None:
                self.stats.skipped['missing geometry'] += 1
                return
            if self.options.importInstances and geomDesc['ID'] in _INSTANCER_TYPES:
                return   # Built as a point cloud by the instancer phase.
            if geomDesc['ID'] == 'GeomMeshFile':
                obj = self._createProxyObject(geomDesc, objName)
            elif geomDesc['ID'] == 'VRayScene':
                obj = self._createVRaySceneObject(geomDesc, objName)
            elif geomDesc['ID'] in ('GeomPlane', 'GeomPerfectSphere'):
                obj = self._createEmptyGeometryObject(geomDesc, objName)
            if obj is None:
                self.stats.skipped[geomDesc['ID']] += 1
                return

        pivot = attrs.get('pivot_offset')
        pivotShift = self._setMeshWorldMatrix(
            obj, self.worldMatrix(attrs.get('transform')),
            mathutils.Vector(tuple(pivot)[:3]) * self.vertexScale if pivot is not None else None)
        self._registerTransformSource(obj, nodeDesc, post=pivotShift)

        if not attrs.get('visible', True):
            obj.hide_render = True
            obj.hide_viewport = True

        # visible maps onto hide_render/hide_viewport inverted, outside _applyAttr, so record it here.
        if self.animTargets is not None and 'visible' in (nodeDesc.get('AnimatedKeys') or {}):
            for prop in ('hide_render', 'hide_viewport'):
                self.animTargets.record(obj, prop, nodeDesc['Name'], 'visible',
                                        written=getattr(obj, prop), interp='CONSTANT',
                                        convert=lambda value: not value)

        self._importObjectProperties(obj, attrs)
        self._importUserAttributes(obj, attrs)

        # Attach displacement/subdivision if the Node's geometry was a wrapper plugin.
        if wrapperDesc := self.geomWrappers.get(geomRefName):
            with _suppressAndLog(f"displacement/subdivision for {objName}"):
                self._attachGeometryWrapper(obj, wrapperDesc)

        self.stats.objects += 1

        # Register for object-reference resolution by materials/lights imported later.
        self.objectByNodePlugin[nodeDesc['Name']] = obj
        self.objectByGeomPlugin[geomName] = obj
        if pathKey := _scenePathKey(attrs):
            self.objectsByPath.setdefault(pathKey, obj)

        # Defer material assignment until after materials are imported.
        if mtlRef := attrs.get('material'):
            # obj.data is None for the Empty-backed geometry plugins; _assignNodeMaterial routes
            # those to obj.vray.material instead of to a slot.
            self.pendingAssignments.append((obj, obj.data, _refName(mtlRef)))

    def _importObjectProperties(self, obj: bpy.types.Object, attrs: dict):
        """ Import object-level render properties onto obj.vray.VRayObjectProperties. The
            object ID needs nothing else, while the render flags (matte, per-ray visibility,
            GI/surface) are also surfaced through Matte/Surface/Visibility property nodes. """
        op = obj.vray.VRayObjectProperties

        # Object ID is written straight onto the Node plugin on export, no node needed.
        # The object was just created, so it already holds the default 0; skip that write.
        # The RNA setter costs ~1 ms per object once the scene is large, which otherwise
        # dominates the import of a scene with tens of thousands of Nodes.
        # A foreign exporter can write a non-numeric or out-of-range id. Losing it must not
        # cost the rest of the Node, which _importObjects drops on an exception.
        if objectID := attrs.get('objectID'):
            with _suppressAndLog(f"object id of '{obj.name}'"):
                op.objectID = int(objectID)

        # Motion-blur sample override lives on the Node plugin (nsamples).
        if nsamples := attrs.get('nsamples'):
            op.override_motion_blur_samples = True
            op.motion_blur_samples = int(nsamples)

        opRef = attrs.get('object_properties')
        if not opRef:
            return
        opDesc = NodesImport.getPluginByName(self.vrsceneDict, _refName(opRef))
        if opDesc is None:
            return
        opAttrs = opDesc['Attributes']

        needMatte = any(a in opAttrs for a in VRayObjectMatteProps.visibleAttrs)
        needSurface = any(a in opAttrs for a in VRayObjectSurfaceProps.visibleAttrs)
        needVisibility = any(a in opAttrs for a in VRayObjectVisibilityProps.visibleAttrs)
        if not (needMatte or needSurface or needVisibility):
            return

        # Fill scalar fields; skip the node-presence toggles and the exclude lists (below).
        skip = {'matte_surface', 'use_surface', 'use_visibility',
                'reflection_exclude', 'refraction_exclude',
                'reflection_list_is_inclusive', 'refraction_list_is_inclusive', 'channels'}
        NodesImport.pluginAttrsToPropGroup(_descWithout('VRayObjectProperties', opAttrs, skip), op,
                                            animTargets=self.animTargets, source=opDesc)

        # Create the property nodes. matte_surface is a real render flag, not a node toggle.
        hadTree = bool(obj.vray.ntree)
        if opAttrs.get('matte_surface'):
            op.matte_surface = True
        if needSurface:
            op.use_surface = True
        if needVisibility:
            op.use_visibility = True
        # The toggle callbacks create the object's V-Ray tree on demand; track it for rollback.
        if not hadTree and obj.vray.ntree:
            self.ledger.track(obj.vray.ntree)

        # Queue the reflect/refract exclude lists for deferred resolution (they ref other Nodes).
        for listType in ('reflection', 'refraction'):
            refs = opAttrs.get(f'{listType}_exclude')
            if not refs:
                continue
            refNames = [_refName(r) for r in refs if isinstance(r, str)]
            if refNames:
                isInclusive = bool(opAttrs.get(f'{listType}_list_is_inclusive'))
                self.pendingObjectSelectors.append(
                    (op, f'{listType}_object_selector', refNames, isInclusive))

    def _importUserAttributes(self, obj: bpy.types.Object, attrs: dict):
        """ Fill obj.vray.UserAttributes from the imported Node user attributes. The server
            decodes the binary blob (user_attributes_bin) into a structured
            'user_attributes_decoded' list of [name, valueType, value]; the plain string form
            (user_attributes, "name=value;...") is parsed as a fallback. """
        userAttrs = obj.vray.UserAttributes

        added = 0
        for name, valueType, value in _decodedUserAttributes(attrs.get('user_attributes_decoded')):
            _addUserAttribute(userAttrs, name, valueType, value)
            added += 1

        if added == 0:
            for name, valueType, value in import_common.parseUserAttributeString(attrs.get('user_attributes')):
                _addUserAttribute(userAttrs, name, valueType, value)

    def _attachGeometryWrapper(self, obj: bpy.types.Object, wrapperDesc: dict):
        """ Attach a GeomDisplacedMesh/GeomStaticSmoothedMesh wrapper to the object as a
            Displacement / Subdivision node in its OBJECT node tree, reversing the export
            in exporting/node_exporters/geometry_node_export.py. """

        if obj.type != 'MESH':
            return

        if not obj.vray.ntree:
            tree_defaults.addObjectNodeTree(obj)
            # Object V-Ray trees are standalone node_groups datablocks; track them for rollback.
            self.ledger.track(obj.vray.ntree)
        objTree = obj.vray.ntree
        objOutput = NodesUtils.getNodeByType(objTree, 'VRayNodeObjectOutput')
        if objOutput is None:
            return

        importContext = self._importContext(objTree)
        attrs = wrapperDesc['Attributes']

        if wrapperDesc['ID'] == 'GeomStaticSmoothedMesh':
            self._attachSubdivision(objTree, objOutput, attrs, importContext, wrapperDesc)
            # A smoothed mesh may also carry a displacement texture.
            texRef = attrs.get('displacement_tex_color') or attrs.get('displacement_tex_float')
            if NodesImport.isPluginLink(texRef):
                self._attachDisplacement(objTree, objOutput, attrs, importContext, wrapperDesc)
        else:
            self._attachDisplacement(objTree, objOutput, attrs, importContext, wrapperDesc)

        _rearrangeAndCheck(objTree, objOutput)

    def _attachDisplacement(self, objTree, objOutput, attrs: dict, importContext, sourceDesc=None):
        node = objTree.nodes.new('VRayNodeDisplacement')
        dispPropGroup = node.GeomDisplacedMesh

        # Fill the scalar attrs; the displacement type, bounds and texture are derived below.
        skip = {'mesh', 'vector_displacement', 'displace_2d', 'min_bound', 'max_bound',
                'displacement_tex_color', 'displacement_tex_float', 'scene_name',
                'displace_2d_transform'}
        # The propgroup-fill path must opt into the cm -> Blender distance rescale.
        NodesImport.pluginAttrsToPropGroup(_descWithout('GeomDisplacedMesh', attrs, skip), dispPropGroup,
                                            'GeomDisplacedMesh', scaleDistance=True,
                                            restoreVRayDefaults=True, stats=self.stats,
                                            animTargets=self.animTargets, source=sourceDesc, node=node)

        # type: 2D wins; otherwise map vector_displacement (0->Normal,1->Vector,2->Abs,3->Object).
        if attrs.get('displace_2d'):
            dispPropGroup.type = '1'
        else:
            vectorDisplacement = int(attrs.get('vector_displacement', 0) or 0)
            dispPropGroup.type = {0: '0', 1: '2', 2: '3', 3: '4'}.get(vectorDisplacement, '0')

        # The exporter writes min_bound/max_bound (a Color triple) when bounds are used.
        if attrs.get('use_bounds') or 'min_bound' in attrs or 'max_bound' in attrs:
            dispPropGroup.use_bounds = True
            if (minBound := attrs.get('min_bound')) is not None:
                dispPropGroup.min_bound_float = minBound[0] if isinstance(minBound, (list, tuple)) else minBound
            if (maxBound := attrs.get('max_bound')) is not None:
                dispPropGroup.max_bound_float = maxBound[0] if isinstance(maxBound, (list, tuple)) else maxBound

        waterLevel = attrs.get('water_level')
        if (waterLevel is not None) and (waterLevel > _WATER_LEVEL_DISABLED):
            dispPropGroup.use_water_level = True
        else:
            dispPropGroup.property_unset('water_level')

        if 'Displacement' in objOutput.inputs:
            objTree.links.new(node.outputs['Displacement'], objOutput.inputs['Displacement'])

        texRef = attrs.get('displacement_tex_color') or attrs.get('displacement_tex_float')
        texSock = node.inputs.get('Displacement Texture')
        if texSock is not None and NodesImport.isPluginLink(texRef):
            connectedPlugin, outputName = NodesImport.getPluginFromLink(importContext, texRef)
            if connectedPlugin is not None:
                NodesImport.createLinkedNode(importContext, texSock, outputName, connectedPlugin)

    def _attachSubdivision(self, objTree, objOutput, attrs: dict, importContext, sourceDesc=None):
        node = objTree.nodes.new('VRayNodeGeomStaticSmoothedMesh')
        # The displacement attrs belong to the displacement node (_attachDisplacement).
        skip = {'mesh', 'displacement_tex_color', 'displacement_tex_float', 'scene_name',
                'min_bound', 'max_bound', 'vector_displacement', 'displace_2d',
                'displacement_amount', 'displacement_shift', 'water_level'}
        NodesImport.pluginAttrsToPropGroup(_descWithout('GeomStaticSmoothedMesh', attrs, skip),
                                            node.GeomStaticSmoothedMesh, 'GeomStaticSmoothedMesh',
                                            restoreVRayDefaults=True, stats=self.stats,
                                            animTargets=self.animTargets, source=sourceDesc, node=node)
        if 'Subdivision' in objOutput.inputs:
            objTree.links.new(node.outputs[0], objOutput.inputs['Subdivision'])

    def _buildHierarchy(self):
        """ Rebuild the parent/child hierarchy from the objects' scene_name paths, after
            all objects and lights exist. A path segment that is itself an imported object
            (a group Node) becomes the parent directly; a segment with no backing plugin
            becomes a PLAIN_AXES Empty. Each object's absolute world matrix is preserved. """
        if not self.objectsByPath:
            return

        # path -> object
        hier = dict(self.objectsByPath)

        # Parents must be finalized before their children re-parent against them.
        ordered = sorted(self.objectsByPath.items(), key=lambda pathAndObj: pathAndObj[0].count('/'))
        for fullPath, obj in ordered:
            parts = fullPath.split('/')
            if len(parts) < 2:
                continue   # Top-level object; stays directly under the import collection.
            parent = self._getOrCreateHierParent(parts[:-1], hier)
            if parent is None or parent is obj:
                continue
            self._parentPreservingWorld(obj, parent)

    def _registerTransformSource(self, obj: bpy.types.Object, pluginDesc: dict,
                                 paramName: str = 'transform', post: mathutils.Matrix = None):
        """ Record the plugin parameter that placed obj, when that parameter has keys. `post` is
            right-multiplied onto every sampled world matrix. """
        if self.animTargets is None:
            return
        if paramName in (pluginDesc.get('AnimatedKeys') or {}):
            self.transformSources[obj] = anim_import.TransformSource(pluginDesc['Name'], paramName, post)

    def _parentPreservingWorld(self, obj: bpy.types.Object, parent: bpy.types.Object):
        """ Parent obj under parent without moving it. A sheared local goes into
            matrix_parent_inverse, which Blender never decomposes.

            Do not use the matrix_world setter here. The depsgraph has not evaluated the new parents,
            so it reads a stale parent world, and it drops shear.
        """
        world = self._hierWorldMatrix(obj)
        parentWorld = self._hierWorldMatrix(parent)
        local = parentWorld.inverted_safe() @ world

        self.staticWorlds[obj] = world.copy()

        obj.parent = parent
        if _hasShear(local.to_3x3()):
            obj.matrix_parent_inverse = parentWorld.inverted_safe()
            obj.matrix_basis = world
        else:
            obj.matrix_parent_inverse.identity()
            obj.matrix_basis = local

    @staticmethod
    def _hierWorldMatrix(obj: bpy.types.Object) -> mathutils.Matrix:
        """ obj's world matrix composed up its parent chain. obj.matrix_world is stale until the
            depsgraph evaluates. """
        chain = []
        node = obj
        while node is not None:
            chain.append(node)
            node = node.parent

        world = chain[-1].matrix_basis.copy()          # the root ignores its parent inverse
        for node in reversed(chain[:-1]):
            world = world @ node.matrix_parent_inverse @ node.matrix_basis
        return world

    def _getOrCreateHierParent(self, chain: list[str], hier: dict) -> bpy.types.Object | None:
        """ Ensure the ancestor chain of Empties/group objects exists and return the
            innermost one. Intermediate levels with no imported object become Empties. """
        parent = None
        for depth in range(1, len(chain) + 1):
            key = '/'.join(chain[:depth])
            node = hier.get(key)
            if node is None:
                node = self._newObject(chain[depth - 1])
                node.empty_display_type = 'PLAIN_AXES'
                if (entry := self.transformOnlyPaths.get(key)) is not None:
                    pluginName, tm = entry
                    node.matrix_basis = tm
                    self.transformSources[node] = anim_import.TransformSource(pluginName)
                else:
                    tm = None
                if parent is not None:
                    if tm is not None:
                        self._parentPreservingWorld(node, parent)
                    else:
                        node.parent = parent
                hier[key] = node
            parent = node
        return parent

    def _resolveAssetPath(self, path: str) -> str:
        """ Resolve a file path referenced by the .vrscene against the scene directory.
            Returns the original path if nothing is found. """
        return import_common.resolveAssetPath(path, self.importBaseDir)

    def _createProxyObject(self, geomDesc: dict, objName: str) -> bpy.types.Object | None:
        """ Create a VRayProxy object for a GeomMeshFile referenced by a Node. Several Nodes
            can reference the same proxy geometry; they share one preview-mesh datablock but
            each is a full VRayProxy object. """

        # Reuse a preview datablock already loaded for this proxy geometry.
        if previewMesh := self.proxyDataByPlugin.get(geomDesc['Name']):
            obj = self._newObject(objName, previewMesh)
            obj.vray.VRayAsset.assetType = blender_utils.VRAY_ASSET_TYPE["Proxy"]
            return obj

        filePath = self._resolveAssetPath(geomDesc['Attributes'].get('file', ''))
        # The source 'scale' is in scene units like the .vrmesh vertices, so fold it into the
        # unit scale. The fill below skips 'scale' to keep the product.
        sourceScale = float(geomDesc['Attributes'].get('scale', 1.0))
        proxyObj, err = _importVRayProxy(self.context, filePath,
                                         scaleUnit=self.metersScale * sourceScale)
        if err:
            # The .vrmesh preview could not be loaded; import the proxy with an empty preview.
            debug.printWarning(f"Proxy '{geomDesc['Name']}': {err}; importing without preview")
            previewMesh = bpy.data.meshes.new(geomDesc['Name'])
            proxyObj = bpy.data.objects.new(objName, previewMesh)
            proxyObj.vray.VRayAsset.assetType = blender_utils.VRAY_ASSET_TYPE["Proxy"]

        # The mesh is tracked before the object, so rollback removes the object first.
        self.ledger.track(proxyObj.data)
        self._adoptObject(proxyObj)

        geomMeshFile = proxyObj.data.vray.GeomMeshFile
        # Writing 'scale' would lose the unit conversion and, via its update callback, rescale
        # the baked preview. Writing the raw 'file' fires a callback that cannot resolve it.
        filtered = {'ID': geomDesc['ID'],
                    'Attributes': {k: v for k, v in geomDesc['Attributes'].items() if k not in ('scale', 'file')}}
        NodesImport.pluginAttrsToPropGroup(filtered, geomMeshFile)
        geomMeshFile['file'] = filePath

        # Assigning through the ID bypasses the 'file' update callback, so the object hierarchy
        # would stay unread. The rollout would read it lazily the first time it is drawn, but the
        # file is known to be good here and the import already budgets for the process.
        from vray_blender.plugins.geometry.GeomMeshFile import rebuildObjectHierarchy
        rebuildObjectHierarchy(geomMeshFile)
        self.proxyDataByPlugin[geomDesc['Name']] = proxyObj.data
        return proxyObj

    def _createVRaySceneObject(self, geomDesc: dict, objName: str) -> bpy.types.Object | None:
        """ Create a V-Ray Scene reference object for a Node whose geometry is a nested
            VRayScene plugin. The referenced .vrscene is rendered from the file at render
            time; its preview mesh is loaded for the viewport. """

        filePath = self._resolveAssetPath(geomDesc['Attributes'].get('filepath', ''))
        mesh = bpy.data.meshes.new(objName)
        self.ledger.track(mesh)
        obj = self._newObject(objName, mesh)
        obj.vray.VRayAsset.assetType = blender_utils.VRAY_ASSET_TYPE["Scene"]

        vrayScene = obj.data.vray.VRayScene
        if err := loadVRayScenePreviewMesh(vrayScene, filePath):
            debug.printWarning(f"VRayScene '{geomDesc['Name']}': {err}")
        vrayScene['filepath'] = filePath
        return obj

    def _assignMaterials(self):
        total = len(self.pendingAssignments) or 1
        for idx, (obj, mesh, mtlName) in enumerate(self.pendingAssignments):
            try:
                if self.importedMaterials:
                    self._assignNodeMaterial(obj, mesh, mtlName)
            except Exception as e:
                self._recordError(mtlName, 'material assignment', e)
            if idx % _YIELD_EVERY == 0:
                yield idx / total

    def _assignNodeMaterial(self, obj: bpy.types.Object, mesh: bpy.types.Mesh | bpy.types.Curves, mtlName: str):

        # mesh is None for the Empty-backed geometry plugins (infinite plane, perfect sphere):
        # Blender gives an Empty no material slots, so the material goes to a pointer on the
        # object instead. None of the slot bookkeeping below applies to them.
        if mesh is not None:
            # A mesh shared by Nodes with different materials cannot use data-linked slots.
            # One copy per (mesh, material), shared by all the Nodes carrying that material.
            firstUserMtl = self.meshMaterialKey.setdefault(mesh.name, mtlName)
            if firstUserMtl != mtlName and len(mesh.materials):
                variantKey = (mesh.name, mtlName)
                if (variant := self.meshVariants.get(variantKey)) is None:
                    variant = mesh.copy()
                    self.ledger.track(variant)
                    variant.materials.clear()
                    self.meshVariants[variantKey] = variant
                    self.stats.skipped['shared mesh split by material'] += 1
                mesh = variant
                obj.data = mesh

            if len(mesh.materials):
                return   # Slots already assigned by a previous Node sharing this mesh

        mtlDesc = NodesImport.getPluginByName(self.vrsceneDict, mtlName)
        if mtlDesc is None:
            self.stats.skipped['missing material'] += 1
            return

        # Peel MtlUVWSelect (C4D/Corona) to reach a per-face MtlMulti.
        innerDesc = self._peelUVWSelect(mtlDesc)
        if innerDesc is not None and innerDesc['ID'] == 'MtlMulti' and not self._isSwitchMtlMulti(innerDesc) \
                and mesh is not None:
            # Per-face multi-material -> one object slot per material ID. Analytic geometry has
            # no faces to key on, so it falls through to the single-material path below.
            if 'mtls_list' in innerDesc['Attributes']:
                _assignMaterialsToSlots(innerDesc, obj, self.importedMaterials)
            return
        # A switch MtlMulti was imported as a single root material node; fall through.

        # Walk wrapper chains to the first material that was actually imported
        seen = set()
        while mtlName not in self.importedMaterials and mtlDesc is not None:
            if (baseAttr := import_common.WRAPPER_BASE_ATTRS.get(mtlDesc['ID'])) is None or mtlName in seen:
                break
            seen.add(mtlName)
            if not (baseRef := mtlDesc['Attributes'].get(baseAttr)):
                break
            mtlName = _refName(baseRef)
            mtlDesc = NodesImport.getPluginByName(self.vrsceneDict, mtlName)

        if bpyMtlName := self.importedMaterials.get(mtlName):
            mtl = bpy.data.materials[bpyMtlName]
            if mesh is None:
                obj.vray.material = mtl
            else:
                mesh.materials.append(mtl)
        else:
            self.stats.skipped['material'] += 1

    def _buildMeshFromGeomStaticMesh(self, meshDesc: dict) -> bpy.types.Mesh | None:
        """ Create a Blender mesh from GeomStaticMesh plugin data.

            The large attributes come in as numpy arrays (see vrscene_import.LARGE_ATTRS):
            vertices (N,3) f32, faces flat (M*3,) i32, normals (K,3) f32 with faceNormals
            flat per-loop indices, face_mtlIDs (M,) i32, and map_channels as a list of
            [channelIndex, uvVertices (U,3) f32, uvFaces flat i32] entries.
        """
        attrs = meshDesc['Attributes']

        vertices = attrs.get('vertices')
        faces = attrs.get('faces')

        if vertices is None or faces is None or len(vertices) == 0 or len(faces) == 0:
            self.stats.skipped['empty GeomStaticMesh'] += 1
            return None

        vertexData = np.asarray(vertices, dtype=np.float32)
        if self.vertexScale != 1.0:
            vertexData = vertexData * self.vertexScale

        mesh = buildTriMeshBase(meshDesc['Name'], vertexData, faces)
        numLoops = len(mesh.loops)
        numFaces = numLoops // 3

        # Material id k lands in slot k (same gap-filling as the MtlMulti ids_list).
        # Ids beyond the slot count are kept as they are, not clamped.
        # SketchUp writes front+back ids (2x faces); use the first numFaces.
        faceMtlIds = attrs.get('face_mtlIDs')
        if faceMtlIds is not None and len(faceMtlIds) in (numFaces, numFaces * 2):
            matArray = np.maximum(np.asarray(faceMtlIds[:numFaces], dtype=np.int32), 0)
            attr = mesh.attributes.new('material_index', 'INT', 'FACE')
            attr.data.foreach_set('value', matArray)

        mesh.update()

        # UV/color channels (must be created after the first mesh.update())
        mapChannels = attrs.get('map_channels') or []
        for channelCount, channel in enumerate(mapChannels, start=1):
            if channelCount > _MAX_UV_CHANNELS:
                debug.printWarning(
                    f"'{meshDesc['Name']}': UV channel count exceeds the supported maximum "
                    f"({_MAX_UV_CHANNELS}); only the first {_MAX_UV_CHANNELS} channels will be imported.")
                break

            channelIdx, uvVerts, uvFaces = int(channel[0]), np.asarray(channel[1]), np.asarray(channel[2]).ravel()

            if len(uvFaces) < numLoops or len(uvVerts) == 0:
                continue

            uvLayer = mesh.uv_layers.new(name=f"vray_channel_id_{channelIdx}")
            if uvLayer is None:
                break   # Blender's UV layer limit reached

            loopUVs = np.ascontiguousarray(uvVerts[uvFaces[:numLoops]][:, :2], dtype=np.float32)
            uvLayer.uv.foreach_set('vector', loopUVs.ravel())

        # Custom split normals
        normals = attrs.get('normals')
        faceNormals = attrs.get('faceNormals')
        if normals is not None and faceNormals is not None and len(faceNormals) == numLoops and len(normals):
            loopNormals = np.ascontiguousarray(
                np.asarray(normals, dtype=np.float32)[np.asarray(faceNormals, dtype=np.int32).ravel()])
            attr = mesh.attributes.new('custom_normal', 'FLOAT_VECTOR', 'CORNER')
            attr.data.foreach_set('vector', loopNormals.ravel())

        mesh.update()
        return mesh

    def _buildHairFromGeomMayaHair(self, hairDesc: dict) -> bpy.types.Curves | None:
        """ Build a Blender hair Curves from a GeomMayaHair. num_hair_vertices (per-strand
            counts), hair_vertices (flat positions) and widths arrive as numpy ndarrays
            (see LARGE_ATTRS). """

        attrs = hairDesc['Attributes']
        sizes = attrs.get('num_hair_vertices')
        verts = attrs.get('hair_vertices')
        if sizes is None or verts is None or len(sizes) == 0 or len(verts) == 0:
            self.stats.skipped['empty GeomMayaHair'] += 1
            return None

        sizes = np.asarray(sizes, dtype=np.int32)
        positions = np.asarray(verts, dtype=np.float32).reshape(-1, 3)

        # add_curves requires each strand to have >= 1 point; drop degenerate strands.
        # Per-CURVE arrays are masked with the same 'valid' mask (see _hairColors/_hairSurfaceUV).
        valid = sizes >= 1
        if not valid.all():
            sizes = sizes[valid]
        if int(sizes.sum()) != len(positions):
            self._recordError(hairDesc['Name'], 'geometry', 'hair vertex count mismatch')
            return None

        if self.vertexScale != 1.0:
            positions = positions * self.vertexScale

        # radius = width / 2; world-unit widths scale with the scene, pixel widths do not.
        widths = attrs.get('widths')
        widthsInPixels = bool(attrs.get('widths_in_pixels', False))
        if widths is not None and len(widths) == len(positions):
            radii = np.asarray(widths, dtype=np.float32) * 0.5
            if not widthsInPixels and self.vertexScale != 1.0:
                radii = radii * self.vertexScale
        else:
            radii = np.full(len(positions), _DEFAULT_HAIR_RADIUS, dtype=np.float32)

        colors, colorDomain = self._hairColors(attrs, len(positions), len(sizes), valid)
        surfaceUV = self._hairSurfaceUV(attrs, len(sizes), valid)

        curves = buildHairCurvesBase(hairDesc['Name'], sizes, positions, radii,
                                     colors=colors, colorDomain=colorDomain,
                                     surfaceUV=surfaceUV, curveType='POLY')
        # V-Ray-only knobs, ignored by the exporter today.
        curves['vray_geom_splines'] = int(attrs.get('geom_splines', 1))
        curves['vray_widths_in_pixels'] = int(widthsInPixels)
        return curves

    def _hairColors(self, attrs: dict, nVerts: int, nStrands: int, valid):
        """ GeomMayaHair colors: per-vertex (POINT) unless xgen_generated (per-strand, CURVE).
            Returns (rgba (N,4) float32, domain) or (None, 'POINT') when absent/inconsistent. """
        raw = attrs.get('colors')
        if raw is None or len(raw) == 0:
            return None, 'POINT'
        array = np.asarray(raw, dtype=np.float32).reshape(-1, 3)
        perStrand = bool(attrs.get('xgen_generated', 0)) or (len(array) == len(valid) and nVerts != len(valid))
        if perStrand:
            if len(array) == len(valid):    # align with the strands kept after the size>=1 mask
                array = array[valid]
            domain, expected = 'CURVE', nStrands
        else:
            domain, expected = 'POINT', nVerts
        if len(array) != expected:
            return None, 'POINT'          # inconsistent -> skip colors rather than misalign
        rgba = np.ones((len(array), 4), dtype=np.float32)
        rgba[:, :3] = array
        return rgba, domain

    def _hairSurfaceUV(self, attrs: dict, nStrands: int, valid):
        """ GeomMayaHair strand_uvw is per-strand; take xy as the Blender surface_uv_coordinate. """
        raw = attrs.get('strand_uvw')
        if raw is None or len(raw) == 0:
            return None
        array = np.asarray(raw, dtype=np.float32).reshape(-1, 3)
        if len(array) == len(valid):
            array = array[valid]
        if len(array) != nStrands:
            return None
        return np.ascontiguousarray(array[:, :2], dtype=np.float32)

    def _importLightsPhase(self):
        lightPluginTypes = set(LightTypeToPlugin.values())

        # Each light builds a node tree, so a few hundred of them is seconds of work.
        done, total = 0, sum(len(descs) for pluginType, descs in self.byType.items()
                             if pluginType in lightPluginTypes) or 1

        for pluginType, descs in self.byType.items():
            if pluginType not in lightPluginTypes:
                # A LIGHT-category plugin with no Blender light type is dropped here.
                pluginModule = findPluginModule(pluginType)
                if pluginModule is not None and pluginModule.TYPE == 'LIGHT':
                    self.stats.skipped[pluginType] += len(descs)
                continue
            for lightDesc in descs:
                try:
                    self._importLight(lightDesc, pluginType)
                except Exception as e:
                    self._recordError(lightDesc['Name'], 'lights', e)
                done += 1
                if done % _YIELD_EVERY == 0:
                    yield done / total

    def _importLight(self, lightDesc: dict, pluginType: str):

        attrs = dict(lightDesc['Attributes'])   # copy: 'transform' is consumed before node fill
        name = _sceneName(attrs, lightDesc['Name'])

        if (texAttr := _LIGHT_COLOR_TEX_ALIAS.get(pluginType)) and NodesImport.isPluginLink(attrs.get('color_tex')):
            attrs.setdefault(texAttr, attrs.pop('color_tex'))

        # units==0 makes 'intensity' a raw radiant intensity; any other value is photometric.
        if pluginType in _INVERSE_SQUARE_LIGHTS and int(attrs.get('units', 0)) == 0 and (intensity := attrs.get('intensity')) is not None:
            attrs['intensity'] = intensity * (self.vertexScale ** 2)

        lightData = NodesImport.createLightFromPluginDesc(pluginType, attrs, name)
        self.ledger.track(lightData)

        lightObj = self._newObject(name, lightData)
        # A missing 'transform' still has to go through worldMatrix (coordinate-system adjustment).
        transform = attrs.pop('transform', None)
        lightObj.matrix_world = self.worldMatrix(transform)
        self._registerTransformSource(lightObj, lightDesc)
        # Key by the plugin name like every other registration; keep the scene-name alias too.
        self.objectByNodePlugin[lightDesc['Name']] = lightObj
        self.objectByNodePlugin.setdefault(name, lightObj)
        if pathKey := _scenePathKey(attrs):
            self.objectsByPath.setdefault(pathKey, lightObj)
        self.stats.lights += 1

        # Read the raw attributes. Every light descriptor excludes the channels_* params.
        for channelAttr, selectMode in _LIGHT_SELECT_CHANNEL_ATTRS.items():
            refs = lightDesc['Attributes'].get(channelAttr)
            for ref in (refs if isinstance(refs, list) else [refs] if refs else []):
                self.lightsByChannelPlugin.setdefault(_refName(ref), []).append(lightObj)
                self.selectModeByChannelPlugin.setdefault(_refName(ref), selectMode)

        # A mesh light's emitter geometry is often a standalone GeomStaticMesh with no Node.
        emitter = None
        if pluginType == 'LightMesh' and (geomRef := attrs.get('geometry')):
            rawGeomName = _refName(geomRef)
            emitter = self._objectForGeometry(self.geomAliases.get(rawGeomName, rawGeomName), transform, objName=f"{name} Emitter", register=False)

        if emitter is None:
            objectResolver = self.resolveObjectPlugin
        else:
            # Resolve this light's geometry ref to its own emitter.
            def objectResolver(pluginName, linkType):
                return emitter if pluginName == rawGeomName else self.resolveObjectPlugin(pluginName, linkType)

        lightNtree = createNodeTreeForLightObject(lightData, isNewLight=True)
        importContext = self._importContext(lightNtree, objectResolver)
        # 'transform' is applied to the object, not exported as a Transform node.
        lightDescNoTm = {**lightDesc, 'Attributes': attrs}
        lightNode = NodesImport.createNode(importContext, lightDescNoTm)
        if lightNode is not None:
            _rearrangeAndCheck(lightNtree, lightNode)

    def _objectForGeometry(self, geomName: str, transform=None, *, objName: str = None,
                           hidden: bool = False, register: bool = True) -> bpy.types.Object | None:
        """ Return an object backing a geometry plugin, building one from the mesh datablock if no Node produced
            it. 'transform' applies only on creation.

            A fur growth surface is hidden and registered, so consumers share it; a mesh-light emitter is a visible
            gizmo and unregistered, so each light gets its own. """
        if obj := self.objectByGeomPlugin.get(geomName):
            return obj
        mesh = self.meshesByPlugin.get(geomName)
        if mesh is None:
            return None
        obj = self._newObject(objName or geomName, mesh)
        obj.matrix_world = self.worldMatrix(transform)
        obj.hide_render = obj.hide_viewport = hidden
        if register:
            self.objectByGeomPlugin[geomName] = obj
        return obj

    def _importCameras(self):
        renderViews = self.byType.get('RenderView', [])
        if not renderViews:
            return

        # Prefer a RenderView that affects settings as the active camera.
        primaryIdx = next((i for i, d in enumerate(renderViews) if not d['Attributes'].get('dont_affect_settings', False)), 0)

        for i, rvDesc in enumerate(renderViews):
            try:
                camObj = self._createCameraObject(rvDesc)
                if self._applyPhysicalCameraSettings(camObj.data, rvDesc):
                    self._autoFrameOrtho(camObj)
                if i == primaryIdx:
                    self.context.scene.camera = camObj
            except Exception as e:
                self._recordError(rvDesc['Name'], 'camera', e)

    def _createCameraObject(self, rvDesc: dict) -> bpy.types.Object:
        """ Create the camera datablock and object, apply the RenderView transform and
            register both with the rollback ledger. Blender's length defaults on the new
            camera are converted to the scene's unit scale. """
        attrs = rvDesc['Attributes']
        camName = _sceneName(attrs, rvDesc['Name'])

        camData = bpy.data.cameras.new(name=camName)
        self.ledger.track(camData)

        # Blender's camera defaults (display size, clip range, focus) are lengths for a 1 m unit.
        meterInUnits = 1.0 / blender_utils.getSceneMetersPerUnit(self.context)
        camData.display_size *= meterInUnits
        camData.clip_start *= meterInUnits
        camData.clip_end *= meterInUnits
        camData.dof.focus_distance *= meterInUnits

        camObj = self._newObject(camName, camData)
        camObj.matrix_world = self.worldMatrix(attrs.get('transform'))
        self._registerTransformSource(camObj, rvDesc)
        # Only Houdini writes RenderView.scene_name as a path. Max's plain string yields no key.
        if pathKey := _scenePathKey(attrs):
            self.objectsByPath.setdefault(pathKey, camObj)
        self.stats.cameras += 1

        return camObj

    def _recordCameraAnim(self, pluginDesc: dict, attrName: str, owner, prop: str,
                          convert=None, bake: bool = False):
        """ Record one camera value the importer maps by hand, outside _applyAttr. """
        if self.animTargets is None or pluginDesc is None:
            return
        if attrName not in (pluginDesc.get('AnimatedKeys') or {}):
            return
        self.animTargets.record(owner, prop, pluginDesc['Name'], attrName,
                                written=getattr(owner, prop, None), convert=convert, bake=bake)

    def _applyPhysicalCameraSettings(self, camData: bpy.types.Camera, rvDesc: dict) -> bool:
        """ Fill the camera datablock and its V-Ray property groups (CameraPhysical/
            SettingsCamera/RenderView/CameraDome): lens/FOV, DOF, clipping and ortho.
            Returns True when the ortho camera needs auto-framing (_autoFrameOrtho). """
        attrs = rvDesc['Attributes']
        # The camera's other plugins are matched by shared scene_name.
        sceneName = attrs.get('scene_name')
        physicalDesc = self._cameraSubPlugin('CameraPhysical', sceneName)
        settingsDesc = self._cameraSubPlugin('SettingsCamera', sceneName)

        vrayCam = camData.vray

        # SettingsCamera holds the V-Ray camera type; apply it for every camera.
        camType = self._applySettingsCamera(settingsDesc, camData, attrs.get('fov'))

        # Orthographic if RenderView says so, or the V-Ray camera type is Orthogonal.
        isOrtho = bool(attrs.get('orthographic', False)) or (camType == camera_utils.CAM_TYPE_ORTHOGONAL)

        needsAutoFrame = False
        if isOrtho:
            # Set the lens type after SettingsCamera, never before.
            camData.type = 'ORTHO'
            orthoWidth = attrs.get('orthographicWidth', 0.0)
            # orthographicWidth is the world-space frame width; auto-fit only if it is missing.
            if orthoWidth > 0.0:
                camData.ortho_scale = orthoWidth * self.vertexScale
                self._recordCameraAnim(rvDesc, 'orthographicWidth', camData, 'ortho_scale',
                                       convert=lambda v: v * self.vertexScale)
            else:
                needsAutoFrame = True
            vrayCam.use_physical = False

        elif physicalDesc is not None:
            # Fill the property group and derive the Blender lens/sensor. CameraPhysical
            # skips normalizeUnits, so its distances are scaled manually here. Copy first,
            # because _cameraSubPlugin hands the same descriptor to every RenderView.
            physicalAttrs = dict(physicalDesc['Attributes'])
            for distAttr in ('focus_distance', 'target_distance'):
                if (v := physicalAttrs.get(distAttr)) is not None:
                    physicalAttrs[distAttr] = v * self.vertexScale
            # Blender has no "focus at target_distance" mode and specify_focus has no UI.
            sourceSpecifyFocus = physicalAttrs.get('specify_focus')
            physicalAttrs['specify_focus'] = True
            # focal_length/fov/film_width proxy camera.lens/angle/sensor_width, which are keyed below.
            ownKeys = {k: v for k, v in (physicalDesc.get('AnimatedKeys') or {}).items()
                       if k not in ('focal_length', 'fov', 'film_width')}
            NodesImport.pluginAttrsToPropGroup({**physicalDesc, 'Attributes': physicalAttrs},
                                                vrayCam.CameraPhysical,
                                                animTargets=self.animTargets,
                                                source={**physicalDesc, 'AnimatedKeys': ownKeys})
            vrayCam.CameraPhysical.enable_vignetting = vrayCam.CameraPhysical.vignetting != 0.0
            # Addon default is off; V-Ray's default is on.
            vrayCam.CameraPhysical.enable_thin_lens_equation = bool(physicalAttrs.get('enable_thin_lens_equation', True))
            # Enable via use_physical, not CameraPhysical.use; its update callback sets both.
            vrayCam.use_physical = True
            if (filmWidth := physicalAttrs.get('film_width')):
                camData.sensor_width = filmWidth
                camData.sensor_fit = 'HORIZONTAL'
                self._recordCameraAnim(physicalDesc, 'film_width', camData, 'sensor_width')
            if physicalAttrs.get('specify_fov') and (fov := physicalAttrs.get('fov')) and fov > 0.0:
                aspect = self._outputAspect()
                camData.angle = camera_utils.correctFovForAspectOnImport(fov, aspect)
                self._recordCameraAnim(physicalDesc, 'fov', camData, 'lens',
                                       convert=_fovKeyToLens(camData, aspect), bake=True)
            elif (focal := physicalAttrs.get('focal_length')):
                camData.lens = focal
                self._recordCameraAnim(physicalDesc, 'focal_length', camData, 'lens')

            # Fill focus_distance even without DoF, since the thin lens equation reads it too.
            # The raw keys still need the vertexScale physicalAttrs got above.
            scaleDistance = lambda v: v * self.vertexScale
            if sourceSpecifyFocus and (focus := physicalAttrs.get('focus_distance')) is not None:
                camData.dof.focus_distance = focus
                self._recordCameraAnim(physicalDesc, 'focus_distance', camData.dof,
                                       'focus_distance', convert=scaleDistance)
            elif (targetDist := physicalAttrs.get('target_distance')) is not None:
                camData.dof.focus_distance = targetDist
                self._recordCameraAnim(physicalDesc, 'target_distance', camData.dof,
                                       'focus_distance', convert=scaleDistance)

            if physicalAttrs.get('use_dof'):
                camData.dof.use_dof = True
                if (fNumber := physicalAttrs.get('f_number')):
                    camData.dof.aperture_fstop = fNumber
                    self._recordCameraAnim(physicalDesc, 'f_number', camData.dof,
                                           'aperture_fstop')

        else:
            # A standard camera uses the RenderView fov (horizontal, radians).
            fov = attrs.get('fov')
            if fov is not None and fov > 0.0:
                camData.sensor_fit = 'HORIZONTAL'
                aspect = self._outputAspect()
                camData.angle = camera_utils.correctFovForAspectOnImport(fov, aspect)
                self._recordCameraAnim(rvDesc, 'fov', camData, 'lens',
                                       convert=_fovKeyToLens(camData, aspect), bake=True)

        # V-Ray clipping is optional (RenderView.clipping); Blender's near/far are always active.
        # A disabled source range is not rendered, but still frames the camera view.
        clipping = bool(attrs.get('clipping'))
        if clipping or self._isViewportClipRange(attrs.get('clipping_near'), attrs.get('clipping_far')):
            if (clipNear := attrs.get('clipping_near')) is not None:
                camData.clip_start = clipNear * self.vertexScale
                self._recordCameraAnim(rvDesc, 'clipping_near', camData, 'clip_start',
                                       convert=lambda v: v * self.vertexScale)
            if (clipFar := attrs.get('clipping_far')) is not None:
                camData.clip_end = clipFar * self.vertexScale
                self._recordCameraAnim(rvDesc, 'clipping_far', camData, 'clip_end',
                                       convert=lambda v: v * self.vertexScale)
        if clipping:
            # RenderView.clipping is only written when the camera settings override is on.
            vrayCam.RenderView.clipping = True
            vrayCam.SettingsCamera.override_camera_settings = True

        # Dome / 360 camera; a CameraPhysical takes precedence.
        domeDesc = self._cameraSubPlugin('CameraDome', sceneName)
        if domeDesc is not None and not vrayCam.use_physical:
            NodesImport.pluginAttrsToPropGroup(domeDesc, vrayCam.CameraDome)
            vrayCam.CameraDome.use = True
            vrayCam.use_dome = True

        return needsAutoFrame

    @staticmethod
    def _isViewportClipRange(clipNear, clipFar) -> bool:
        """ Whether a disabled RenderView clipping range can stand in for Blender's: V-Ray's
            defaults (0 and 1e18) mean no clipping, and a 0 near plane is invalid in Blender. """
        return isinstance(clipNear, (int, float)) and isinstance(clipFar, (int, float)) \
            and 0.0 < clipNear < clipFar < _VRAY_NO_FAR_CLIP

    def _applySettingsCamera(self, settingsDesc: dict | None, camData: bpy.types.Camera,
                             renderViewFov: float | None) -> str:
        """ Apply a SettingsCamera description to a camera's property group.
            Returns the resulting V-Ray camera type, or the default type if there was nothing
            to apply. """
        vrayCam = camData.vray
        settingsCamera = vrayCam.SettingsCamera
        if settingsDesc is None:
            return camera_utils.CAM_TYPE_STANDARD

        srcAttrs = settingsDesc['Attributes']

        # SettingsCamera.fov applies only when the RenderView wrote none; -1 means ignore.
        srcFov = srcAttrs.get('fov')
        overrideFov = (srcFov is not None) and (srcFov > 0.0) and (renderViewFov is None)
        if not overrideFov:
            settingsDesc = _descWithout(settingsDesc['ID'], srcAttrs, {'fov'})

        NodesImport.pluginAttrsToPropGroup(settingsDesc, settingsCamera)
        settingsCamera.override_fov = overrideFov
        if overrideFov:
            camData.sensor_fit = 'HORIZONTAL'
            camData.angle = camera_utils.correctFovForAspectOnImport(srcFov, self._outputAspect())

        camType = str(settingsCamera.type)

        # For these types the source 'height' is the vertical FOV in degrees.
        if (camType in (camera_utils.CAM_TYPE_SPHERICAL_PANORAMA, camera_utils.CAM_TYPE_CYLINDRICAL_VERTICAL_FOV)) and ((height := srcAttrs.get('height')) is not None):
            settingsCamera.vertical_fov = math.radians(height)

        # override_camera_settings is the master switch for the whole group.
        if (camType != camera_utils.CAM_TYPE_STANDARD) or overrideFov:
            settingsCamera.override_camera_settings = True

        return camType

    def _autoFrameOrtho(self, camObj: bpy.types.Object):
        """ Set an orthographic camera's ortho_scale to fit the imported geometry,
            preserving the camera's orientation and position (pan). """
        viewInv = camObj.matrix_world.inverted()

        minX = minY = 1e30
        maxX = maxY = -1e30
        found = False
        for obj in self.ledger.byType(bpy.types.Object):
            if obj.type != 'MESH' or obj.data is None or len(obj.data.vertices) == 0:
                continue
            for corner in obj.bound_box:
                v = viewInv @ (obj.matrix_world @ mathutils.Vector(corner))
                minX, maxX = min(minX, v.x), max(maxX, v.x)
                minY, maxY = min(minY, v.y), max(maxY, v.y)
                found = True

        if not found:
            return

        # ortho_scale spans the larger view dimension; leave a small margin.
        width = max(maxX - minX, maxY - minY)
        camObj.data.ortho_scale = width * 1.08

    def _cameraSubPlugin(self, pluginType: str, sceneName: str) -> dict | None:
        """ Find a camera sub-plugin (CameraPhysical/SettingsCamera) belonging to the
            camera identified by sceneName. A single plugin of that type with no match is
            treated as a scene-global setting; 2+ candidates with no match return None. """
        descs = self.byType.get(pluginType, [])
        if sceneName:
            match = next((d for d in descs if d['Attributes'].get('scene_name') == sceneName), None)
            if match is not None:
                return match
        return descs[0] if len(descs) == 1 else None

    def _importEnvironment(self):
        envDesc = self._firstReferencing('SettingsEnvironment')
        channelDescs = []
        if self.options.importRenderChannels:
            channelDescs = [p for ptype, descs in self.byType.items()
                            if ptype.startswith('RenderChannel') for p in descs]

        # Build the world tree if there is anything for it: environment, effects, or channels.
        if envDesc is None and not channelDescs:
            return

        self._buildWorld(envDesc, channelDescs)

    def _buildWorld(self, envDesc: dict | None, channelDescs: list):

        world = bpy.data.worlds.new(f"{Path(self.options.filePath).stem}_world")
        self.ledger.track(world)
        self.context.scene.world = world
        addWorldNodeTree(world)

        ntree = world.node_tree
        # The world-tree textures/effects may reference scene objects (fog gizmos, etc.).
        importContext = self._importContext(ntree)

        if envDesc is not None:
            self._fillEnvironment(envDesc, ntree, importContext, world)
            self._importEffects(envDesc, ntree, importContext)

        if channelDescs:
            self._importRenderChannels(channelDescs, ntree, importContext, world)

        NodesTools.arrangeImportedTree(ntree, next(n for n in ntree.nodes if n.bl_idname == 'VRayNodeWorldOutput'))

    def _fillEnvironment(self, envDesc: dict, ntree, importContext, world):
        envNode = next((n for n in ntree.nodes if n.bl_idname == 'VRayNodeEnvironment'), None)
        if envNode is None:
            return

        attrs = envDesc['Attributes']

        # global_light_level lives on world.vray, not on a node.
        if (globalLightLevel := attrs.get('global_light_level')) is not None:
            world.vray.global_light_level = globalLightLevel[0] if isinstance(globalLightLevel, (list, tuple)) else globalLightLevel

        # (socket, color attr, texture attr, multiplier attr, use flag) per override,
        # mirroring exporting/world_export.py ENVIRONMENT_OVERRIDES.
        overrides = (
            ("Background", 'bg_color', 'bg_tex', 'bg_tex_mult', 'use_bg'),
            ("GI", 'gi_color', 'gi_tex', 'gi_tex_mult', 'use_gi'),
            ("Reflection", 'reflect_color', 'reflect_tex', 'reflect_tex_mult', 'use_reflect'),
            ("Refraction", 'refract_color', 'refract_tex', 'refract_tex_mult', 'use_refract'),
            ("Secondary Matte", 'secondary_matte_color', 'secondary_matte_tex', 'secondary_matte_tex_mult', 'use_secondary_matte'),
        )

        for socketName, colorAttr, texAttr, multAttr, useAttr in overrides:
            socket = envNode.inputs.get(socketName)
            if socket is None:
                continue

            if useAttr in attrs:
                socket.use = bool(attrs[useAttr])
            if multAttr in attrs:
                socket.multiplier = attrs[multAttr]

            texValue = attrs.get(texAttr)
            if NodesImport.isPluginLink(texValue):
                # bg_tex etc. references a texture plugin, so build and link it.
                connectedPlugin, outputName = NodesImport.getPluginFromLink(importContext, texValue)
                if connectedPlugin is not None:
                    socket.use = True
                    NodesImport.createLinkedNode(importContext, socket, outputName, connectedPlugin)
                    continue

            # Plain color value on the socket. The server omits default-valued attrs.
            colorValue = attrs.get(colorAttr, texValue)
            if isinstance(colorValue, (list, tuple)) and len(colorValue) >= 3:
                socket.value = colorValue[:3]
                if useAttr not in attrs:
                    socket.use = True

    def _importEffects(self, envDesc: dict, ntree, importContext):
        """ Reverse SettingsEnvironment.environment_volume: create the fog/toon effect
            nodes and link them into the VRayNodeEffectsHolder. """
        volumes = envDesc['Attributes'].get('environment_volume')
        if not volumes:
            return

        holder = next((n for n in ntree.nodes if n.bl_idname == 'VRayNodeEffectsHolder'), None)
        if holder is None:
            return

        for ref in (volumes if isinstance(volumes, (list, tuple)) else [volumes]):
            effDesc = NodesImport.getPluginByName(self.vrsceneDict, _refName(ref))
            if effDesc is None or _isToonOutlinesVolume(effDesc):
                continue
            try:
                node = NodesImport.createNode(importContext, effDesc)
                if node is None:
                    self.stats.skipped[effDesc['ID']] += 1   # SKIPPED effect (no node class)
                    continue
                self._attachToContainer(holder, node, 'VRaySocketEffect', 'Effect')
                self.stats.extras += 1
            except Exception as e:
                self._recordError(effDesc['Name'], 'environment effect', e)

    def _importRenderChannels(self, channelDescs: list, ntree, importContext, world):
        """ Create render-channel nodes and link them into VRayNodeRenderChannels. """
        channelsNode = next((n for n in ntree.nodes if n.bl_idname == 'VRayNodeRenderChannels'), None)
        if channelsNode is None:
            return

        aliasToVariant = _renderChannelAliasVariants()
        lightMixNode = None
        importedLightSelects = 0

        for chDesc in channelDescs:
            try:
                chDesc = _derivedChannelDesc(chDesc)
                node = self._createRenderChannelNode(chDesc, ntree, importContext, aliasToVariant, world)
                if node is not None:
                    self._attachToContainer(channelsNode, node, 'VRaySocketRenderChannel', 'Channel')
                    if chDesc['ID'] == 'RenderChannelLightMix':
                        lightMixNode = node
                    if chDesc['ID'] == 'RenderChannelLightSelect':
                        self._fillLightSelectLights(node, chDesc)
                        importedLightSelects += 1
            except Exception as e:
                self._recordError(chDesc['Name'], 'render channel', e)

        if lightMixNode is not None and importedLightSelects:
            # A scene that enumerates its own Light Select channels is Light Mix 'manual' mode.
            lightMixNode.RenderChannelLightMix.mode = 'manual'

    def _createRenderChannelNode(self, chDesc: dict, ntree, importContext, aliasToVariant: dict, world):
        chType = chDesc['ID']

        if chType in _EXPORTER_MANAGED_CHANNELS:
            self.stats.skipped[chType] += 1
            return None

        if chType == 'RenderChannelDenoiser':
            # Denoiser props live on world.vray.RenderChannelDenoiser, not the node.
            node = ntree.nodes.new('VRayNodeRenderChannelDenoiser')
            NodesImport.pluginAttrsToPropGroup(chDesc, world.vray.RenderChannelDenoiser, restoreVRayDefaults=True, stats=self.stats)
            return node

        if chType == 'RenderChannelLightSelect' and int(chDesc['Attributes'].get('light_select_mode', 0)) in _LIGHTMIX_ONLY_SELECT_MODES:
            # These carry no lights and the exporter recreates them from the LightMix settings.
            self.stats.skipped['LightMix LightSelect'] += 1
            return None

        if chType in ('RenderChannelColor', 'RenderChannelGlossiness'):
            # Named variant for known aliases; the hidden base node for any other alias.
            alias = int(chDesc['Attributes'].get('alias', -1))
            variantBlId = aliasToVariant.get(alias, f'VRayNode{chType}')
            node = ntree.nodes.new(variantBlId)
            NodesImport.pluginAttrsToNodeProps(chDesc, node)
            return node

        # Other channel types have an auto-generated node.
        node = NodesImport.createNode(importContext, chDesc)
        if node is None:
            self.stats.skipped[chType] += 1
        return node

    def _fillLightSelectLights(self, node, chDesc: dict):
        """ Fill a LightSelect channel's light list from the light->channel references collected
            during the lights phase, which runs before this one. """
        channelPluginName = chDesc['Name']
        propGroup = node.RenderChannelLightSelect

        for lightObj in self.lightsByChannelPlugin.get(channelPluginName, ()):
            propGroup.light_selector.addListItem(self.context, lightObj)

        # A channel remapped from RenderChannelColor has no light_select_mode of its own.
        if 'light_select_mode' not in chDesc['Attributes']:
            if selectMode := self.selectModeByChannelPlugin.get(channelPluginName):
                propGroup.light_select_mode = selectMode

    @staticmethod
    def _attachToContainer(container, node, socketType: str, socketPrefix: str):
        """ Link node's default output to the next free typed input on a container
            (effects holder / render channels node), adding a socket if needed. Mirrors
            plugins/channel/RenderChannelsPanel._createRenderChannel. """
        tree = container.id_data

        # Light Mix / Denoiser have their own container socket and accept no other channel
        targetSock = getSpecialChannelSocket(container, node.bl_idname)
        if targetSock is None:
            targetSock = next((s for s in container.inputs
                               if not s.is_linked and s.bl_idname == socketType), None)
        if targetSock is None:
            count = sum(1 for s in container.inputs if s.bl_idname == socketType)
            targetSock = addInput(container, socketType, f"{socketPrefix} {count + 1}")
            moveExtendSocketToBottom(container)

        tree.links.new(node.outputs[0], targetSock)

    # Render-settings plugins applied onto scene.vray property groups. Only plugins the addon
    # draws belong here, or the import leaves the scene rendering differently with nothing in
    # the UI to show why. One reachable attribute counts as drawn.
    _RENDER_SETTINGS_TYPES = (
        'SettingsGI', 'SettingsLightCache', 'SettingsDMCGI', 'SettingsDMCSampler',
        'SettingsImageSampler', 'SettingsCaustics', 'SettingsOptions',
        'SettingsRegionsGenerator', 'SettingsRTEngine', 'SettingsRenderChannels',
        'SettingsDefaultDisplacement', 'SettingsMotionBlur', 'SettingsCameraDof', 'SettingsHair',
        'SettingsTextureCache', 'SettingsOutput',
    )

    # Registered as scene property groups, but no panel draws them, so they are applied only
    # under importUnexposedSettings. SettingsColorMapping is the costly one, changing the image
    # through a Widget nothing renders. Draw either of them and it moves to the list above.
    _UNEXPOSED_SETTINGS_TYPES = (
        'SettingsIrradianceMap', 'SettingsColorMapping',
    )

    # Plugins where only part of the group is drawn, so copy just those attributes. Anything else
    # would be invisible state again, one level down from _UNEXPOSED_SETTINGS_TYPES.
    _EXPOSED_SETTINGS_ATTRS = {
        # VRAY_PT_GI draws only these three, and asserts primary_engine is Brute Force, so
        # importing an Irradiance Map or Photon Map primary would break the panel.
        'SettingsGI': {'on', 'secondary_engine', 'use_light_cache_for_interactive'},
        # The Brute Force sub-panel draws depth only, not subdivs.
        'SettingsDMCGI': {'depth'},
        # Reachable only through SettingsImageSampler's custom_draw.
        'SettingsDMCSampler': {'time_dependent'},
        'SettingsHair': {'min_hair_width'},
        'SettingsTextureCache': {'max_mipmap_resolution'},
        # VRAY_PT_GpuTextureOptions draws the texture attrs and the GPU image sampler rollouts the
        # ray attrs. The rest are filled from the image sampler on export (see _importGpuSampling)
        # or drawn nowhere, like enable_bucket_sampler, which at 0 makes V-Ray GPU progressive.
        'SettingsRTEngine': {
            'opencl_resizeTextures', 'opencl_texsize', 'opencl_textureFormat', 'out_of_core_textures',
            'gpu_bundle_size', 'gpu_samples_per_pixel',
        },
        # The Depth of Field rollout draws these.
        'SettingsCameraDof': {
            'on', 'aperture', 'center_bias', 'focal_dist', 'sides_on', 'sides_num',
            'rotation', 'anisotropy',
        },
        # img_dir/img_file are left out, since a foreign output path is worse than none, and the
        # resolution goes to scene.render rather than to the property group.
        'SettingsOutput': {
            'img_noAlpha', 'img_separateAlpha', 'relements_separateFolders',
            'img_file_needFrameNumber', 'img_deepFile',
        },
    }

    # img_format is addon-only, so no .vrscene carries it, but the output filename names the
    # format the source was writing. Values are the keys of lib_utils.FormatToSettings.
    _EXT_TO_IMG_FORMAT = {
        '.png': '0', '.jpg': '1', '.jpeg': '1', '.tif': '2', '.tiff': '2',
        '.tga': '3', '.sgi': '4', '.rgb': '4', '.exr': '5', '.vrst': '6',
    }

    # Deprecated ENUM values mapped onto their modern equivalent. Unmapped, they fail the enum
    # check and the addon default silently wins.
    _DEPRECATED_ENUM_REMAP = {
        # 0 (fixed rate) and 2 (adaptive subdivision) are both superseded by 1 (bucket).
        'SettingsImageSampler': {'type': {'0': '1', '2': '1'}},
    }

    def _applySettings(self, pluginTypes):
        """ Copy the given Settings* plugins onto their matching scene.vray property groups. """
        sceneVray = self.context.scene.vray

        for pluginType in pluginTypes:
            settingsDesc = self._firstReferencing(pluginType)
            if settingsDesc is None:
                continue
            propGroup = getattr(sceneVray, pluginType)

            # Drop the attributes of a partly-drawn group that no panel shows, for the same
            # reason as _UNEXPOSED_SETTINGS_TYPES, one level down.
            if (exposedAttrs := self._EXPOSED_SETTINGS_ATTRS.get(pluginType)) is not None:
                attrs = {k: v for k, v in settingsDesc['Attributes'].items() if k in exposedAttrs}
                if dropped := (len(settingsDesc['Attributes']) - len(attrs)):
                    self.stats.skipped[f'{pluginType} attrs (no UI)'] += dropped
                settingsDesc = {'ID': pluginType, 'Attributes': attrs}

            if remaps := self._DEPRECATED_ENUM_REMAP.get(pluginType):
                attrs = dict(settingsDesc['Attributes'])
                for attrName, mapping in remaps.items():
                    if (newValue := mapping.get(str(attrs.get(attrName)))) is not None:
                        attrs[attrName] = newValue
                settingsDesc = {'ID': pluginType, 'Attributes': attrs}

            # pluginAttrsToPropGroup silently leaves the default for an ENUM it cannot store.
            if pluginModule := findPluginModule(pluginType):
                for attrName, attrValue in settingsDesc['Attributes'].items():
                    attrDesc = attribute_utils.getAttrDesc(pluginModule, attrName)
                    if attrDesc and attrDesc['type'] == 'ENUM' and hasattr(propGroup, attrName) \
                            and not attribute_utils.valueInEnumItems(attrDesc, str(attrValue)):
                        self.stats.skipped[f'{pluginType}.{attrName}={attrValue} (unsupported)'] += 1

            try:
                NodesImport.pluginAttrsToPropGroup(settingsDesc, propGroup, scaleDistance=True)
                self.stats.settings += 1
            except Exception as e:
                self._recordError(pluginType, 'settings', e)

        # Turn auto-save off for a GI cache imported without a save path.
        for pluginType in ('SettingsIrradianceMap', 'SettingsLightCache', 'SettingsCaustics'):
            cachePropGroup = getattr(sceneVray, pluginType)
            if cachePropGroup.auto_save and not cachePropGroup.auto_save_file:
                cachePropGroup.auto_save = False

    def _importAAFilter(self):
        """ Apply the anti-aliasing filter, which the source encodes as a standalone Filter*
            plugin, because SettingsImageSampler has no attribute referencing it. The addon
            models the choice as that plugin's own name in SettingsImageSampler.filter_type (an
            addon-only parameter, so no .vrscene carries it) plus the plugin's property group;
            the Image Sampler panel draws both in its Anti-Aliasing Filter rollout.
        """

        sampler = self.context.scene.vray.SettingsImageSampler
        attrDesc = attribute_utils.getAttrDesc(findPluginModule('SettingsImageSampler'), 'filter_type')
        # The enum's values are the plugin names, bar the 'NONE' entry.
        present = [item[0] for item in attrDesc['items']
                   if item[0] != 'NONE' and self.byType.get(item[0])]

        if not present:
            # V-Ray filters only when the plugin is there, so its absence means no filter.
            sampler.filter_type = 'NONE'
            return

        for extra in present[1:]:
            self.stats.skipped[f'{extra} (several AA filters)'] += 1

        sampler.filter_type = present[0]
        self._applySettings((present[0],))

    # InfoRenderEngine.engine_type of the V-Ray GPU engines, as the DCC exporters write it.
    _GPU_ENGINE_TYPES = ('cuda', 'rtx', 'metal', 'hip')

    def _importGpuSampling(self):
        """ Invert the GPU math of the SettingsImageSampler and SettingsRTEngine exporters. They
            derive dmc_maxSubdivs from samples_limit, dmc_minSubdivs from gpu_samples_per_pixel and
            the SettingsRTEngine noise threshold and time limit from the image sampler, so the
            values _applySettings copied would not survive a re-export on their own.
        """
        samplerDesc = self._firstReferencing('SettingsImageSampler')
        if samplerDesc is None:
            return
        samplerAttrs = samplerDesc['Attributes']
        sceneVray = self.context.scene.vray
        sampler = sceneVray.SettingsImageSampler

        infoDesc = NodesImport.getPluginByType(self.vrsceneDict, 'InfoRenderEngine')
        if infoDesc and infoDesc['Attributes'].get('engine_type') in self._GPU_ENGINE_TYPES:
            rtDesc = self._firstReferencing('SettingsRTEngine')
            rtAttrs = rtDesc['Attributes'] if rtDesc else {}

            # V-Ray GPU renders buckets only for sampler type 0 or 1 with enable_bucket_sampler set
            # (V-Ray defaults both to 0), and progressive otherwise. The addon always exports the
            # flag set, so the type alone has to carry the choice.
            isBucket = rtAttrs.get('enable_bucket_sampler', 0) and str(samplerAttrs.get('type', 0)) in ('0', '1')
            sampler.type = '1' if isBucket else '3'

            # GPU progressive reads its limits off SettingsRTEngine, which the exporter fills from these.
            if not isBucket:
                if (threshold := rtAttrs.get('noise_threshold')) is not None:
                    sampler.progressive_threshold = threshold
                if (maxTime := rtAttrs.get('max_render_time')) is not None:
                    sampler.progressive_maxTime = maxTime

        # samples_limit is addon-only, so no .vrscene carries it. Set the item as upgrade_0026 does,
        # because onUpdateSamplesLimit would write dmc_maxSubdivs back.
        if 'dmc_maxSubdivs' in samplerAttrs:
            sampler['samples_limit'] = _maxSubdivsGPUToSamplesLimit(sampler.dmc_maxSubdivs)

        # On GPU the rays per pixel are also the bucket sampler's min subdivs.
        if sampler.type == '1' and 'dmc_minSubdivs' in samplerAttrs:
            sceneVray.SettingsRTEngine.gpu_samples_per_pixel = _minSubdivsToRaysPerPixel(sampler.dmc_minSubdivs)

    def _importSettings(self):
        self._applySettings(self._RENDER_SETTINGS_TYPES)
        self._importGpuSampling()
        self._importAAFilter()

        if self.options.importUnexposedSettings:
            self._applySettings(self._UNEXPOSED_SETTINGS_TYPES)
        else:
            for pluginType in self._UNEXPOSED_SETTINGS_TYPES:
                if self._firstReferencing(pluginType) is not None:
                    self.stats.skipped[f'{pluginType} (no UI)'] += 1

        if unitsDesc := NodesImport.getPluginByType(self.vrsceneDict, 'SettingsUnitsInfo'):
            unitsAttrs = unitsDesc['Attributes']

            # frames_scale == 0 means "use seconds_scale instead".
            fps = unitsAttrs.get('frames_scale') or 0.0
            if fps <= 0.0 and (secondsScale := unitsAttrs.get('seconds_scale')):
                fps = 1.0 / secondsScale
            if fps > 0.0:
                render = self.context.scene.render
                render.fps = max(1, round(fps))
                render.fps_base = render.fps / fps

        # Resolution goes to scene.render rather than to the SettingsOutput property group the
        # rest of the plugin was applied onto above.
        if outputDesc := self._firstReferencing('SettingsOutput'):
            outAttrs = outputDesc['Attributes']
            if (w := outAttrs.get('img_width')) and (h := outAttrs.get('img_height')):
                self.context.scene.render.resolution_x = int(w)
                self.context.scene.render.resolution_y = int(h)

        # The Output panel shows the settings of whichever image format is selected, resolving
        # the property group through FormatToSettings, so every one of them is reachable. Import
        # them all rather than only the selected one, because the source may name a format the
        # addon does not offer and the user is free to switch afterwards. Derived from the same
        # mapping the panel uses so the two cannot drift apart.
        self._applySettings(tuple(dict.fromkeys(lib_utils.FormatToSettings.values())))

        # Select the format the source was writing, so the settings imported above are the ones
        # the panel shows. Only the extension is read; the path itself is still ignored.
        if outputDesc is not None:
            ext = os.path.splitext(str(outputDesc['Attributes'].get('img_file', '')))[1].lower()
            if imgFormat := self._EXT_TO_IMG_FORMAT.get(ext):
                self.context.scene.vray.SettingsOutput.img_format = imgFormat

    def _importExtras(self):
        for splatDesc in self.byType.get('GeomGaussians', []):
            try:
                self._importSplat(splatDesc)
            except Exception as e:
                self._recordError(splatDesc['Name'], 'splat', e)

        # The Node wrapping a GeomHair carries the hair material and world transform.
        owners = self._nodeOwnersByGeometry()

        for furDesc in self.byType.get('GeomHair', []):
            try:
                mtlName, transform = owners.get(furDesc['Name'], (None, None))
                self._importFur(furDesc, mtlName, transform)
            except Exception as e:
                self._recordError(furDesc['Name'], 'fur', e)

        for decalDesc in self.byType.get('VRayDecal', []):
            try:
                self._importDecal(decalDesc)
            except Exception as e:
                self._recordError(decalDesc['Name'], 'decal', e)

        for clipperDesc in self.byType.get('VRayClipper', []):
            try:
                self._importClipper(clipperDesc)
            except Exception as e:
                self._recordError(clipperDesc['Name'], 'clipper', e)

        # Instancers are handled by _importInstancers when the option is on.
        if not self.options.importInstances:
            for instType in _INSTANCER_TYPES:
                count = len(self.byType.get(instType, []))
                if count:
                    self.stats.skipped[f'{instType} (instances not applied)'] += count

    def _instancerBuildOrder(self, parsedByName: dict) -> list:
        """ Instancer plugin names ordered so an instancer used as another's prototype comes first
            (V-Ray for Houdini nests packed primitives this way). """
        dependencies = {}
        for name, (_, parsed) in parsedByName.items():
            deps = set()
            for protoKey in parsed.prototypes:
                for nodeName in protoKey:
                    nodeDesc = NodesImport.getPluginByName(self.vrsceneDict, nodeName)
                    if nodeDesc is None or nodeDesc['ID'] != 'Node':
                        continue
                    geomName = _refName(nodeDesc['Attributes'].get('geometry'))
                    if geomName in parsedByName:
                        deps.add(geomName)
            dependencies[name] = deps

        ordered, visiting, done = [], set(), set()

        def visit(name):
            if name in done:
                return
            if name in visiting:
                debug.printWarning(f"Cyclic instancer prototype chain at '{name}'")
                return
            visiting.add(name)
            for dep in dependencies.get(name, ()):
                visit(dep)
            visiting.discard(name)
            done.add(name)
            ordered.append(name)

        for name in parsedByName:
            visit(name)
        return ordered

    def _importInstancers(self):

        # Wrapping-Node transform per instancer geometry plugin (applied to the point cloud).
        owners = self._nodeOwnersByGeometry()

        # Parse all first. The build order depends on which instancers reference which.
        parsedByName = {}
        for instType in _INSTANCER_TYPES:
            for desc in self.byType.get(instType, []):
                try:
                    parsed = instancer_import.parseInstancer(desc, self)
                    if parsed is None or parsed.count == 0:
                        self.stats.skipped[f'{instType} (empty)'] += 1
                        continue
                    parsedByName[desc['Name']] = (desc, parsed)
                except Exception as e:
                    self._recordError(desc['Name'], 'instancer', e)

        for name in self._instancerBuildOrder(parsedByName):
            desc, parsed = parsedByName[name]
            try:
                _, ownerTm = owners.get(name, (None, None))
                obj = instancer_import.buildInstancerObject(self, parsed, ownerTm)
                if obj is None:
                    continue
                self.stats.extras += 1
                # Register the point cloud under its wrapping Node, which _importNodeObject skipped.
                if (ownerDesc := self._nodeOwnerDescs.get(name)) is not None:
                    self.objectByNodePlugin.setdefault(ownerDesc['Name'], obj)
                    self._registerTransformSource(obj, ownerDesc)
                    if pathKey := _scenePathKey(ownerDesc['Attributes']):
                        self.objectsByPath.setdefault(pathKey, obj)
            except Exception as e:
                self._recordError(name, 'instancer', e)

    def _importSplat(self, splatDesc: dict):

        obj = self._newObject(splatDesc['Name'])   # Gaussian splats are Empties
        NodesImport.pluginAttrsToPropGroup(splatDesc, obj.vray.GeomGaussians,
                                            animTargets=self.animTargets, source=splatDesc)
        # Resolve a relative .ply path against the scene directory.
        if rawFile := splatDesc['Attributes'].get('file'):
            obj.vray.GeomGaussians.file = self._resolveAssetPath(rawFile)
        obj.vray.isVRayGaussian = True
        obj.matrix_world = self.worldMatrix(splatDesc['Attributes'].get('transform'))
        self.stats.extras += 1

        if err := splat_preview.loadPreview(obj):
            debug.printWarning(f"Splat '{splatDesc['Name']}': {err}")

    def _createEmptyGeometryObject(self, geomDesc: dict, objName: str):
        """ Build the Empty standing for a GeomPlane / GeomPerfectSphere. Called from the Node
            walk, which then applies the wrapping Node's transform and visibility, since
            neither plugin has a transform of its own. Their params live on obj.vray.<plugin>
            because an Empty has no data block.
        """
        pluginId = geomDesc['ID']
        obj = self._newObject(objName)
        propGroup = getattr(obj.vray, pluginId)

        NodesImport.pluginAttrsToPropGroup({'ID': pluginId, 'Attributes': geomDesc['Attributes']},
                                            propGroup, scaleDistance=True, stats=self.stats)

        if pluginId == 'GeomPerfectSphere':
            obj.vray.isVRayPerfectSphere = True
            obj.empty_display_type = 'SPHERE'
            obj.empty_display_size = propGroup.radius
        else:
            obj.vray.isVRayInfinitePlane = True
            obj.empty_display_type = 'SINGLE_ARROW'
            obj.empty_display_size = 2.0

        return obj

    def _importFur(self, furDesc: dict, mtlName: str | None = None, transform=None):
        furData = bpy.data.hair_curves.new(furDesc['Name'])
        self.ledger.track(furData)
        furObj = self._newObject(furDesc['Name'], furData)
        furObj.vray.isVRayFur = True
        # The wrapping Node's transform; GeomHair itself has none.
        furObj.matrix_world = self.worldMatrix(transform)

        # Fill the scalar attrs only; 'mesh' and the *_tex links are handled separately.
        # Dropping every string drops only links: GeomHair declares no STRING parameter.
        # Add one and it has to be excluded here by name.
        furAttrs = furDesc['Attributes']
        scalarAttrs = {k: v for k, v in furAttrs.items()
                       if k != 'mesh' and not NodesImport.isPluginLink(v)}
        # GeomHair 'scale' has no property-group field; fold it into the base length/thickness.
        furScale = scalarAttrs.pop('scale', 1.0)
        if furScale and furScale != 1.0:
            for k in ('length_base', 'thickness_base'):
                if k in scalarAttrs:
                    scalarAttrs[k] = scalarAttrs[k] * furScale
        # perArea is a density, not a distance, and normalizeUnits leaves it alone.
        if 'perArea' in scalarAttrs and self.vertexScale != 1.0 and int(scalarAttrs.get('distribution', 0)) == 1:
            scalarAttrs['perArea'] = scalarAttrs['perArea'] / (self.vertexScale ** 2)
        filtered = {'ID': 'GeomHair', 'Attributes': scalarAttrs}
        # The addon's GeomHair defaults are authoring values, not V-Ray's.
        NodesImport.pluginAttrsToPropGroup(filtered, furData.vray.GeomHair, scaleDistance=True,
                                            restoreVRayDefaults=True, stats=self.stats)
        if meshRef := furAttrs.get('mesh'):
            geomName = self.geomAliases.get(_refName(meshRef), _refName(meshRef))
            if baseObj := self._objectForGeometry(geomName, transform, hidden=True):
                furData.vray.GeomHair.object_selector.addListItem(self.context, baseObj)

        self.stats.extras += 1

        # The *_tex masks are 'linked_only', so only a node tree can hold them. Without one every
        # hair grows to the full length_base at full density.
        if texLinks := {k: v for k, v in furAttrs.items()
                        if k.endswith('_tex') and NodesImport.isPluginLink(v)}:
            self._attachFurTextures(furObj, texLinks)

        # _assignMaterials() has already run by the time _importExtras() executes.
        if mtlName:
            self._assignNodeMaterial(furObj, furData, mtlName)

    def _attachFurTextures(self, furObj: bpy.types.Object, texLinks: dict):
        """ Wire the GeomHair *_tex inputs into the fur object's node tree.

            addFurNodeTree copies the data property group (growth-mesh selector included) onto the
            output node, which fur_export reads instead once a tree exists. So call this only
            after that group is filled. """

        tree_defaults.addFurNodeTree(furObj)
        # Fur V-Ray trees are standalone node_groups datablocks; track them for rollback.
        furTree = furObj.vray.ntree
        self.ledger.track(furTree)

        furOutput = NodesUtils.getNodeByType(furTree, 'VRayNodeFurOutput')
        if furOutput is None:
            return

        importContext = self._importContext(furTree)
        socketByAttr = {a: s for s in furOutput.inputs
                        if (a := getattr(s, 'vray_attr', '')) in texLinks}
        for attrName, texRef in texLinks.items():
            if (texSock := socketByAttr.get(attrName)) is None:
                self.stats.skipped[f'GeomHair.{attrName} (no socket)'] += 1
                continue
            connectedPlugin, outputName = NodesImport.getPluginFromLink(importContext, texRef)
            if connectedPlugin is not None:
                NodesImport.createLinkedNode(importContext, texSock, outputName, connectedPlugin)

        _rearrangeAndCheck(furTree, furOutput)

    def _importDecal(self, decalDesc: dict):

        obj = createDecalObject(self.context, decalDesc['Name'])
        self.ledger.track(obj.data)
        addDecalNodeTree(obj)
        # Standalone node_groups datablock; track for rollback (see _attachGeometryWrapper).
        self.ledger.track(obj.vray.ntree)
        objTree = obj.vray.ntree

        importContext = self._importContext(objTree)

        decalOutputNode = NodesImport.createNode(importContext, decalDesc)

        generateDecalPreviewMesh(obj)
        if decalOutputNode is not None:
            _rearrangeAndCheck(objTree, decalOutputNode)

        obj.matrix_world = self.worldMatrix(decalDesc['Attributes'].get('transform'))

        if pathKey := _scenePathKey(decalDesc['Attributes']):
            self.objectsByPath.setdefault(pathKey, obj)

        # Reparent from the operator's default collection to the import collection.
        self._adoptObject(obj)
        self.stats.extras += 1

        # Assign the decal's material (imported alongside object materials).
        if mtlRef := decalDesc['Attributes'].get('material'):
            if bpyMtlName := self.importedMaterials.get(_refName(mtlRef)):
                obj.data.materials.append(bpy.data.materials[bpyMtlName])

    def _importClipper(self, clipperDesc: dict):
        """ Import a VRayClipper as an object carrying obj.vray.VRayClipper. Uses the
            referenced clip mesh when present, else an (empty) mesh for a transform-driven
            plane clipper. Resolves the clip material and the exclusion node list. """
        attrs = clipperDesc['Attributes']
        name = _sceneName(attrs, clipperDesc['Name'])

        mesh = None
        if clipRef := attrs.get('clip_mesh'):
            geomName = self.geomAliases.get(_refName(clipRef), _refName(clipRef))
            mesh = self.meshesByPlugin.get(geomName)
        useObjMesh = mesh is not None
        if mesh is None:
            mesh = bpy.data.meshes.new(name)
            self.ledger.track(mesh)
        obj = self._newObject(name, mesh)

        clipper = obj.vray.VRayClipper
        skip = {'material', 'clip_mesh', 'exclusion_nodes', 'transform', 'enabled',
                'use_obj_mesh', 'scene_name'}
        NodesImport.pluginAttrsToPropGroup(
            {'ID': 'VRayClipper', 'Attributes': {k: v for k, v in attrs.items() if k not in skip}},
            clipper, 'VRayClipper', animTargets=self.animTargets, source=clipperDesc)
        clipper.use_obj_mesh = useObjMesh

        obj.matrix_world = self.worldMatrix(attrs.get('transform'))

        # The cut material is the clipper object's own. A mesh clipper's cuts are shaded by the
        # clip mesh and V-Ray ignores 'material' there.
        if (not useObjMesh) and (mtlRef := attrs.get('material')):
            if bpyMtlName := self.importedMaterials.get(_refName(mtlRef)):
                mesh.materials.append(bpy.data.materials[bpyMtlName])

        exclusion = attrs.get('exclusion_nodes') or []
        for ref in (exclusion if isinstance(exclusion, (list, tuple)) else [exclusion]):
            if exObj := self.objectByNodePlugin.get(_refName(ref)):
                # Not addListItem(), which schedules a timer to clear the selector fields
                item = clipper.exclusion_selector.selectedItems.add()
                item.objectPtr = exObj
                item.name = exObj.name
                item.objectType = 'OBJECT'

        # The clipper_enabled toggle's update callback reads context.object (None on import).
        enabled = bool(attrs.get('enabled', True))
        clipper.enabled = enabled
        clipper['clipper_enabled'] = enabled
        obj.display_type = 'WIRE' if enabled else 'SOLID'

        self.objectByNodePlugin[clipperDesc['Name']] = obj
        if pathKey := _scenePathKey(attrs):
            self.objectsByPath.setdefault(pathKey, obj)
        self.stats.extras += 1

    def _applyAnimationRange(self):
        """ Set the scene frame range from SettingsOutput.anim_start/anim_end (in V-Ray time units) and
            the fps from SettingsUnitsInfo. """
        scene = self.context.scene

        if (settingsOutput := NodesImport.getPluginByType(self.vrsceneDict, 'SettingsOutput')) is not None:
            attrs = settingsOutput['Attributes']
            animStart, animEnd = attrs.get('anim_start'), attrs.get('anim_end')
            if isinstance(animStart, (int, float)) and isinstance(animEnd, (int, float)):
                first = round(animStart * self.framesPerSecond)
                last = round(animEnd * self.framesPerSecond)
                if last > first:
                    scene.frame_start, scene.frame_end = first, last

        if (unitsInfo := NodesImport.getPluginByType(self.vrsceneDict, 'SettingsUnitsInfo')) is not None:
            attrs = unitsInfo['Attributes']
            fps = attrs.get('frames_scale')
            if not isinstance(fps, (int, float)) or fps <= 0.0:
                secondsScale = attrs.get('seconds_scale')
                fps = (1.0 / secondsScale) if isinstance(secondsScale, (int, float)) and secondsScale > 0.0 else None
            if fps:
                scene.render.fps = max(1, round(fps))

    def _importAnimation(self):
        """ Key every recorded target, then the transforms. """
        if self.animTargets is None:
            return

        with _suppressAndLog("animation frame range"):
            self._applyAnimationRange()

        keysByAttr = anim_import.collectKeys(self.vrsceneDict)

        if self.animTargets.targets:
            yield from anim_import.writeAnimation(self.animTargets, keysByAttr,
                                                  self.framesPerSecond, ledger=self.ledger,
                                                  stats=self.stats, yieldEvery=_YIELD_EVERY)

        if self.transformSources:
            # Top-level objects only. The walk recurses into children.
            if self.collection is not None:
                roots = [obj for obj in self.collection.objects if obj.parent is None]
            else:
                roots = [obj for obj in self.ledger.byType(bpy.types.Object) if obj.parent is None]
            self.stats.animatedTransforms = anim_import.animateTransforms(
                roots, self.transformSources, keysByAttr, self.worldMatrix,
                self.framesPerSecond, self.staticWorlds,
                ledger=self.ledger, stats=self.stats, hasShear=_hasShear)
        yield 1.0

    def _importVfbLayers(self):
        """ Replace the scene's VFB correction layers with the file's, applying them to
            the running VFB (mirrors the load-post handling in events.py). """
        vfbDesc = self._firstReferencing('SettingsVFB')
        if vfbDesc is None:
            return
        layersJson = vfbDesc['Attributes'].get('vfb2_layers')
        if not layersJson:
            return


        self.context.scene.vray.SettingsVFB['vfb2_layers'] = layersJson
        VfbEventHandler.updateVfbLayers(layersJson, settingsAreFromScene=True)
        vray.setVfbLayers(layersJson)

    def _makePathsRelative(self):
        """ Rewrite the imported images' paths as '//' references to the .blend. Only
            absolute paths are touched, skipping packed images and missing-file placeholders. """
        for image in self.ledger.byType(bpy.types.Image):
            if not os.path.isabs(image.filepath):
                continue
            if (relative := path_utils.tryGetRelativePath(image.filepath)) is not None:
                image.filepath = relative

    def _firstReferencing(self, pluginType: str) -> dict | None:
        descs = self.byType.get(pluginType, [])
        if not descs:
            return None
        # All but the active camera's settings plugin are marked dont_affect_settings.
        return next((d for d in descs if not d['Attributes'].get('dont_affect_settings', False)), descs[0])

    def _outputAspect(self) -> float:
        """ width/height of the source SettingsOutput, for correctFovForAspectOnImport(). """
        if outputDesc := self._firstReferencing('SettingsOutput'):
            attrs = outputDesc['Attributes']
            if (w := attrs.get('img_width')) and (h := attrs.get('img_height')):
                return float(w) / float(h)
        return 1.0

    # --------------------------------------------------------------------------
    # Helpers
    # --------------------------------------------------------------------------

    def _nodeOwnersByGeometry(self) -> dict:
        """ geometry plugin name -> (material name or None, transform) off the wrapping Node, for geometry that
            gets no object of its own (GeomHair, the instancers).

            The first Node wins the transform, the first one naming a material wins the material: a Node may wrap
            the geometry without shading it. """
        if self._nodeOwners is None:
            self._nodeOwners = {}
            for nodeDesc in self.byType.get('Node', []):
                attrs = nodeDesc['Attributes']
                if not (geomRef := _refName(attrs.get('geometry'))):
                    continue
                mtlName = _refName(mtlRef) if (mtlRef := attrs.get('material')) else None
                if (owner := self._nodeOwners.get(geomRef)) is None:
                    self._nodeOwners[geomRef] = (mtlName, attrs.get('transform'))
                    self._nodeOwnerDescs.setdefault(geomRef, nodeDesc)
                elif mtlName and owner[0] is None:
                    self._nodeOwners[geomRef] = (mtlName, owner[1])
        return self._nodeOwners

    def _newObject(self, name: str, data = None) -> bpy.types.Object:
        """ Create an object in the import collection, registered for rollback. Track its data first, so rollback
            removes the object before the datablock. """
        return self._adoptObject(bpy.data.objects.new(name, data))

    def _adoptObject(self, obj: bpy.types.Object) -> bpy.types.Object:
        """ Move an object into the import collection and register it for rollback. The proxy and decal creators
            link into the context collection, hence the unlink. """
        for coll in list(obj.users_collection):
            coll.objects.unlink(obj)
        self.collection.objects.link(obj)
        self.ledger.track(obj)
        return obj

    def _importContext(self, ntree, objectResolver = None):
        """ The import context for one of the scene's node trees. """
        return NodesImport.ImportContext(ntree, self.vrsceneDict,
                                         objectResolver=objectResolver or self.resolveObjectPlugin,
                                         sceneBaseDir=self.importBaseDir,
                                         ledger=self.ledger,
                                         stats=self.stats,
                                         animTargets=self.animTargets,
                                         sourceMetersScale=self.metersScale)

    def _setMeshWorldMatrix(self, obj: bpy.types.Object, m: mathutils.Matrix,
                           pivot: mathutils.Vector | None = None):
        """ Assign a world matrix, baking any shear and pivot offset into the mesh.

            A Blender object holds loc/rot/scale, so assigning a sheared matrix silently drops the shear and leaves
            the object rotated wrong. Fold the whole 3x3 into the geometry and keep the translation.

            `pivot` is Node.pivot_offset in Blender units. It moves the object origin and the mesh
            moves back by the same amount, so the render does not change.

            Returns T(pivot) when the origin was moved, else None. """
        basis = m.to_3x3()
        isMesh = isinstance(obj.data, bpy.types.Mesh)
        sheared = isMesh and _hasShear(basis)
        hasPivot = pivot is not None and pivot.length > 1e-9

        if hasPivot:
            m = m @ mathutils.Matrix.Translation(pivot)
            if not isMesh:
                # No mesh to move back, so leave the origin.
                m = m @ mathutils.Matrix.Translation(-pivot)
                hasPivot = False

        if sheared or hasPivot:
            mesh = obj.data
            if mesh.users > 1:
                obj.data = mesh = mesh.copy()
                self.ledger.track(mesh)
            meshBake = basis.to_4x4() if sheared else mathutils.Matrix.Identity(4)
            if hasPivot:
                meshBake = meshBake @ mathutils.Matrix.Translation(-pivot)
            mesh.transform(meshBake)
            # Shear does not preserve angles; normals need the inverse transpose.
            if sheared and (attr := mesh.attributes.get('custom_normal')) is not None:
                normals = np.empty(len(attr.data) * 3, dtype=np.float32)
                attr.data.foreach_get('vector', normals)
                normalMatrix = np.array(basis.inverted_safe().transposed()).astype(np.float32)
                normals = normals.reshape(-1, 3) @ normalMatrix.T
                lengths = np.linalg.norm(normals, axis=1, keepdims=True)
                normals = normals / np.where(lengths == 0.0, 1.0, lengths)
                attr.data.foreach_set('vector', normals.ravel())
            if sheared:
                m = mathutils.Matrix.Translation(m.translation)
        obj.matrix_world = m
        return mathutils.Matrix.Translation(pivot) if hasPivot else None

    def worldMatrix(self, tmValue):
        """ Convert a world-space TRANSFORM value to a Blender matrix, scaling the
            translation from source scene units to Blender units and applying the
            coordinate-system adjustment (identity for Z-up right-handed sources).
        """
        if tmValue is None:
            return self.coordAdjust.copy() if self.coordAdjust is not None else mathutils.Matrix()

        m = attribute_utils.attrValueToMatrix(tmValue, applyScale=False)
        m.translation = m.translation * self.vertexScale
        if self.coordAdjust is not None:
            m = self.coordAdjust @ m
        return m

    def _buildCoordAdjust(self, unitsInfo: dict | None):
        """ Build the matrix that maps the source coordinate system to Blender's Z-up
            right-handed one, from SettingsUnitsInfo.scene_upDir + coordinate_system
            (0=right-handed, 1=left-handed). Returns None (no adjustment) for a Z-up
            right-handed source. Mirrors V-Ray for C4D makeCoordinateSystemAdjustmentMatrix
            but targets Blender's +Z up instead of C4D's +Y up. """
        blenderUp = mathutils.Vector((0.0, 0.0, 1.0))
        up = blenderUp.copy()
        coordSystem = 0
        if unitsInfo is not None:
            attrs = unitsInfo['Attributes']
            rawUp = attrs.get('scene_upDir')
            if isinstance(rawUp, (list, tuple)) and len(rawUp) >= 3:
                up = mathutils.Vector(rawUp[:3])
                if up.length > 1e-9:
                    up.normalize()
                else:
                    up = blenderUp.copy()
            coordSystem = int(attrs.get('coordinate_system', 0) or 0)

        # A Z-up right-handed source (Max/SketchUp/Z-up Maya/Blender) needs no adjustment.
        if (up - blenderUp).length < 1e-6 and coordSystem == 0:
            return None

        # Rotation mapping the source up-axis onto Blender's +Z.
        adjust = mathutils.Matrix.Identity(4)
        if (up - blenderUp).length >= 1e-6:
            axis = up.cross(blenderUp)
            if axis.length < 1e-9:
                # Anti-parallel to +Z, so rotate 180 degrees about X.
                adjust = mathutils.Matrix.Rotation(math.pi, 4, 'X')
            else:
                adjust = mathutils.Matrix.Rotation(up.angle(blenderUp), 4, axis.normalized())

        # A left-handed source needs its Z basis column negated to become right-handed.
        if coordSystem == 1:
            adjust = adjust @ mathutils.Matrix.Diagonal((1.0, 1.0, -1.0, 1.0))

        return adjust

    def _recordError(self, pluginName: str, phase: str, e: Exception | str):
        """ Log a failure and add it to the stats. A plain string is a validation failure with no exception behind
            it, so it gets no traceback. """
        if isinstance(e, str):
            debug.printError(f"scene_import.{phase}: '{pluginName}': {e}")
        else:
            debug.printExceptionInfo(e, f"scene_import.{phase}: '{pluginName}'")
        self.stats.errors.append((pluginName, phase, str(e)))


def _descWithout(pluginId: str, attrs: dict, skip) -> dict:
    """ A descriptor carrying 'attrs' minus 'skip', for a pluginAttrsToPropGroup fill whose caller handles the
        skipped attributes itself. """
    return {'ID': pluginId, 'Attributes': {k: v for k, v in attrs.items() if k not in skip}}


def _isToonOutlinesVolume(effDesc: dict) -> bool:
    """ A VolumeVRayToon that exists only to switch on the per-material outlines.

        toonMaterialOnly=2 ('apply only to objects with a BRDFToonOverride material') is an internal flag, written
        by this addon and by V-Ray for C4D on the singleton they emit whenever a material wires a BRDFToonOverride,
        never by hand. The line parameters live on those materials, so importing it would add an effect node the
        user never made, on top of the one the exporter regenerates from the materials anyway. """
    if effDesc['ID'] != 'VolumeVRayToon':
        return False
    # An ENUM can arrive from the parser as an int, a float or a string.
    return str(effDesc['Attributes'].get('toonMaterialOnly', '')) in ('2', '2.0')


def _renderChannelAliasVariants() -> dict:
    """ {alias -> VRayNode bl_idname} for the RenderChannelColor/RenderChannelGlossiness
        variants, mirroring nodes/nodes.py _createAdditionalRenderChannelNodes'
        class-name derivation. """
    result = {}
    for desc in customRenderChannelNodesDesc:
        params = desc.get('params', {})
        if (alias := params.get('alias')) is not None and (name := params.get('name')):
            result[int(alias)] = f"VRayNodeRenderChannel{name.replace(' ', '').replace('-', '')}"
    return result


def _derivedChannelDesc(chDesc: dict) -> dict:
    """ Retype a plain RenderChannelColor whose alias names one of the plugins derived
        from it. Only the plugin type is rewritten; the alias round-trips as it came in. """
    if chDesc['ID'] != 'RenderChannelColor':
        return chDesc

    alias = chDesc['Attributes'].get('alias')
    if alias is None or (pluginType := _LEGACY_COLOR_ALIAS_TO_PLUGIN.get(int(alias))) is None:
        return chDesc

    return chDesc | {'ID': pluginType}


def _hasShear(basis: mathutils.Matrix) -> bool:
    """ True when the basis vectors are not mutually perpendicular. loc/rot/scale cannot hold it. """
    basisColumns = [basis.col[i] for i in range(3)]
    if any(c.length < 1e-9 for c in basisColumns):
        return False
    return any(abs(basisColumns[i].normalized().dot(basisColumns[j].normalized())) > 1e-4
               for i, j in ((0, 1), (0, 2), (1, 2)))


def _fovKeyToLens(camData: bpy.types.Camera, aspect: float):
    """ Returns a converter from a V-Ray fov key to camData.lens. """
    sensor = camData.sensor_height if camData.sensor_fit == 'VERTICAL' else camData.sensor_width
    return lambda fov: 0.5 * sensor / math.tan(0.5 * camera_utils.correctFovForAspectOnImport(fov, aspect))


def _rearrangeAndCheck(ntree: bpy.types.NodeTree, outputNode: bpy.types.Node):
    """ Tidy a freshly-built node tree around its output node. """
    NodesTools.arrangeImportedTree(ntree, outputNode)
    NodesTools.selectOnlyNode(ntree, outputNode)


# V-Ray user-attribute value_type enum on VRayUserAttributeItem: 0=Int, 1=Float, 2=Color, 3=String.
_USER_ATTR_VALUE_FIELD = {'0': 'value_int', '1': 'value_float', '2': 'value_color', '3': 'value_string'}


def _decodedUserAttributes(decoded):
    """ Yield (name, valueType, value) from the server-decoded user_attributes_decoded list
        ([name, valueType, value] entries). """
    if not isinstance(decoded, (list, tuple)):
        return
    for entry in decoded:
        if isinstance(entry, (list, tuple)) and len(entry) >= 3 and isinstance(entry[0], str):
            yield entry[0], str(int(entry[1])), entry[2]


def _addUserAttribute(userAttrs, name: str, valueType: str, value):
    """ Append one item to a VRayUserAttributes property group. """
    if valueType not in _USER_ATTR_VALUE_FIELD:
        return
    item = userAttrs.user_attributes.add()
    item.name = name
    item.value_type = valueType
    # parseUserAttributeString types the value by guesswork, so it may not fit the field.
    # Leave the item at its default rather than dropping the attributes after it.
    with _suppressAndLog(f"user attribute '{name}'"):
        if valueType == '2':
            item.value_color = tuple(value)[:3]
        elif valueType == '0':
            item.value_int = int(value)
        elif valueType == '1':
            item.value_float = float(value)
        else:
            item.value_string = str(value)


def _scenePathKey(attrs: dict) -> str | None:
    """ The hierarchy path from scene_name element [1] ("scene/A/B/leaf"), with the
        "scene/" prefix stripped and empty segments dropped ("A/B/leaf"). The last
        segment is the object itself; the preceding ones are its ancestor groups.
        Returns None when there is no path (materials, RenderView, single-element). """
    sceneName = attrs.get('scene_name')
    if not (isinstance(sceneName, (list, tuple)) and len(sceneName) > 1):
        return None
    path = sceneName[1]
    if not (isinstance(path, str) and path.startswith('scene/')):
        return None
    parts = [p for p in path[len('scene/'):].split('/') if p]
    return '/'.join(parts) if parts else None


def _saveSceneSettings(scene: bpy.types.Scene) -> list:
    """ (owner, attr, value) for the scene values an import can change, so that rollback() can
        put them back: the frame range and fps from the animation phase, and the resolution and
        every scene.vray settings group from the settings phase.
    """
    owners = [(scene, ('frame_start', 'frame_end')),
              (scene.render, ('fps', 'fps_base', 'resolution_x', 'resolution_y'))]
    for prop in scene.vray.bl_rna.properties:
        if prop.type == 'POINTER' and isinstance(propGroup := getattr(scene.vray, prop.identifier), bpy.types.PropertyGroup):
            # Some groups list properties in bl_rna that they do not expose, hence the hasattr().
            owners.append((propGroup, [p.identifier for p in propGroup.bl_rna.properties
                                       if not p.is_readonly and p.type not in ('POINTER', 'COLLECTION')
                                       and hasattr(propGroup, p.identifier)]))
    return [(owner, attr, _toPlainValue(getattr(owner, attr))) for owner, attrs in owners for attr in attrs]


def _toPlainValue(value):
    # An array property reads as a view onto the live data, so it would follow later changes.
    return tuple(value) if isinstance(value, bpy.types.bpy_prop_array) else value


class _suppressAndLog:
    """ Context manager that warns about an exception instead of propagating it, for the
        steps whose failure costs one detail rather than the import. """
    def __init__(self, what: str):
        self.what = what

    def __enter__(self):
        return self

    def __exit__(self, excType, excValue, excTraceback):
        if excValue is not None:
            debug.printWarning(f"scene_import {self.what}: {excValue}")
        return True
