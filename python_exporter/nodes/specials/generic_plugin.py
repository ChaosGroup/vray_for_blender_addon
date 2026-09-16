# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import contextlib

import bpy

from vray_blender.lib import attribute_utils
from vray_blender.lib.mixin import VRayNodeBase
from vray_blender.nodes.sockets import (HIDDEN_SOCKET_COLOR, VRaySocketAColor, VRaySocketColor,
                                        VRaySocketInt, VRayValueSocket)
from vray_blender.nodes.utils import selectedObjectTagUpdate
from vray_blender.plugins import findPluginModule


# Blender does not keep a reference to the sequence returned by an EnumProperty items callback,
# so it has to be cached here or the item strings are garbage collected and the enum draws junk.
_enumItemsCache = {}


def _getEnumItems(sock):
    """ Enum items of the socket's attribute, read from the plugin description. Empty when V-Ray
        ships no description for the plugin.
    """
    key = (sock.node.vray_plugin, sock.vray_attr)

    if (items := _enumItemsCache.get(key)) is None:
        items = []
        if pluginModule := findPluginModule(key[0]):
            if attrDesc := attribute_utils.getAttrDesc(pluginModule, key[1]):
                items = [(str(i[0]), str(i[1]), str(i[2]) if len(i) > 2 else "")
                         for i in attrDesc.get('items', ())]
        _enumItemsCache[key] = items

    return items


# The stock sockets carry the limits of a hand-authored UI, because a generated node overrides
# them per attribute with a socket class of its own (nodes/sockets.registerDynamicSocketClass).
# Those per-attribute classes read a property group, which a generic node does not have, so it
# uses the stock classes - and has to widen the ones whose limits would silently clamp an
# imported value. A bitmap 3000 pixels wide is the case that found this.
_INT_LIMIT = 2 ** 30
_COLOR_LIMIT = 2 ** 20


class VRaySocketGenericInt(VRaySocketInt):
    bl_idname = 'VRaySocketGenericInt'
    bl_label  = 'Integer socket'

    value: bpy.props.IntProperty(
        name = "Value",
        description = "Value",
        min = -_INT_LIMIT,
        max =  _INT_LIMIT,
        soft_min = -100,
        soft_max =  100,
        default = 0,
        update = selectedObjectTagUpdate
    )


class VRaySocketGenericColor(VRaySocketColor):
    bl_idname = 'VRaySocketGenericColor'
    bl_label  = 'Color socket'

    value: bpy.props.FloatVectorProperty(
        name = "Color",
        description = "Color",
        subtype = 'COLOR',
        size = 3,
        min = -_COLOR_LIMIT,
        max =  _COLOR_LIMIT,
        soft_min = 0.0,
        soft_max = 1.0,
        default = (0.0, 0.0, 0.0),
        update = selectedObjectTagUpdate
    )


class VRaySocketGenericAColor(VRaySocketAColor):
    bl_idname = 'VRaySocketGenericAColor'
    bl_label  = 'Color with Alpha socket'

    value: bpy.props.FloatVectorProperty(
        name = "Color",
        description = "Color",
        subtype = 'COLOR',
        size = 4,
        min = -_COLOR_LIMIT,
        max =  _COLOR_LIMIT,
        soft_min = 0.0,
        soft_max = 1.0,
        default = (0.0, 0.0, 0.0, 1.0),
        update = selectedObjectTagUpdate
    )


class VRaySocketGenericEnum(VRayValueSocket):
    """ Enum socket for generic plugin nodes.

        Unlike VRaySocketEnum, it stores its own value instead of proxying a property group,
        and reads its items from the plugin description at draw time. That way one registered
        class serves every plugin, and the socket still works when there is no description at
        all - it then falls back to editing the raw value.
    """
    bl_idname = 'VRaySocketGenericEnum'
    bl_label  = 'Generic enum socket'

    # The V-Ray value verbatim, e.g. "1". This is the storage; 'selected' is only the widget,
    # and it writes through to here - so this is where the update callback has to live or an
    # edit never reaches an interactive render.
    value: bpy.props.StringProperty(
        name = "Value",
        description = "Value",
        default = "",
        update = selectedObjectTagUpdate
    )

    def _setSelected(self, index):
        items = _getEnumItems(self)
        if index < len(items):
            self.value = items[index][0]

    def _getSelected(self):
        return next((i for i, item in enumerate(_getEnumItems(self)) if item[0] == self.value), 0)

    selected: bpy.props.EnumProperty(
        name = "Value",
        description = "Value",
        items = lambda self, context: _getEnumItems(self),
        get = _getSelected,
        set = _setSelected,
        options = set()
    )

    def draw_impl(self, context, layout, node, text):
        if self.hasActiveFarLink():
            layout.label(text=text)
        elif _getEnumItems(self):
            layout.prop(self, 'selected', text=text)
        else:
            layout.prop(self, 'value', text=text)

    def draw_property(self, context, layout, text, expand=False, slider=True):
        propName = 'selected' if _getEnumItems(self) else 'value'
        layout.prop(self, propName, text=text, expand=expand)

    def exportUnlinked(self, nodeCtx, pluginDesc, attrDesc):
        # V-Ray enum values are ints, which the descriptions spell as strings. A handful are
        # genuinely strings in the AppSDK, so anything non-numeric is passed through as is.
        value = self.value
        with contextlib.suppress(ValueError):
            value = int(value)
        pluginDesc.setAttribute(attrDesc['attr'], value)

    @classmethod
    def draw_color_simple(cls):
        return HIDDEN_SOCKET_COLOR


class VRayNodeGenericPlugin(VRayNodeBase):
    """ Node for a V-Ray plugin the addon has no generated node class for.

        One registered class serves every such plugin: the plugin type is held in 'vray_plugin'
        and every value lives in a socket created when the node is built, so nothing has to be
        registered per plugin. Registering a node class or property group at import time would
        not survive a .blend reload - the class does not exist while the file is being read and
        the nodes would come back undefined.
    """
    bl_idname = 'VRayNodeGenericPlugin'
    bl_label  = 'V-Ray Plugin'
    bl_icon   = 'PLUGIN'

    vray_type:   bpy.props.StringProperty(default='TEXTURE')
    vray_plugin: bpy.props.StringProperty(default='NONE')

    # Attributes whose sockets form an indexed list ('textures' -> "Textures 1", "Textures 2").
    # Comma separated. A one-element list still has to export as a list, so this cannot be
    # inferred from the number of sockets sharing an attribute.
    list_attrs: bpy.props.StringProperty(default='', options={'HIDDEN'})

    @classmethod
    def poll(cls, ntree):
        return hasattr(ntree, 'vray')

    def draw_buttons(self, context, layout):
        layout.label(text=self.vray_plugin)

    def draw_buttons_ext(self, context, layout):
        layout.label(text=f"Plugin: {self.vray_plugin}")

        for sock in self.inputs:
            # Sockets of types that are not drawn on the node itself (bools, enums, strings ...)
            # are only reachable from the sidebar.
            if (not sock.enabled) and (fnDraw := getattr(sock, 'draw_property', None)):
                fnDraw(context, layout, sock.name)

    def copy(self, srcNode):
        # The exported plugin is named after unique_id (lib/names.Names.struct), which Blender
        # duplicates along with the node.
        from vray_blender.lib.names import syncObjectUniqueName
        syncObjectUniqueName(self, reset=True)

    def update(self):
        from vray_blender.nodes.nodes import vrayNodeUpdate
        vrayNodeUpdate(self)

    def isListAttr(self, attrName: str):
        return attrName in self.list_attrs.split(',')

    def markListAttr(self, attrName: str):
        attrs = [a for a in self.list_attrs.split(',') if a]
        if attrName not in attrs:
            attrs.append(attrName)
            self.list_attrs = ','.join(attrs)


########  ########  ######   ####  ######  ######## ########     ###    ######## ####  #######  ##    ##
##     ## ##       ##    ##   ##  ##    ##    ##    ##     ##   ## ##      ##     ##  ##     ## ###   ##
##     ## ##       ##         ##  ##          ##    ##     ##  ##   ##     ##     ##  ##     ## ####  ##
########  ######   ##   ####  ##   ######     ##    ########  ##     ##    ##     ##  ##     ## ## ## ##
##   ##   ##       ##    ##   ##        ##    ##    ##   ##   #########    ##     ##  ##     ## ##  ####
##    ##  ##       ##    ##   ##  ##    ##    ##    ##    ##  ##     ##    ##     ##  ##     ## ##   ###
##     ## ########  ######   ####  ######     ##    ##     ## ##     ##    ##    ####  #######  ##    ##

def getRegClasses():
    return (
        VRaySocketGenericInt,
        VRaySocketGenericColor,
        VRaySocketGenericAColor,
        VRaySocketGenericEnum,
        VRayNodeGenericPlugin,
    )


def register():
    for regClass in getRegClasses():
        bpy.utils.register_class(regClass)


def unregister():
    _enumItemsCache.clear()

    for regClass in reversed(getRegClasses()):
        bpy.utils.unregister_class(regClass)
