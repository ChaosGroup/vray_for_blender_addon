# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

""" Bridge between the native .vrscene import (VRayBlenderLib) and the vrscene dict
    contract consumed by nodes/importing.py and nodes/operators/import_file.py.
"""

import os
import queue
import time

from vray_blender.bin import VRayBlenderLib as vray


# Attributes exposed as zero-copy numpy arrays instead of plain Python values.
# Consumed only by the geometry/instancer builders in scene_import.py and by
# creators._imageFromRawBitmap; they are never dispatched through the generic node
# import machinery, which expects plain Python value conventions.
LARGE_ATTRS = {
    'GeomStaticMesh': [
        'vertices', 'faces', 'normals', 'faceNormals', 'face_mtlIDs',
        'edge_visibility', 'velocities', 'map_channels',
    ],
    'GeomMayaHair': ['num_hair_vertices', 'hair_vertices', 'widths', 'colors', 'strand_uvw'],
    'Instancer2': ['instances'],
    'Instancer': ['instances'],
    'GeomInstancer': ['transforms', 'instance_ids', 'velocities'],
    # Embedded texture pixels (SketchUp exports every texture this way). A Python list
    # here means one PyLong per pixel - 263M of them on a 400 MB scene.
    'RawBitmapBuffer': ['pixels'],
}


def buildVrsceneDict(importId: int, filePath: str) -> list[dict]:
    """ Pull the imported plugin data from the native module and shape it into the
        vrscene dict contract: [{"ID": pluginType, "Name": name, "Attributes": {...}}].

        Each plugin record also carries an "AnimatedParams" key with the names of the
        params that have keyframes in the source scene (unused by the static import).

        Call after the import finished callback has reported success. The numpy views
        in the result stay valid after vray.importVrsceneRelease().
    """
    plugins = vray.getImportedVrscene(importId, LARGE_ATTRS)

    vrsceneDict = [
        {
            "ID": pluginType,
            "Name": name,
            "Attributes": attributes,
            "AnimatedParams": animatedParams,
        }
        for name, pluginType, attributes, animatedParams in plugins
    ]

    dirPath = os.path.dirname(filePath)
    vrsceneDict.append({
        "ID": 'ImportSettings',
        "Name": "Import Settings",
        # Some consumers read Dirpath from the top level (the vrmat parser convention,
        # e.g. _createNodeTexBitmap), others from Attributes (the vrscene parser
        # convention). Provide both.
        "Dirpath": dirPath,
        "Attributes": {
            'filepath': filePath,
            'dirpath': dirPath,
        },
    })

    return vrsceneDict


def importVrsceneSync(filePath: str, timeout: float = 300.0, typeFilter: str = "") -> list[dict]:
    """ Read a .vrscene through the server importer and return the vrscene dict, with
        distance-typed attributes normalized to descriptor units (what the node import
        machinery expects). Blocks until the server has read the scene.

        typeFilter: comma-separated plugin-type prefixes (e.g. "Mtl") - the server then
        converts and sends only the matching plugins.

        Used by the material-import path (importMaterials with a .vrscene); the full
        scene import stays on the modal operator, which drives the same native import
        asynchronously.
    """
    from vray_blender import engine
    from vray_blender.nodes.operators import import_vrscene
    from vray_blender.vray_tools.import_common import getPluginByType

    engine.ensureRunning()

    importId = vray.importVrsceneStart(filePath, typeFilter=typeFilter)
    try:
        result = None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                evt = import_vrscene._importEvents.get(timeout=0.25)
            except queue.Empty:
                continue
            # Progress events and events from an earlier, abandoned import are skipped
            # (imports don't run concurrently: the modal operator is exclusive).
            if evt[1] != importId or evt[0] != 'finished':
                continue
            result = evt[2]
            break

        if result is None:
            raise RuntimeError(f"Timed out after {timeout}s reading '{filePath}'")
        if result.status != 0:
            raise RuntimeError(
                f"Failed to read '{filePath}': {getattr(result, 'errorText', '')}")

        vrsceneDict = buildVrsceneDict(importId, filePath)
    finally:
        vray.importVrsceneRelease(importId)

    metersScale = 1.0
    if unitsInfo := getPluginByType(vrsceneDict, 'SettingsUnitsInfo'):
        metersScale = float(unitsInfo['Attributes'].get('meters_scale', 1.0)) or 1.0
    normalizeUnits(vrsceneDict, metersScale)

    return vrsceneDict


def normalizeUnits(vrsceneDict: list[dict], metersScale: float):
    """ Convert distance-typed attribute values from the source scene's length unit
        (1 unit = metersScale meters, per SettingsUnitsInfo) to the unit declared in
        the attribute's descriptor, which is what the import machinery
        (_fillNodeProperties / scaleToSceneLengthUnit) assumes.

        World-space transforms are NOT touched here: their translations are scaled at
        the consumption sites in scene_import.py, because transform values in texture
        space (e.g. UVWGen.uvw_transform) must not be scaled.
    """
    from vray_blender.plugins import findPluginModule
    from vray_blender.lib import attribute_utils
    from vray_blender.vray_tools.import_common import isScaledDistanceAttr

    unitFactors = {
        'centimeters': metersScale * 100.0,
        'meters': metersScale,
    }

    # Camera plugins are consumed directly by the camera importer, not through the
    # unit-aware node/propgroup path, and their "distance" params (orthographicWidth,
    # clipping, focal_length in mm, ...) round-trip through V-Ray raw. Scaling them here
    # would double-apply units (e.g. orthographicWidth 0.9 -> 90).
    _SKIP_UNIT_NORMALIZE = {'RenderView', 'CameraPhysical', 'SettingsCamera', 'CameraDome'}

    for pluginDesc in vrsceneDict:
        if pluginDesc['ID'] in _SKIP_UNIT_NORMALIZE:
            continue
        # findPluginModule returns None (no error log) for synthetic (ImportSettings)
        # or unsupported (SettingsPhotonMap, ...) plugin types.
        pluginModule = findPluginModule(pluginDesc['ID'])
        if pluginModule is None:
            continue

        attrs = pluginDesc.get('Attributes', {})
        for attrName, attrValue in attrs.items():
            if not isinstance(attrValue, (int, float)) or isinstance(attrValue, bool):
                continue

            attrDesc = attribute_utils.getAttrDesc(pluginModule, attrName)
            if not isScaledDistanceAttr(attrDesc):
                continue

            factor = unitFactors.get(attrDesc['ui'].get('units', 'centimeters'))
            if factor is not None and factor != 1.0:
                attrs[attrName] = attrValue * factor
