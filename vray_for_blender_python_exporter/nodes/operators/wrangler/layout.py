# SPDX-FileCopyrightText: Blender Foundation
# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Align / center / select-hierarchy ops are based on Blender's
# Node Wrangler add-on (GPL-2.0-or-later).

""" Node layout and selection operators: align, center, select parent /
    children, and add a shared UV mapping node to selected bitmaps.
"""

from copy import copy

import bpy

from vray_blender.lib.mixin import VRayOperatorBase
from vray_blender.exporting.tools import getInputSocketByAttr
from vray_blender.nodes.tools import deselectNodes
from vray_blender.nodes.operators.wrangler.helpers import absLoc
from vray_blender.nodes.operators.wrangler.poll import isVrayEditor, hasEditTree, hasSelection


########## Align Selected ##########

class VRAY_OT_WR_align_selected(VRayOperatorBase):
    """Align selected nodes in a grid pattern"""
    bl_idname = "vray.wr_align_nodes"
    bl_label = "Align Nodes"
    bl_options = {'REGISTER', 'UNDO'}

    margin: bpy.props.IntProperty(
        name="Margin",
        description="The amount of space between nodes",
        default=50,
    )

    @classmethod
    def poll(cls, context):
        return isVrayEditor(context) and hasEditTree(context) and hasSelection(context)

    def execute(self, context):
        nodes = context.space_data.edit_tree.nodes
        margin = self.margin

        selection = [node for node in nodes if node.select and node.type != 'FRAME']
        if not selection:
            self.report({'WARNING'}, "No nodes to arrange in selection.")
            return {'CANCELLED'}

        activeLoc = None
        if nodes.active in selection:
            activeLoc = copy(nodes.active.location_absolute)

        xCenters = [node.location_absolute.x + (node.dimensions.x / 2) for node in selection]
        yCenters = [node.location_absolute.y - (node.dimensions.y / 2) for node in selection]
        midX = (max(xCenters) + min(xCenters)) / 2
        midY = (max(yCenters) + min(yCenters)) / 2
        horizontal = (max(xCenters) - min(xCenters)) > (max(yCenters) - min(yCenters))

        if horizontal:
            selection.sort(key=lambda node: node.location_absolute.x + (node.dimensions.x / 2))
        else:
            selection.sort(key=lambda node: node.location_absolute.y - (node.dimensions.y / 2), reverse=True)

        cursor = 0.0
        for node in selection:
            nodeMargin = margin * 0.5 if node.hide else margin
            if horizontal:
                node.location_absolute.x = cursor
                cursor += nodeMargin + node.dimensions.x
                node.location_absolute.y = midY + (node.dimensions.y / 2)
            else:
                hideOffset = (node.dimensions.y - (node.bl_height_min + 6)) / 2 if node.hide else 0
                node.location_absolute.y = cursor - hideOffset
                cursor -= (nodeMargin * 0.3) + node.dimensions.y
                node.location_absolute.x = midX - (node.dimensions.x / 2)

        if activeLoc is not None:
            diff = activeLoc - nodes.active.location_absolute
            for node in selection:
                node.location_absolute += diff
        else:
            locs = ([node.location_absolute.x + (node.dimensions.x / 2) for node in selection]
                    if horizontal
                    else [node.location_absolute.y - (node.dimensions.y / 2) for node in selection])
            newMid = (max(locs) + min(locs)) / 2
            for node in selection:
                if horizontal:
                    node.location_absolute.x += (midX - newMid)
                else:
                    node.location_absolute.y += (midY - newMid)
        return {'FINISHED'}


########## Center Selected ##########

class VRAY_OT_WR_center_nodes(VRayOperatorBase):
    """Move selected nodes to the center of the node editor"""
    bl_idname = "vray.wr_center_nodes"
    bl_label = "Center Nodes"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return isVrayEditor(context) and hasEditTree(context) and hasSelection(context)

    def execute(self, context):
        # Only move outermost selected nodes (children move with their frames).
        roots = [node for node in context.selected_nodes if not (node.parent and node.parent.select)]
        if not roots:
            return {'CANCELLED'}

        xs, ys, rights, bottoms = [], [], [], []
        for node in roots:
            loc = absLoc(node)
            xs.append(loc.x)
            ys.append(loc.y)
            if node.type == 'FRAME':
                rights.append(loc.x + node.width)
                bottoms.append(loc.y - node.height)
            elif node.type == 'REROUTE':
                rights.append(loc.x)
                bottoms.append(loc.y)
            else:
                rights.append(loc.x + node.width)
                bottoms.append(loc.y - node.dimensions.y)

        midX = (min(xs) + max(rights)) / 2
        midY = (max(ys) + min(bottoms)) / 2

        for node in roots:
            node.location.x -= midX
            node.location.y -= midY
        return {'FINISHED'}


########## Select Hierarchy ##########

class VRAY_OT_WR_select_hierarchy(VRayOperatorBase):
    bl_idname = "vray.wr_select_parent_child"
    bl_label = "Select Parent or Children"
    bl_options = {'REGISTER', 'UNDO'}

    _parent_desc = "Select frame containing the selected nodes"
    _child_desc = "Select members of the selected frame"

    option: bpy.props.EnumProperty(
        name="Option",
        items=(
            ('PARENT', 'Select Parent', _parent_desc),
            ('CHILD', 'Select Children', _child_desc),
        ),
    )

    @classmethod
    def description(cls, _context, properties):
        return cls._parent_desc if properties.option == 'PARENT' else cls._child_desc

    @classmethod
    def poll(cls, context):
        return isVrayEditor(context) and hasEditTree(context) and hasSelection(context)

    def execute(self, context):
        nodes = context.space_data.edit_tree.nodes
        selected = [node for node in nodes if node.select]
        if self.option == 'PARENT':
            for node in selected:
                if node.parent:
                    node.parent.select = True
        else:
            for node in selected:
                for child in nodes:
                    if child.parent == node:
                        child.select = True
        return {'FINISHED'}


########## Add UV Mapping ##########

class VRAY_OT_WR_add_uvw_mapping(VRayOperatorBase):
    """Create a VRayNodeUVWMapping and wire it into the 'uvwgen' input of every
       selected V-Ray node that exposes one. Reuses one mapping node across the
       selection so a single UV setup can drive a whole bitmap set."""
    bl_idname = "vray.wr_add_uvw_mapping"
    bl_label = "Add UV Mapping to Selected"
    bl_options = {'REGISTER', 'UNDO'}

    mapping_type: bpy.props.EnumProperty(
        name="Mapping Type",
        items=[
            ('UV',          "UV",          "UV mapping (UVWGenMayaPlace2dTexture)"),
            ('PROJECTION',  "Projection",  "Generated/projection mapping (UVWGenProjection)"),
            ('OBJECT',      "Object",      "Object mapping (UVWGenObject)"),
            ('ENVIRONMENT', "Environment", "Environment mapping (UVWGenEnvironment)"),
        ],
        default='UV',
    )

    @classmethod
    def poll(cls, context):
        return isVrayEditor(context) and hasEditTree(context) and hasSelection(context)

    def execute(self, context):
        ntree = context.space_data.edit_tree
        selected = [node for node in ntree.nodes if node.select]

        # Discover which selected nodes have a uvwgen socket and don't already have it driven.
        # Skip anything without the socket (most BRDFs, mtls).
        targets = []
        for node in selected:
            uvwSocket = getInputSocketByAttr(node, 'uvwgen') if hasattr(node, 'vray_plugin') else None
            if uvwSocket is not None and not uvwSocket.is_linked:
                targets.append((node, uvwSocket))

        if not targets:
            self.report({'INFO'}, "No selected V-Ray nodes expose a UV mapping input")
            return {'CANCELLED'}

        uvwNode = ntree.nodes.new('VRayNodeUVWMapping')
        uvwNode.mapping_node_type = self.mapping_type
        # Place to the left of the leftmost target.
        leftX = min(node.location.x for node, _ in targets) - 300.0
        midY = sum(node.location.y for node, _ in targets) / len(targets)
        uvwNode.location = (leftX, midY)

        mappingOutput = uvwNode.outputs.get('Mapping') or (uvwNode.outputs[0] if uvwNode.outputs else None)
        if mappingOutput is None:
            self.report({'WARNING'}, "UVW mapping node has no output socket")
            return {'CANCELLED'}

        linked = 0
        for _node, uvwSocket in targets:
            try:
                ntree.links.new(mappingOutput, uvwSocket)
                linked += 1
            except RuntimeError:
                pass

        deselectNodes(ntree)
        uvwNode.select = True
        ntree.nodes.active = uvwNode
        ntree.update_tag()
        self.report({'INFO'}, f"Wired UV mapping into {linked} node(s)")
        return {'FINISHED'}


########## Registration ##########

def getRegClasses():
    return (
        VRAY_OT_WR_align_selected,
        VRAY_OT_WR_center_nodes,
        VRAY_OT_WR_select_hierarchy,
        VRAY_OT_WR_add_uvw_mapping,
    )


def register():
    for regClass in getRegClasses():
        bpy.utils.register_class(regClass)


def unregister():
    for regClass in reversed(getRegClasses()):
        bpy.utils.unregister_class(regClass)
