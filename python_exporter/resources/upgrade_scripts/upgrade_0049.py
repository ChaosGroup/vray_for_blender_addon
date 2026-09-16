# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
from vray_blender.exporting.tools import isObjectVrayProxy
from vray_blender.lib.blender_utils import updateShadowAttr


def run():
    # Since version 49 'scale' and 'flip_axis' gained shadow attributes so their changes can
    # transform the preview mesh in place.
    for mesh in bpy.data.meshes:
        geomMeshFile = getattr(getattr(mesh, 'vray', None), 'GeomMeshFile', None)
        if geomMeshFile and geomMeshFile.file:
            updateShadowAttr(geomMeshFile, 'scale')
            updateShadowAttr(geomMeshFile, 'flip_axis')


def check():
    return any(isObjectVrayProxy(obj) for obj in bpy.data.objects)
