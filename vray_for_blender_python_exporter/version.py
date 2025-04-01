import bpy
import os

from vray_blender import debug

# Version is stored in bl_info in __init__.py
from vray_blender import bl_info as vray_bl_info, UPGRADE_NUMBER
from vray_blender import build_number


def getAddonVersion():
    """ Get the 'version' field of bl_info as a string """
    vrayVer = vray_bl_info["version"]
    return '.'.join(vrayVer)


def getAddonUpgradeNumber():
    # Return the upgrade number in format 000# (the one used in upgrade script names)
    return f"{UPGRADE_NUMBER:-04}"


def getSceneUpgradeNumber():
    upgradeNum = bpy.context.scene.vray.Exporter.vrayAddonUpgradeNumber
    
    if upgradeNum == 0:
        # 0 is a special number for legacy scenes created before the concept was introduced.
        sceneVer = getNumericVersion(getSceneVersionString())

        if sceneVer == '6.20.00':
            upgradeNum = 0
        elif sceneVer == '7.00.10':
            upgradeNum = 1
        else:
            debug.report('WARNING', "Obsolete scene format. Automatic upgrade will be attempted but may fail.")
        
    # Return string in 000# format to allow sorting.
    return f"{upgradeNum:-04}"


def setSceneUpgradeNumber(upgradeNum: int):
    bpy.context.scene.vray.Exporter.vrayAddonUpgradeNumber = upgradeNum


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


def getVersionAsNumbers(ver: str):
    return [int(v) for v in ver.split('.')]


def findUpgradeScripts(fromUpgradeNum: str, toUpgradeNum: str):
    """ Return a list of all upgrade modules that need to be run in order to upgrade
        a scene to the required version.

    Args:
        fromVersionString (str): the current version of the scene
        toVersionString (str): the desired version of the scene
    """
    from pathlib import PurePath
    from vray_blender.lib.path_utils import getUpgradeScriptsDir

    upgradeScriptsDir = getUpgradeScriptsDir()
    allScripts = [] # List of tuple(upgradeNumber, fromVer, toVer, moduleName)

    for _, _, fileNames in os.walk(upgradeScriptsDir):
        for file in fileNames:
            path = PurePath(file)
            if path.suffix == '.py':
                ver = path.stem[len('upgrade_'):]
                parts  = ver.split('__')
                allScripts.append((parts[0], parts[1], parts[2], path.stem))
        # Do not recurse
        break    
    
    relevantScripts = [v for v in allScripts if fromUpgradeNum < v[0] and v[0] <= toUpgradeNum]
    return sorted(relevantScripts)


def upgradeScene(fromUpgradeNum: str, toUpgradeNum: str):
    import importlib
    from vray_blender.events import isDefaultScene

    if isDefaultScene():
        debug.printAlways(f"Upgrading default scene")
    else:
        debug.printAlways(f"Upgrading scene {bpy.data.filepath}")

    scriptInfos = findUpgradeScripts(fromUpgradeNum, toUpgradeNum)

    # Run in succession all scripts needed to upgrade from the scene version to the
    # current addon version. Any error will abort the procedure and alert the user.
    for scriptInfo in scriptInfos:
        upgradeNum    = scriptInfo[0]
        toVer         = scriptInfo[2].replace('_', '.')
        upgradeScript = scriptInfo[3]

        upgradeScriptModule = f"vray_blender.resources.upgrade_scripts.{upgradeScript}"
        upgradeModule = None
        
        try:
            upgradeModule = importlib.import_module(upgradeScriptModule)
        except ImportError as e:
            debug.printError(f"Failed to import upgrade script: {upgradeScriptModule}")
            
        debug.printAlways(f"Running upgrade script {upgradeScriptModule}")
        
        try:
            upgradeModule.run()

            # Set the new upgrade number to the scene
            setSceneUpgradeNumber(int(upgradeNum))
        except  Exception as e:
            debug.printExceptionInfo(e, f"Scene upgrade failed with exception.")
            debug.report('INFO', f'Scene upgrade failed. See console log for details.')
            return False

    debug.report('INFO', f"Scene upgraded to current VRay for Blender version.")

    return True

