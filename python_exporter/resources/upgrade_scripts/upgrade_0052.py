# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy

from vray_blender.utils.upgrade_scene import scopedForUpgrade

# Output sockets no longer take the "Out " prefix their 'out_*' attribute name gave them.
# Rename the ones already stored in the scene so old and new nodes read the same.
#
# Rename in place. Building a replacement socket and moving the links onto it loses them,
# because outputs.move() invalidates the reference and the following remove() then deletes the
# new socket.
_PREFIX = "Out "

# Collections whose datablocks can own a V-Ray node tree.
_TREE_OWNERS = ('materials', 'worlds', 'objects', 'lights', 'cameras', 'node_groups')


def _trees(collections):
    for name in _TREE_OWNERS:
        for db in collections(getattr(bpy.data, name)):
            # A node group is itself the tree; the others own one.
            tree = db if isinstance(db, bpy.types.NodeTree) else getattr(db, 'node_tree', None)
            if tree is not None:
                yield tree


def _renameOutputs(tree, dryRun: bool):
    found = False

    for node in tree.nodes:
        for sock in node.outputs:
            if not sock.name.startswith(_PREFIX):
                continue
            if dryRun:
                return True
            sock.name = sock.name[len(_PREFIX):]
            found = True

    return found


def run():
    for tree in _trees(scopedForUpgrade):
        _renameOutputs(tree, dryRun=False)


def check():
    return any(_renameOutputs(tree, dryRun=True) for tree in _trees(scopedForUpgrade))
