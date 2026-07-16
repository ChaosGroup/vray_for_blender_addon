# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy

# The Boolean `use_gpu_rtx` was replaced by the Enum `gpu_device_type` (CUDA/RTX/HIP)
# when the HIP backend was introduced. Once the BoolProperty was removed from
# VRayExporter, Blender keeps the value from older .blend files around as an
# ID (custom) property on the exporter property group, accessible via dict-style
# lookup. This script reads that legacy value, writes the equivalent new enum
# identifier.
_LEGACY_PROP = 'use_gpu_rtx'


def _legacyExporters():
    """ Yield (exporter, useRtx) for every scene that still carries the
        old use_gpu_rtx value as an ID property.
    """
    for scene in bpy.data.scenes:
        exporter = scene.vray.Exporter
        legacyValue = exporter.get(_LEGACY_PROP)
        if legacyValue is not None:
            yield exporter, bool(legacyValue)


def run():
    for exporter, useRtx in _legacyExporters():
        exporter.gpu_device_type = 'RTX' if useRtx else 'CUDA'


def check():
    return any(True for _ in _legacyExporters())
