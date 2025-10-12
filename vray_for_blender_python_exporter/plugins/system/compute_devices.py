import bpy
import sys
from vray_blender.bin import VRayBlenderLib as vray
from vray_blender.engine import resetActiveIprRendering

class DeviceType:
    CUDA = "0"
    RTX = "1"
    METAL = "2"


class ComputeDeviceSelector(bpy.types.PropertyGroup):
    """ Property group that show the state of a single compute device in the UI """
    
    deviceId: bpy.props.IntProperty(
        name="Device ID",
        description="Unique identifier for the compute device",
        default=0
    )
    
    deviceName: bpy.props.StringProperty(
        name="Device Name",
        description="Display name for the compute device",
        default=""
    )
    
    deviceEnabled: bpy.props.BoolProperty(
        name="Enabled",
        description="Enable this compute device for rendering",
        default=True,
        update=lambda self, context: self._updateDeviceEnabled(context)
    )

    def _updateDeviceEnabled(self, context):
        """Update the device enabled state"""
        computeDevices = context.scene.vray.Exporter.computeDevices

        if not computeDevices.devicesUpdatingEnabled:
            return
        if sys.platform == "win32":
            deviceIsRTX = computeDevices.gpuDeviceType == DeviceType.RTX
            devicesList = computeDevices.devicesOptix if deviceIsRTX else computeDevices.devicesCUDA
        else:
            devicesList = computeDevices.devicesMetal

        vray.setComputeDevices([d.deviceId for d in devicesList if d.deviceEnabled], int(DeviceType.METAL))

        # Reset the active ipr rendering ensuring that the changes has taken effect.
        if sys.platform == "win32" and context.scene.vray.Exporter.use_gpu_rtx == deviceIsRTX:
            resetActiveIprRendering()


class ComputeDevices(bpy.types.PropertyGroup):
    """Property group for managing compute device selection"""

    devicesUpdatingEnabled: bpy.props.BoolProperty(
        name="Compute Device Setting Enabled",
        description="Enable setting the states of compute devices",
        default=True
    )
    
    gpuDeviceType: bpy.props.EnumProperty(
        name="GPU Device Type",
        description="Choose between CUDA and RTX for GPU rendering",
        items=(
            (DeviceType.CUDA, "CUDA", "Use CUDA for GPU rendering"),
            (DeviceType.RTX, "RTX", "Use RTX for GPU rendering (if supported)"), # Optix
        ),
        default='0'
    )

    devicesCUDA: bpy.props.CollectionProperty(
        name="Compute Devices CUDA",
        description="Collection of available CUDA compute devices",
        type=ComputeDeviceSelector
    )

    devicesOptix: bpy.props.CollectionProperty(
        name="Compute Devices Optix",
        description="Collection of available Optix compute devices",
        type=ComputeDeviceSelector
    )

    devicesMetal: bpy.props.CollectionProperty(
        name="Compute Devices Metal",
        description="Collection of available Metal compute devices",
        type=ComputeDeviceSelector
    )

    def updateComputeDeviceSelectors(self, devicesType: int, deviceNames: list[str], defaultDeviceStates: list[bool]):
        """ Update the compute device selectors
        
        Args:
            deviceType (int): The type of device to update
            deviceNames (list[str]): A list of the names of the devices to update
            defaultDeviceStates (list[bool]): A list of the enabled states of the devices to update
        """

        if sys.platform == "win32":
            devices = self.devicesCUDA if str(devicesType) == DeviceType.CUDA else self.devicesOptix
        else:
            devices = self.devicesMetal

        # Save previous states of device selectors that are in deviceType
        devicePreviousState = {d.deviceName: d.deviceEnabled for d in devices}
        
        # Disable the devicesUpdatingEnabled property to prevent the device selectors from triggering
        # their update function as it calls vray.setComputeDevices(). 
        self.devicesUpdatingEnabled = False

        # Clear any existing device selectors
        devices.clear()

        for id, deviceName in enumerate(deviceNames):
            newDevice = devices.add()
            newDevice.deviceId = id
            newDevice.deviceName = deviceName
            
            if deviceName not in devicePreviousState:
                newDevice.deviceEnabled = False
            else:
                newDevice.deviceEnabled = devicePreviousState[deviceName]
        
        # Check if there are any enabled devices in the current device collection,
        # and if not, use the defaultDeviceStates dictionary to enable the devices.
        if not any(d.deviceEnabled for d in devices):
            for d in devices:
                d.deviceEnabled = defaultDeviceStates[d.deviceId]

        # Now set the enabled devices
        vray.setComputeDevices([d.deviceId for d in devices if d.deviceEnabled], devicesType if sys.platform != "win32" else int(DeviceType.METAL))

        self.devicesUpdatingEnabled = True



# Note: The registration of ComputeDeviceSelector and ComputeDevices property groups
# is handled in Exporter.py to ensure that they are available before being referenced
# in the VRayExporter property group.