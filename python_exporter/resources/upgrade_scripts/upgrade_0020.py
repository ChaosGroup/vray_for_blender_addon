# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
from vray_blender.exporting.tools import isObjectVrayProxy


def run():
    for obj in bpy.data.objects:
        if isObjectVrayProxy(obj):
            geomMeshFile = obj.data.vray.GeomMeshFile
            if geomMeshFile.previewType == 'Point':
                geomMeshFile.previewType = 'Point' # Re-create the preview mesh


def check():
    return any(isObjectVrayProxy(obj) for obj in bpy.data.objects)
