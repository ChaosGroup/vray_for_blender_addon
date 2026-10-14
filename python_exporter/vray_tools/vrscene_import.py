# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

""" Bridge between the native .vrscene import (VRayBlenderLib) and the vrscene dict
    contract consumed by nodes/importing/ and nodes/operators/import_file.py.
"""

import queue
import time

from vray_blender.bin import VRayBlenderLib as vray
from vray_blender.vray_tools import import_common


# Events from the native import callbacks, drained by the modal operator's timer or by
# importVrsceneSync. The callbacks run on a connection thread and must not touch bpy data.
importEvents = queue.Queue()


def vrsceneImportProgressCallback(importId: int, pluginsDone: int, pluginsTotal: int, stage: str):
    importEvents.put(('progress', importId, pluginsDone, pluginsTotal, stage))


def vrsceneImportFinishedCallback(importId: int, result):
    importEvents.put(('finished', importId, result))


# Import status codes, mirroring VrayZmqWrapper::ImportStatus.
STATUS_OK = 0
STATUS_PARSE_ERROR = 1
STATUS_CANCELLED = 2
STATUS_INTERNAL_ERROR = 3


def formatImportError(result, filePath: str) -> str:
    """ The message for a failed import result, naming the parse location the server reported.
        Shared so a sync read reports the same detail the modal operator does. """
    if result.status == STATUS_PARSE_ERROR:
        location = f" at {result.errorFile}:{result.errorLine}" if result.errorFile else ""
        return f"Failed to parse {filePath}{location}: {result.errorText}"

    return f"V-Ray scene import failed for {filePath}: {result.errorText}"


# Attributes exposed as zero-copy numpy arrays instead of plain Python values.
# Consumed only by the geometry/instancer builders in scene_import.py and by
# creators._imageFromRawBitmap; they are never dispatched through the generic node
# import machinery, which expects plain Python value conventions.
LARGE_ATTRS = {
    # GeomStaticMesh's edge_visibility and velocities are absent because the server drops
    # them (UNREAD_ATTRS in vrscene_import_controller.cpp). GeomInstancer.velocities is read.
    'GeomStaticMesh': [
        'vertices', 'faces', 'normals', 'faceNormals', 'face_mtlIDs', 'map_channels',
    ],
    'GeomMayaHair': ['num_hair_vertices', 'hair_vertices', 'widths', 'colors', 'strand_uvw'],
    'Instancer2': ['instances'],
    'Instancer': ['instances'],
    'GeomInstancer': ['transforms', 'instance_ids', 'velocities'],
    # Embedded texture pixels (SketchUp exports every texture this way). A Python list
    # here means one PyLong per pixel, 263M of them on a 400 MB scene.
    'RawBitmapBuffer': ['pixels'],
}


def buildVrsceneDict(importId: int) -> list[dict]:
    """ Pull the imported plugin data from the native module and shape it into the
        vrscene dict contract: [{"ID": pluginType, "Name": name, "Attributes": {...}}].

        Each record also carries "AnimatedKeys": {attrName: [(vrayTime, value), ...]}, empty unless
        the import asked for keyframes. It holds only attributes with two or more distinct keys.

        Call after the import finished callback has reported success. The numpy views
        in the result stay valid after vray.importVrsceneRelease().
    """
    plugins = vray.getImportedVrscene(importId, LARGE_ATTRS)

    vrsceneDict = [
        {
            "ID": pluginType,
            "Name": name,
            "Attributes": attributes,
            "AnimatedKeys": animatedKeys,
        }
        for name, pluginType, attributes, animatedKeys in plugins
    ]

    return vrsceneDict


def importVrsceneSync(filePath: str, timeout: float = 300.0, typeFilter: str = "") -> list[dict]:
    """ Read a .vrscene through the server importer and return the vrscene dict as the
        server sent it. Blocks until the server has read the scene.

        The caller runs normalizeUnits(), and must run it after fixPluginParams(), which
        renames the Max light aliases and their attributes. Normalizing first leaves every
        distance attr on those lights unscaled.

        typeFilter: comma-separated plugin-type prefixes (e.g. "Mtl"). The server then
        converts and sends only the matching plugins.

        Used by the material-import path (importMaterials with a .vrscene); the full
        scene import stays on the modal operator, which drives the same native import
        asynchronously.
    """
    from vray_blender import engine

    engine.ensureRunning()

    importId = vray.importVrsceneStart(filePath, typeFilter=typeFilter)
    try:
        result = None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                event = importEvents.get(timeout=0.25)
            except queue.Empty:
                continue
            # Progress events and events from an earlier, abandoned import are skipped
            # (imports don't run concurrently, the modal operator is exclusive).
            if event[1] != importId or event[0] != 'finished':
                continue
            result = event[2]
            break

        if result is None:
            raise RuntimeError(f"Timed out after {timeout}s reading '{filePath}'")
        if result.status != STATUS_OK:
            raise RuntimeError(formatImportError(result, filePath))

        vrsceneDict = buildVrsceneDict(importId)
    finally:
        vray.importVrsceneRelease(importId)

    return vrsceneDict


def normalizeUnits(vrsceneDict: list[dict], metersScale: float):
    """ Convert distance-typed attribute values in scene units, from the source scene's length
        unit (1 unit = metersScale meters, per SettingsUnitsInfo) to centimeters, which is
        what the import machinery (scaleDistanceValue) assumes.

        Attrs with an explicit ui.units are in that fixed unit regardless of the scene scale,
        and are left as they are.

        Transforms are not touched here. World-space ones are scaled at the consumption sites
        in scene_import.py, node inputs in engine._createTransformNode.
    """
    from vray_blender.plugins import findPluginModule
    from vray_blender.lib import attribute_utils
    from vray_blender.vray_tools.import_common import isScaledDistanceAttr

    factor = metersScale * 100.0
    if factor == 1.0:
        return

    for pluginDesc in vrsceneDict:
        pluginModule = findPluginModule(pluginDesc['ID'])
        if pluginModule is None:
            continue

        # Camera plugins are consumed directly by the camera importer, not through the
        # unit-aware node/propgroup path, and their "distance" params (orthographicWidth,
        # clipping, focal_length in mm, ...) round-trip through V-Ray raw. Scaling them here
        # would double-apply units (e.g. orthographicWidth 0.9 -> 90).
        if pluginModule.TYPE == 'CAMERA':
            continue

        attrs = pluginDesc.get('Attributes', {})
        animatedKeys = pluginDesc.get('AnimatedKeys') or {}
        for attrName, attrValue in attrs.items():
            # Scalars and numeric vectors (e.g. TexTriPlanar.frame_offset); scaleToSceneLengthUnit
            # takes both, so a vector skipped here would get only the second stage.
            if not attribute_utils.isNumber(attrValue) and \
                    not (isinstance(attrValue, (list, tuple)) and attrValue and all(attribute_utils.isNumber(v) for v in attrValue)):
                continue

            attrDesc = attribute_utils.getAttrDesc(pluginModule, attrName)
            if not isScaledDistanceAttr(attrDesc) or attrDesc['ui'].get('units'):
                continue

            attrs[attrName] = attribute_utils.scaleNumeric(attrValue, factor)
            # Scale the keys by the same factor as the static value.
            if (keys := animatedKeys.get(attrName)) is not None:
                animatedKeys[attrName] = [(time, attribute_utils.scaleNumeric(value, factor)) for time, value in keys]
