# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

from vray_blender.lib import plugin_utils

plugin_utils.loadPluginOnModule(globals(), __name__)


def onRadiusUpdate(propGroup, context, attrName):
    """ Resize the Empty's display sphere to match the rendered sphere. A 'SPHERE' Empty is
        drawn at empty_display_size as its radius, so the two map directly.
    """
    assert attrName == 'radius'

    if (obj := propGroup.id_data) and obj.vray.isVRayPerfectSphere:
        obj.empty_display_size = propGroup.radius
