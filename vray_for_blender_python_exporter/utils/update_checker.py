# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later


import bpy

from datetime import datetime, timedelta
import json
from os import getenv
import urllib.request

from vray_blender import debug, bl_info
from vray_blender.lib import blender_utils
from vray_blender.lib.mixin import VRayOperatorBase
from vray_blender.version import getBuildVersionString

from vray_blender.bin import VRayBlenderLib as vray


_UPDATE_CONFIG_URL = "https://config.static.chaos.com/vblender/version.json"

if vray.isCommunityEdition:
    _PRODUCT_NAME = f"{bl_info['name']} Community Edition"
    _VERSION_NAME = "CE"
else:
    _PRODUCT_NAME = bl_info['name']
    _VERSION_NAME = "Full"

# When automatic checks are enabled, they will be performed at this interval
# during the same Blender session.
_AUTO_CHECK_INTERVAL_HOURS = 24

# Pulse for the new version check. An actual check will be performed
# only if the confuguration at the time of invocation permits it.
_UPDATE_CHECK_PULSE_SECONDS = 1800


class UpdateStatus:
    Error     = 0
    Available = 1
    NoUpdate  = 2

    current = NoUpdate
    version = []
    versionString = ""
    downloadURL = ""

    

def _downloadUpdateConfig(url: str):
    """ Download a JSON file and return its contents as a Python dictionary """
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            str_response = response.read().decode('utf-8')
            return json.loads(str_response)
    except Exception as e:
        debug.printError(f"Failed to download version config from {url}: {e}")
        return None


def _isAddonUpdated(versionConfig):
    """ Return True if the version of the addon is less than the version 
        stored in the version confing file downloaded from the webconfig 
        service.

        Args:
        addonConfig: the version config file as dictionary
    """
    from vray_blender import bl_info

    lastVersion    = [int(v) for v in versionConfig['VersionChecker'][_VERSION_NAME]['LastVersion'].split('.')]
    currentVersion = [int(v) for v in bl_info['version']]
    
    return currentVersion < lastVersion


def _showUpdateDialog():
    currentVersion = f"{_PRODUCT_NAME} {getBuildVersionString()}"
    latestVersion  = UpdateStatus.versionString
    hasNewVersion  = UpdateStatus.current == UpdateStatus.Available

    dlg = {
        'type': 'UpdateDialog',
        'downloadURL': UpdateStatus.downloadURL,
        'currentVersion': currentVersion,
        'latestVersion': latestVersion if hasNewVersion else "",
        'autoCheck': 'true' if blender_utils.getVRayPreferences(bpy.context).auto_check_for_updates else 'false'
    }

    vray.showUserDialog(json.dumps(dlg))


def onInitialCheck():
    """ Called the very first time after Blender is started """
    checkForUpdates(force=True, showDialog=False)
    

def onCheckTimer():
    """ Check whether a new verison of the addon is available. """
    checkForUpdates(force=False, showDialog=False)
    return _UPDATE_CHECK_PULSE_SECONDS


def _getUpdateInfo():
    """ Check for an updated version of V-Ray for Blender.

        Args:
        force:  If False, perform the check only if the Auto Check is OFF and
                a predefined interval has elapsed from the last check.
    """
    prefs = blender_utils.getVRayPreferences(bpy.context)
    
    if config := _downloadUpdateConfig(_UPDATE_CONFIG_URL):
        prefs.last_check_for_updates = datetime.now().timestamp()
        
        isNewVersionAvailable = _isAddonUpdated(config)
        versionConfig = config['VersionChecker'][_VERSION_NAME]
        latestVersion = versionConfig['LastVersion']
        
        UpdateStatus.current = UpdateStatus.Available if isNewVersionAvailable else UpdateStatus.NoUpdate
        UpdateStatus.version = latestVersion.split(".")
        UpdateStatus.versionString = f"{_PRODUCT_NAME} (v{latestVersion})"
        UpdateStatus.downloadURL = versionConfig['DownloadURL']
        
        if isNewVersionAvailable:
            debug.printInfo(f"New V-Ray for Blender version available: {UpdateStatus.versionString}")
    else:
        UpdateStatus.current = UpdateStatus.Error

    vray.setUpdateAvailable(UpdateStatus.current == UpdateStatus.Available)
    

def checkForUpdates(force: bool, showDialog: bool):
    """ Runs the check procedure 
    
        Args:
        force: Force the check even if the auto update timeout has not been expired yet
        showDialog: show a user dialog with the result of the check
    """
    if (not force) and (not _shouldCheckForUpdates()):
        return
        
    _getUpdateInfo()
    
    if showDialog:
        if UpdateStatus.current != UpdateStatus.Error:
            _showUpdateDialog()
        else:
            bpy.ops.vray.message_box('INVOKE_DEFAULT', 
                                     message = 'An error occurred while checking for updates.\n' +
                                               'We were unable to fetch update information,\n' +
                                               'please try again later.',
                                     icon = 'ERROR')


def _shouldCheckForUpdates():
    prefs = blender_utils.getVRayPreferences(bpy.context)

    if not prefs.auto_check_for_updates:
        return False
            
    # NOTE: last_check_for_updates has about 1 minute resolution due to 
    # the underlying Float type representation. This is quite sufficient for now
    # but may be confusing during debugging.
    nextCheckTime = datetime.fromtimestamp(prefs.last_check_for_updates) + timedelta(hours=_AUTO_CHECK_INTERVAL_HOURS)
    if datetime.now() < nextCheckTime:
        return False
    
    return True
   

def onUpdateSettingsChanged(autoCheck: bool):
    """ A callback for the Update dialog shown by the ZmqServer 
    
        Args:
        autoCheck: the new value of Auto Check set by the user
    """
    prefs = blender_utils.getVRayPreferences(bpy.context)
    
    if prefs.auto_check_for_updates != autoCheck:
        # Avoid recursion
        prefs.auto_check_for_updates = autoCheck

    if autoCheck:
        # Set force to True so that when the user turns autoupdates on, 
        # the first check was performed immediately
        checkForUpdates(force=True, showDialog=False)


def autoCheckForUpdatesFeatureEnabled():
    from vray_blender.version import isReleaseBuild
    return isReleaseBuild() or (getenv("VBLENDER_AUTO_CHECK_FOR_UPDATES") == '1')
 
    
class VRAY_OT_check_for_updates(VRayOperatorBase):
    bl_idname      = "vray.check_for_updates"
    bl_label       = "Check for updates"
    bl_description = "Check for an updated version of the V-Ray Addon."
    bl_options     = {'INTERNAL'}

    def execute(self, context):
        checkForUpdates(force=True, showDialog=True)
        return {'FINISHED'}
    

    @classmethod
    def description(cls, context, properties):
        from vray_blender.utils.update_checker import UpdateStatus
        if UpdateStatus.current == UpdateStatus.Available:
            return "There is a new version of the V-Ray addon. Click here for more info."
        return __class__.bl_description

def _getRegClasses():
    return (
        VRAY_OT_check_for_updates,
    )


def register():
    for regClass in _getRegClasses():
        bpy.utils.register_class(regClass)


def unregister():
    for regClass in _getRegClasses():
        bpy.utils.unregister_class(regClass)
