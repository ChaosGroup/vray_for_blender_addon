# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Bookkeeping for a Cosmos drag-and-drop of more than one asset.
#
# The Cosmos browser lets the user select several assets and drag them out in one
# operation. Its drag payload then carries a 'packages' array instead of a single 'id'
# (see cosmos_drag_drop.py), and the addon requests one import per entry - all of them
# with the same drop point, because there is only one cursor position to drop at.
#
# Placement needs nothing from this module. _dropPlacement in cosmos_handler.py parks
# the 3D cursor on the drop point for the duration of each import, and every Cosmos
# importer creates its object at the cursor, so all the assets of one drop land on the
# same spot. That is also what a regular import through the browser's Import button
# does: it carries no drop coordinates, so its assets land on wherever the 3D cursor
# already is. Both paths follow the same rule.
#
# What is tracked here is how many assets a drop carried and how many of them have been
# reported back. The addon has no other way to tell a single-asset drop from one asset
# of a multi-asset drop: each import returns on its own, carrying the drop coordinates
# but nothing about the drop as a whole. Material assignment needs that distinction -
# see cosmos_handler.py.
#
# Everything here runs on the main thread - the drop operator opens a batch, and the
# import timer consumes it - so no locking is needed, the same as cosmos_asset_set.

import time

from mathutils import Vector

from vray_blender import debug


# Two sets of drop coordinates belong to the same drop when they match to within this.
# The coordinates the addon sends are echoed back unchanged by the server, so an exact
# comparison would do; the epsilon is only there so that a float rebuilt with a
# different last bit somewhere along the way still matches.
_MATCH_EPS = 1e-6

# Seconds of silence after which an unfinished batch is given up on. An asset that has
# to be downloaded first can take a while to arrive, so this is as generous as the Asset
# Set session timeout.
#
# The case this really covers is an asset of the drop that FAILS to import: the server
# reports it through onAssetImportFailed, which forwards to sendSetMemberFailed, and that
# returns early on an empty instance token (server cosmos_importer.cpp:875). A dropped
# asset has no token - only an Asset Set member does - so the failure produces no
# MsgControlOnImportAsset at all and noteArrival is never reached for it. The batch is
# then one arrival short forever, and until it expires a single-asset drop at the very
# same coordinates is taken for one of its members and has its material withheld. The
# timeout is what bounds that, so it is a backstop with a job, not just hygiene.
_BATCH_TIMEOUT_SECONDS = 300.0


class _DropBatch:
    """One drop of several assets, tracked until all of them have been accounted for."""

    __slots__ = ('dropPos', 'expected', 'seen', 'noticeShown', 'lastActivity')

    def __init__(self, dropPos: Vector, expected: int):
        self.dropPos  = dropPos.copy()
        self.expected = expected
        # Assets of this batch reported so far.
        self.seen     = 0
        # Set once the "several materials, none assigned" notice has been reported, so
        # one drop produces one message instead of one per material.
        self.noticeShown = False
        self.lastActivity = time.monotonic()


# Open batches, oldest first. Never more than a couple of entries: one per drop still
# being imported.
_batches: list[_DropBatch] = []


def clearBatches():
    """Forget every open batch.

    Called on addon unregister. The state is plain numbers, so nothing breaks if a batch
    is dropped while its assets are still in flight - they are placed on the drop point
    either way, and only the material-assignment decision is affected.
    """
    _batches.clear()


def openBatch(dropPos: Vector, assetCount: int):
    """Announce a drop of 'assetCount' assets at 'dropPos'.

    Called by the drop operator right before it fires the imports. A single-asset drop
    opens nothing: there is nothing to tell apart later, which is also what an import
    that matches no batch is treated as.
    """
    _expireBatches()

    if assetCount < 2:
        return

    _batches.append(_DropBatch(dropPos, assetCount))
    debug.printDebug(
        f"Cosmos drop: expecting {assetCount} assets at "
        f"({dropPos.x:.3f}, {dropPos.y:.3f}, {dropPos.z:.3f})"
    )


def isMultiAssetDrop(settings) -> bool:
    """True when this import is one asset of a drop that carried several.

    A material is assigned to the object it was dropped on only for a single-asset drop.
    All the assets of one drag share a drop point and a target slot, so assigning each
    of them would leave the last to arrive as the winner - and the arrival order is
    whatever the Cosmos service delivers, not the order the user selected them in.
    V-Ray for 3ds Max draws the line in the same place (`numAssets<=1` in
    galaxy_import_handler_max.cpp), and V-Ray for Cinema 4D never assigns a Cosmos
    material to an object at all.
    """
    return _findBatch(settings) is not None


def noteMultiMaterialNotice(settings) -> bool:
    """True the first time it is called for a drop, False afterwards.

    Lets the caller tell the user once per drop that the materials were imported without
    being assigned, rather than once for every material in it.
    """
    batch = _findBatch(settings)
    if batch is None or batch.noticeShown:
        return False
    batch.noticeShown = True
    return True


def noteArrival(settings):
    """Count one asset of a multi-asset drop as reported.

    Called once per import the timer processes, whatever its type - a batch that is only
    ever closed by its timeout would keep catching assets dropped at the same point long
    after it is done.
    """
    batch = _findBatch(settings)
    if batch is None:
        return

    batch.seen += 1
    batch.lastActivity = time.monotonic()
    if batch.seen >= batch.expected:
        _closeBatch(batch)


def _dropPosition(settings) -> Vector | None:
    """The drop point of an import, or None when it did not come from a drop."""
    if not getattr(settings, 'hasDropCoords', False):
        return None
    return Vector((settings.worldX, settings.worldY, settings.worldZ))


def _findBatch(settings) -> _DropBatch | None:
    """The open batch an import belongs to, or None.

    Matched on the drop point rather than on the package id: the id an import reports is
    the one Cosmos actually resolved, which for a package with variants is not
    necessarily the id that was requested, whereas the drop coordinates are passed
    through the server untouched.
    """
    dropPos = _dropPosition(settings)
    if dropPos is None:
        return None

    _expireBatches()

    for batch in _batches:
        if (dropPos - batch.dropPos).length <= _MATCH_EPS:
            return batch
    return None


def _closeBatch(batch: _DropBatch):
    if batch in _batches:
        _batches.remove(batch)
        debug.printDebug(f"Cosmos drop: all {batch.expected} assets of the drop accounted for")


def _expireBatches():
    """Drop the batches that have gone quiet, so a drop whose assets never arrived does
    not keep catching later drops at the same point."""
    now = time.monotonic()
    for batch in list(_batches):
        if now - batch.lastActivity > _BATCH_TIMEOUT_SECONDS:
            debug.printDebug(
                f"Cosmos drop: giving up on {batch.expected - batch.seen} asset(s) of a "
                f"multi-asset drop that never arrived"
            )
            _batches.remove(batch)
