# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
from vray_blender.utils.upgrade_scene import scoped

# Mappings from legacy short tokens to the new descriptive tokens.
# Applied to SettingsOutput render paths (all scenes).
_RENDER_REPLACEMENTS = {
    '$F': '$file',
    '$C': '$camera',
    '$S': '$scene',
}

# Bake paths also replace the legacy object-name token.
_BAKE_REPLACEMENTS = {
    **_RENDER_REPLACEMENTS,
    '$O': '$object',
}


def _applyReplacements(value: str, replacements: dict) -> str:
    for old, new in replacements.items():
        value = value.replace(old, new)
    return value


def _upgradeRenderPaths(scene: bpy.types.Scene):
    settingsOutput = scene.vray.SettingsOutput
    for attr in ('img_file', 'img_dir'):
        old = getattr(settingsOutput, attr, '')
        new = _applyReplacements(old, _RENDER_REPLACEMENTS)
        if new != old:
            setattr(settingsOutput, attr, new)


def _upgradeBakePaths(scene: bpy.types.Scene):
    batchBake = scene.vray.BatchBake
    # Cover both the shared defaults and any per-object list entries.
    items = [batchBake.default_item] + list(batchBake.list_items)
    for item in items:
        for attr in ('img_file', 'img_dir'):
            old = getattr(item, attr, '')
            new = _applyReplacements(old, _BAKE_REPLACEMENTS)
            if new != old:
                setattr(item, attr, new)

def _upgradeCloudJobName(scene: bpy.types.Scene):
    old = scene.vray.Exporter.vray_cloud_job_name
    new = _applyReplacements(old, _RENDER_REPLACEMENTS)
    if new != old:
        scene.vray.Exporter.vray_cloud_job_name = new


def run():
    for scene in scoped(bpy.data.scenes):
        _upgradeRenderPaths(scene)
        _upgradeBakePaths(scene)
        _upgradeCloudJobName(scene)


def check():
    for scene in scoped(bpy.data.scenes):
        settingsOutput = scene.vray.SettingsOutput
        for attr in ('img_file', 'img_dir'):
            val = getattr(settingsOutput, attr, '')
            if any(token in val for token in _RENDER_REPLACEMENTS):
                return True

        batchBake = scene.vray.BatchBake
        items = [batchBake.default_item] + list(batchBake.list_items)
        for item in items:
            for attr in ('img_file', 'img_dir'):
                val = getattr(item, attr, '')
                if any(token in val for token in _BAKE_REPLACEMENTS):
                    return True

        val = scene.vray.Exporter.vray_cloud_job_name 
        if any(token in val for token in _RENDER_REPLACEMENTS):
            return True

    return False
