
import re
import datetime
import struct
import uuid

import bpy

from vray_blender.lib import path_utils


" Match Blender light ot V-Ray plugin"
LightBlenderToVrayPlugin = {
    'AREA'  : 'LightRectangle',
    'POINT' : 'LightOmni',
    'SPOT'  : 'LightSpot',
    'SUN'   : 'SunLight',
}

" Match V-Ray light type to Blender light type"
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

" Match V-Ray light type ot V-Ray plugin"
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


def getLightPluginName(light):
    if light.vray.light_type == 'BLENDER':
        return LightBlenderToVrayPlugin[light.type]
    
    return LightTypeToPlugin[light.vray.light_type]


def getLightPropGroup(light):
    return getattr(light.vray, getLightPluginName(light))



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


# This funciton will substitue special format sequences with
# the correspondent values
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
    for v in re.findall("%\w", s):
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
