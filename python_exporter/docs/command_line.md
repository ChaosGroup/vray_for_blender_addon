# VRay for Blender command line parameters

[Home](../README.md)


These parameters are only meant to be used by developers.

V-Ray parameters should be added to the Blender's command line separated from Blender's native parameters by a double-dash:

`blender.exe --blender_arg1 --blender_arg2 -- --vray_arg1 --vray_arg2`

### V-Ray for Blender command line parameters:

`--vray-debug-ui` 
  * mark VRay UI panels using an asterisk (*) after the name
  * make visible some developer-only UI
  * enable writing of VRayZmqServer log to the default location (USER_TEMP/vray_blender)

`--vray-server-folder path_to_folder`
  * change the location of VRayZmqServer

`--dumpInfoLog path_to_folder`
  * enable writing of VRayZmqServer log to the specified location 