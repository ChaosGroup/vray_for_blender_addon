# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import os
import binascii
import math
import struct

import bpy

from vray_blender.plugins.BRDF import BRDFScanned
from vray_blender.nodes.tools import calculateTreeBounds, deselectNodes, rearrangeTree, rearrangeTreeRecursive
from vray_blender.lib import attribute_utils, attribute_types
from vray_blender.lib import path_utils, image_utils
from vray_blender.plugins import getPluginModule, findPluginModule, getPluginAttr
from vray_blender.plugins.skipped_plugins import SKIPPED_PLUGINS, THIRD_PARTY_INTEGRATION_PLUGINS
from vray_blender.nodes.curves_node import getCurvesNode, encodeMapping
from vray_blender.nodes.importing import generic_node
from vray_blender.nodes import utils as NodeUtils
from vray_blender.nodes.sockets import addInput, getHiddenInput, moveExtendSocketToBottom
from vray_blender import debug
from vray_blender.exporting.tools import isColorSocket, getVRayBaseSockType, getInputSocketByName, getInputSocketByAttr, getOutputSocketByAttr
from vray_blender.exporting.node_exporters.uvw_node_export import UVWGenRandomizerModes
from vray_blender.lib.names import syncObjectUniqueName
from vray_blender.lib.lib_utils import cleanCosmosTextureName
from vray_blender.vray_tools import import_common
# Re-exported for the existing NodesImport.* call sites (import_file, scene_import, ...).
from vray_blender.vray_tools.import_common import (IndexedVrsceneDict, getPluginByName,
                                                   getPluginByType)

from numpy import allclose
from mathutils import Matrix, Color, Vector


# Output socket renames for plugins imported as a different plugin type.
_REMAPPED_OUTPUT_SOCKETS = {
    'TexBezierCurve': {
        'Color' : 'Out Color'
    },
    'TexBezierCurveColor': {
        'Color' : 'Out Color'
    },
    # TexLayered imports as a TexLayeredMax node; None is its unnamed default output.
    'TexLayered': {
        None : 'Color'
    }
}

# Plugins that select their single output with an attribute, imported as a plugin that
# exposes one socket per choice instead: attr name -> {attr value: target output attr}.
# A value with no entry has no equivalent socket and is left to the creator to handle.
_VALUE_SELECTED_OUTPUTS = {
    # Imported as TexAColorOp, whose channel outputs are all 'Color A * Mult A'.
    'TexAColorChannel': ('mode', {
        '0': 'red',
        '1': 'green',
        '2': 'blue',
        '3': 'alpha',
        '4': 'intensity',
    }),
    # Imported as TexHairSampler. Max only ever writes 0,1,2,3,6,7 (it remaps its own
    # enum on export, 4->6 and 5->7); of those only 3 (hair opacity) has no socket,
    # because TexHairSampler excludes hair_transparency/hair_incandescence.
    'TexMaxHairInfo': ('output', {
        '0': 'distance_along_strand',
        '1': 'random_by_strand',
        '2': 'hair_color',
        '6': 'distance_along_strand_absolute',
        '7': 'position_across_strand',
    }),
}


def getSelectedOutputAttr(pluginDesc: dict):
    """ The target output socket attr for a plugin whose output is selected by an attribute
        value, or None when the plugin is not remapped this way or the value has no match. """
    if (entry := _VALUE_SELECTED_OUTPUTS.get(pluginDesc['ID'])) is None:
        return None
    attrName, outputsByValue = entry
    return outputsByValue.get(str(pluginDesc['Attributes'].get(attrName, '0')))


# SKIPPED_PLUGINS, minus the ones for which a node is still created.
_UNSUPPORTED_PLUGINS = set(SKIPPED_PLUGINS).difference({
    'TexBitmap', 'TexBezierCurve', 'TexBezierCurveColor'
})


# Shader-graph plugins that must never fall back to a generic node: they are consumed before a
# node is created, a node of their own is built for them by other code, or V-Ray does not mean
# them to be authored at all (the 'not meant to be used' group of SKIPPED_PLUGINS - importing
# those would put a node in the tree for something that is an implementation detail).
_NO_GENERIC_NODE = {
    'MtlSingleBRDF',        # unwrapped by _processNonNodePlugins
    'TexBitmap',            # imported as the meta image texture node
    'BitmapBuffer',
    'TexOSL',               # hand-written node classes (MANUALLY_CREATED_PLUGINS)
    'MtlOSL',
    'BSDFPointParticle',    # not meant to be used
    'MtlStreakFade',
}


def _importsAsGenericNode(pluginType: str):
    """ Whether a plugin the addon generates no node class for is still imported, as a
        generic plugin node carrying whatever parameters the .vrscene set.
    """
    return (pluginType not in _NO_GENERIC_NODE) and generic_node.isImportableAsGenericNode(pluginType)


class ImportContext:
    def __init__(self, nodeTree: bpy.types.NodeTree, vrsceneDict: dict, isConversion = False, locationsMap = None,
                 texNamePrefix = "", texNameOwners = None, objectResolver = None,
                 sceneBaseDir = "", stats = None, ledger = None):
        self.isConversion = isConversion
        # SceneImportStats of the running scene import; None for the cosmos/material-only paths.
        self.stats = stats
        # Rollback ledger of the running scene import; None for the cosmos/material-only paths.
        self.ledger = ledger
        self.nodeTree = nodeTree
        self.vrsceneDict = vrsceneDict
        self.locationsMap = locationsMap
        # Prefix for the imported image names (the Cosmos asset name); '' keeps the file stems.
        self.texNamePrefix = texNamePrefix
        # Cleaned plugin names of the asset's parts; a file starting with one is left unprefixed.
        self.texNameOwners = texNameOwners or []
        # objectResolver(pluginName, linkType) -> Object | None; None leaves object-referencing
        # attrs (TexDistance.objects, LightMesh.geometry) unresolved.
        self.objectResolver = objectResolver
        # Plugins whose node is currently being built; createNode uses it to break cycles.
        self.creatingPlugins: set[str] = set()
        # Plugin name -> the node created for it.
        self.nodeByPlugin: dict[str, bpy.types.Node] = {}
        # Node -> the group instance path (tuple of (instanceName, groupDefName)) it belongs to.
        self.groupPathByNode = {}

        # Source scene directory for resolving asset paths (IES, scanned materials, ...).
        self.sceneBaseDir = sceneBaseDir or import_common.getImportDir(vrsceneDict)

        # Memoized resolvePath results.
        self._assetPathCache: dict[str, str] = {}

        # Memoized source UV set name -> map channel index (see uvChannelId).
        self._uvChannelIds: dict[str, int] | None = None

    def uvChannelId(self, uvSetName: str) -> int | None:
        """ The map channel index the source gave a named UV set, or None if no mesh names it. """
        if self._uvChannelIds is None:
            self._uvChannelIds = {}
            typeIndex = getattr(self.vrsceneDict, 'typeIndex', None) or {}
            for meshDesc in typeIndex.get('GeomStaticMesh', []):
                attrs = meshDesc['Attributes']
                # Parallel lists, one name per channel.
                for name, channel in zip(attrs.get('map_channels_names') or [],
                                         attrs.get('map_channels') or []):
                    if name:
                        self._uvChannelIds.setdefault(name, int(channel[0]))

        return self._uvChannelIds.get(uvSetName)

    def resolvePath(self, path):
        if path in self._assetPathCache:
            return self._assetPathCache[path]
        resolved = import_common.resolveAssetPath(path, self.sceneBaseDir, self.locationsMap)
        self._assetPathCache[path] = resolved
        return resolved

    def getImageName(self, imageFilepath: str):
        """ The name for the image datablock loaded from 'imageFilepath'. """
        if not self.texNamePrefix:
            return bpy.path.display_name_from_filepath(imageFilepath)

        name = cleanCosmosTextureName(imageFilepath)
        lowerName = name.lower()

        # The whole first token has to match.
        def ownedBy(owner: str):
            owner = owner.lower()
            return lowerName == owner or lowerName.startswith(owner + '_')

        if any(ownedBy(owner) for owner in self.texNameOwners if owner):
            return name

        return f"{self.texNamePrefix} {name}"


# 3ds Max standard light types, aliased onto the addon's own light plugins.
_MAX_LIGHT_PLUGINS = {
    'LightOmniMax'    : 'LightOmni',
    'LightSpotMax'    : 'LightSpot',
    'LightDirectMax'  : 'MayaLightDirect',
    'LightIESMax'     : 'LightIES',
    'LightAmbientMax' : 'LightAmbient',
    'LightDirect'     : 'MayaLightDirect',
}

# Attenuation params Max renamed.
_MAX_LIGHT_ATTRS = {
    'near_attenuation'       : 'decay_near_on',
    'near_attenuation_start' : 'decay_near_start',
    'near_attenuation_end'   : 'decay_near_end',
    'far_attenuation'        : 'decay_far_on',
    'far_attenuation_start'  : 'decay_far_start',
    'far_attenuation_end'    : 'decay_far_end',
}


def _aliasMaxLight(pluginDesc: dict, aliasType: str):
    """ Rewrite a Max standard light onto the addon's equivalent light plugin. """
    attrs = pluginDesc['Attributes']

    if pluginDesc['ID'] == 'LightSpotMax':
        # 'fallsize' is the whole cone (LightSpot.coneAngle); decay_type: 0 none, 1 linear, 2 square.
        if (fallsize := attrs.pop('fallsize', None)) is not None:
            attrs['coneAngle'] = fallsize
            if (hotspot := attrs.pop('hotspot', None)) is not None:
                attrs['penumbraAngle'] = float(hotspot) - float(fallsize)
        if (decayType := attrs.pop('decay_type', None)) is not None:
            attrs['decay'] = float(decayType)

    for maxAttr, vrayAttr in _MAX_LIGHT_ATTRS.items():
        if (value := attrs.pop(maxAttr, None)) is not None:
            attrs.setdefault(vrayAttr, value)

    pluginDesc['ID'] = aliasType


def fixPluginParams(vrsceneDict: dict, forceDefaultUVChannel: bool):
    """ Fix any plugin parameters that need special handling.

    forceDefaultUVChannel(bool) : True if the uvw_channel indices should be overwritten to -1.
    """
    addedPlugins = []   # added after the loop, so it does not walk into them

    for pluginDesc in vrsceneDict:
        pluginType = pluginDesc['ID']
        attrs = pluginDesc.get('Attributes')
        if attrs is None:
            # Non-plugin entries (the synthetic 'ImportSettings' record) carry no attributes.
            continue

        # Keep before the branches below and anything else keyed on the plugin type.
        if (aliasType := _MAX_LIGHT_PLUGINS.get(pluginType)) is not None:
            _aliasMaxLight(pluginDesc, aliasType)
            pluginType = aliasType

        if forceDefaultUVChannel and pluginType == 'UVWGenChannel':
            # -1 is the default-channel sentinel.
            attrs['uvw_channel'] = -1

        elif pluginType == 'BRDFVRayMtl':
            if (value := attrs.get('gtr_energy_compensation', None)) is not None:
                if type(value) is bool:
                    # Upgrade from the older version, set to the new default
                    attrs['gtr_energy_compensation'] = '2'

            if (opacity := attrs.get('opacity', None)) is not None:
                # The node exposes only 'opacity_color'; opacity_source 0 means 'opacity' is
                # the active source, 1 means 'opacity_color' is.
                if int(attrs.get('opacity_source', 0)) == 0:
                    if type(opacity) is str:
                        attrs['opacity_color'] = opacity
                    elif isinstance(opacity, (int, float)):
                        attrs['opacity_color'] = Color((opacity, opacity, opacity))
                    attrs['opacity_source'] = 1 # opacity_color is the single source
                del attrs['opacity']

            if fogMult := attrs.get("fog_mult", None):
                # Mirrors the exporter's fog_mult inversion.
                fogDepthValue = 1.0/fogMult if fogMult > 1e-6 else 0.0
                attrs['fog_mult'] = fogDepthValue

        elif pluginType == 'BRDFBump':
            if (value := attrs.get('bump_tex', None)) is not None:
                attrs['bump_tex_color'] = attrs['bump_tex']
                del attrs['bump_tex']

            if (value := attrs.get('bump_tex_mult', None)) is not None:
                attrs['bump_tex_mult_tex'] = attrs['bump_tex_mult']
                del attrs['bump_tex_mult']

            if 'bump_object_space' not in attrs:
                attrs['bump_object_space'] = False

        elif pluginType == 'BRDFLight':
            # The node shows the 'transparency' attribute as 'Opacity'. The exporter writes
            # 1-opacity and wraps a linked opacity in a TexInvert; both are reversed here.
            transp = attrs.get('transparency', (0.0, 0.0, 0.0, 0.0))
            if _isPluginLink(transp):
                invDesc = getPluginByName(vrsceneDict, _parsePluginLink(transp)[0])
                if invDesc is not None and invDesc['ID'] == 'TexInvert':
                    attrs['transparency'] = invDesc['Attributes'].get('texture', transp)
                else:
                    # A foreign source links the map itself, so opacity is that map inverted.
                    # Give it the TexInvert the branch above unwraps, rather than V-Ray's own
                    # transparency_invert - the .vrscene loader honours that flag but the live
                    # AppSDK render ignores it, which would leave the viewport disagreeing
                    # with the exported scene.
                    invertName = f"{pluginDesc['Name']}@opacityInvert"
                    addedPlugins.append({'ID': 'TexInvert', 'Name': invertName,
                                         'Attributes': {'texture': transp}})
                    attrs['transparency'] = invertName
            elif hasattr(transp, '__len__'):
                attrs['transparency'] = [1.0 - c for c in transp]
            elif isinstance(transp, (int, float)):
                attrs['transparency'] = 1.0 - transp

        elif pluginType == 'BitmapBuffer':
            # Restore V-Ray's own defaults for the attributes the node defaults differently.
            colorSpace = attrs.get('rgb_color_space')
            if not colorSpace:
                attrs['rgb_color_space'] = 'raw'    # V-Ray's default for an empty space
            elif isinstance(colorSpace, str):
                # The dropdown models only raw/sRGB/ACEScg.
                csDesc = attribute_utils.getAttrDesc(getPluginModule('BitmapBuffer'), 'rgb_color_space')
                if csDesc and not attribute_utils.valueInEnumItems(csDesc, colorSpace):
                    attrs['rgb_color_space'] = 'lin_srgb'

            # '0' (Linear) is a valid source value.
            if attrs.get('transfer_function') is None:
                attrs['transfer_function'] = '1'    # gamma corrected

    for addedDesc in addedPlugins:
        # append(), not extend(): IndexedVrsceneDict updates its indexes only there.
        vrsceneDict.append(addedDesc)

    _normalizeMapSlots(vrsceneDict)
    _resolveUVWGenSelectFallbacks(vrsceneDict)
    _scaleBerconWorldSizes(vrsceneDict)


# UVWGenBercon.map modes whose coordinates are in scene units. '0' (Explicit Map Channel 2D)
# and '4' (Screen) are dimensionless; '1' (Explicit Map Channel Real World), '2' (Object XYZ)
# and '3' (World XYZ) are metric. Enum per the Maya/Houdini plugin descriptions.
_BERCON_SPATIAL_MAPS = (1, 2, 3)

# The feature size each Bercon texture measures in its mapping space.
_BERCON_SIZE_ATTRS = {
    'TexBerconNoise': ('noise_size',),
    'TexBerconTile':  ('tile_size',),
    'TexBerconWood':  ('wood_size',),
}

# UVWGenBercon's own translation. 'size_x/y/z' are NOT in this list: they are dimensionless
# scale multipliers, and scaling them shifts the mapping (verified by A/B render against
# the std_tests plugin_textures references).
_BERCON_UVWGEN_ATTRS = ('offset_x', 'offset_y', 'offset_z',
                        'offset_x2', 'offset_y2', 'offset_z2')


def _scaleBerconWorldSizes(vrsceneDict: dict):
    """ Convert the Bercon feature sizes and UVWGenBercon offsets from source scene units to
        Blender scene units.

        They are plain FLOATs in the plugin descriptions, so neither normalizeUnits nor
        _scaleAttrValue touches them - and they cannot simply be marked ui.quantityType,
        because whether they are a length at all depends on the paired UVWGenBercon: only its
        metric 'map' modes put them in scene units, the UV and screen modes leave them
        dimensionless. Without this, a centimetre-unit source scene imports with the noise
        100x too coarse (it reads as a flat gradient, or a single giant tile).
    """
    unitsInfo = getPluginByType(vrsceneDict, 'SettingsUnitsInfo')
    metersScale = float(unitsInfo['Attributes'].get('meters_scale', 1.0)) if unitsInfo else 1.0

    def scaled(value):
        return attribute_utils.scaleToSceneLengthUnit(value * metersScale, 'meters')

    def isSpatial(genName: str):
        genDesc = getPluginByName(vrsceneDict, genName)
        if genDesc is None or genDesc['ID'] != 'UVWGenBercon':
            return False
        return int(float(genDesc['Attributes'].get('map', 0))) in _BERCON_SPATIAL_MAPS

    for pluginDesc in vrsceneDict:
        attrs = pluginDesc.get('Attributes')
        if attrs is None:
            continue

        if (sizeAttrs := _BERCON_SIZE_ATTRS.get(pluginDesc['ID'])) is not None:
            gen = attrs.get('uvwgen')
            if not (_isPluginLink(gen) and isSpatial(_parsePluginLink(gen)[0])):
                continue
        elif pluginDesc['ID'] == 'UVWGenBercon':
            sizeAttrs = _BERCON_UVWGEN_ATTRS
            if int(float(attrs.get('map', 0))) not in _BERCON_SPATIAL_MAPS:
                continue
        else:
            continue

        for attrName in sizeAttrs:
            value = attrs.get(attrName)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                attrs[attrName] = scaled(value)


def _resolveUVWGenSelectFallbacks(vrsceneDict: dict):
    """ Write each UVWGenSelect's 'uvwgen' fallback from the (uvwgen_id, face_material_id) ->
        UVWGen mapping the MtlUVWSelect wrappers carry. """
    # (alias, faceId) -> UVWGen link, plus a face-0/first fallback per alias.
    byAliasFace: dict[tuple[str, int], str] = {}
    aliasToGen: dict[str, str] = {}
    for pluginDesc in vrsceneDict:
        if pluginDesc['ID'] != 'MtlUVWSelect':
            continue
        attrs = pluginDesc.get('Attributes', {})
        ids, gens = attrs.get('uvwgen_ids'), attrs.get('uvwgens')
        if not isinstance(ids, list) or not isinstance(gens, list):
            continue
        faceIds = attrs.get('face_material_ids')
        for i, alias in enumerate(ids):
            if i >= len(gens) or not alias or not _isPluginLink(gens[i]):
                continue
            faceId = int(faceIds[i]) if isinstance(faceIds, list) and i < len(faceIds) else 0
            byAliasFace[(alias, faceId)] = gens[i]
            if alias not in aliasToGen or faceId == 0:
                aliasToGen[alias] = gens[i]

    if not byAliasFace:
        return

    # Trace which face ID each UVWGenSelect resolves under, via the MtlMulti sub-material
    # that owns it. A sub-material listed for both the unselected id 0 and a real selection
    # id takes the selection.
    index = {pd['Name']: pd for pd in vrsceneDict if 'Name' in pd}

    def collectSelects(rootName: str) -> set[str]:
        found, stack, seen = set(), [rootName], set()
        while stack:
            name = stack.pop()
            if name in seen:
                continue
            seen.add(name)
            pd = index.get(name)
            if pd is None:
                continue
            if pd['ID'] == 'UVWGenSelect':
                found.add(name)
            for value in pd.get('Attributes', {}).values():
                for item in (value if isinstance(value, list) else (value,)):
                    if _isPluginLink(item):
                        stack.append(item.partition("::")[0])
        return found

    ownerFace: dict[str, int] = {}
    for pluginDesc in vrsceneDict:
        if pluginDesc['ID'] != 'MtlUVWSelect':
            continue
        baseRef = pluginDesc['Attributes'].get('base_mtl')
        base = index.get(baseRef.partition("::")[0]) if _isPluginLink(baseRef) else None
        if base is None or base['ID'] != 'MtlMulti':
            continue
        mtls = base['Attributes'].get('mtls_list', [])
        idList = base['Attributes'].get('ids_list', [])
        for i, subRef in enumerate(mtls):
            if not _isPluginLink(subRef):
                continue
            faceId = int(idList[i]) if i < len(idList) else 0
            for usName in collectSelects(subRef.partition("::")[0]):
                prev = ownerFace.get(usName)
                if prev is None or (prev == 0 and faceId != 0):
                    ownerFace[usName] = faceId

    for pluginDesc in vrsceneDict:
        if pluginDesc['ID'] != 'UVWGenSelect':
            continue
        attrs = pluginDesc['Attributes']
        if not (alias := attrs.get('uvwgen_id')):
            continue
        faceId = ownerFace.get(pluginDesc['Name'], 0)
        if (gen := byAliasFace.get((alias, faceId)) or aliasToGen.get(alias)) is not None:
            attrs['uvwgen'] = gen


def _getDefaultOutputSocketName(pluginType: str):
    """ Returns the name of the default output socket for this plugin type. """

    pluginModule = findPluginModule(pluginType)
    if pluginModule is None:
        # V-Ray ships no description for this plugin. It still imports, as a generic node whose
        # output is named for its category - and the name is needed here, because callers assert
        # on it (_processNonNodePlugins) and would otherwise drop the whole attribute.
        return generic_node.defaultOutputName(pluginType)

    # Get the Blender name of sockets redefined in Node.output_sockets section of the plugin module description
    if outputSockets := pluginModule.Node.get('output_sockets'):
        # Deal with plugins for which the single output is defined explicitly as a property, e.g. TexLuminance
        if len(outputSockets) == 1 and outputSockets[0]['name'] != '_default_':
            outSockDesc = outputSockets[0]
            outSockAttrDesc = getPluginAttr(pluginModule, outSockDesc['name'])
            return outSockDesc.get('label', attribute_utils.getAttrDisplayName(outSockAttrDesc))
        
        # Deal with plugins for which the default output has been renamed
        if defaultOutput := next((o for o in outputSockets if o['name'] == '_default_'), None):
            if 'label' in defaultOutput:
                return defaultOutput['label']

    # No explicit name has been found, return the generic name for the plugin type
    if pluginModule.TYPE == 'MATERIAL':
        return "Material"
    elif pluginModule.TYPE == 'UVWGEN':
        return "Mapping"
    elif pluginModule.TYPE == 'BRDF':
        return "BRDF"
    elif pluginModule.TYPE == 'GEOMETRY':
        return "Geometry"
    elif pluginModule.TYPE == 'EFFECT':
        return "Output"
    elif pluginModule.TYPE == 'RENDERCHANNEL':
        return "Channel"
    elif pluginModule.TYPE == 'TEXTURE':
        return "Color"



def getInputSocketNameByAttr(pluginModule, attrName: str):
    """ Return the Blender name of an input socket given its V-Ray attribute name. """
    if attrDesc := attribute_utils.getAttrDesc(pluginModule, attrName):
        return attrDesc.get('name', attribute_utils.getAttrDisplayName(attrDesc))

    return None


def _getOutputSocketNameByAttr(pluginType: str, outputAttrName: str):
    """ Return the Blender name of an output socket given its V-Ray attribute name """
    pluginModule = findPluginModule(pluginType)
    if pluginModule is None:
        return None

    if outputSockets := pluginModule.Node.get('output_sockets'):
        if outputSocketDesc := next((o for o in outputSockets if o['name'] == outputAttrName), None):
            if label := outputSocketDesc.get('label'):
                return label
            else:
                return attribute_utils.formatAttributeName(outputSocketDesc['name'])

    else:
        pluginOutputParams = [a for a in pluginModule.Parameters if a['type'] in attribute_types.NodeOutputTypes]
        if outputSocketDesc := next((o for o in pluginOutputParams if o['attr'] == outputAttrName), None):
            return attribute_utils.formatAttributeName(outputSocketDesc['attr'])

    debug.printWarning(f"Plugin {pluginType} does not have output attribute {outputAttrName}; using the default output")
    return _getDefaultOutputSocketName(pluginType)


def _resolveTileTokens(path: str):
    """ Substitute the first concrete tile index for a <UDIM>/<UVTILE> token in a path. """
    return path.replace('<UDIM>', '1001').replace('<UVTILE>', 'u1_v1')


# Maps for images [path->name] instead of iterating over bpy.data.images every time.
_pathKeyCache: dict[str, str] = {}
_baseNameCache: dict[str, str] = {}


def _pathKey(path: str) -> str:
    if (key := _pathKeyCache.get(path)) is None:
        key = os.path.normcase(os.path.normpath(os.path.abspath(_resolveTileTokens(path))))
        _pathKeyCache[path] = key
    return key


def _pathBaseName(path: str) -> str:
    if (name := _baseNameCache.get(path)) is None:
        name = os.path.normcase(os.path.basename(_resolveTileTokens(path)))
        _baseNameCache[path] = name
    return name


def _findLoadedImage(imageFilepath: str):
    """ The already loaded image datablock for a file, matched on the path, or None. """
    fileName = _pathBaseName(imageFilepath)
    filePath = _pathKey(imageFilepath)

    for image in bpy.data.images:
        if not image.filepath or _pathBaseName(image.filepath) != fileName:
            continue
        if _pathKey(bpy.path.abspath(image.filepath, library=image.library)) == filePath:
            return image

    return None


def createLightFromPluginDesc(pluginType: str, attrs: dict, name: str) -> bpy.types.Light:
    """ Create a Blender light datablock for a V-Ray light plugin, with the addon light_type,
        the matching Blender light type, and the area gizmo size from u_size/v_size (in cm,
        doubled to Blender's full-size convention). """
    from vray_blender.lib.lib_utils import LightTypeToPlugin, LightVrayTypeToBlender

    vrayType = next((k for k, v in LightTypeToPlugin.items() if v == pluginType), 'OMNI')
    blType = LightVrayTypeToBlender.get(vrayType, 'POINT')

    lightData = bpy.data.lights.new(name=name, type=blType)
    lightData.vray.light_type = vrayType

    if lightData.type == 'AREA':
        for vrAttr, blAttr in (("u_size", "size"), ("v_size", "size_y")):
            if vrAttr in attrs:
                setattr(lightData, blAttr,
                        attribute_utils.scaleToSceneLengthUnit(attrs[vrAttr], "centimeters") * 2)

    return lightData


def scaleDistanceValue(attrDesc, attrValue):
    """ Convert a distance-typed attr value from the descriptor unit (cm, as left by
        normalizeUnits) to Blender scene units. Attrs marked options.skipUnitScale and
        non-distance attrs pass through unchanged. """
    if import_common.isScaledDistanceAttr(attrDesc):
        return attribute_utils.scaleToSceneLengthUnit(attrValue, attrDesc['ui'].get('units', 'centimeters'))
    return attrValue


def radiansIfDegrees(attrDesc, attrValue):
    """ Convert a degrees-valued attr to radians, how Blender stores an ANGLE property. """
    if not attrDesc or "ui" not in attrDesc:
        return attrValue
    if isinstance(attrValue, bool) or not isinstance(attrValue, (int, float)):
        return attrValue

    ui = attrDesc["ui"]
    units = ui.get("units") or ("degrees" if ui.get("quantityType") == "angle" else "")
    return math.radians(attrValue) if units == "degrees" else attrValue


def _scaleAttrValue(attrDesc, attrValue, isConversion: bool):
    """ Unit transforms applied when importing an attr value onto a node: distance
        cm -> scene units, angle degrees -> radians, PERCENTAGE fraction -> percent.
        Skipped for Cycles conversion. """
    if isConversion or "ui" not in attrDesc:
        return attrValue

    # Only real scalars get the angle/percentage transform (distance handles both).
    isScalar = isinstance(attrValue, (int, float)) and not isinstance(attrValue, bool)
    if attrDesc["ui"].get("quantityType") == "distance":
        attrValue = scaleDistanceValue(attrDesc, attrValue)
    else:
        attrValue = radiansIfDegrees(attrDesc, attrValue)

    if attrDesc.get("subtype") == "PERCENTAGE" and isScalar:
        attrValue = attrValue * 100.0

    return attrValue


def loadImage(imageFilepath, importDir, bitmapTexture, makeRelative=False, imageName="", ledger=None):
    if imageFilepath is not None:
        originalFilepath = imageFilepath

        if not os.path.exists(imageFilepath):
            debug.printInfo("Couldn't find file: %s" % imageFilepath)
            debug.printInfo("Trying to search under import directory...")

            # NOTE: Windows style filepath could be stored here
            # Convert to UNIX slashes
            imageFilepath = path_utils.unifyPath(imageFilepath)

            # The same resolution policy ImportContext.resolvePath applies.
            imageFilepath = import_common.resolveAssetPath(imageFilepath, importDir)

        imageBlockName = imageName or bpy.path.display_name_from_filepath(imageFilepath)

        if loadedImage := _findLoadedImage(imageFilepath):
            bitmapTexture.image = loadedImage
        elif os.path.isfile(imageFilepath):
            bitmapTexture.image = bpy.data.images.load(imageFilepath)
            bitmapTexture.image.name = imageBlockName
            if ledger is not None:
                ledger.track(bitmapTexture.image)
        elif (placeholder := bpy.data.images.get(imageBlockName)) is not None:
            # A placeholder created earlier for the same missing file.
            bitmapTexture.image = placeholder
        elif os.path.splitext(originalFilepath)[1]:
            # Placeholder Image for a missing file, keeping the original reference.
            debug.printInfo(f"Bitmap file not found, creating placeholder: {originalFilepath}")
            image = bpy.data.images.new(imageBlockName, 1, 1)
            image.source = 'FILE'
            image.filepath = originalFilepath
            bitmapTexture.image = image
            if ledger is not None:
                ledger.track(image)
        else:
            debug.printError(f"Unable to find file: {imageFilepath}")

        if makeRelative and bitmapTexture.image is not None:
            bitmapTexture.image.filepath = bpy.path.relpath(bitmapTexture.image.filepath)


def _pluginAttrsToPropGroup(pluginDesc, propGroup, pluginType: str = None, scaleDistance: bool = False,
                            restoreVRayDefaults: bool = False, stats = None):
    """ Apply a plugin description's Attributes directly to a property group.

        Works for both a node's plugin property group (e.g. node.LightOmni) and a datablock's
        property group (e.g. light.vray.LightOmni).

        scaleDistance: convert quantityType=="distance" attrs from the descriptor unit (cm) to
        Blender scene units. Pass it only for post-normalizeUnits sources.

        restoreVRayDefaults: fill the attributes the source omitted with V-Ray's own default
        instead of the addon's authoring one (see _vrayDefaultsFor). Pass it only for .vrscene
        sources.
    """
    pluginType = pluginType or pluginDesc['ID']
    pluginModule = getPluginModule(pluginType)

    attrs = pluginDesc['Attributes']
    if restoreVRayDefaults and pluginModule is not None:
        restored = _vrayDefaultsFor(pluginType, pluginModule, set(attrs), stats)
        if not scaleDistance:
            restored = {k: scaleDistanceValue(attribute_utils.getAttrDesc(pluginModule, k), v)
                        for k, v in restored.items()}
        attrs = {**restored, **attrs}

    for attrName in attrs:
        if not hasattr(propGroup, attrName):
            continue

        attrDesc  = attribute_utils.getAttrDesc(pluginModule, attrName)
        attrValue = attrs[attrName]

        if scaleDistance:
            attrValue = scaleDistanceValue(attrDesc, attrValue)
        attrValue = radiansIfDegrees(attrDesc, attrValue)

        if attrDesc and (attrDesc['type'] == 'ENUM') \
                and not attribute_utils.valueInEnumItems(attrDesc, str(attrValue)):
            # A V-Ray enum value the addon does not model.
            debug.printWarning(f"Unsupported ENUM value '{str(attrValue)}' for attribute: {pluginType}.{attrName}")
            continue

        # A single incompatible attribute must not abort the whole plugin/material import.
        try:
            currentValue = getattr(propGroup, attrName)
            if hasattr(currentValue, '__len__') and not isinstance(currentValue, str):
                # Color/vector array property: assign a sequence of matching length.
                setattr(propGroup, attrName, attrValue[:len(currentValue)])
            else:
                setattr(propGroup, attrName, type(currentValue)(attrValue))
        except Exception as ex:
            debug.printError(f"Import: could not set {pluginType}.{attrName} = {attrValue!r}: {ex}")


def _pluginAttrsToNodeProps(pluginDesc, node):
    pluginType = pluginDesc['ID']
    if not hasattr(node, pluginType):
        debug.printWarning(f"Import: node '{node.bl_idname}' has no '{pluginType}' property group; skipping its attributes")
        return
    _pluginAttrsToPropGroup(pluginDesc, getattr(node, pluginType), pluginType)


CollapsibleTypes = {
    'TexAColor': 'texture',
    'TexFloat': 'input',            # constant float wrapper
    'TexFloatToColor': 'input',
    'TexColorToFloat': 'input',
    'TexInvertFloat': 'texture',
    'TexInvert': 'texture',
    'FloatToTex': 'input'
}


def _collapseTexCombine(pluginDesc: dict):
    """ The constant a TexCombine* with no linked texture reduces to, or None when a real
        texture is linked.

        The plugin blends between the value and the texture by texture_multiplier (it does not
        add them), inverts, and only then applies result_multiplier. An absent 'texture' means
        no texture at all rather than a black one. """
    attrs = pluginDesc['Attributes']
    if _isPluginLink(attrs.get('texture')):
        return None
    tex = attrs.get('texture')
    texConst = tex if isinstance(tex, (list, tuple)) else None
    texMult = float(attrs.get('texture_multiplier', 1.0))
    resultMult = float(attrs.get('result_multiplier', 1.0))

    if pluginDesc['ID'] == 'TexCombineFloat':
        out = float(attrs.get('value', 0.0))
        if texConst is not None:
            # The texture is always clamped to 0 from below, and to 1 from above on texture_clamp.
            texValue = max(0.0, float(texConst[0]))
            if attrs.get('texture_clamp'):
                texValue = min(1.0, texValue)
            out += (texValue - out) * texMult
        return out * resultMult

    color = attrs.get('color', (0.0, 0.0, 0.0))
    out = []
    for i in range(3):
        c = float(color[i])
        if texConst is not None and i < len(texConst):
            c += (float(texConst[i]) - c) * texMult
        if attrs.get('result_invert'):
            c = 1.0 - c
        out.append(c * resultMult)
    return Color(out)


# TexAColorOp outputs documented as "<x> of Color A * Mult A", so 'mode' cannot affect them.
# 'alpha' is excluded on purpose: it comes from result_alpha, not color_a.
_ACOLOROP_A_OUTPUTS = {'result_a': 'color', 'red': 0, 'green': 1, 'blue': 2,
                       'intensity': 'intensity'}


def _collapseAColorOp(pluginDesc: dict, outputName: str | None):
    """ The constant a TexAColorOp reduces to, or None when it does real work.

        Max wraps plain scalars in one of these and reads them back via '::intensity'. """
    attrs = pluginDesc['Attributes']

    # outputName may be the raw V-Ray name ('intensity') or the remapped label ('Result A').
    key = (outputName or 'result_a').strip().lower().replace(' ', '_')
    channel = _ACOLOROP_A_OUTPUTS.get(key)
    if channel is None:
        return None
    if 'color_a' not in attrs or _isPluginLink(attrs['color_a']) or _isPluginLink(attrs.get('mult_a')):
        return None
    # result_alpha is carried by the full-colour output, so it must be at its default there.
    if channel == 'color' and float(attrs.get('result_alpha', 0.0)) != 0.0:
        return None

    mult = float(attrs.get('mult_a', 1.0))
    color = [float(c) * mult for c in attrs['color_a']]

    if channel == 'color':
        return Color(color[:3])
    if channel == 'intensity':
        # Mirrors TexColorToFloat's colour -> float convention below.
        return (color[0] + color[1] + color[2]) / 3.0
    return color[channel] if channel < len(color) else 0.0


def _collapseToValue(importContext: ImportContext, pluginDesc: dict, seen: set = None,
                     outputName: str = None):
    """ Transforms plugin from 'CollapsibleTypes' to a simple value (either float or list of floats). """

    # A cyclic collapsible chain (TexInvert -> TexInvert) would recurse forever.
    if seen is None:
        seen = set()
    if pluginDesc['Name'] in seen:
        debug.printWarning(f"Cyclic plugin reference at '{pluginDesc['Name']}'; breaking the link")
        return None
    seen.add(pluginDesc['Name'])

    pluginType = pluginDesc['ID']

    if pluginType in ('TexCombineColor', 'TexCombineFloat', 'TexCombineColorLightMtl'):
        return _collapseTexCombine(pluginDesc)

    if pluginType == 'TexAColorOp':
        return _collapseAColorOp(pluginDesc, outputName)

    if pluginType not in CollapsibleTypes:
        return None

    value = None
    valueAttr = CollapsibleTypes[pluginType]
    # The value attr is omitted when it holds the plugin default.
    if valueAttr not in pluginDesc['Attributes']:
        return None
    pluginValue = pluginDesc['Attributes'][valueAttr]

    if _isPluginLink(pluginValue):
        linkedPlugin, _ = _getPluginFromLink(importContext, pluginValue)
        if not linkedPlugin:
            debug.printWarning(f"Invalid plugin link: {pluginValue}")
            return None
        pluginValue = _collapseToValue(importContext, linkedPlugin, seen)
        if pluginValue is None:
            return None
        
    if pluginType == 'TexFloatToColor':
        if pluginDesc['Attributes'].get('invert'):
            pluginValue = 1.0 - pluginValue
        value = Color((pluginValue, pluginValue, pluginValue))
    elif pluginType == 'TexColorToFloat':
        return (pluginValue[0] + pluginValue[1] + pluginValue[2]) / 3.0
    elif pluginType == 'TexInvertFloat':
        return 1.0 - pluginValue
    elif pluginType == 'TexInvert':
        return Color((1.0 - pluginValue[0], 1.0 - pluginValue[1], 1.0 - pluginValue[2]))
    else:
        value = pluginValue
    
    return value


def _createTransformNode(importContext: ImportContext, attrValue, attrSocketName: str, node: bpy.types.Node, groupPath: tuple = ()):
    m = attrValue if isinstance(attrValue, Matrix) else attribute_utils.attrValueToMatrix(attrValue, True)

    if allclose(m, Matrix.Identity(4)):
        # If the transformation is identity, do not create a separate V-Ray Transform node for it
        return

    offset, rotate, scale = m.decompose()
    rotate = rotate.to_euler('XYZ')

    tmNode = NodeUtils.createNode(importContext.nodeTree, 'VRayNodeTransform')
    tmNode.inputs['Offset'].value   = (offset[0],
                                      offset[1],
                                      offset[2])
    tmNode.inputs['Rotation'].value = (rotate[0], rotate[1], rotate[2])
    tmNode.inputs['Scale'].value    = (scale[0],  scale[1],  scale[2])

    importContext.nodeTree.links.new(tmNode.outputs['Transform'], node.inputs[attrSocketName])
    importContext.groupPathByNode[tmNode] = groupPath

def _setMatrixSocketValue(node: bpy.types.Node, attrSocketName: str, attrValue):
    """ Set a VRaySocketTransform (16-float MATRIX socket) from a parsed matrix, which the
        importer delivers as three column vectors ((v0),(v1),(v2)). A TRANSFORM value
        ((3x3), offset) contributes only its 3x3 part. Leaves identity on an unexpected
        shape rather than raising. """
    rows = attrValue
    if isinstance(attrValue, (list, tuple)) and len(attrValue) == 2 \
            and isinstance(attrValue[0], (list, tuple)) and len(attrValue[0]) == 3 \
            and isinstance(attrValue[0][0], (list, tuple)):
        rows = attrValue[0]   # TRANSFORM ((c0,c1,c2), offset) -> the 3x3 columns

    m = Matrix()   # 4x4 identity
    try:
        # Transpose column-major V-Ray storage into Blender's row-major matrix.
        for c in range(3):
            for r in range(3):
                m[c][r] = float(rows[r][c])
    except (TypeError, ValueError, IndexError):
        debug.printWarning(f"Import: unexpected MATRIX value shape for {node.name}.{attrSocketName}; left at identity")
        m = Matrix()

    # The socket stores the flat 16-float form.
    node.inputs[attrSocketName].value = [b for a in m for b in a]


# UNUSED at the moment
def _createMatrixNode(ntree: bpy.types.NodeTree, attrValue, attrSocketName: str, node: bpy.types.Node):
    m = Matrix()

    if type(attrValue) in {list, tuple}:
        # Transpose the matrix, Blender's format is row-first
        for c in range(3):
            for r in range(3):
                m[c][r] = attrValue[r][c]

    else:
        tmArray = struct.unpack("fffffffff", binascii.unhexlify(bytes(attrValue, 'ascii')))
        i = 0
        for c in range(3):
            for r in range(3):
                m[c][r] = tmArray[i]
                i += 1

    if allclose(m, Matrix.Identity(3)):
        # If the transformation is identity, do not create a separate V-Ray Transform node for it
        return

    _, rotate, scale = m.decompose()
    rotate = rotate.to_euler('XYZ')

    mNode = NodeUtils.createNode(ntree, 'VRayNodeMatrix')
    mNode.rotate = (rotate[0], rotate[1], rotate[2])
    mNode.scale  = (scale[0],  scale[1],  scale[2])

    ntree.links.new( mNode.outputs['Matrix'],  node.inputs[attrSocketName])


def _isPluginLink(value):
    return type(value) is str


def _parsePluginLink(pluginLink: str):
    """ Parse a plugin name set as attribute value into plugin name and output socket attribute name. """
    if pluginLink.find("::") != -1:
        pluginName, outSocketName = pluginLink.split("::")
        assert pluginName != "" and outSocketName != ""
        return pluginName, outSocketName
    else:
        return pluginLink, None


def _getPluginFromLink(importContext: ImportContext, pluginLink: str):
    """ Returns the plugin and output socket name given a fully qualified plugin link (plugnName::output) """
    if (not pluginLink) or (not _isPluginLink(pluginLink)):
        return None, None

    pluginName, outputSocketAttr = _parsePluginLink(pluginLink)

    if not pluginName:
        raise Exception(f"Plugin refereced by {pluginLink} not found in .vrmat")

    plugin = getPluginByName(importContext.vrsceneDict, pluginName)

    if not plugin:
        # This may happen when the plugin is referenced from another .vrmat file
        # like in e.g. HDRI definitions
        return None, None

    pluginType = plugin['ID']

    if outputSocketAttr:
        outputSocketName = _getOutputSocketNameByAttr(pluginType, outputSocketAttr)
    else:
        outputSocketName = _getDefaultOutputSocketName(pluginType)

    return plugin, outputSocketName


def _getPluginConnectedToInput(importContext, inputAttrName, pluginDesc):
    """ Returns the plugin and output referenced by attribute named "inputAttrName" """

    inputValue = pluginDesc['Attributes'][inputAttrName]
    if _isPluginLink(inputValue):
        return _getPluginFromLink(importContext, inputValue)

    return None, None


# Superseded source enum values, mapped to the addon's equivalent.
_ENUM_VALUE_REMAP = {
    ('SunLight', 'sky_model'): {'4': '5'},
    ('TexSky', 'sky_model'): {'4': '5'},
}


def _setNodeEnumProperty(attrDesc: dict, attrName: str, attrValue, pluginType: str, propGroup):
    """ Set value of Enum property of node """

    # Attribute is not mappable, so simply set it's value
    attrValue = str(attrValue)
    attrValue = _ENUM_VALUE_REMAP.get((pluginType, attrName), {}).get(attrValue, attrValue)
    if not attribute_utils.valueInEnumItems(attrDesc, attrValue):
        # A V-Ray enum value the addon does not model.
        debug.printWarning(f"Unsupported ENUM value '{attrValue}' for attribute: {pluginType}.{attrName}")
        return

    setattr(propGroup, attrName, attrValue)


def _setNodePrimitiveProperty(attrName: str, attrValue, pluginType: str, propGroup):
    """ Setting properties with Int, Float or String types"""

    # UVWGenRandomizer's 'mode' is a bit mask, one bit per mode check box in the UI.
    if pluginType == "UVWGenRandomizer" and attrName == "mode":
        attrValue = int(attrValue)
        for modeName, modeMask in UVWGenRandomizerModes.items():
            # Set every flag, not just the ones in the mask.
            setattr(propGroup, modeName, bool(attrValue & modeMask))
    else:
        setattr(propGroup, attrName, attrValue)


def _getMetaInputSocket(pluginModule, node, attrName):
    """ If the attribute is part of a meta input socket, return the meta socket
        instead of the socket for the actual attribute.
    """
    # Find the COLOR_TEXTURE meta that owns this attr and return ITS socket.
    for p in pluginModule.Parameters:
        if p['type'] == 'COLOR_TEXTURE' and attrName in (p['color_prop'], p['tex_prop']):
            return getInputSocketByAttr(node, p['attr'])

        # A BRDF_USE/COLOR_USE meta owns its 'target_prop' (BRDFScanned.ccmult,
        # MtlOverride.gi_mtl, ...), which has no stand-alone socket of its own.
        if (boundProps := p.get('bound_props')) and attrName == boundProps['target_prop']:
            return getInputSocketByAttr(node, p['attr'])

    return None


def _getInputSocket(node, attrName):
    pluginModule = getPluginModule(node.vray_plugin)

    if (sock := _getMetaInputSocket(pluginModule, node, attrName)) is not None:
        excludedParams = pluginModule.Options.get('excluded_parameters', [])
        if sock.vray_attr not in excludedParams:
            return sock
        else:
            debug.printError(f"Import: Parameter '{attrName}' is not part of node {node.name} definition")
            return None

    if (sock := getInputSocketByAttr(node, attrName)) is None:
        excludedParams = pluginModule.Options.get('excluded_parameters', [])
        if attrName not in excludedParams:
            debug.printWarning(f"Import: Parameter '{attrName}' is not part of node {node.name} definition")

    return sock


def _processNonNodePlugins(importContext: ImportContext, inPluginOutput: str, connectedPlugin: dict, inputSocket: bpy.types.NodeSocket):
    """ Find the first plugin in the chain starting from 'inputSocket' for which a node should be created.
        Apply any TexCombineColor properties to the input socket.

        Returns:
            The plugin description and the name of the output socket of the next plugin in the chain
            for which a node should be created.
    """
    assert inputSocket is not None
    assert connectedPlugin is not None

    seen = set()
    while connectedPlugin:
        # A cyclic wrapper chain (TexCombine -> ... -> TexCombine) would loop forever.
        if connectedPlugin['Name'] in seen:
            debug.printWarning(f"Cyclic plugin reference at '{connectedPlugin['Name']}'; breaking the link")
            break
        seen.add(connectedPlugin['Name'])

        connectedPluginType = connectedPlugin['ID']
        connectedThroughAttr = ""

        # Export-only wrapper plugins (MtlSingleBRDF, TexCombineColor, ...) have no UI node.
        match connectedPluginType:
            case "TexCombineColor" | "TexCombineFloat" | "TexCombineColorLightMtl":
                if not _passthroughIsNoOp(connectedPlugin):
                    break
                connectedThroughAttr = "texture"
            case "TexFloatToColor":
                if not _passthroughIsNoOp(connectedPlugin):
                    break
                connectedThroughAttr = "input"
            case "TexColorToFloat":
                connectedThroughAttr = "input"
            case "MtlSingleBRDF":
                connectedThroughAttr = "brdf"
            case _:
                break

        # Multiplier or converter is not connected
        if connectedThroughAttr not in connectedPlugin["Attributes"]:
            return connectedPlugin, inPluginOutput

        nextPlugin, nextPluginOutput = _getPluginConnectedToInput(importContext, connectedThroughAttr, connectedPlugin)

        if not nextPlugin:
            # No plugin is connected to the input
            break

        assert nextPluginOutput

        connectedPlugin, inPluginOutput = nextPlugin, nextPluginOutput

    return connectedPlugin, inPluginOutput


def _createLinkedNode(
    importContext: ImportContext,
    inputSocket: bpy.types.NodeSocket,
    connectedPluginOutput: str,
    connectedPlugin: dict
):
    """ Create the node connected to 'inputSocket'

    Args:
        importContext (ImportContext): import context
        inputSocket (bpy.types.NodeSocket): The input socket to connect.
        connectedPluginOutput (str): The name of the output socket to connect to.
        connectedPlugin (dict):      The type of the plugin of the new node.
    """
    assert connectedPlugin is not None

    connectedPlugin, connectedPluginOutput = _processNonNodePlugins(
        importContext, connectedPluginOutput, connectedPlugin, inputSocket)
    assert connectedPlugin is not None

    if (collapsedValue := _collapseToValue(importContext, connectedPlugin,
                                           outputName=connectedPluginOutput)) is not None:
        # The connected plugin collapsed to a value.
        _assignSocketValue(inputSocket, collapsedValue)
        return None

    preExisting = connectedPlugin['Name'] in importContext.nodeByPlugin
    newNode = createNode(importContext, connectedPlugin)

    if newNode is None:
        # Some leaf nodes may be omitted if they have their default value, e.g. UVWGenChannel
        return

    # Deal with remapped socket names
    if remappedPluginOutput := _REMAPPED_OUTPUT_SOCKETS.get(connectedPlugin['ID'], {}).get(connectedPluginOutput):
        connectedPluginOutput = remappedPluginOutput

    # A plugin whose output is chosen by an attribute value links to the matching socket of
    # the node it was remapped to. Skipped when the creator fell back to the source plugin's
    # own node, which has no such socket.
    if outputAttr := getSelectedOutputAttr(connectedPlugin):
        if outSocket := getOutputSocketByAttr(newNode, outputAttr):
            connectedPluginOutput = outSocket.name

    # Prefer the named output; fall back to a sole output carrying a different name
    # (e.g. VolumeFog's only output is 'Effect', not 'Output').
    outSock = next((o for o in newNode.outputs if o.name == connectedPluginOutput), None)

    # A generic node starts with only its category's default output; any other output a
    # referencing plugin names is created on demand. Checked before the sole-output fallback
    # below, which would otherwise always claim that default output.
    if outSock is None and connectedPluginOutput \
            and newNode.bl_idname == generic_node.GENERIC_NODE_TYPE:
        outSock = generic_node.ensureOutput(newNode, connectedPluginOutput)

    # Fall back to the primary (first) output when the named one cannot apply. Gating this on
    # single-output nodes dropped links into UVWGenChannel, which has 'Mapping' and 'Uvw Coords'.
    namedOutputCannotApply = (not connectedPluginOutput) \
        or getattr(newNode, 'vray_plugin', '') != connectedPlugin['ID']
    if outSock is None and newNode.outputs \
            and (len(newNode.outputs) == 1 or namedOutputCannotApply):
        outSock = newNode.outputs[0]

    if outSock is None:
        # A Cycles conversion can legitimately reach a node without the output.
        if not importContext.isConversion:
            debug.printWarning(f"Output socket '{connectedPluginOutput}' not found on node "
                               f"'{newNode.name}' ({connectedPlugin['ID']}); skipping link")
        return

    # An effect output (VolumeFog etc.) feeds an effect input (the EffectsHolder) or a legacy
    # glass BRDF's 'volume' slot, and nothing else.
    if outSock.bl_idname == 'VRaySocketEffectOutput' and inputSocket.bl_idname != 'VRaySocketEffect' \
            and getattr(inputSocket, 'vray_attr', '') != 'volume':
        if not preExisting:
            importContext.nodeByPlugin.pop(connectedPlugin['Name'], None)
            importContext.nodeTree.nodes.remove(newNode)
        return

    if outSock.hide:
        outSock.hide = False
        outSock.enabled = True
    importContext.nodeTree.links.new(outSock, inputSocket)

    # Links created from Python never reach insert_link.
    if onLinkConnected := getattr(inputSocket, 'onLinkConnected', None):
        onLinkConnected()


_boundTemplateCache = {}  # pluginType -> {boundAttr: templateAttr}, see _getBoundTemplateMap


def _getBoundTemplateMap(pluginType: str, pluginModule):
    """ Return {boundPropertyName: templateAttrName} for a plugin type: for each
        object-selector TEMPLATE attribute, the plugin-list/object attribute it is
        bound to (via options.template.args.bound_property). Cached per plugin type.
    """
    mapping = _boundTemplateCache.get(pluginType)
    if mapping is None:
        mapping = {}
        for attr in pluginModule.Parameters:
            if attr.get('type') == 'TEMPLATE':
                templateArgs = attr.get('options', {}).get('template', {}).get('args', {})
                if boundProp := templateArgs.get('bound_property'):
                    mapping[boundProp] = attr['attr']
        _boundTemplateCache[pluginType] = mapping
    return mapping


def _fillObjectSelectorFromRefs(importContext: ImportContext, node, pluginType: str, pluginModule,
                                attrName: str, attrValue):
    """ Resolve an object-referencing attribute (PLUGIN / PLUGIN_LIST with link_info)
        to Blender objects and populate the node's bound object-selector template.

        Returns True if the attribute was handled (whether or not any object resolved).
    """
    from vray_blender.lib.defs import LinkInfo

    attrDesc = attribute_utils.getAttrDesc(pluginModule, attrName)
    linkDesc = attrDesc.get('options', {}).get('link_info') if attrDesc else None
    if not linkDesc:
        return False

    templateAttr = _getBoundTemplateMap(pluginType, pluginModule).get(attrName)
    if not templateAttr:
        return False   # No selector template to populate (e.g. a socket-only binding)

    selector = getattr(getattr(node, pluginType), templateAttr, None)
    if selector is None:
        return False

    linkType = linkDesc.get('link_type', LinkInfo.OBJECTS)
    refs = attrValue if isinstance(attrValue, (list, tuple)) else [attrValue]

    # Multi-object selectors expose a selectedItems collection; single-object selectors
    # (e.g. VolumeAerialPerspective.sun) expose a boundPropObj pointer.
    isMulti = hasattr(selector, 'selectedItems')

    for ref in refs:
        if not _isPluginLink(ref):
            continue
        refName = _parsePluginLink(ref)[0]
        obj = importContext.objectResolver(refName, linkType)
        if obj is None:
            continue
        if isMulti:
            if obj.name not in selector.selectedItems:
                item = selector.selectedItems.add()
                item.objectPtr = obj
                item.name = obj.name
                item.objectType = 'OBJECT'
        elif hasattr(selector, 'boundPropObj'):
            selector.boundPropObj = obj
            break   # single-object selector: only the first resolved object

    return True


_colorTexMapCache = {}  # pluginType -> ({colorProp: attr}, {texProp: attr}), see _getColorTexMaps


def _getColorTexMaps(pluginType: str, pluginModule):
    """ Return (colorPropMap, texPropMap) for a plugin type: the reverse lookup from a
        COLOR/ACOLOR or TEXTURE attribute name to the COLOR_TEXTURE meta-attribute that
        owns it. Cached per plugin type.
    """
    maps = _colorTexMapCache.get(pluginType)
    if maps is None:
        colorPropMap = {}
        texPropMap = {}
        for attr in pluginModule.Parameters:
            if attr["type"] == "COLOR_TEXTURE":
                colorPropMap[attr["color_prop"]] = attr["attr"]
                texPropMap[attr["tex_prop"]] = attr["attr"]
        maps = (colorPropMap, texPropMap)
        _colorTexMapCache[pluginType] = maps
    return maps


def _remapColorTextureAttr(attrDesc: dict, attrName: str, colorPropMap: dict, texPropMap: dict) -> str:
    """ Return the COLOR_TEXTURE meta-attr that owns a COLOR/ACOLOR/TEXTURE attr, or the
        attr name unchanged when no meta owns it. """
    # Handling attributes that are represented as COLOR_TEXTURE
    if attrDesc['type'] in ("COLOR", "ACOLOR"):
        if (mappedAttr := colorPropMap.get(attrName)) is not None:
            return mappedAttr
    elif attrDesc['type'] == "TEXTURE":
        if (mappedAttr := texPropMap.get(attrName)) is not None:
            return mappedAttr
    return attrName


def _applyAttr(importContext: ImportContext, node: bpy.types.Node, propGroup, pluginType: str,
               pluginDesc: dict, attrName: str, origAttrName: str, attrDesc: dict, attrValue,
               attrSocketName: str, isVisibleSocket: bool):
    """ Apply a single plugin attribute onto the node: set the matching property group
        member / socket value, or create and link the node the attribute references. """
    # Attribute is a output type - nothing to do
    if attrDesc['type'] in attribute_types.NodeOutputTypes:
        return

    elif attrDesc['type'] == 'MATRIX':
        if not isVisibleSocket:
            return
        _setMatrixSocketValue(node, attrSocketName, attrValue)

    elif attrDesc['type'] == 'TRANSFORM':
        if not isVisibleSocket:
            return
        # The Transform helper joins the group of the node it feeds.
        _createTransformNode(importContext, attrValue, attrSocketName, node,
                             tuple(pluginDesc.get('GroupPath', ())))

    elif attrDesc['type'] == 'ENUM':
        _setNodeEnumProperty(attrDesc, attrName, attrValue, pluginType, propGroup)

    elif attrDesc['type'] not in attribute_types.NodeInputTypes:
        # Blender's bool setter rejects ints other than 0/1.
        if attrDesc['type'] == 'BOOL' and not isinstance(attrValue, bool):
            attrValue = bool(attrValue)
        _setNodePrimitiveProperty(attrName, attrValue, pluginType, propGroup)

    elif _isPluginLink(attrValue):
        # Resolve the target socket by vray_attr, not by the derived display name.
        inputSock = _getInputSocket(node, origAttrName)
        if inputSock is None:
            return
        connectedPlugin, outputName = _getPluginFromLink(importContext, attrValue)
        if connectedPlugin is not None:
            _createLinkedNode(importContext, inputSock, outputName, connectedPlugin)

    elif  attrSocket := _getInputSocket(node, attrName):
        _assignSocketValue(attrSocket, attrValue)


# Addon defaults that encode a Blender-space correction, not an authoring preference.
_NO_VRAY_DEFAULT = {
    'GeomHair.gravity_vector',          # Blender is Z-down
    'RenderChannelDenoiser.name',       # channel identity
    'GeomDisplacedMesh.water_level',    # -1e30 is V-Ray's disabled sentinel
    # A colour map slot folded into a COLOR_TEXTURE meta: the meta socket takes the tex value
    # when there is one, so V-Ray's 0 arrives as a black transparency and the fog goes opaque.
    'EnvironmentFog.transparency_tex',
}


_mapSlotPairCache = {}  # pluginType -> (('<name>_tex', '<name>'), ...), see _mapSlotPairs


def _mapSlotConstant(attrDesc, pluginModule):
    """ The plain '<name>' parameter a '<name>_tex' map slot belongs to, or None.
        A pair folded into a COLOR_TEXTURE meta does not count, the meta carries both halves.
    """
    attrName = attrDesc['attr']
    if attrDesc['type'] not in ('FLOAT_TEXTURE', 'INT_TEXTURE', 'TEXTURE') \
            or not attrName.endswith('_tex'):
        return None
    if any(p['type'] == 'COLOR_TEXTURE' and attrName in (p['color_prop'], p['tex_prop'])
           for p in pluginModule.Parameters):
        return None
    return pluginModule.ParametersByAttr.get(attrName[:-len('_tex')])


def _mapSlotRestoreValue(attrDesc, pluginModule):
    """ What a scalar '<name>_tex' slot the source omitted should import as.
        Only for slots V-Ray defaults to 0 to mean 'no map'. 1.0 is the neutral that leaves
        '<name>' alone, whether V-Ray multiplies it by the slot or defers to the slot.
    """
    if attrDesc['type'] not in ('FLOAT_TEXTURE', 'INT_TEXTURE'):
        return None
    if attrDesc.get('options', {}).get('vray_default') not in (0, 0.0):
        return None
    return None if _mapSlotConstant(attrDesc, pluginModule) is None else 1.0


def _mapSlotPairs(pluginType: str, pluginModule):
    """ The ('<name>_tex', '<name>') attribute name pairs of a plugin. Cached per plugin type. """
    pairs = _mapSlotPairCache.get(pluginType)
    if pairs is None:
        pairs = tuple((attrDesc['attr'], constant['attr'])
                      for attrDesc in pluginModule.Parameters
                      if (constant := _mapSlotConstant(attrDesc, pluginModule)) is not None)
        _mapSlotPairCache[pluginType] = pairs
    return pairs


def _normalizeMapSlots(vrsceneDict: dict):
    """ Drop the map slots a source wrote out as NULL, then move a source '<name>' onto
        '<name>_tex' for the '<name>' the addon excludes and has no property for.
    """
    for pluginDesc in vrsceneDict:
        attrs = pluginDesc.get('Attributes')
        if attrs is None or (pluginModule := findPluginModule(pluginDesc['ID'])) is None:
            continue
        excluded = getattr(pluginModule, 'Options', {}).get('excluded_parameters', [])
        for texAttr, plainAttr in _mapSlotPairs(pluginDesc['ID'], pluginModule):
            # Max writes an empty slot as 'lineColor_tex=NULL', which arrives as an empty name.
            if attrs.get(texAttr, None) == '':
                del attrs[texAttr]

            if texAttr in attrs or plainAttr not in excluded or plainAttr not in attrs:
                continue
            attrs[texAttr] = attrs[plainAttr]


def _vrayDefaultsFor(pluginType: str, pluginModule, presentAttrs, stats = None) -> dict:
    """ V-Ray's own default for every attribute 'presentAttrs' omits and the addon overrides.
        lib.plugin_utils records the replaced value as options['vray_default']. The returned
        values are in V-Ray's own units.
    """
    restored = {}

    for attrDesc in pluginModule.Parameters:
        vrayDefault = attrDesc.get('options', {}).get('vray_default')
        if vrayDefault is None:
            continue

        attrName = attrDesc['attr']
        if (attrName in presentAttrs) or (f'{pluginType}.{attrName}' in _NO_VRAY_DEFAULT):
            continue

        if (mapSlotValue := _mapSlotRestoreValue(attrDesc, pluginModule)) is not None:
            restored[attrName] = mapSlotValue
            continue

        if attrDesc['type'] == 'ENUM' and not attribute_utils.valueInEnumItems(attrDesc, str(vrayDefault)):
            if stats is not None:
                stats.skipped[f'{pluginType}.{attrName}={vrayDefault} (unsupported)'] += 1
            continue

        if attrDesc['type'] == 'BOOL' and isinstance(vrayDefault, str):
            # The addon narrows a few enums to a checkbox (VolumeVRayToon.toonMaterialOnly),
            # so V-Ray's default arrives as the enum's string index - and bool('0') is True.
            vrayDefault = vrayDefault not in ('0', '')

        restored[attrName] = vrayDefault

    return restored


def _withVRayDefaults(importContext: ImportContext, pluginType: str, pluginModule, pluginAttrs: dict,
                      skippedAttrs):
    """ 'pluginAttrs' plus V-Ray's default for every attribute the source omitted. """
    restored = _vrayDefaultsFor(pluginType, pluginModule,
                                set(pluginAttrs) | set(skippedAttrs), importContext.stats)

    return {**restored, **pluginAttrs} if restored else pluginAttrs


def _fillNodeProperties(importContext: ImportContext, node: bpy.types.Node, pluginDesc: dict, pluginType: str, skippedAttrs = {}):
    """ Create Node without specific parameters"""

    pluginAttrs = pluginDesc['Attributes']
    pluginModule = getPluginModule(pluginType)

    if pluginModule is None:
        debug.printError(f"Plugin '{pluginType}' is not yet supported! This shouldn't happen! Please, report this!")
        return None

    # This property group holds all plugin settings
    propGroup = getattr(node, pluginType)

    # Reverse maps from a color/texture attribute to its owning COLOR_TEXTURE meta-attr
    colorPropMap, texPropMap = _getColorTexMaps(pluginType, pluginModule)

    # Attributes the source omitted keep V-Ray's default rather than the addon's authoring one.
    attrsToApply = _withVRayDefaults(importContext, pluginType, pluginModule, pluginAttrs, skippedAttrs)

    # Now go through all plugin attributes and check
    # if we should create other nodes or simply set the value
    for attrName in attrsToApply:
        if attrName in skippedAttrs:
            continue
        attrValue = attrsToApply[attrName]

        attrDesc = attribute_utils.getAttrDesc(pluginModule, attrName)
        # TODO: Figure out what to do with these params
        if not attrDesc:
            continue

        attrValue = _scaleAttrValue(attrDesc, attrValue, importContext.isConversion)

        # Catching socket mismatches during generic nodes creation.
        try:
            # Object-referencing attributes (link_info) fill the node's object selector.
            # Only the full-scene importer supplies an objectResolver.
            if importContext.objectResolver is not None and \
                    _fillObjectSelectorFromRefs(importContext, node, pluginType, pluginModule, attrName, attrValue):
                continue

            origAttrName = attrName
            attrName = _remapColorTextureAttr(attrDesc, attrName, colorPropMap, texPropMap)
            # After a COLOR_TEXTURE remap the visible socket is the meta color-texture socket
            # (e.g. "Source Color"), not the raw tex_prop/color_prop attribute's socket.
            attrSocketName = getInputSocketNameByAttr(pluginModule, attrName)
            isVisibleSocket = attrSocketName in node.inputs

            _applyAttr(importContext, node, propGroup, pluginType, pluginDesc,
                       attrName, origAttrName, attrDesc, attrValue, attrSocketName, isVisibleSocket)

        except Exception as ex:
            debug.printExceptionInfo(ex, "nodes.importing._fillNodeProperties")

    return node


def _assignSocketValue(socket: bpy.types.NodeSocket, value):
    """ Assign a value to a socket's 'value' property, truncating or padding (alpha=1.0) a
        sequence to the socket's component count.
    """
    current = getattr(socket, 'value', None)
    currentIsSeq = hasattr(current, '__len__') and not isinstance(current, str)
    valueIsSeq = hasattr(value, '__len__') and not isinstance(value, str)

    if currentIsSeq and valueIsSeq:
        n = len(current)
        seq = list(value)
        if len(seq) > n:
            seq = seq[:n]
        elif len(seq) < n:
            seq = seq + [1.0] * (n - len(seq))
        socket.value = seq
    elif currentIsSeq and isinstance(value, (int, float)) and not isinstance(value, bool):
        # V-Ray treats a float in a color/texture slot as a uniform gray.
        socket.value = [float(value)] * len(current)
    else:
        # Reconcile scalar type mismatches (e.g. a float value feeding an int socket).
        if isinstance(current, bool):
            socket.value = bool(value) if isinstance(value, (int, float)) else value
        elif isinstance(current, int) and isinstance(value, float):
            socket.value = int(value)
        else:
            socket.value = value


def _createGenericNode(importContext: ImportContext, pluginDesc: dict):
    pluginType  = pluginDesc['ID']
    pluginName  = pluginDesc['Name']

    try:
        node = NodeUtils.createNode(importContext.nodeTree, f'VRayNode{pluginType}', pluginName)
    except RuntimeError:
        # A V-Ray plugin the addon has no node class for. Textures, mapping generators and
        # materials still import, as a generic node built from the parameters the scene set.
        if _importsAsGenericNode(pluginType):
            return generic_node.createGenericNode(importContext, pluginDesc)
        debug.printWarning(f"The asset being imported contains a plugin of type {pluginType}, which is not recognized by V-Ray for Blender.")
        return None

    _fillNodeProperties(importContext, node, pluginDesc, pluginType)

    return node


# Wrapper plugins with no node of their own -> the attr holding the wrapped texture.
_PASSTHROUGH_WRAPPERS = {
    'TexCombineColor': 'texture',
    'TexCombineColorLightMtl': 'texture',
    'TexCombineFloat': 'texture',
    'TexColorToFloat': 'input',
    'ColorTextureToMono': 'color_texture',  # Max color->mono; 'value' is unused by the renderer
    'TexFloatToColor': 'input',
    'TexMaxGamma': 'input',    # Max color-management wrapper
    'TexAColor': 'texture',
    'TexIntToFloat': 'input',  # int->float converter
    'FloatToTex': 'input',     # float->texture adapter; its only param is 'input'
    # UVWGen read as a texture. Link the UVWGen straight to the consumer: on export
    # _needUVWGenToColorConversion re-inserts the equivalent wrapper (a TexUVW) itself.
    'TexUVWGenToTexture': 'input',
    # Same adapter, under the name the exporter emits. Its uvwgen socket cannot be linked.
    'TexUVW': 'uvwgen',
}

# Non-passthrough params, and their plugin defaults, that make a wrapper do real work.
# A wrapper listed here is a no-op only while every one of them is at its default; one
# not listed here is always a no-op.
_PASSTHROUGH_NOOP_DEFAULTS = {
    'TexFloatToColor':         {'invert': False},
    'TexCombineColor':         {'texture_multiplier': 1.0,
                                'result_invert': False, 'result_multiplier': 1.0},
    'TexCombineFloat':         {'texture_multiplier': 1.0,
                                'texture_clamp': False, 'result_multiplier': 1.0},
    'TexCombineColorLightMtl': {'color': (0.0, 0.0, 0.0), 'result_multiplier': 1.0},
    # component: 0 all, 1 u, 2 v, 3 w. Only 'all' passes the UVW through unchanged.
    'TexUVW':                  {'component': 0},
}


def _passthroughIsNoOp(pluginDesc: dict) -> bool:
    """ True when a passthrough wrapper does no real work. """
    pluginType = pluginDesc['ID']
    attrs = pluginDesc['Attributes']

    if pluginType == 'TexMaxGamma':
        # color_space enum: 0 Linear, 1 Inverse gamma, 2 sRGB.
        if _isPluginLink(attrs.get('multiplier')) or _isPluginLink(attrs.get('gamma')):
            return False
        if abs(float(attrs.get('multiplier', 1.0)) - 1.0) > 1e-4:
            return False
        colorSpace = str(attrs.get('color_space', '0'))
        if colorSpace == '0':
            return True
        return colorSpace == '1' and abs(float(attrs.get('gamma', 1.0)) - 1.0) < 1e-4

    defaults = _PASSTHROUGH_NOOP_DEFAULTS.get(pluginType)
    if defaults is None:
        return True
    for attr, default in defaults.items():
        if attr not in attrs:
            continue
        value = attrs[attr]
        if _isPluginLink(value):
            return False    # a texture drives this param
        if isinstance(default, bool):
            if bool(value) != default:
                return False
        elif isinstance(default, tuple):
            if any(abs(float(v) - d) > 1e-4 for v, d in zip(value, default)):
                return False
        elif abs(float(value) - default) > 1e-4:
            return False
    return True


def createNode(importContext: ImportContext, pluginDesc: dict):
    pluginName = pluginDesc['Name']

    # A cyclic plugin reference would recurse forever through _createLinkedNode.
    if pluginName in importContext.creatingPlugins:
        debug.printWarning(f"Cyclic plugin reference at '{pluginName}'; breaking the link")
        return None

    importContext.creatingPlugins.add(pluginName)
    try:
        return _createNodeImpl(importContext, pluginDesc)
    finally:
        importContext.creatingPlugins.discard(pluginName)


def _createNodeImpl(importContext: ImportContext, pluginDesc: dict):
    # Imported lazily to avoid a module cycle (creators imports engine at module level).
    from vray_blender.nodes.importing import creators

    pluginType = pluginDesc['ID']
    pluginName = pluginDesc['Name']

    # Third-party host-integration plugins (Forest, Houdini, C4D noise, Nuke) are never imported.
    if pluginType in THIRD_PARTY_INTEGRATION_PLUGINS:
        debug.printWarning(f"Skipping import of third-party integration plugin '{pluginType}' ({pluginName}); create it via the V-Ray plugin node instead.")
        if importContext.stats is not None:
            importContext.stats.skipped[pluginType] += 1
        return None

    # See through a wrapper only when it is a no-op.
    if _passthroughIsNoOp(pluginDesc) and (innerAttr := _PASSTHROUGH_WRAPPERS.get(pluginType)) is not None:
        innerRef = pluginDesc['Attributes'].get(innerAttr)
        if _isPluginLink(innerRef):
            if (innerPlugin := _getPluginFromLink(importContext, innerRef)[0]) is not None:
                return createNode(importContext, innerPlugin)
        return None

    if (pluginType in _UNSUPPORTED_PLUGINS) and not _importsAsGenericNode(pluginType):
        debug.printWarning(f"The asset being imported contains a plugin of type {pluginType}, which is not recognized by V-Ray for Blender. Please contact support.")
        if importContext.stats is not None:
            importContext.stats.skipped[pluginType] += 1
        return None

    if (existing := importContext.nodeByPlugin.get(pluginName)) is not None:
        return existing

    creator = creators.NODE_CREATORS.get(pluginType, _createGenericNode)
    node = creator(importContext, pluginDesc)

    if node is not None:
        importContext.nodeByPlugin[pluginName] = node
        importContext.groupPathByNode[node] = tuple(pluginDesc.get('GroupPath', ()))
    return node
