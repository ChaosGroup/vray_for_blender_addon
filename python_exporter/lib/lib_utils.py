# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later


import re
import uuid
from pathlib import PurePath

import bpy

from vray_blender import debug


# Match Blender light to V-Ray plugin
LightBlenderToVrayPlugin = {
    'AREA'  : 'LightRectangle',
    'POINT' : 'LightOmni',
    'SPOT'  : 'LightSpot',
    'SUN'   : 'SunLight',
}

# Match V-Ray light type to Blender light type
LightVrayTypeToBlender = {
    'AMBIENT'   : 'POINT',
    'DIRECT'    : 'POINT',
    'IES'       : 'POINT',
    'MESH'      : 'POINT',
    'OMNI'      : 'POINT',
    'SPHERE'    : 'POINT',
    'SPOT'      : 'SPOT',
    'SUN'       : 'SUN',
    'RECT'      : 'AREA',
    'DOME'      : 'POINT',
    'LUMINAIRE' : 'POINT'
}

# Match Blender light type to the V-Ray light_type enum value used when converting
# a native Blender light to a V-Ray light.
BlenderToVrayLightType = {
    'POINT' : 'OMNI',
    'SPOT'  : 'SPOT',
    'AREA'  : 'RECT',
    'SUN'   : 'SUN',
}

# Match V-Ray light type to V-Ray plugin
LightTypeToPlugin = {
    'AMBIENT'   : 'LightAmbient',
    'DIRECT'    : 'MayaLightDirect',
    'IES'       : 'LightIES',
    'MESH'      : 'LightMesh',
    'OMNI'      : 'LightOmni',
    'SPHERE'    : 'LightSphere',
    'SPOT'      : 'LightSpot',
    'SUN'       : 'SunLight',
    'RECT'      : 'LightRectangle',
    'DOME'      : 'LightDome',
    'LUMINAIRE' : 'LightLuminaire'
}

FormatToSettings = {
    '0' : 'SettingsPNG',
    '1' : 'SettingsJPEG',
    '2' : 'SettingsTIFF',
    '3' : 'SettingsTGA',
    '4' : 'SettingsSGI',
    '5' : 'SettingsEXR',
    '6' : 'SettingsVRST',
}


def getUUID():
    return str(uuid.uuid1()).split("-")[0]


def getLightPluginType(light: bpy.types.Light):
    """ Returns the type of the V-Ray plugin corresponding to the type of the light. """
    from vray_blender.nodes.tools import isVrayLight

    if not isVrayLight(light):
        return LightBlenderToVrayPlugin[light.type]
    
    return LightTypeToPlugin[light.vray.light_type]


def getLightPropGroup(light: bpy.types.Light, lightType: str):
    """ Get the active propGroup of a light, as lights have separate propGroup objects
        for legacy and node modes.

    Args:
        light (bpy.types.Light): the light
        lightType (str): the name of the V-Ray plugin, e.g. LightSpot
    """
    from vray_blender.nodes.utils import getNodeByType
    
    # Lights have two different propgroups for legacy and node lights, get the correct one
    propGroup = None
    
    if light.node_tree and (node := getNodeByType(light.node_tree, f'VRayNode{lightType}')):
        propGroup = getattr(node, lightType)
    else:
        propGroup = getattr(light.vray, lightType)

    return propGroup



# Strips string from deprecated chars
#
# NOTE: Some unicode conversion support?
#
def cleanString(s, stripSigns=True):
    if stripSigns:
        s = s.replace("+", "p")
        s = s.replace("-", "m")
    for i in range(len(s)):
        c = s[i]
        if c in "|@":
            continue
        if not ((c >= 'A' and c <= 'Z') or (c >= 'a' and c <= 'z') or (c >= '0' and c <= '9')):
            s = s.replace(c, "_")
    return s




# The suffix V-Ray adds to a plugin name, e.g. '/Glass_mtl@mtl_2'.
_reCosmosPluginSuffix = re.compile(r'@.*$')

# Cosmos' 3D high/low detail prefix, e.g. '/_3DH_Lamp_Floor_013_vrayluminaire@light_27'.
_reCosmosDetailPrefix = re.compile(r'^_3d[hl]_', re.IGNORECASE)

# The package id Cosmos puts on every downloaded file, e.g. 'ff7f0a36_Nrm_2k_raw.tx'.
_reCosmosFilePrefix = re.compile(r'^[0-9a-f]{8}_', re.IGNORECASE)

# Detail level and color space, e.g. 'HDR_Map_723_8k_lin' -> 'HDR_Map_723_8k'.
_reCosmosFileSuffix = re.compile(r'_(3dh|3dl|raw|srgb|lin|linear)$', re.IGNORECASE)

# Illegal in file names (image names end up in one, see image_utils._saveTemporaryImage);
# '@' and '|' also separate the generated V-Ray plugin names and survive lib/names.py.
_reUnsafeNameChars = re.compile(r'[\\/:*?"<>|@\x00-\x1f]')


def _collapseUnderscores(name: str):
    return re.sub(r'_{2,}', '_', name).strip('_')


def sanitizeDatablockName(name: str):
    """ Make a name coming from outside Blender safe to use as a datablock name. """
    return _reUnsafeNameChars.sub('_', name).strip()


def getCosmosAssetName(cosmosAssetContext):
    """ The Cosmos asset name to name the imported datablocks after, or '' if there is none. """
    if not cosmosAssetContext:
        return ""

    return sanitizeDatablockName(cosmosAssetContext.assetName)


def cleanImportedPluginName(pluginName: str):
    """ '/Glass_mtl@mtl_2' -> 'Glass', '/_3DH_Lamp_Floor_013_vrayluminaire@light_27' ->
        'Lamp_Floor_013_vrayluminaire'.
    """
    name = _reCosmosPluginSuffix.sub('', pluginName.lstrip('/'))
    name = _reCosmosDetailPrefix.sub('', name)

    if name.endswith('_mtl'):
        name = name[:-len('_mtl')]

    return _collapseUnderscores(name) or pluginName


def cleanCosmosTextureName(filePath: str):
    """ 'ff7f0a36_Nrm_2k_raw.tx' -> 'Nrm_2k',
        '405a8918_Flowers_01_Diff_3dh_srgb.tx' -> 'Flowers_01_Diff'.
    """
    stem = PurePath(filePath).stem
    name = _reCosmosFilePrefix.sub('', stem)

    # The tokens may be combined, e.g. '..._3dh_srgb'.
    while (shorter := _reCosmosFileSuffix.sub('', name)) != name:
        name = shorter

    return _collapseUnderscores(name) or stem


def getPropGroup(parentID, propGroupPath):
    path = propGroupPath.split(".")
    propGroup = parentID
    for p in path:
        propGroup = getattr(propGroup, p)
    return propGroup


def isRestrictedContext(ctx: bpy.types.Context):
    """ Return True if this is a restricted context. """
    return type(ctx).__name__ == '_RestrictContext'



def parseFramesToFlatList(inputString: str):
    """ Parses a custom frame string into a sorted flat list of unique frames.
        Example: "5, 20-24:2, 10-12, 10" -> [5, 10, 11, 12, 20, 22, 24]
    """

    segments = inputString.replace(' ', '').replace('..', '-').split(",")

    frames = set()

    for segment in segments:
        if not segment:
            # Skip empty items
            continue

        try:
            match = re.fullmatch(r"(\d+)-(\d+)(?:\:(\d+))?", segment)
            if match:
                startFrame = int(match.group(1))
                endFrame   = int(match.group(2))
                step       = int(match.group(3) or 1)

                if endFrame < startFrame:
                    return None, f"Reversed frame range in frames list: {segment}"

                frames.update(range(startFrame, endFrame + 1, step))
            else:
                # Single frame
                startFrame = int(segment)
                if startFrame < 0:
                    return None, "Negative frame number in frames list"
                frames.add(startFrame)
        except Exception:
            return None, f"Invalid frame range specification: {inputString}"

    return sorted(frames), None


def framesToSequences(frames: list[int]):
    """ Groups a sorted list of unique frames into evenly stepped [start, end, step] runs.
        Example: [5, 10, 11, 12, 20, 22, 24] -> [[5, 10, 5], [11, 12, 1], [20, 24, 2]]

        The sequences must expand to exactly the frames they were built from, in the same
        order, because the render sequence sent to V-Ray and the frame list used to drive
        the export are matched up by index while rendering.
    """

    sequences = []
    i = 0

    while i < len(frames):
        if i + 1 == len(frames):
            sequences.append([frames[i], frames[i], 1])
            break

        step = frames[i + 1] - frames[i]
        end = i + 1
        while (end + 1 < len(frames)) and (frames[end + 1] - frames[end] == step):
            end += 1

        sequences.append([frames[i], frames[end], step])
        i = end + 1

    return sequences

