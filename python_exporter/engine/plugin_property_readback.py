# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

""" Write back plugin property values that V-Ray filled in while rendering.

    Some V-Ray plugin parameters are outputs: the core writes into them and the host
    application is expected to show the result. The Velocity render element's
    'max_velocity_last_frame' is one - it reports the largest velocity seen so far, which
    is what a user needs in order to choose a matching 'max_velocity'.

    This is a production-rendering feature. ZmqServer sends the values after each finished
    frame with the RendererOnPluginPropertyValues message, VRayBlenderLib caches them, and
    VRayRendererProdBase._postRender() pulls them from there. Interactive rendering is not
    covered: V-Ray only writes these parameters when it reaches the plugins' frameEnd(),
    which for an interactive render happens when the render is stopped.

    _postRender() runs inside RenderEngine.render(), where writing to Blender data is not
    safe, so the values are queued here and written from a one-shot timer.
"""

import queue

import bpy

from vray_blender import debug


# (pluginName, propertyName, value) tuples waiting to be written to the node tree.
_pendingValues = queue.Queue()


def queuePluginPropertyValues(values):
    """ Schedule a list of (pluginName, propertyName, value) tuples to be written to the
        Blender node tree.
    """
    if not values:
        return

    _pendingValues.put(values)

    if not bpy.app.timers.is_registered(_applyQueuedValues):
        bpy.app.timers.register(_applyQueuedValues)


def _applyQueuedValues():
    """ Drain the queue on the main thread. One-shot timer. """
    try:
        while not _pendingValues.empty():
            _applyPluginPropertyValues(_pendingValues.get())
    except Exception as ex:
        debug.printExceptionInfo(ex, "plugin_property_readback._applyQueuedValues")

    return None


def _collectChannelNodes() -> dict:
    """ Map the unique id of each wired render channel node to the node itself. The unique
        id is the last segment of the exported plugin name, see Names.treeNode().
    """
    from vray_blender.engine.render_elements import iterChannelLinks

    nodes = {}

    for world in bpy.data.worlds:
        for _inSock, node in iterChannelLinks(world):
            if uniqueId := getattr(node, 'unique_id', ''):
                nodes[uniqueId] = node

    return nodes


def _applyPluginPropertyValues(values):
    if not values:
        return

    channelNodes = _collectChannelNodes()

    for pluginName, propName, value in values:
        node = channelNodes.get(pluginName.rpartition('|')[2])
        if node is None:
            # The channel node has been deleted or unwired since the render started.
            continue

        propGroup = getattr(node, getattr(node, 'vray_plugin', ''), None)
        if (propGroup is None) or (not hasattr(propGroup, propName)):
            debug.printError(f"Cannot write back {pluginName}::{propName}: no such property")
            continue

        # Skip no-op writes so that we neither mark the .blend modified nor trigger a
        # scene update on every rendered frame.
        if getattr(propGroup, propName) != value:
            setattr(propGroup, propName, value)
