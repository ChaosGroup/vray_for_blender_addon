# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

""" The texture slot picker shown on property pages.

    Draws a picker onto every parameter row the user could wire a texture into on the node itself,
    so a texture can be created, replaced, entered or cleared without opening the Node Editor.
    Used by the Material tab, the Light data tab, the World tab and the Scene Lister detail pane -
    they all opt in with lib.draw_utils.slotEditing(makeSlotContext(...)).

    The model - which sockets qualify, how a slot is addressed, and the graph edits - lives in
    nodes/slots.py. This module is only the UI.
"""

import bpy

from vray_blender.lib import draw_utils
from vray_blender.lib.mixin import VRayOperatorBase
from vray_blender.lib.image_utils import getVRayImageFormatFilter
from vray_blender.nodes import navigation as NodesNav
from vray_blender.nodes import slots as Slots
from vray_blender.nodes.texture_groups import (TEXTURE_GROUP_ICONS, clearItemsCache,
                                               getTextureItemsByGroup, getTextureItemsOfGroup)

# The image texture node is a meta node and is not part of buildItemsList('TEXTURE'), so the picker
# offers it explicitly - it is also by far the most common choice.
BITMAP_NODE_TYPE = 'VRayNodeMetaImageTexture'

_PICKER_ICON = 'NODE_TEXTURE'


############################################################
# Slot context - what a property page hands to draw_utils.slotEditing()
############################################################

class SlotContext:
    """ Identifies the tree a property page is editing, so every slot row it draws can address its
        socket without re-deriving the owner. Resolved once per panel, never per row - an earlier
        prototype rescanned bpy.data.materials on every socket draw. """

    def __init__(self, ownerType: str, ownerName: str):
        self.ownerType = ownerType
        self.ownerName = ownerName

    def _stampAddress(self, op, node, socket):
        op.owner_type = self.ownerType
        op.owner_name = self.ownerName
        op.node_name = node.name
        op.attr_name = socket.vray_attr

    def _drawPickerButton(self, layout, node, socket):
        op = layout.operator('vray.slot_open_picker', text="", icon=_PICKER_ICON)
        self._stampAddress(op, node, socket)

    def drawSlot(self, context, layout, socket, label, drawArgs) -> bool:
        """ Draw a texture slot row. Returns False for a socket that is not a texture slot, leaving
            the caller to draw it exactly as before. """
        if not Slots.isTextureSlot(socket):
            return False

        node = socket.node
        sourceNode = Slots.getSlotSourceNode(socket)

        if sourceNode is None:
            self._drawUnlinked(context, layout, node, socket, label, drawArgs)
        else:
            self._drawLinked(layout, node, socket, label, sourceNode)

        return True

    def _drawUnlinked(self, context, layout, node, socket, label, drawArgs):
        """ The socket's own value widget, with the picker button glued on. Letting the socket draw
            itself is what keeps the label column aligned with every other row - the split is
            Blender's, not ours. """
        # One aligned row holding the value widget and the picker, and nothing else. The animation
        # decorator is left to Blender: the row does draw a real property, so the automatic
        # decorator finds it. Reserving a second decorator column by hand is what made the value
        # widget noticeably shorter than the plain rows above and below it.
        row = layout.row(align=True)

        if hasattr(socket, 'draw_property'):
            socket.draw_property(context, row, label, **drawArgs)
        else:
            socket.draw(context, row, node, label)

        self._drawPickerButton(row, node, socket)

    def _drawLinked(self, layout, node, socket, label, sourceNode):
        """ The value is driven by a texture, so the widget is replaced by the texture itself:
            enter it, replace it, or clear it. """
        row = layout.row(align=True)
        row.use_property_decorate = False

        field = draw_utils.propertySplitRow(row, label)
        sub = field.row(align=True)
        # A link whose 'use' toggle is off is ignored on export. Grey it rather than hide it, so a
        # connection that is doing nothing is still visible and clearable.
        sub.active = Slots.isSlotUseEnabled(socket)

        # The label names the node that actually drives the slot, which getFarNodeLink resolves
        # across reroutes, muted nodes and node groups. Only offer to enter it when it lives in
        # THIS tree: a node inside a group is addressed by a name that does not exist here, so the
        # button would either do nothing or, on a name collision, select an unrelated node.
        label = Slots.nodeDisplayName(sourceNode)
        nearNode = socket.links[0].from_node if socket.links else None

        # Compared with ==, never 'is': both sides are separately fetched RNA wrappers around the
        # same node (one from getFarNodeLinkImpl, one from socket.links), and Python identity is not
        # guaranteed to hold between them. With 'is' this was always False, so even a directly
        # linked texture fell through to the plain label below and could no longer be entered.
        if nearNode == sourceNode:
            enter = sub.operator('vray.set_panel_node', text=label, icon=_PICKER_ICON)
            enter.owner_type = self.ownerType
            enter.owner_name = self.ownerName
            enter.node_name = sourceNode.name
        else:
            sub.label(text=label, icon=_PICKER_ICON)

        # No separate clear button: the picker menu already carries Clear when the slot is linked,
        # so a third button on every linked row would only crowd it.
        self._drawPickerButton(sub, node, socket)

        # Decorator-column pad. Keep the BLANK1 icon.
        row.label(text="", icon='BLANK1')


def drawSlotRow(context, layout, node, attrName: str, label: str) -> bool:
    """ Draw one attribute as a texture slot row, or return False for the caller to draw it itself.

        For UI templates (plugins/templates/*), which build their own layout and call layout.prop
        directly, so they never reach UIPainter._drawSocketRow and would otherwise show no picker -
        a light's colour is drawn by templateColorTemperature, not by the plain socket path.

        Returns False when there is no active slotEditing() block, when the node has no socket for
        the attribute, or when that socket is not a texture slot.
    """
    slotContext = draw_utils.getSlotContext()
    if (slotContext is None) or (node is None):
        return False

    from vray_blender.exporting.tools import getInputSocketByAttr
    if not (socket := getInputSocketByAttr(node, attrName)):
        return False

    return slotContext.drawSlot(context, layout, socket, label, {})


def makeSlotContext(context, ownerId, ntree) -> SlotContext | None:
    """ The SlotContext for a property page, or None when the tree must not be edited from one
        (library-linked data, or a tree we cannot address). Pass the result to slotEditing(). """
    owner = Slots.slotOwnerFor(ownerId, ntree)
    return SlotContext(*owner) if owner else None


############################################################
# Operators
############################################################

class VRaySlotOperatorBase(VRayOperatorBase):
    """ Base for the slot operators. Carries the owner-agnostic address and resolves it in
        execute(); nodes are addressed by name, never by pointer. """
    owner_type: bpy.props.StringProperty(options={'HIDDEN'})
    owner_name: bpy.props.StringProperty(options={'HIDDEN'})
    node_name:  bpy.props.StringProperty(options={'HIDDEN'})
    attr_name:  bpy.props.StringProperty(options={'HIDDEN'})

    def resolve(self):
        return Slots.resolveSlot(self.owner_type, self.owner_name, self.node_name, self.attr_name)


class VRAY_OT_set_panel_node(VRayOperatorBase):
    """ Show this node's parameters in the property pages """
    bl_idname = 'vray.set_panel_node'
    bl_label = "Show Node Parameters"
    bl_options = {'INTERNAL'}

    owner_type: bpy.props.StringProperty(options={'HIDDEN'})
    owner_name: bpy.props.StringProperty(options={'HIDDEN'})
    node_name:  bpy.props.StringProperty(options={'HIDDEN'})

    def execute(self, context):
        # Address a node in the same tree the slot operators use, without needing a socket.
        slot = Slots.resolveSlotNode(self.owner_type, self.owner_name, self.node_name)
        if slot is None:
            return {'CANCELLED'}

        ntree, node = slot
        if ntree.nodes.active == node and node.select:
            return {'CANCELLED'}

        # Seed the node we are leaving so Back can return to it.
        previous = NodesNav.getPanelNode(ntree, NodesNav.treeTypeOf(ntree))
        NodesNav.setPanelNode(ntree, node)
        NodesNav.recordNavigation((self.owner_type, self.owner_name), node.name,
                                  previous.name if previous else "")

        from vray_blender.nodes.utils import tagRedrawShadingEditors
        tagRedrawShadingEditors()
        return {'FINISHED'}


class VRAY_OT_slot_open_picker(VRaySlotOperatorBase):
    """ Add a texture to this parameter """
    bl_idname = 'vray.slot_open_picker'
    bl_label = "Texture"
    bl_options = {'INTERNAL'}

    def invoke(self, context, event):
        # Popped from invoke() so the menu appears under the cursor while the button is still held.
        target = context.window_manager.vray.slot_target
        target.owner_type = self.owner_type
        target.owner_name = self.owner_name
        target.node_name = self.node_name
        target.attr_name = self.attr_name

        bpy.ops.wm.call_menu(name='VRAY_MT_slot_picker')
        return {'FINISHED'}


class VRAY_OT_slot_assign_texture(VRaySlotOperatorBase):
    """ Connect this texture to the parameter """
    bl_idname = 'vray.slot_assign_texture'
    bl_label = "Assign Texture"
    bl_options = {'INTERNAL', 'UNDO'}

    node_type: bpy.props.StringProperty(options={'HIDDEN'})

    def execute(self, context):
        if not (slot := self.resolve()):
            return {'CANCELLED'}

        if Slots.assignTexture(slot, self.node_type) is None:
            self.report({'WARNING'}, f"{self.node_type} cannot drive this parameter")
            return {'CANCELLED'}

        Slots.retagSlotOwner(context, slot)
        return {'FINISHED'}


class VRAY_OT_slot_open_image(VRaySlotOperatorBase):
    """ Load an image file and connect it to the parameter """
    bl_idname = 'vray.slot_open_image'
    bl_label = "Open Image"
    bl_options = {'INTERNAL', 'UNDO'}

    filepath: bpy.props.StringProperty(subtype='FILE_PATH', options={'HIDDEN'})
    filter_glob: bpy.props.StringProperty(default=getVRayImageFormatFilter(), options={'HIDDEN'})
    relative_path: bpy.props.BoolProperty(name="Relative Path", default=True)

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        if not (slot := self.resolve()):
            return {'CANCELLED'}

        if not self.filepath:
            return {'CANCELLED'}

        # Load BEFORE touching the graph. assignTexture deletes whatever was driving the slot, so a
        # file Blender cannot read would otherwise destroy the existing texture and then raise -
        # and a raising operator pushes no undo step, making it unrecoverable.
        try:
            image = Slots.loadImage(self.filepath, self.relative_path)
        except RuntimeError as ex:
            self.report({'WARNING'}, f"Could not load image: {ex}")
            return {'CANCELLED'}

        node = Slots.assignTexture(slot, BITMAP_NODE_TYPE)
        if node is None:
            self.report({'WARNING'}, "An image cannot drive this parameter")
            return {'CANCELLED'}

        if getattr(node, 'texture', None) is not None:
            node.texture.image = image

        Slots.retagSlotOwner(context, slot)
        return {'FINISHED'}


class VRAY_OT_slot_clear(VRaySlotOperatorBase):
    """ Disconnect and delete the texture driving this parameter """
    bl_idname = 'vray.slot_clear'
    bl_label = "Clear Texture"
    bl_options = {'INTERNAL', 'UNDO'}

    def execute(self, context):
        if not (slot := self.resolve()):
            return {'CANCELLED'}

        # Report CANCELLED when nothing changed so Blender does not push an empty undo step.
        if not Slots.clearSlot(slot):
            return {'CANCELLED'}

        Slots.retagSlotOwner(context, slot)
        return {'FINISHED'}


############################################################
# Picker menus
############################################################

def _stampFromTarget(op, target, nodeType=None):
    op.owner_type = target.owner_type
    op.owner_name = target.owner_name
    op.node_name = target.node_name
    op.attr_name = target.attr_name
    if nodeType is not None:
        op.node_type = nodeType


class VRAY_MT_slot_picker(bpy.types.Menu):
    bl_idname = 'VRAY_MT_slot_picker'
    bl_label = "Add Texture"
    bl_options = {'SEARCH_ON_KEY_PRESS'}

    def draw(self, context):
        layout = self.layout
        target = context.window_manager.vray.slot_target

        # A menu popped by wm.call_menu has no owning button, and Blender gives such a menu
        # EXEC_REGION_WIN (popup_menu_create_block: 'pup->but ? InvokeRegionWin : ExecRegionWin').
        # Its entries then skip invoke() entirely, so 'Open Image...' never reached fileselect_add
        # and its execute() just returned CANCELLED on an empty filepath. The other entries are
        # exec-only and were unaffected, which is why this was the only dead one.
        layout.operator_context = 'INVOKE_DEFAULT'

        # Loading an image is the only bitmap entry: picking a bare V-Ray Bitmap node and then
        # having to set its file is a strictly worse path to the same place.
        _stampFromTarget(layout.operator('vray.slot_open_image', text="Open Image...", icon='FILE_FOLDER'), target)

        layout.separator()

        for groupId, groupLabel, _items in getTextureItemsByGroup():
            layout.menu(_groupMenuIdname(groupId), text=groupLabel, icon=TEXTURE_GROUP_ICONS.get(groupId, 'NONE'))

        # Only offer Clear when there is something to clear.
        if (slot := Slots.resolveSlot(target.owner_type, target.owner_name, target.node_name, target.attr_name)) \
                and slot.socket.is_linked:
            layout.separator()
            _stampFromTarget(layout.operator('vray.slot_clear', text="Clear", icon='X'), target)


def _groupMenuIdname(groupId: str) -> str:
    return f'VRAY_MT_slot_picker_{groupId.lower()}'


def _makeGroupMenuClass(groupId: str, groupLabel: str):
    def draw(self, context):
        layout = self.layout
        target = context.window_manager.vray.slot_target
        for idname, label in getTextureItemsOfGroup(groupId):
            _stampFromTarget(layout.operator('vray.slot_assign_texture', text=label), target, idname)

    return type(_groupMenuIdname(groupId), (bpy.types.Menu,), {
        'bl_idname': _groupMenuIdname(groupId),
        'bl_label': groupLabel,
        'bl_options': {'SEARCH_ON_KEY_PRESS'},
        'draw': draw,
    })


# One Menu class per group, built once and cached: register() and unregister() must see the same
# class objects, or unregister would be handed freshly created classes that were never registered.
_groupMenuClasses = []


def _getGroupMenuClasses():
    from vray_blender.nodes.texture_groups import TEXTURE_GROUPS, FALLBACK_GROUP, FALLBACK_GROUP_LABEL

    if not _groupMenuClasses:
        for groupId, groupLabel, _members in TEXTURE_GROUPS:
            _groupMenuClasses.append(_makeGroupMenuClass(groupId, groupLabel))
        _groupMenuClasses.append(_makeGroupMenuClass(FALLBACK_GROUP, FALLBACK_GROUP_LABEL))

    return tuple(_groupMenuClasses)


def getRegClasses():
    return _getGroupMenuClasses() + (
        VRAY_OT_set_panel_node,
        VRAY_OT_slot_open_picker,
        VRAY_OT_slot_assign_texture,
        VRAY_OT_slot_open_image,
        VRAY_OT_slot_clear,
        VRAY_MT_slot_picker,
    )


def register():
    from vray_blender.lib.class_utils import registerClass
    for regClass in getRegClasses():
        registerClass(regClass)


def unregister():
    for regClass in reversed(getRegClasses()):
        bpy.utils.unregister_class(regClass)

    # Both caches key off the registered plugin/node set, so a reload must rebuild them.
    Slots.clearCaches()
    clearItemsCache()
