# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy

def _getDocsMapping():
    chaosDocsUrl = 'https://documentation.chaos.com/space/VBLD/'

    chaosDocsMapping = []

    def _registerRollout(paramName: str, id: str):
        chaosDocsMapping.append((f'bpy.types.chaosscatter{paramName.lower()}', id),)

    def _registerOperator(paramName: str, id: str):
        chaosDocsMapping.append((f'bpy.ops.chaos_scatter.{paramName.lower()}', id),)

    _registerRollout('Settings.*', '1232044159')
    _registerRollout('TargetItem.*', '1232076810')
    _registerRollout('ModelItem.*', '1232666817')
    _registerRollout('Surface.*', '1231716382')
    _registerRollout('Transforms.*', '1232765047')
    _registerRollout('CameraClipping.*', '1231781910')
    _registerRollout('LookAt.*', '1231978556')
    _registerRollout('Clusters.*', '1231388754')
    _registerRollout('InstancePaint.*', '1231716559')
    _registerRollout('AreaItem', '1232175317')

    _registerRollout('*', '1220018245')
    _registerOperator('*', '1220018245')

    return chaosDocsUrl, chaosDocsMapping


def register():
    bpy.utils.register_manual_map(_getDocsMapping)

def unregister():
    bpy.utils.unregister_manual_map(_getDocsMapping)
