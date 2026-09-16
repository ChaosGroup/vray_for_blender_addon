# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
import os

from vray_blender import debug

# Version is stored in bl_info in __init__.py
from vray_blender import bl_info as vray_bl_info, UPGRADE_NUMBER
from vray_blender import build_number

from vray_blender.bin import VRayBlenderLib as vray

def getAddonVersion():
    """ Get the 'version' field of bl_info as a string """
    vrayVer = vray_bl_info["version"]
    return '.'.join(vrayVer)


def getAddonUpgradeNumber():
    # Return the upgrade number in format 000# (the one used in upgrade script names)
    return f"{UPGRADE_NUMBER:-04}"


def getSceneUpgradeNumber(scene: bpy.types.Scene = None):
    """ Returns the V-Ray version number of the scene in format '0000'.
        The first upgrade script is numbered 0001, so we can return 0 for
        both pre-version scenes and such not created with V-Ray. In both
        cases all upgrade scripts need to be run.

        `scene` defaults to the active scene; pass a specific scene (e.g. one
        linked from a source file during append) to read that scene's number.
    """

    from vray_blender.lib.blender_utils import isDefaultScene

    scene = scene or bpy.context.scene
    upgradeNum = scene.vray.Exporter.vrayAddonUpgradeNumber
    if upgradeNum == 0:
        # 0 is a special number for legacy scenes created before the concept of upgrade version
        # number was introduced, and for scenes that were not created using V-Ray.
        sceneVer = getNumericVersion(getSceneVersionString(scene))
        if sceneVer == '6.20.00':
            upgradeNum = 0 # Explicitly set to the same value just for clarity
        if sceneVer== '7.00.10':
            upgradeNum = 1
        elif sceneVer == '7.00.40':
            upgradeNum = 12
        elif sceneVer == '7.10.00':
            upgradeNum = 20
        elif sceneVer == '7.10.01':
            upgradeNum = 21
        elif sceneVer == '7.20.00':
            upgradeNum = 29
        elif sceneVer == '7.20.01':
            upgradeNum = 33
        elif sceneVer: # Most likely a non-upgraded default scene
            upgradeNum = UPGRADE_NUMBER
        else:
            upgradeNum = 1

    # Return string in 0000 format to allow sorting.
    return f"{upgradeNum:-04}"


def setSceneUpgradeNumber(upgradeNum: int):
    bpy.context.scene.vray.Exporter.vrayAddonUpgradeNumber = upgradeNum


def getDatablockUpgradeNumber(datablock, defaultWhenUnset: int) -> int:
    """ Return the V-Ray upgrade number stamped on a datablock's `.vray` group.
        If it was never stamped (e.g. data from an older file, or appended data),
        return `defaultWhenUnset` - the scene number on a full load, 0 on append.
    """
    vrayProps = datablock.vray
    if vrayProps.is_property_set('upgradeNumber'):
        return vrayProps.upgradeNumber
    return defaultWhenUnset


def setDatablockUpgradeNumber(datablock, upgradeNum: int):
    datablock.vray.upgradeNumber = upgradeNum


def getBuildVersionString():
    """ Get the full version string identifying the current build of the addon. """
    try:
        vrayVerString = getAddonVersion()
        buildDate = build_number.BUILD_DATE

        if isReleaseBuild():
            # This is an official build, revision number is not relevant
            return f"{vrayVerString} from {buildDate}"
        else:
            buildVer = build_number.BUILD[0:7]
            return f"{vrayVerString}, revision {buildVer} from {buildDate}"

    except (KeyError, IndexError):
        raise Exception("Could not read V-Ray addon data!")


def getSceneVersionString(scene: bpy.types.Scene = None):
    """ Get the V-Ray addon version used to save the scene (defaults to active scene). """
    return (scene or bpy.context.scene).vray.Exporter.vrayAddonVersion


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


def formatNumericVersion(ver: list[str]):
    """ Return the version as numbers only, e.g. 7.20.01.

        Args:
        ver: a list of major, minor, hotfix version numbers as strings
    """
    assert len(ver) == 3
    return '.'.join(ver)


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
                allScripts.append((ver, path.stem))
        # Do not recurse
        break

    relevantScripts = [v for v in allScripts if fromUpgradeNum < v[0] and v[0] <= toUpgradeNum]
    return sorted(relevantScripts)


def upgradeScene(fromUpgradeNum: str, toUpgradeNum: str, writeSceneNumber: bool = True):
    """ Upgrade the currently loaded scene by running in turn all available upgrade
        scripts for versions between from and to upgarde numbers.

    Args:
        fromUpgradeNum (str): the version to upgrade from
        toUpgradeNum (str): the version to upgrade to
        writeSceneNumber (bool): advance the active scene's upgrade number after each
            script. Set False for append/link, where the active scene must not change
            (the imported datablocks carry their own numbers, see upgradeImportedData).

    Returns:
        bool: True if the upgrade was successful
    """
    from vray_blender.lib.blender_utils import isDefaultScene
    from vray_blender.lib.sys_utils import importModule
    from vray_blender.nodes.utils import DisableAutoConnect
    from vray_blender.utils.upgrade_scene import setCurrentScriptNum

    sceneName = "default scene" if isDefaultScene() else f"'{bpy.data.filepath}'"
    debug.printAlways(f"Update scene from v{int(fromUpgradeNum)} to v{int(toUpgradeNum)}: {sceneName}")

    scriptInfos = findUpgradeScripts(fromUpgradeNum, toUpgradeNum)

    # Run in succession all scripts needed to upgrade from the scene version to the
    # current addon version. Any error will abort the procedure and alert the user.
    for scriptInfo in scriptInfos:
        upgradeNum    = scriptInfo[0]
        upgradeScript = scriptInfo[1]

        upgradeScriptModule = f"resources.upgrade_scripts.{upgradeScript}"
        upgradeModule = None

        if (upgradeModule := importModule(upgradeScriptModule)) is None:
            debug.reportError(f'Scene version update failed. See console log for details.')
            return False

        debug.printDebug(f"Running version update script {upgradeScriptModule}")

        # Tell the scope helpers which script is running so scripts can gate their
        # per-datablock iterations via scopedForUpgrade().
        setCurrentScriptNum(int(upgradeNum))

        try:
            # Not all upgrades in the range may affect the current scene, run only the ones that do
            if upgradeModule.check():
                with DisableAutoConnect():
                    upgradeModule.run()

            # Set the new upgrade number to the scene
            if writeSceneNumber:
                setSceneUpgradeNumber(int(upgradeNum))
        except  Exception as e:
            debug.reportError(f'Scene version update failed. See console log for details.', exc=e)
            return False

    debug.printAlways("Scene successfully updated to current VRay for Blender version.")

    return True


def upgradeImportedData(importedUids: set, baselineVersion: int = 0) -> bool:
    """ Upgrade appended/linked datablocks.

        Runs the upgrade chain scoped to the imported datablocks, starting from
        `baselineVersion` - the version the imported data was created with (read from
        the source file's scene) so already-applied scripts are NOT re-run. A stamped
        datablock (from a newer file) uses its own recorded number instead. The active
        scene's upgrade number is left untouched; imported datablocks are stamped to
        the current addon version on success.

    Args:
        importedUids (set[int]): session_uid values of the imported datablocks.
        baselineVersion (int): version to upgrade un-stamped imported data from.

    Returns:
        bool: True if the upgrade was successful
    """
    from vray_blender.utils.upgrade_scene import UpgradeScope, stampScopedDatablocks

    if not importedUids:
        return True

    addonUpgradeNum = getAddonUpgradeNumber()
    with UpgradeScope(importedUids, defaultVersion=baselineVersion):
        ok = upgradeScene(f"{baselineVersion:-04}", addonUpgradeNum, writeSceneNumber=False)
        if ok:
            stampScopedDatablocks(int(addonUpgradeNum))
    return ok


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
    from vray_blender.utils.upgrade_scene import setCurrentScriptNum

    if fromUpgradeNum == toUpgradeNum:
        return False

    scriptInfos = findUpgradeScripts(fromUpgradeNum, toUpgradeNum)

    # Run in succession all scripts needed to upgrade from the scene version to the
    # current addon version. Any error will abort the procedure and alert the user.
    for scriptInfo in scriptInfos:
        upgradeNum    = scriptInfo[0]
        upgradeScript = scriptInfo[1]

        upgradeScriptModule = f"resources.upgrade_scripts.{upgradeScript}"
        upgradeModule = None

        if (upgradeModule := importModule(upgradeScriptModule)) is None:
            debug.reportError(f"Scene version updatecheck failed. See console log for details.")
            return False

        # Gate scripts' per-datablock iterations (scopedForUpgrade) to this script.
        setCurrentScriptNum(int(upgradeNum))

        try:
            if upgradeModule.check():
                return True
        except  Exception as e:
            debug.reportError(f"Scene version upgdate check failed. See console log for details.", exc=e)
            return False

    return False


def isReleaseBuild():
    """ Return True if the addon's build is of type 'release' """
    from vray_blender import build_number
    return build_number.BUILD == 'release'
