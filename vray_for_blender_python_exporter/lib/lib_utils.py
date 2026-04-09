# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later


import re
import datetime
import uuid

import bpy

from vray_blender import debug
from vray_blender.lib import path_utils


# Match Blender light to V-Ray plugin
LightBlenderToVrayPlugin = {
    'AREA'  : 'LightRectangle',
    'POINT' : 'LightOmni',
    'SPOT'  : 'LightSpot',
    'SUN'   : 'SunLight',
}

# Match V-Ray light type to Blender light type
LightVrayTypeToBlender = {
    'AMBIENT' : 'POINT',
    'DIRECT'  : 'POINT',
    'IES'     : 'POINT',
    'MESH'    : 'POINT',
    'OMNI'    : 'POINT',
    'SPHERE'  : 'POINT',
    'SPOT'    : 'SPOT',
    'SUN'     : 'SUN',
    'RECT'    : 'AREA',
    'DOME'    : 'POINT'
}

# Match V-Ray light type to V-Ray plugin
LightTypeToPlugin = {
    'AMBIENT' : 'LightAmbient',
    'DIRECT'  : 'MayaLightDirect',
    'IES'     : 'LightIES',
    'MESH'    : 'LightMesh',
    'OMNI'    : 'LightOmni',
    'SPHERE'  : 'LightSphere',
    'SPOT'    : 'LightSpot',
    'SUN'     : 'SunLight',
    'RECT'    : 'LightRectangle',
    'DOME'    : 'LightDome'
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


# This function will substitute special format sequences with
# the corresponding values
#
def getDefFormatDict():
    blendFileName = None
    sceneName     = None
    cameraName    = None

    # During registration bpy.data is not yet ready
    if type(bpy.data) is bpy.types.BlendData:
        scene = bpy.context.scene

        # Blend-file name without extension
        blendFileName = path_utils.getFilename(bpy.data.filepath, ext=False) if bpy.data.filepath else "default"

        blendFileName = cleanString(blendFileName, stripSigns=False)
        sceneName     = cleanString(scene.name)
        cameraName    = cleanString(scene.camera.name) if scene.camera else None

    formatDict = {
        '$C': ("Camera Name", cameraName if cameraName else "CameraName"),
        '$S': ("Scene Name", sceneName),
        '$F': ("Blendfile Name", blendFileName),
    }

    return formatDict


def formatVariablesDesc():
    FormatVariablesDict = getDefFormatDict()

    format_vars = ["%s - %s" % (v, FormatVariablesDict[v][0]) for v in FormatVariablesDict]

    format_help = "; ".join(format_vars)
    format_help += "; Any time variable (see Python's \"datetime\" module help)"

    return format_help


def formatName(s, formatDict=None):
    if not formatDict:
        formatDict = getDefFormatDict()

    for v in formatDict:
        s = s.replace(v, formatDict[v][1])

    t = datetime.datetime.now()
    for v in re.findall(r"%\w", s):
        try:
            s = s.replace(v, t.strftime(v))
        except:
            pass

    return s


def getPropGroup(parentID, propGroupPath):
    path = propGroupPath.split(".")
    propGroup = parentID
    for p in path:
        propGroup = getattr(propGroup, p)
    return propGroup


def isRestrictedContext(ctx: bpy.types.Context):
    """ Return True if this is a restricted context. """
    return type(ctx).__name__ == '_RestrictContext'



def parseFrames(inputString: str, fnAppendFrame, fnAppendRange):
    """ Parses a custom frame string into a single flat list of unique frames.
        Example: "1,5,10-12, 20-24:2" -> [1, 5, 10, 11, 12, 20, 22, 24]
    """
    
    segments = inputString.replace(' ', '').replace('..', '-').split(",")

    flatFrameList = []
    
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
                
                fnAppendRange(flatFrameList, startFrame, endFrame, step)
            else:
                # Single frame
                startFrame = int(segment)
                if startFrame < 0:
                    debug.printError("Negative frame number in frames list")
                    return None
                fnAppendFrame(flatFrameList, startFrame)
        except:
            debug.printError(f"Invalid frame range specification: {inputString}")
            return None
        
    return flatFrameList


def parseFramesToSequences(inputString: str):
    """ Parses a custom frame string into a list of (start, end, step) items.
        Example: "5,10-12, 20-24:2" -> [[5,5,1], [10,12,1], [20,24,2]]
    """
    
    def _appendFrame(seq, frame: int):
        seq.append([frame, frame, 1])

    def _appendRange(seq, start: int, end: int, step: int):
        seq.append([start, end, step])

    return parseFrames(inputString, _appendFrame, _appendRange)



def parseFramesToFlatList(inputString: str):
    """ Parses a custom frame string into a flat list of frames.
        Example: "5,10-12, 20-24:2" -> [5, 10, 11, 12, 20, 22, 24]
    """

    def _appendFrame(flatList, frame: int):
        flatList.append(frame)

    def _appendRange(flatList, start: int, end: int, step: int):
        flatList.extend(list(range(start, end + 1, step)))

    return parseFrames(inputString, _appendFrame, _appendRange)

def filterSequencesByViewLayerUse(viewLayerName: str, sequences: list[list[float]]):
    """ Filter the parts of the sequences that are not enabled in the selected view layer """

    from vray_blender.lib.blender_utils import getViewLayerUseFCurve

    if vlFCurve := getViewLayerUseFCurve(viewLayerName):
        
        filteredSequences = []
        for sequence in sequences:
            startFrame = None
            endFrame = None

            sequenceStart, sequenceEnd, step = sequence
            for frame in range(sequenceStart, sequenceEnd + 1, step):
                vlayerEnabled = vlFCurve.evaluate(frame)

                if not vlayerEnabled and startFrame:
                    endFrame = max(frame - step, startFrame)
                    filteredSequences.append([startFrame, endFrame, step])
                    startFrame = None
                if vlayerEnabled and not startFrame:
                    startFrame = frame
            
            if (not endFrame) and startFrame:
                filteredSequences.append([startFrame, sequenceEnd - (sequenceEnd - startFrame) % step, step])
                
        return filteredSequences
    return sequences

