# Version is stored in bl_info in __init__.py
from vray_blender import bl_info as vray_bl_info
from vray_blender import build_number
import bpy


def getAddonVersion():
    """ Get the 'version' field of bl_info as a string """
    vrayVer = vray_bl_info["version"]
    return '.'.join(vrayVer)


def getBuildVersionString():
    """ Get the full version string identifying the current build of the addon. """
    try:
        vrayVerString = getAddonVersion()
        buildVer = build_number.BUILD[0:7]
        buildDate = build_number.BUILD_DATE
    
        return f"{vrayVerString}, revision {buildVer} from {buildDate}" 

    except (KeyError, IndexError):
        raise Exception("Could not read V-Ray addon data!")


def getSceneVersionString():
    """ Get the V-Ray addon version used to save the scene. """
    return bpy.context.scene.vray.Exporter.vrayAddonVersion
    

def getHostAppVersionString():
    """ Get addon version string suitable for using in the header of exported .vrscene files.

    Returns:
        str: The host app version string.
    """
    vrayVerString = getAddonVersion()
    return f"V-Ray for Blender, {build_number.BUILD} ({vrayVerString})" 


def getNumericVersion(versionString:str):
    return versionString.split(',')[0]


def getUpgradeScriptModule(fromVersionString:str, toVersionString: str):
    """ Return the import declaration for the script which will perform the 
        upgrade between the two versions.
    """
    # There are some scenes that have been saved before the versioning was implememted.
    # For them, run the scripts from the very beginning
    FIRST_RECORDED_VERSION = "6_20_00"
    fromVer = getNumericVersion(fromVersionString).replace('.', '_') or FIRST_RECORDED_VERSION
    toVer   = getNumericVersion(toVersionString).replace('.', '_')
    
    # Use a double underscore to separate the versions so that we could unambiguously
    # parse them from the file name, e.g. when more than one consecutive upgrade has
    # to be done and we need to determine which upgrade scripts to run.
    return f"vray_blender.resources.upgrade_scripts.upgrade_{fromVer}__{toVer}"