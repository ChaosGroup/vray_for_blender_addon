# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
from vray_blender.exporting.tools import isObjectVrayProxy
from vray_blender.lib.blender_utils import updateShadowAttr
from vray_blender.utils.upgrade_scene import scopedForUpgrade


def _proxies():
    # Meshes carry no upgrade number, so go through the objects using them.
    return (obj for obj in scopedForUpgrade(bpy.data.objects) if isObjectVrayProxy(obj))


def run():
    # Since version 49 'scale' and 'flip_axis' gained shadow attributes so their changes can
    # transform the preview mesh in place.
    for obj in _proxies():
        geomMeshFile = obj.data.vray.GeomMeshFile
        if geomMeshFile.file:
            updateShadowAttr(geomMeshFile, 'scale')
            updateShadowAttr(geomMeshFile, 'flip_axis')


def check():
    return any(True for _ in _proxies())
