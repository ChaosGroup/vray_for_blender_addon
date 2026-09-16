# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

""" Navigation UI for the V-Ray property pages: a breadcrumb, and the shading tree.

    Host-agnostic - the Material tab, the World tab, the Light data tab and the Scene Lister detail
    pane all call drawNavigation() with the tree they are editing. Nothing here reads context.material.

    The shading tree has two presentations behind the 'shading_tree_style' preference:

      BREADCRUMB  just the trail
      MENU_GLYPH  one button opening a flat menu whose hierarchy is drawn with box characters
"""

import bpy

from vray_blender.lib.blender_utils import getVRayPreferences
from vray_blender.lib.mixin import VRayOperatorBase
from vray_blender.nodes import navigation as NodesNav
from vray_blender.nodes import slots as Slots
from vray_blender.ui.node_icons import getNodeIcon

# Trail segments past this are collapsed behind a '...' that jumps to the root. Kept small because
# a Properties panel is narrow: three V-Ray node names already fill it.
_MAX_CRUMBS = 3


def _drawNavOperator(layout, ownerType, ownerName, node, text, depress=False, emboss=True):
    icon, iconValue = getNodeIcon(node)
    op = layout.operator('vray.set_panel_node', text=text, icon=icon, icon_value=iconValue,
                         depress=depress, emboss=emboss)
    op.owner_type = ownerType
    op.owner_name = ownerName
    op.node_name = node.name
    return op


def _crumbLabel(node) -> str:
    """ A trail segment's text. Drops the "V-Ray " prefix every node label carries: it is redundant
        here and costs six characters of a panel narrow enough that three names already truncate. """
    label = Slots.nodeDisplayName(node)
    return label[6:] if label.startswith("V-Ray ") else label


def _drawHistoryButton(row, ownerType, ownerName, delta, icon):
    _index, target = NodesNav.historyTarget((ownerType, ownerName), delta)

    sub = row.row(align=True)
    sub.enabled = target is not None
    op = sub.operator('vray.panel_node_history', text="", icon=icon)
    op.owner_type = ownerType
    op.owner_name = ownerName
    op.delta = delta


def drawNavButtons(layout, ownerType, ownerName, ntree, activeNode):
    """ [back][forward][up], in one aligned row. Shared by every navigation style: the arrows are
        how the user retraces their steps, so a style that offers a different way to jump around
        still needs them.
    """
    navRow = layout.row(align=True)
    _drawHistoryButton(navRow, ownerType, ownerName, -1, 'BACK')
    _drawHistoryButton(navRow, ownerType, ownerName, 1, 'FORWARD')

    upRow = navRow.row(align=True)
    upRow.enabled = NodesNav.getParentNode(ntree, activeNode) is not None
    up = upRow.operator('vray.panel_node_up', text="", icon='SORT_DESC')
    up.owner_type = ownerType
    up.owner_name = ownerName
    up.node_name = activeNode.name
    return navRow


def drawBreadcrumb(layout, ownerType, ownerName, ntree, treeType, activeNode):
    """ [back][forward][up] root > ... > current on one line, each segment jumping to that node.

        alignment='LEFT' is what makes the segments size to their own text: without it the row
        spreads them across its full width and each button's icon ends up far from its label.
        The segments are embossed so they read as buttons rather than as a run of static text.
    """
    path = NodesNav.getNodePath(ntree, activeNode, treeType)

    # Buttons and trail share one line, buttons on the left. The outer row is align=False and
    # LEFT-aligned so each child sizes to its own content - that is what keeps the trail readable
    # here, unlike the earlier attempt that put the segments in the same *aligned* row as the
    # buttons and squeezed every one of them down to "V-...".
    line = layout.row(align=False)
    line.alignment = 'LEFT'

    drawNavButtons(line, ownerType, ownerName, ntree, activeNode)

    # align=True so the segments and their '>' separators butt against each other. An unaligned row
    # pads every item, which on a trail of alternating buttons and separator icons adds up to a
    # visible gap on each side of every '>'.
    row = line.row(align=True)
    row.alignment = 'LEFT'

    if len(path) > _MAX_CRUMBS:
        _drawNavOperator(row, ownerType, ownerName, path[0], "...")
        row.label(text="", icon='RIGHTARROW_THIN')
        path = path[-(_MAX_CRUMBS - 1):]

    for index, node in enumerate(path):
        if index:
            row.label(text="", icon='RIGHTARROW_THIN')
        _drawNavOperator(row, ownerType, ownerName, node,
                         _crumbLabel(node), depress=(node == activeNode))


def _drawTreeRows(layout, ownerType, ownerName, rows, activeNode):
    """ One row per node, hierarchy drawn with box characters. """
    col = layout.column(align=True)

    for row in rows:
        line = col.row(align=True)

        prefix = "".join('|   ' if bar else '    ' for bar in row.barMask)
        prefix += '\\-- ' if row.isLast else '|-- '
        text = f"{prefix}{Slots.nodeDisplayName(row.node)}"

        if row.isRef:
            text += "  (linked again)"

        _drawNavOperator(line, ownerType, ownerName, row.node, text,
                         depress=(row.node == activeNode), emboss=False)


def _treeRows(ntree, treeType):
    return NodesNav.collectShadingTree(NodesNav.getTreeRootNode(ntree, treeType))


def getShadingTreeStyle(context) -> str:
    try:
        return getVRayPreferences(context).shading_tree_style
    except Exception:
        return 'BREADCRUMB'


def drawNavigation(layout, context, ownerType, ownerName, ntree, treeType, activeNode):
    """ Whichever navigation presentation the 'shading_tree_style' preference selects. """
    if ntree is None or activeNode is None:
        return

    style = getShadingTreeStyle(context)

    # Record the node actually on screen, from the draw rather than from the navigation operators.
    # The panel target is resolved from tree selection (getPanelNode), so it also moves when the
    # user clicks a node in the Shader Editor - navigation the operators never see. Recording only
    # their jumps left the cursor pointing at a node the user had already left, and Back then
    # skipped over it. recordNavigation is a no-op while the target still matches the cursor, so
    # redraws are free and stepping through history does not re-record.
    NodesNav.recordNavigation((ownerType, ownerName), activeNode.name)

    if style == 'MENU_GLYPH':
        _setTarget(context, ownerType=ownerType, ownerName=ownerName, treeType=treeType)
        label = Slots.nodeDisplayName(activeNode)
        icon, iconValue = getNodeIcon(activeNode)

        line = layout.row(align=False)
        line.alignment = 'LEFT'
        drawNavButtons(line, ownerType, ownerName, ntree, activeNode)

        row = line.row(align=True)
        row.menu('VRAY_MT_shading_tree', text=label, icon=icon, icon_value=iconValue)
        return

    drawBreadcrumb(layout, ownerType, ownerName, ntree, treeType, activeNode)


############################################################
# Menu presentation
############################################################

# Which tree the menu should draw. It cannot carry operator properties, so the target is stamped
# during draw - but keyed by the area that drew it, because two editors showing different materials
# (a pinned Properties tab beside the Scene Lister) both draw in the same redraw and a single slot
# would leave whichever drew last owning both dropdowns.
_targetsByArea: dict = {}


def _areaKey(context):
    area = getattr(context, 'area', None)
    return area.as_pointer() if area else 0


def _setTarget(context, **target):
    _targetsByArea[_areaKey(context)] = target


def _getTarget(context):
    return _targetsByArea.get(_areaKey(context), {})


def _drawTargetTree(layout, context):
    target = _getTarget(context)
    ownerType, ownerName = target.get('ownerType'), target.get('ownerName')
    treeType = target.get('treeType')
    if not ownerName:
        return

    if (ntree := Slots.resolveOwnerTree(ownerType, ownerName)) is None:
        return

    rows = _treeRows(ntree, treeType)
    if not rows:
        layout.label(text="No textures connected", icon='INFO')
        return

    activeNode = NodesNav.getPanelNode(ntree, treeType)
    _drawTreeRows(layout, ownerType, ownerName, rows, activeNode)


class VRAY_MT_shading_tree(bpy.types.Menu):
    bl_idname = 'VRAY_MT_shading_tree'
    bl_label = "Shading Tree"

    def draw(self, context):
        _drawTargetTree(self.layout, context)


############################################################
# Operators
############################################################

class VRAY_OT_panel_node_up(VRayOperatorBase):
    """ Show the parameters of the node this one feeds """
    bl_idname = 'vray.panel_node_up'
    bl_label = "Up"
    bl_options = {'INTERNAL'}

    owner_type: bpy.props.StringProperty(options={'HIDDEN'})
    owner_name: bpy.props.StringProperty(options={'HIDDEN'})
    node_name:  bpy.props.StringProperty(options={'HIDDEN'})

    def execute(self, context):
        resolved = Slots.resolveSlotNode(self.owner_type, self.owner_name, self.node_name)
        if resolved is None:
            return {'CANCELLED'}

        ntree, node = resolved
        parent = NodesNav.getParentNode(ntree, node)
        if parent is None:
            return {'CANCELLED'}

        NodesNav.setPanelNode(ntree, parent)
        NodesNav.recordNavigation((self.owner_type, self.owner_name), parent.name)
        _tagNavRedraw()
        return {'FINISHED'}


class VRAY_OT_panel_node_history(VRayOperatorBase):
    """ Step back or forward through the nodes visited in the property pages """
    bl_idname = 'vray.panel_node_history'
    bl_label = "Navigation History"
    bl_options = {'INTERNAL'}

    owner_type: bpy.props.StringProperty(options={'HIDDEN'})
    owner_name: bpy.props.StringProperty(options={'HIDDEN'})
    delta: bpy.props.IntProperty(default=-1, options={'HIDDEN'})

    @classmethod
    def description(cls, context, properties):
        return "Go back to the previously edited node" if properties.delta < 0 \
            else "Go forward to the next edited node"

    def execute(self, context):
        ownerKey = (self.owner_type, self.owner_name)
        index, nodeName = NodesNav.historyTarget(ownerKey, self.delta)
        if nodeName is None:
            return {'CANCELLED'}

        resolved = Slots.resolveSlotNode(self.owner_type, self.owner_name, nodeName)
        if resolved is None:
            # The node is gone (deleted, or the file was reloaded). Move the cursor anyway so a
            # second click keeps walking instead of getting stuck on a dead entry.
            NodesNav.setHistoryIndex(ownerKey, index)
            return {'CANCELLED'}

        ntree, node = resolved
        NodesNav.setPanelNode(ntree, node)
        NodesNav.setHistoryIndex(ownerKey, index)
        _tagNavRedraw()
        return {'FINISHED'}


def _tagNavRedraw():
    from vray_blender.nodes.utils import tagRedrawShadingEditors
    tagRedrawShadingEditors()


def getRegClasses():
    return (
        VRAY_OT_panel_node_up,
        VRAY_OT_panel_node_history,
        VRAY_MT_shading_tree,
    )


def register():
    from vray_blender.lib.class_utils import registerClass
    for regClass in getRegClasses():
        registerClass(regClass)


def unregister():
    for regClass in reversed(getRegClasses()):
        bpy.utils.unregister_class(regClass)
