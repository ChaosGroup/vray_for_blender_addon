# V-Ray for Blender Python Exporter


`vray_for_blender_python_exporter` is a Blender add-on that acts as the main Python layer for V-Ray integration. It exports Blender scene data (geometry, materials, animation, etc.) to V-Ray, manages rendering jobs, and provides a flexible UI and API for artists and technical users. The add-on communicates with the V-Ray backend (aka V-Ray ZMQ server) via VRayBlenderLib (aka V-Ray For Blender Native) and supports both interactive and production rendering workflows.


## Directory Structure

- `engine/` – Render engine integration, scheduling, and communication
- `exporting/` – Scene, object, and property exporters
- `plugins/` – V-Ray plugin definitions and templates
- `lib/` – Utility modules (attributes, paths, color, etc.)
- `nodes/` – Custom node types, sockets, and node UI
- `ui/` – Property panels, menus, and UI classes
- `utils/` – Cosmos, upgrade, and baking utilities
- `docs/` – Documentation for command line, animation, meta properties, sockets, and UI templates


## Documentation

- [Command line options](docs/command_line.md)
- [Animation export](docs/animation_export.md)
- [Meta properties](docs/meta_properties.md)
- [Custom sockets](docs/sockets.md)
- [UI templates](docs/ui_templates.md)
