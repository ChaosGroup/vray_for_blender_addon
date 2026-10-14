# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy

from vray_blender.exporting.tools import removeOutputSocketLinks, getInputSocketByAttr, getOutputSocketByAttr
from vray_blender.lib import plugin_utils
from vray_blender.lib.mixin import VRayOperatorBase
from vray_blender.nodes.utils import getNodeOfPropGroup

plugin_utils.loadPluginOnModule(globals(), __name__)


def onUpdateSeparate(propGroup, context, attrName):
    """ Expose the Red/Green/Blue/Alpha channel outputs so the node works as a color
        splitter. Triggered by the 'Separate Color' menu entry via the add-node settings.
    """
    node = getNodeOfPropGroup(propGroup)
    for channel in ('red', 'green', 'blue', 'alpha'):
        if outSock := getOutputSocketByAttr(node, channel):
            outSock.hide    = False
            outSock.enabled = True

    updateInputSockets(node)


def onUpdateMode(propGroup, context, attrName):
    """ Re-evaluate the input sockets after the operation has changed. """
    if node := getNodeOfPropGroup(propGroup):
        updateInputSockets(node)


def nodeInit(node):
    """ Hide the inputs the freshly created node's default operation does not read. """
    updateInputSockets(node)


####################################################################################################
# Which inputs each output channel reads.
#
# Read off the plugin implementation (TexAColorOp in tex_sampler.cpp) rather than the parameter
# descriptions, which are wrong for several channels. Throughout, A is color_a * mult_a and B is
# color_b * mult_b.
####################################################################################################

_INPUTS_A    = frozenset(('color_a', 'mult_a'))
_INPUTS_B    = frozenset(('color_b', 'mult_b'))
_INPUTS_BOTH = _INPUTS_A | _INPUTS_B

# Channels that read the A operand alone. sin/cos/asin/acos/atan are not among them; they take the
# product of both operands.
_A_ONLY_CHANNELS = frozenset((
    'result_a', 'red', 'green', 'blue', 'alpha', 'intensity',
    'abs', 'ceil', 'exp', 'floor', 'log', 'log10', 'sqrt', 'tan',
))

# The channel 'mode' routes to the node's Result output, indexed by the enum value.
_MODE_CHANNELS = (
    'result_a', 'result_b', 'product', 'sum', 'difference', 'power', 'division',
    'minimum', 'maximum', 'absolute_difference', 'lower_intensity_color',
    'greater_intensity_color', 'sin', 'cos', 'abs', 'ceil', 'exp', 'floor', 'log',
    'log10', 'sqrt', 'fmod', 'average', 'tan', 'asin', 'acos', 'atan', 'atan2',
    'bias_schlick', 'gain_schlick', 'bias_perlin', 'gain_perlin',
)


def _channelInputs(channel: str):
    """ The inputs a single output channel reads. """
    if channel in _A_ONLY_CHANNELS:
        return _INPUTS_A
    if channel == 'result_b':
        return _INPUTS_B
    if channel == 'power':
        # The exponent is the scalar mult_b. color_b is not read at all.
        return _INPUTS_A | {'mult_b'}

    return _INPUTS_BOTH


def _requiredInputs(node):
    """ The inputs read by every channel the node currently produces: the one 'mode' routes to
        the Result output, plus every output socket the user has added.
    """
    # The property page evaluates this once per row on every redraw, so walk the sockets once
    # instead of looking each channel up by name. The Result output carries no vray_attr; 'mode'
    # already accounts for it.
    channels = [_MODE_CHANNELS[int(node.TexAColorOp.mode)]]
    channels += [s.vray_attr for s in node.outputs if s.enabled and s.vray_attr]

    return set().union(*(_channelInputs(c) for c in channels))


def updateInputSockets(node):
    """ Show the inputs the node's current channels read and hide the rest.

        A linked socket is never hidden. The channel set changes on every mode switch and on every
        output added or removed, and hiding a socket drops its link.
    """
    required = _requiredInputs(node)

    # result_alpha is not in the loop and stays shown. 'mode' always routes a color channel to
    # Result, and every color channel reads result_alpha.
    for attrName in _INPUTS_BOTH:
        sock = getInputSocketByAttr(node, attrName)
        enabled = (attrName in required) or sock.is_linked

        if sock.enabled != enabled:
            sock.enabled = enabled
            sock.hide    = not enabled


# Conditions backing the property page rows. The condition compiler calls these with the plugin
# property group and the node being drawn.
def isColorAActive(propGroup, node):
    return 'color_a' in _requiredInputs(node)


def isColorBActive(propGroup, node):
    return 'color_b' in _requiredInputs(node)


def isMultAActive(propGroup, node):
    return 'mult_a' in _requiredInputs(node)


def isMultBActive(propGroup, node):
    return 'mult_b' in _requiredInputs(node)


# Number of columns to spread the (long) channel list over in the Add/Remove menus.
_MENU_COLUMNS = 3


def _outputChannels(node):
    """ (identifier, name, description) for every selectable output channel. """
    return [(i.identifier, i.name, i.description)
            for i in node.TexAColorOp.bl_rna.properties['outputs'].enum_items]


def _outputItems(node, wantShown):
    """ Output channels filtered by whether their socket is currently shown.
        wantShown=False -> channels that can be added, True -> channels that can be removed.
    """
    items = []
    if node is not None and hasattr(node, 'TexAColorOp'):
        for identifier, name, desc in _outputChannels(node):
            sock = getOutputSocketByAttr(node, identifier)
            if bool(sock and sock.enabled) == wantShown:
                items.append((identifier, name, desc))
    return items


def _drawChannelMenu(menu, context, wantShown, operatorId):
    node = getattr(context, 'node', None) or getattr(context, 'active_node', None)
    items = _outputItems(node, wantShown)

    # Lay out as explicit side-by-side columns so the popup sizes to its content
    # instead of stretching to fill the screen width (as column_flow does).
    row = menu.layout.row()
    perColumn = (len(items) + _MENU_COLUMNS - 1) // _MENU_COLUMNS
    for start in range(0, len(items), perColumn or 1):
        column = row.column(align=True)
        for identifier, name, _desc in items[start:start + perColumn]:
            column.operator(operatorId, text=name).channel = identifier


class VRAY_MT_node_texacolorop_add(bpy.types.Menu):
    bl_idname = 'VRAY_MT_node_texacolorop_add'
    bl_label  = "Add Output"

    def draw(self, context):
        _drawChannelMenu(self, context, wantShown=False, operatorId='vray.node_texacolorop_add_output')


class VRAY_MT_node_texacolorop_remove(bpy.types.Menu):
    bl_idname = 'VRAY_MT_node_texacolorop_remove'
    bl_label  = "Remove Output"

    def draw(self, context):
        _drawChannelMenu(self, context, wantShown=True, operatorId='vray.node_texacolorop_remove_output')


class VRAY_OT_node_texacolorop_add_output(VRayOperatorBase):
    bl_idname      = 'vray.node_texacolorop_add_output'
    bl_label       = "Add Output"
    bl_description = "Show an output socket for the selected channel"
    bl_options     = {'INTERNAL', 'UNDO'}

    channel: bpy.props.StringProperty()

    def execute(self, context):
        outSock = getOutputSocketByAttr(context.node, self.channel)
        outSock.hide    = False
        outSock.enabled = True
        updateInputSockets(context.node)
        return {'FINISHED'}


class VRAY_OT_node_texacolorop_remove_output(VRayOperatorBase):
    bl_idname      = 'vray.node_texacolorop_remove_output'
    bl_label       = "Remove Output"
    bl_description = "Hide the selected output socket"
    bl_options     = {'INTERNAL', 'UNDO'}

    channel: bpy.props.StringProperty()

    def execute(self, context):
        outSock = getOutputSocketByAttr(context.node, self.channel)
        outSock.enabled = False
        outSock.hide    = True
        removeOutputSocketLinks(outSock)
        updateInputSockets(context.node)
        return {'FINISHED'}


def nodeDraw(context, layout, node):
    propGroup = node.TexAColorOp

    channels = _outputChannels(node)
    shown = sum(1 for ident, _, _ in channels
                if (s := getOutputSocketByAttr(node, ident)) and s.enabled)
    total = len(channels)

    col = layout.column()
    col.use_property_decorate = False
    col.context_pointer_set('node', node)

    # [ mode | + | - ] on a single row.
    row = col.row(align=True)
    row.prop(propGroup, 'mode', text="")

    addCell = row.row(align=True)
    addCell.enabled = shown < total
    addCell.menu('VRAY_MT_node_texacolorop_add', text="", icon='ADD')

    removeCell = row.row(align=True)
    removeCell.enabled = shown > 0
    removeCell.menu('VRAY_MT_node_texacolorop_remove', text="", icon='REMOVE')


def getRegClasses():
    return (
        VRAY_OT_node_texacolorop_add_output,
        VRAY_OT_node_texacolorop_remove_output,
        VRAY_MT_node_texacolorop_add,
        VRAY_MT_node_texacolorop_remove,
    )


def register():
    for regClass in getRegClasses():
        bpy.utils.register_class(regClass)


def unregister():
    for regClass in getRegClasses():
        bpy.utils.unregister_class(regClass)
