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
    """ Returns the V-Ray version number of the scene in format '0000'. 
        The first upgrade script is numbered 0001, so we can return 0 for
        both pre-version scenes and such not created with V-Ray. In both
        cases all upgrade scripts need to be run.
    """
    upgradeNum = bpy.context.scene.vray.Exporter.vrayAddonUpgradeNumber
    
    if upgradeNum == 0:
        # 0 is a special number for legacy scenes created before the concept of upgrade version 
        # number was introduced, and for scenes that were not created using V-Ray.
        sceneVer = getNumericVersion(getSceneVersionString())

        if sceneVer == '6.20.00':
            upgradeNum = 0 # Explicitly set to the same value just for clarity
        if sceneVer == '7.00.10':
            upgradeNum = 1

    # Return string in 0000 format to allow sorting.
    return f"{upgradeNum:-04}"


def setSceneUpgradeNumber(upgradeNum: int):
    bpy.context.scene.vray.Exporter.vrayAddonUpgradeNumber = upgradeNum


def getBuildVersionString():
    """ Get the full version string identifying the current build of the addon. """
    try:
        vrayVerString = getAddonVersion()
        buildDate = build_number.BUILD_DATE
        
        if build_number.BUILD == 'release':
            # This is an official build, revision number is not relevant
            return f"{vrayVerString} from {buildDate}" 
        else:
            buildVer = build_number.BUILD[0:7]
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
    """ Upgrade the currently loaded scene by running in turn all available upgrade
        scripts for versions between from and to upgarde numbers.

    Args:
        fromUpgradeNum (str): the version to upgrade from
        toUpgradeNum (str): the version to upgrade to

    Returns:
        bool: True if the upgrade was successful
    """
    from vray_blender.events import isDefaultScene
    from vray_blender.lib.sys_utils import importModule

    if isDefaultScene():
        debug.printAlways(f"Updating default scene version")
    else:
        debug.printAlways(f"Updating scene version of {bpy.data.filepath}")

    scriptInfos = findUpgradeScripts(fromUpgradeNum, toUpgradeNum)

    # Run in succession all scripts needed to upgrade from the scene version to the
    # current addon version. Any error will abort the procedure and alert the user.
    for scriptInfo in scriptInfos:
        upgradeNum    = scriptInfo[0]
        toVer         = scriptInfo[2].replace('_', '.')
        upgradeScript = scriptInfo[3]

        upgradeScriptModule = f"resources.upgrade_scripts.{upgradeScript}"
        upgradeModule = None
        
        if (upgradeModule := importModule(upgradeScriptModule)) is None:
            debug.reportError(f'Scene version update failed. See console log for details.')
            return False
            
        debug.printAlways(f"Running version update script {upgradeScriptModule}")
        
        try:
            upgradeModule.run()

            # Set the new upgrade number to the scene
            setSceneUpgradeNumber(int(upgradeNum))
        except  Exception as e:
            debug.reportError(f'Scene version update failed. See console log for details.', exc=e)
            return False

    debug.report('INFO', f"Scene updated to current VRay for Blender version.")

    return True


def checkIfSceneNeedsUpgrade(fromUpgradeNum: str, toUpgradeNum: str):
    """ Check whether the currently loaded scene actuallly contains data which would be upgraded
        by the scripts for versions between from and to upgarde numbers. If there is no such data
        in the scene, just change the scene upgrade version number.

    Args:
        fromUpgradeNum (str): the version to upgrade from
        toUpgradeNum (str): the version to upgrade to

    Returns:
        bool: True if upgrade script(s) should be run
    """

    from vray_blender.lib.sys_utils import importModule

    scriptInfos = findUpgradeScripts(fromUpgradeNum, toUpgradeNum)

    # Run in succession all scripts needed to upgrade from the scene version to the
    # current addon version. Any error will abort the procedure and alert the user.
    for scriptInfo in scriptInfos:
        upgradeScript = scriptInfo[3]

        upgradeScriptModule = f"resources.upgrade_scripts.{upgradeScript}"
        upgradeModule = None
        
        if (upgradeModule := importModule(upgradeScriptModule)) is None:
            debug.reportError(f"Scene version updatecheck failed. See console log for details.", exc=e)
            return False
        
        try:
            if upgradeModule.check():
                return True
        except  Exception as e:
            debug.reportError(f"Scene version upgdate check failed. See console log for details.", exc=e)
            return False

    return False

