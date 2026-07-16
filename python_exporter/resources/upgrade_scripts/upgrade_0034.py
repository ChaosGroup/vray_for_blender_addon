# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

from vray_blender.lib.mixin import VRayNodeBase
from vray_blender.utils.upgrade_scene import UpgradeContext, upgradeScene, sceneNeedsUpgrade

def _texEdgesPostCopy(ctx, links, newNode):
    # The show_hidden_edges parameter was fixed and exposed in the UI,
    # to preserve old scenes it must be set to True.
    newNode.TexEdges.show_hidden_edges = True

UPGRADE_INFO = {
    'nodes': {
        'VRayNodeTexEdges': {
            'node_post_copy': _texEdgesPostCopy
        }
    }
}

def run():
    upgradeScene(UPGRADE_INFO)

def check():
    return sceneNeedsUpgrade(UPGRADE_INFO)
