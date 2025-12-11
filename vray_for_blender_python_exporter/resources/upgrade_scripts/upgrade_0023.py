import bpy
from vray_blender.lib.blender_utils import getVRayPreferences

def _transferDevices(newDevices, perSceneDevices):
    if [d.deviceName for d in newDevices] == [d.deviceName for d in perSceneDevices]:
        newDevices.clear()
        for device in perSceneDevices:
            newDevice = newDevices.add()
            newDevice.deviceId = device.deviceId
            newDevice.deviceName = device.deviceName
            newDevice.deviceEnabled = device.deviceEnabled

def run():
    prefs = getVRayPreferences()

    computeDevices = prefs.compute_devices
    exporter = bpy.context.scene.vray.Exporter
    perSceneDevices = exporter.computeDevices

    _transferDevices(computeDevices.devicesCUDA, perSceneDevices.devicesCUDA)
    _transferDevices(computeDevices.devicesOptix, perSceneDevices.devicesOptix)

    prefs.verbose_level = exporter.verbose_level

    prefs.loaded_from_scene = True

def check():
    return not getVRayPreferences().loaded_from_scene