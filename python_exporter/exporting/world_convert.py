# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

""" Conversion of a native Cycles world to a V-Ray environment.

    The 'read' half, mirroring light_convert: resolves a Cycles world tree to the single
    environment shader Cycles would evaluate. Used by world_export (on the fly at render time) and
    by nodes.importing.convertWorld (baked into an editable V-Ray world tree).
"""

import bpy
from mathutils import Color

from vray_blender import debug


def _findBackground(socket: bpy.types.NodeSocket):
    """ The Background node Cycles would evaluate, following reroutes. A Mix/Add of several
        Backgrounds resolves to the first - V-Ray has no per-ray-type shader split. """
    seen = set()
    stack = [socket]
    isSplit = False

    while stack:
        sock = stack.pop(0)
        if not sock.is_linked:
            continue
        node = sock.links[0].from_node
        if node.name in seen:
            continue
        seen.add(node.name)

        if node.bl_idname == 'ShaderNodeBackground':
            return node, isSplit
        if node.bl_idname == 'NodeReroute':
            stack.append(node.inputs[0])
        elif node.bl_idname in ('ShaderNodeMixShader', 'ShaderNodeAddShader'):
            isSplit = True   # more than one environment shader; V-Ray carries only one
            stack.extend(s for s in node.inputs if s.type == 'SHADER')

    return None, isSplit


def convertCyclesWorld(world: bpy.types.World):
    """ Resolve a native Cycles world to (colour, strength, textureSocket), or None if there is
        nothing to convert. 'textureSocket' is set when the Background colour is textured, and the
        colour is unused in that case. """
    if world is None or not world.node_tree:
        return None

    output = next((n for n in world.node_tree.nodes
                   if n.bl_idname == 'ShaderNodeOutputWorld' and n.is_active_output), None)
    if output is None:
        return None

    surface = output.inputs.get('Surface')
    if surface is None or not surface.is_linked:
        return None

    background, isSplit = _findBackground(surface)
    if background is None:
        debug.report(severity='WARNING',
                     msg=f"World '{world.name}': no Background node found, the environment "
                         "will not be converted")
        return None

    if isSplit:
        debug.report(severity='WARNING',
                     msg=f"World '{world.name}' mixes several Background shaders. V-Ray uses a "
                         "single environment, so only the first one is converted")

    colorSocket = background.inputs['Color']
    strengthSocket = background.inputs['Strength']

    if strengthSocket.is_linked:
        debug.report(severity='WARNING',
                     msg=f"World '{world.name}': a textured Background 'Strength' is not "
                         "supported, using 1.0")
        strength = 1.0
    else:
        strength = strengthSocket.default_value

    return (Color(colorSocket.default_value[:3]),
            strength,
            colorSocket if colorSocket.is_linked else None)
