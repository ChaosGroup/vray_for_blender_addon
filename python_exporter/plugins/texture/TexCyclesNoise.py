# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
from vray_blender.exporting import node_export as commonNodesExport
from vray_blender.lib import plugin_utils
from vray_blender.lib.defs import NodeContext, PluginDesc
from vray_blender.lib.names import Names

from vray_blender.bin import VRayBlenderLib as vray

plugin_utils.loadPluginOnModule(globals(), __name__)

def nodeInit(node: bpy.types.Node):
    # The conditions on sockets are triggered only when the value of the socket is updated. 
    # Force an update in order to set the initial socket state.
    # NOTE: the 'inputs' collection is not fully initialized at thit point. While its
    # contents is valid, the key lookup does not work reliably. This is why the search
    # for the socket is done using interation.
    if sock := next((i for i in node.inputs if i.name == 'Dimensions'), None):
        sock.value = sock.value


   