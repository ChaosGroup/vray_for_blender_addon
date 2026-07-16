# VRay for Blender Native

`vray_for_blender_native` is a bridge between Blender and the V-Ray rendering system, specifically the VRayZmqServer process. It enables advanced rendering workflows by exporting Blender scene data (meshes, hair, smoke, etc.) to V-Ray, managing rendering jobs, and providing real-time communication and control via ZeroMQ. The library exposes its functionality to Python using Boost.Python, making it easy to integrate with Blender add-ons.


## Directory Structure

- `api/` – Python API bindings and interop code
- `export/` – Scene and asset exporters, ZMQ server, and rendering logic
- `utils/` – Logging, threading, file writing, and platform utilities

## Usage

- Import and use the Python API in your Blender add-on to control V-Ray rendering, export scenes, and manage assets.
- See `api/python_api.cpp` for available functions and usage patterns.
- Example (Python):

   ```python
   import VRayBlenderLib
   VRayBlenderLib.init('vray_log.txt')
   # ... create renderer, export scene, start rendering, etc.
   VRayBlenderLib.exit()
   ```