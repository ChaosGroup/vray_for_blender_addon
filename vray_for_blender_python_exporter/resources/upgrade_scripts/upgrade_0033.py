# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy

def run():
    for world in bpy.data.worlds:
        if world.vray.is_vray_class and world.use_nodes:
            world.node_tree.vray.tree_type = 'WORLD'

def check():
    for world in bpy.data.worlds:
        if world.vray.is_vray_class and world.use_nodes and not world.node_tree.vray.tree_type:
            return True
