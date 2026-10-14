# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

""" Per-node thumbnails in the V-Ray node editor.

    V-Ray UV-samples textures through the AppSDK sampler, with no render. BRDFs, materials and
    the context-dependent textures are rendered small instead. A POST_PIXEL handler paints the
    results above their nodes.

    classify.py decides which nodes get one and how, service.py holds the request and cache
    bookkeeping, draw.py does the drawing.
"""


def isEnabled() -> bool:
    """ Feature flag plus the user preference. """
    from vray_blender import features
    if not features.isEnabled(features.Feature.NODE_THUMBNAILS):
        return False

    from vray_blender.lib.blender_utils import getVRayPreferences
    try:
        prefs = getVRayPreferences()
    except Exception:
        return True
    return bool(getattr(prefs, 'node_thumbnails', True))


def register():
    from vray_blender import features
    if features.isEnabled(features.Feature.NODE_THUMBNAILS):
        from vray_blender.preview.thumbs import draw, ui
        draw.register()
        ui.register()


def unregister():
    from vray_blender.preview.thumbs import draw, service, ui
    ui.unregister()
    draw.unregister()
    service.reset()


def onFileLoad():
    from vray_blender.preview.thumbs import service
    service.reset()


def onUndoRedo():
    """ Undo and redo restore datablocks directly, so no property callback runs and nothing
        reports which nodes changed. Drop every cached key; the warm renderers stay.
    """
    from vray_blender.preview.thumbs import service
    service.invalidateAll()


def onSettingChanged():
    """ A thumbnail pref changed. Pixels are keyed by content AND resolution, so a new resolution
        re-requests by itself and only the held pref value has to go. Turning the feature OFF
        releases the warm renderers too - nothing pumps once the draw handler stops asking.
    """
    from vray_blender.nodes.utils import tagRedrawNodeEditor
    from vray_blender.preview.thumbs import service

    service.onResolutionPrefChanged()
    if not isEnabled():
        service.reset()
    tagRedrawNodeEditor()
