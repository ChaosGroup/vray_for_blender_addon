# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import os
import re

import bpy

from vray_blender import debug
from vray_blender.lib.defs import ExporterContext, PluginDesc
from vray_blender.lib.mixin import VRayOperatorBase
from vray_blender.plugins.templates import common


# The ids passed to template_list(). They only have to be unique among the lists drawn in the same
# region, and there is never more than one Object Hierarchy rollout on screen.
_LIST_ID           = 'vray_proxy_visibility'
_SHADERSET_LIST_ID = 'vray_proxy_shader_sets'
_SLOT_LIST_ID      = 'vray_proxy_material_slots'

# The width of the name column of a list row, as a fraction of the row. The names are indented by
# their depth and the material dropdowns are not, so a split is what keeps the dropdowns lined up.
_NAME_COLUMN = 0.6

# Matches the RNA path of a list item, e.g. 'vray.GeomMeshFile.object_hierarchy.items[3]'.
_ITEM_PATH = re.compile(r'^(?P<owner>.+)\.items\[(?P<index>\d+)\]$')

# Guards the group -> children cascade in _onItemEnabledChanged: assigning 'enabled' on a child
# fires that child's own update callback.
_cascading = False

# What the rows of a list hold. Bumped when the reader starts recording something the saved rows
# cannot carry, so that a list stored by an older add-on is re-read once instead of quietly
# behaving as if the file lacked what it never looked for.
#   1: the shader sets, without which no row can carry a material override
#   2: which rows have a single child, which decides where the material dropdown is drawn
#   3: 'perObject' is now False for a file with no shader sets, which used to be vacuously True
#   4: the material the slots were imported with, which is what labels them
#   5: that material itself and not only its name, which is what Reset puts back
_LIST_SCHEMA = 5

# Lists waiting to be read from their proxy file, as (mesh name, RNA path of the template) pairs.
# draw() cannot read the file itself - that spawns the 'vraytools' process - so it queues the list
# here and a one-shot timer does the reading, the same deferral the add-on uses in
# engine/zmq_process.py and debug.py.
_pendingRebuilds = []


def _rebuildPending():
    """ Timer callback: read the proxy files of the lists queued by draw(). """
    queued, _pendingRebuilds[:] = list(_pendingRebuilds), []

    for meshName, templatePath in queued:
        if (mesh := bpy.data.meshes.get(meshName)) is None:
            continue

        try:
            template = mesh.path_resolve(templatePath)
        except ValueError:
            # The list was removed or renamed between the draw and this callback.
            continue

        # Re-checked, not assumed: several panels may have queued the same list, and a Reset in
        # between could have read it already.
        if template.needsRebuild():
            # The state is kept: this also runs for a list that only has to catch up with the
            # reader, and losing the user's check marks over a schema bump would be gratuitous.
            if err := template.rebuild(preserveState=True):
                debug.printError(err)

    _tagPropertiesRedraw()
    return None  # one-shot


def _scheduleRebuild(template):
    """ Queue a list to be read once the current draw is over. """
    key = (template.id_data.name, template.path_from_id())

    if key in _pendingRebuilds:
        return

    _pendingRebuilds.append(key)
    if len(_pendingRebuilds) == 1:
        bpy.app.timers.register(_rebuildPending)


def _tagPropertiesRedraw():
    """ Repaint the Properties editors, so a list that has just been read appears. """
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == 'PROPERTIES':
                area.tag_redraw()


def _resolveItem(item: VRayProxyHierarchyItem):
    """ Return the (template, index) pair owning a list item, resolved from its RNA path. """
    if not (match := _ITEM_PATH.match(item.path_from_id())):
        return None, -1

    return item.id_data.path_resolve(match['owner']), int(match['index'])


def _onItemEnabledChanged(item, context):
    """ Toggling a group toggles its whole subtree, like the Cinema 4D item tree does. """
    global _cascading

    # The list lives on the mesh datablock; tag it so that an interactive render picks up the
    # changed visibility.
    item.id_data.update_tag()

    if _cascading or not item.isGroup:
        return

    template, index = _resolveItem(item)
    if template is None:
        return

    # The list is in DFS pre-order, so the subtree of a row is exactly the following rows
    # with a greater depth.
    _cascading = True
    try:
        for descendant in template.items[index + 1:]:
            if descendant.depth <= item.depth:
                break
            descendant.enabled = item.enabled
    finally:
        _cascading = False


def _onItemMaterialChanged(item, context):
    # The list lives on the mesh datablock, so tag it and every object using it - the material is
    # exported as part of the object's Node, not of the mesh.
    mesh = item.id_data
    mesh.update_tag()

    for obj in (o for o in bpy.data.objects if o.data is mesh):
        obj.update_tag()


# Row types. A row can be both: an Alembic object without face sets is its own shader set, so the
# two land on the same row, while a '.vrmesh' shader set called 'mtl1' gets a row of its own.
_TYPE_OBJECT    = 1   # an object of the proxy file - it can be shown or hidden
_TYPE_SHADERSET = 2   # a shader set of the proxy file - it can carry a material override


def buildHierarchyRows(objectNames: list[str], shaderSets: list[dict] = None):
    """ Turn the object paths and the shader sets of a proxy file into the rows of the list.

        Intermediate levels of the hierarchy get a row of their own even when they are not
        objects in the proxy file. The rows are emitted in DFS pre-order, so that the subtree of
        a row is exactly the following rows with a greater depth - the list filtering, the group
        checkbox cascade and the material inheritance all rely on this.

        The shader sets go into the same tree, because V-Ray builds their names out of the object
        names (AlembicMeshFile::initMeshSetsData): an object with no face sets gets one shader set
        named exactly after it, one with face sets gets '<objectName><faceSetName>' per face set.
        A name that is an object is therefore merged onto that object's row, one that extends an
        object becomes a child of it labelled with the remainder, and one that resembles no object
        at all - which is every '.vrmesh' shader set - becomes a row of its own.

        Args:
            objectNames: the full '/'-separated paths of the objects in the file.
            shaderSets: the file's shader sets as {'name': str, 'id': int} dicts, in file order.

        Returns:
            tuple[list[dict], bool]: the rows, one dict per row with the keys assigned below, and
                whether the file has shader sets and they are its objects - see
                perObjectMaterials().
    """
    children  = {}   # fullPath -> list of child paths, in the order they were first seen
    types     = {}   # fullPath -> the ORed row types
    shaderIds = {}   # fullPath -> the ID of the shader set sitting on that path
    labels    = {}   # fullPath -> the row label, when it is not the last segment of the path
    owners    = {}   # shader set name -> the object it belongs to, for the ones that have one
    objects   = []   # the object paths, in file order
    roots     = []

    for objName in objectNames:
        # Same splitting rule as the proxy-to-mesh hierarchy builder in proxy.py
        parts = [p for p in objName.split('/') if p] or [objName]

        # Keep the leading slash - the paths are exported back to V-Ray and have to match
        # the names read from the file exactly.
        prefix = '/' if objName.startswith('/') else ''

        path   = ""
        parent = None

        for part in parts:
            path = f"{path}/{part}" if path else f"{prefix}{part}"

            if path not in children:
                children[path] = []
                types[path]    = 0
                if parent is None:
                    roots.append(path)
                else:
                    children[parent].append(path)

            parent = path

        if not (types[path] & _TYPE_OBJECT):
            objects.append(path)
        types[path] |= _TYPE_OBJECT

    # The objects are matched longest first, so that an object nested inside another one claims its
    # own shader sets rather than leaving them to its ancestor.
    byLength = sorted(objects, key=len, reverse=True)
    perObject = True

    for shaderSet in (shaderSets or []):
        name = shaderSet['name']

        if name in shaderIds:
            # Two IDs can carry the same name - the shading groups of a '.vrmesh' are not
            # guaranteed unique. One row can only stand for one of them, so the first ID wins.
            continue

        if name in children:
            # The shader set is a node of the object tree already - an object with no face sets, or
            # (rarely) an intermediate level. Merge onto that row instead of adding a second one.
            perObject = perObject and bool(types[name] & _TYPE_OBJECT)
            types[name] |= _TYPE_SHADERSET
            shaderIds[name] = shaderSet['id']
            continue

        perObject = False
        # '<objectName><faceSetName>', with no separator at use_face_sets=1 and a '/' at 2
        owner = next((o for o in byLength if name.startswith(o)), None)

        children[name]  = []
        types[name]     = _TYPE_SHADERSET
        shaderIds[name] = shaderSet['id']
        labels[name]    = name[len(owner):].lstrip('/') if owner else name

        if owner:
            owners[name] = owner

        # Appended after the object's own children, so the face sets of an object stay together
        # below it. A shader set that resembles no object at all becomes a root row.
        (children[owner] if owner else roots).append(name)

    # The remainder is what makes the shader set list readable ('inside_up'), but exporters name
    # every object's face set the same ('Max_MaterialID_0'), and in that list the owner is no longer
    # the row above. Name the owner on the labels that would otherwise collide - two sets under the
    # same owner with the same remainder would be one name and have already been dropped above, so
    # this always tells them apart. A set with no owner is labelled with its full name and is unique
    # to begin with.
    labelUses = {}
    for label in labels.values():
        labelUses[label] = labelUses.get(label, 0) + 1

    for name, owner in owners.items():
        if labelUses[labels[name]] > 1:
            labels[name] = f"{labels[name]} ({owner.rsplit('/', 1)[-1]})"

    rows  = []
    stack = [(p, 0) for p in reversed(roots)]

    while stack:
        path, depth = stack.pop()
        rows.append({
            'name'       : labels.get(path) or path.rsplit('/', 1)[-1],
            'fullPath'   : path,
            'depth'      : depth,
            'isGroup'    : bool(children[path]),
            'isObject'   : bool(types[path] & _TYPE_OBJECT),
            'isShaderSet': bool(types[path] & _TYPE_SHADERSET),
            'shaderId'      : shaderIds.get(path, -1),
            'branchTypes'   : 0,
            'soleChildTypes': 0,
        })
        stack.extend((child, depth + 1) for child in reversed(children[path]))

    # 'branchTypes' is the OR of the types of everything below a row, which is what decides whether
    # the row offers a control that only its descendants can act on. Walking the pre-order rows
    # backwards accumulates it in a single pass: the rows that follow a row and are deeper than it
    # are exactly its subtree.
    pending = []    # (depth, ORed types) of the rows still looking for their parent

    for row in reversed(rows):
        branch = 0

        while pending and pending[-1][0] > row['depth']:
            branch |= pending.pop()[1]

        row['branchTypes'] = branch
        pending.append((row['depth'], branch | types[row['fullPath']]))

    # A row is in the list of a type when it is one or stands above one, and a group with a single
    # child there has nothing of its own to assign: its material would reach that child and nothing
    # else, and the child carries a dropdown anyway. Record where that is the case, per list, so
    # that a chain of one-child groups does not repeat the same assignment at every level.
    inList = {row['fullPath']: types[row['fullPath']] | row['branchTypes'] for row in rows}

    for row in rows:
        for rowType in (_TYPE_OBJECT, _TYPE_SHADERSET):
            if sum(1 for child in children[row['fullPath']] if inList[child] & rowType) == 1:
                row['soleChildTypes'] |= rowType

    # A file with no shader sets at all is not 'per object' - it is a file whose materials the rows
    # cannot address at any granularity, and what stands in for them is the object's material slots.
    return rows, (perObject and bool(shaderSets))


class VRayProxyImportedSlot(bpy.types.PropertyGroup):
    """ One material slot of the proxy as the importer created it.

        Both halves are needed, and for different reasons. The inherited 'name' is the material's
        name at the time of the import and never changes again - it is what labels the row, and it
        has to keep naming the shading group of the asset even after the material next to it has
        been swapped or renamed. The pointer is the material itself, which is what Reset puts back;
        being a pointer it survives a rename, and it counts as a user, so the material is still
        there to go back to once the user has assigned over every slot. Both are empty for a slot
        that came in empty. See TemplateProxyObjectHierarchy.captureImportedSlots().
    """

    material: bpy.props.PointerProperty(
        type = bpy.types.Material,
        description = "The material the proxy importer put in this slot"
    )


class VRayProxyHierarchyItem(bpy.types.PropertyGroup):
    """ A single row of the Object Hierarchy rollout - an object, a group above one, or a shader set.

        The hierarchy is stored flattened in DFS pre-order rather than as a nested structure -
        Blender's UIList is flat anyway, and the pre-order makes both filtering and the
        group cascade single passes over the collection.
    """

    # 'name' is inherited from PropertyGroup and holds the last segment of fullPath

    fullPath: bpy.props.StringProperty(
        description = "The full path of the object inside the proxy file"
    )

    depth: bpy.props.IntProperty(
        description = "The nesting level of the item, used to indent it in the list"
    )

    isGroup: bpy.props.BoolProperty(
        description = "True if the item has children"
    )

    isObject: bpy.props.BoolProperty(
        description = "True if an object with this exact path exists in the proxy file. Intermediate "
                      "levels of the hierarchy may not be objects themselves"
    )

    isShaderSet: bpy.props.BoolProperty(
        description = "True if the proxy file has a shader set of this name. V-Ray shades by shader "
                      "set, so only these rows can carry a material override"
    )

    shaderId: bpy.props.IntProperty(
        default = -1,
        description = "The ID of the shader set the row stands for, -1 when it is not one. It is "
                      "also the material ID of the faces V-Ray shades with that set"
    )

    branchTypes: bpy.props.IntProperty(
        description = "The types of the rows below this one, ORed together"
    )

    soleChildTypes: bpy.props.IntProperty(
        description = "The row types this row has exactly one child of. Such a row's material "
                      "would reach that child and nothing else, so it is left to the child"
    )

    expanded: bpy.props.BoolProperty(
        default = True,
        description = "Show the children of the item"
    )

    # 'enabled' is the row's membership of the visibility list, not its visibility as such. With
    # the default 'Include' list type the two coincide - the checked objects are the visible ones -
    # but under 'Exclude' the checked ones are the hidden ones.
    enabled: bpy.props.BoolProperty(
        name = "Enable",
        default = True,
        description = "Put the object in the visibility list. With the 'Include' list type the "
                      "listed objects are the only visible ones, with 'Exclude' they are the "
                      "hidden ones",
        update = _onItemEnabledChanged
    )

    material: bpy.props.PointerProperty(
        type = bpy.types.Material,
        name = "Material",
        description = "Material to use for this item instead of the proxy's own material. "
                      "Setting it on a group applies it to the whole branch",
        update = _onItemMaterialChanged
    )


    def inList(self, rowType: int):
        """ True when the row belongs in the list showing the rows of this type.

            That is the row itself being one, or standing above one: a group is in the list its
            children are in, since the control it offers acts on them.
        """
        ownTypes = (_TYPE_OBJECT if self.isObject else 0) \
                 | (_TYPE_SHADERSET if self.isShaderSet else 0)

        return bool((ownTypes | self.branchTypes) & rowType)


    def hasChildrenInList(self, rowType: int):
        """ True when the row has children in the list showing the rows of this type.

            Which is not the same as having children at all - in the visibility list an object
            whose only children are its face sets is a leaf, and a disclosure triangle on it would
            reveal nothing.
        """
        return bool(self.branchTypes & rowType)


    def canOverrideMaterial(self):
        """ True when a material set on this row would reach at least one shader set. """
        return self.inList(_TYPE_SHADERSET)


    def showsMaterial(self, rowType: int):
        """ True when the row's own material dropdown is drawn in the list of this type.

            Not on a group with a single child there: whatever it were given would reach that child
            and nothing else, which the child's own dropdown already says. A row that is a shader
            set itself always shows one - no child stands in for its own set.
        """
        return self.canOverrideMaterial() \
               and (self.isShaderSet or not (self.soleChildTypes & rowType))


    def canToggleVisibility(self):
        """ True when the row stands for something that can be shown or hidden.

            A bare shader set is not: the visibility list matches object names.
        """
        return self.inList(_TYPE_OBJECT)


def _drawTreeControls(row: bpy.types.UILayout, item, index: int, rowType: int):
    """ The indent and the disclosure triangle at the head of a row. """
    if item.depth:
        # UILayout has no indent for list rows, fake it with a scaled separator
        row.separator(factor=item.depth * 1.5)

    if item.hasChildrenInList(rowType):
        op = row.operator('vray.proxy_hierarchy_toggle_expand', text="", emboss=False,
                          icon='DISCLOSURE_TRI_DOWN' if item.expanded else 'DISCLOSURE_TRI_RIGHT')
        op.index = index
    else:
        row.label(text="", icon='BLANK1')


def _noteDropTargets(uiList: bpy.types.UIList, hasTargets: bool):
    """ Tell the material pickup whether this list has rows a material can be dropped on, and say
        whether it should draw them as drop targets right now.

        Recorded on every draw rather than declared once at registration, because it is not a
        property of the list but of what it is drawing: the visibility list carries a material
        only where the shader sets of the file are its objects, and none at all otherwise - a
        '.vrmesh' with no shader sets has nothing in its hierarchy to assign to, and its materials
        are the object's slots. Without this the pickup would wear its cursor over a list that
        cannot take what it is carrying.

        Returns:
            bool: whether a pickup is in progress and this list has somewhere for it to land.
    """
    from vray_blender.ui.lister.assign_drag import pickedUpMaterial, setListTakesDrop

    setListTakesDrop(type(uiList).__name__, hasTargets)

    return hasTargets and (pickedUpMaterial() is not None)


def _drawRowName(row: bpy.types.UILayout, item, dropIndex: int, dropTarget: str):
    """ The name of a tree row, with the icon its type calls for.

        Args:
            dropIndex: the index of the row when a material can be dropped on its name, -1 when
                none can - see _drawLabel().
            dropTarget: which list drew the row, for the drop operator - see its 'target'.
    """
    if item.isObject:
        nameIcon = 'OUTLINER_OB_MESH'
    elif item.isShaderSet:
        nameIcon = 'MATERIAL_DATA'
    else:
        nameIcon = 'OUTLINER_COLLECTION'

    # A shader set that carries a material is drawn with that material's own preview instead of the
    # generic swatch, so that a glance down the list says what each set is shaded with. Only where
    # the swatch would have been: an object row keeps the object icon even where it is its own
    # shader set, since the list is a hierarchy of objects first.
    # UILayout.icon() is what renders the preview and hands back its icon id - it needs a real
    # datablock, so a set with no material of its own keeps the swatch. The preview is generated on
    # demand, so the very first redraw after an assignment may still draw the id it had before.
    if (nameIcon == 'MATERIAL_DATA') and (item.material is not None):
        nameIcon, iconValue = 'NONE', row.icon(item.material)
    else:
        iconValue = 0

    _drawLabel(row, item.name, nameIcon, iconValue, dropIndex, dropTarget)


def _drawLabel(cell: bpy.types.UILayout, text: str, icon: str, iconValue: int, dropIndex: int,
               dropTarget: str):
    """ The name of a row, which during a material pickup is also what a material is dropped on.

        'Assign Material' in the Scene Lister picks a material up and the next click drops it, and
        what it drops onto in the viewport is found by a raycast. A list row is not something a
        raycast can find, so the name becomes a button while a pickup lasts and Blender's own hit
        testing finds it instead. It runs a panel button on mouse release, which is precisely the
        release that ends the drop.

        Nothing about the row changes for it. emboss=False leaves the widget background undrawn
        (interface_widgets.cc:2653) and a left aligned row makes the button hug its text, so what
        is left is the name and its icon exactly where the label put them. What the button adds is
        the hover brightening every button gets (widget_state(), interface_widgets.cc:2645, which
        applies whatever the emboss) - and that is the drop target marking itself out under the
        cursor, one row at a time.

        Args:
            dropIndex: the index of the row, or -1 when nothing is being dropped or the row has
                no material of its own to set - see _noteDropTargets().
            dropTarget: which list drew the row, which is how the operator finds it again and
                which selection it moves - see the operator's 'target'.
    """
    if dropIndex < 0:
        cell.label(text=text, icon=icon, icon_value=iconValue)
        return

    button = cell.row()
    button.alignment = 'LEFT'

    op = button.operator('vray.proxy_hierarchy_drop_material', text=text, emboss=False,
                         icon=icon, icon_value=iconValue)
    op.index  = dropIndex
    op.target = dropTarget


def _filterRows(uiList: bpy.types.UIList, items, rowType: int):
    """ The row flags of a list showing the rows of one type - see filter_items() below.

        The two lists are views of the same collection, so each starts by dropping the rows that
        are not its own. Those are then out of the way entirely: they cannot match the search and
        they never keep an ancestor on screen.
    """
    numItems = len(items)
    flags = [uiList.bitflag_filter_item] * numItems

    for i, item in enumerate(items):
        if not item.inList(rowType):
            flags[i] = 0

    # 'filter_name' is Blender's own list filter field, drawn by _drawListFilter() below. It is
    # flagged PROP_TEXTEDIT_UPDATE, so it re-filters on every keystroke without the user
    # having to confirm the field.
    searchText = uiList.filter_name.lower()

    if searchText:
        # A row survives if its own path matches or any of its descendants match, so that the
        # parents of a hit stay visible and the hierarchy is not broken. The rows are in DFS
        # pre-order, so one forward pass with a stack of the current ancestors is enough.
        # Collapsed groups are ignored while filtering - a match nested inside one is still
        # shown, the same as in the Cinema 4D item tree.
        # Matching is a plain case-insensitive substring test on the full path, as in C4D, and
        # not Blender's fnmatch: the paths are the values exported to V-Ray, so a literal
        # match is what the user is reasoning about.
        invert = uiList.use_filter_invert
        keep = [False] * numItems
        ancestors = []

        for i, item in enumerate(items):
            # Every row takes its slot on the ancestor stack, including the ones this list does
            # not show, so that the slots stay in step with the depths.
            del ancestors[item.depth:]
            ancestors.append(i)

            # Inverting flips the per-row test, not the final result: 'show what does not
            # match, plus its parents'. Flipping the finished flags instead would hide the
            # ancestors of the surviving rows and leave a broken tree.
            if flags[i] and ((searchText in item.fullPath.lower()) is not invert):
                for ancestor in ancestors:
                    keep[ancestor] = True

        for i in range(numItems):
            if not keep[i]:
                flags[i] = 0

        return flags, []

    # No search text - hide everything below a collapsed row.
    collapsedDepth = -1

    for i, item in enumerate(items):
        if not flags[i]:
            continue

        if collapsedDepth >= 0:
            if item.depth > collapsedDepth:
                flags[i] = 0
                continue
            collapsedDepth = -1

        if item.hasChildrenInList(rowType) and not item.expanded:
            collapsedDepth = item.depth

    return flags, []


def _slotLabel(owner, index: int, slot):
    """ How a material slot is labelled in the list: its index, then the material it came with.

        The index leads because it is what the faces of the proxy carry. The name is the one
        recorded when the slots were created, not the one in the dropdown next to it - the row
        stands for a shading group of the asset, and would stop naming it the moment the material
        was changed. A slot added by hand after that has no recorded name and falls back to the
        material it holds.
    """
    vrayData = getattr(getattr(owner, 'data', None), 'vray', None)
    template = getattr(getattr(vrayData, 'GeomMeshFile', None), 'object_hierarchy', None)

    if (template is not None) and (index < len(template.importedSlots)):
        recorded = template.importedSlots[index].name
    else:
        recorded = slot.material.name if slot.material else ""

    return f"{index}: {recorded}" if recorded else f"{index}:"


def _drawListFilter(uiList: bpy.types.UIList, layout: bpy.types.UILayout):
    """ Only the name filter, deliberately without Blender's default sort controls.

        The default filter panel also offers 'sort alphabetically' and 'reverse'. Those must
        not be shown here: the rows are stored in DFS pre-order, and both the collapse
        handling in _filterRows() and the group checkbox cascade rely on a row's subtree
        being exactly the following rows with a greater depth. Reordering the list would
        silently break both.
    """
    row = layout.row(align=True)
    row.prop(uiList, 'filter_name', text="", icon='VIEWZOOM')
    row.prop(uiList, 'use_filter_invert', text="", icon='ARROW_LEFTRIGHT')


class VRAY_UL_ProxyVisibility(bpy.types.UIList):
    """ The objects of the proxy file, and the groups above them.

        A bare shader set is not here: it stands for nothing that can be shown or hidden, and its
        material is set in the shader set list instead.
    """

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        # The operators need the list they belong to; the row's context is the only way to hand a
        # property group to an operator.
        layout.context_pointer_set('vrayProxyHierarchy', data)

        # Where the shader sets are the objects, each row carries the material of its own object.
        # Where they are not, no row here can, and the whole width goes to the name.
        withMaterial = data.perObjectMaterials()

        # Which is also what decides whether a material can be dropped on this list at all: with
        # no material column there is nowhere for one to land, and the pickup must not claim
        # otherwise by wearing its cursor over the rows.
        dropping = _noteDropTargets(self, withMaterial)

        if withMaterial:
            split = layout.split(factor=_NAME_COLUMN)
            row   = split.row(align=True)
        else:
            split = None
            row   = layout.row(align=True)

        showsMaterial = withMaterial and item.showsMaterial(_TYPE_OBJECT)

        _drawTreeControls(row, item, index, _TYPE_OBJECT)
        row.prop(item, 'enabled', text="")

        # A material is dropped on the name, and only where the row has a dropdown to put it in.
        _drawRowName(row, item, index if (dropping and showsMaterial) else -1, 'VISIBILITY')

        if split is not None:
            materialCell = split.row(align=True)

            if showsMaterial:
                materialCell.prop(item, 'material', text="")
            else:
                # A row with no shader set of its own - an Alembic instance shares the one of the
                # object it was cloned from - or a group standing in front of a single child. Keep
                # the column, so the dropdowns of the other rows stay lined up.
                materialCell.label(text="")


    def filter_items(self, context, data, propname):
        return _filterRows(self, getattr(data, propname), _TYPE_OBJECT)


    def draw_filter(self, context, layout):
        _drawListFilter(self, layout)


class VRAY_UL_ProxyShaderSets(bpy.types.UIList):
    """ The shader sets of the proxy file, and the objects and groups above them.

        Drawn only where the shader sets are not the objects, which is where the material cannot be
        set in the visibility list itself. A material on one of the object rows covers every shader
        set below it.
    """

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        layout.context_pointer_set('vrayProxyHierarchy', data)

        # This list exists because the file has shader sets, and a shader set row always carries a
        # material of its own - so there is always somewhere here for a drop to land.
        dropping = _noteDropTargets(self, True)

        showsMaterial = item.showsMaterial(_TYPE_SHADERSET)

        split = layout.split(factor=_NAME_COLUMN)
        row   = split.row(align=True)

        _drawTreeControls(row, item, index, _TYPE_SHADERSET)

        # A material is dropped on the name, and only where the row has a dropdown to put it in.
        _drawRowName(row, item, index if (dropping and showsMaterial) else -1, 'SHADER_SET')

        materialCell = split.row(align=True)

        if showsMaterial:
            materialCell.prop(item, 'material', text="")
        else:
            # A group standing in front of a single shader set: the row below it is the assignment.
            materialCell.label(text="")


    def filter_items(self, context, data, propname):
        return _filterRows(self, getattr(data, propname), _TYPE_SHADERSET)


    def draw_filter(self, context, layout):
        _drawListFilter(self, layout)


class VRAY_UL_ProxyMaterialSlots(bpy.types.UIList):
    """ The material slots of the proxy object, for a file that defines no shader sets.

        There is then nothing in the hierarchy a material can be attached to, and the slots are
        what the proxy is shaded with - the importer creates them from the file's shader sets or,
        for a Cosmos asset, from the 'ids_list' of the material file's MtlMulti. This list is the
        object's own collection, so an assignment here is the slot itself and needs no export path
        of its own: it is already what the exporter turns into the 'ids_list' MtlMulti.
    """

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        # 'data' is the object the slots belong to, since that is whose collection this list draws.
        # A dropped material is written to one of its slots, so the drop operator needs it.
        layout.context_pointer_set('vrayProxySlotOwner', data)

        # Every row here is a slot and every slot holds a material, so a drop always lands.
        dropping = _noteDropTargets(self, True)

        split = layout.split(factor=_NAME_COLUMN)

        # For a collection of material slots Blender hands draw_item() the preview of the slot's
        # material as 'icon', falling back to the generic swatch for an empty one, which is what
        # Blender's own MATERIAL_UL_matslots draws.
        _drawLabel(split.row(align=True), _slotLabel(data, index, item), 'NONE', icon,
                   index if dropping else -1, 'SLOT')

        split.row(align=True).prop(item, 'material', text="")


class VRAY_OT_proxy_hierarchy_toggle_expand(VRayOperatorBase):
    bl_idname      = 'vray.proxy_hierarchy_toggle_expand'
    bl_label       = "Expand/Collapse"
    bl_description = "Show or hide the children of the item"
    # Expanding a row is pure UI state, keep it out of the undo stack
    bl_options     = {'INTERNAL'}

    index: bpy.props.IntProperty(default=-1)

    def execute(self, context):
        template = getattr(context, 'vrayProxyHierarchy', None)

        if (template is None) or not (0 <= self.index < len(template.items)):
            return {'CANCELLED'}

        item = template.items[self.index]
        item.expanded = not item.expanded
        return {'FINISHED'}


class VRAY_OT_proxy_hierarchy_drop_material(VRayOperatorBase):
    """ The drop half of the Scene Lister's 'Assign Material' pickup, for a row of one of the lists
        this rollout draws. Drawn by _drawLabel(), which is where the rest of it is explained.
    """
    bl_idname      = 'vray.proxy_hierarchy_drop_material'
    bl_label       = "Assign Material"
    bl_description = "Assign the material you are carrying to this item. Esc cancels the pickup"
    bl_options     = {'INTERNAL', 'UNDO'}

    index: bpy.props.IntProperty(default=-1)

    # Which list drew the row. The first two are views of the same collection, so the index means
    # the same in both and only the selection to move differs; the third indexes the material slots
    # of the object instead.
    target: bpy.props.EnumProperty(
        items = (('VISIBILITY', "Visibility",     ""),
                 ('SHADER_SET', "Shader Sets",    ""),
                 ('SLOT',       "Material Slots", "")),
        default = 'VISIBILITY'
    )

    def execute(self, context):
        from vray_blender.ui.lister.assign_drag import dropPickedUpMaterial

        # Taken before anything else, because this is what ends the pickup: it has to end whether
        # or not the row it was dropped on is still there, or the modal would be left running with
        # no way out but Esc.
        material = dropPickedUpMaterial(context)

        if material is None:
            return {'CANCELLED'}

        if self.target == 'SLOT':
            return self._assignToSlot(context, material)

        template = getattr(context, 'vrayProxyHierarchy', None)

        # The row can be gone: the button is drawn on one redraw and runs on the release after the
        # next, and a Reset in between rebuilds the rows.
        if (template is None) or not (0 <= self.index < len(template.items)):
            return {'CANCELLED'}

        # A plain assignment, the same one the dropdown makes - which is also what puts the
        # material in that dropdown, since nothing else about the row has changed.
        template.items[self.index].material = material

        # Select the row the material landed on, as a click on it would have done - in the list
        # that drew it, since the other one may well not be showing that row.
        if self.target == 'SHADER_SET':
            template.activeShaderSet = self.index
        else:
            template.activeItem = self.index

        return {'FINISHED'}


    def _assignToSlot(self, context, material):
        """ The material slot list assigns the slot itself - there is no override to carry. """
        owner = getattr(context, 'vrayProxySlotOwner', None)

        if (owner is None) or not (0 <= self.index < len(owner.material_slots)):
            return {'CANCELLED'}

        owner.material_slots[self.index].material = material
        owner.active_material_index = self.index
        return {'FINISHED'}


class TemplateProxyObjectHierarchy(common.VRayUITemplate):
    """ What is inside a V-Ray Proxy file: which of its objects are rendered, and with what.

        The rollout draws two areas over one collection of rows read from the file. *Visibility*
        lists the objects and the groups above them, each with a checkbox; the checked paths are
        exported as the plugin's visibility list, and the Include/Exclude dropdown in its header
        says whether they are the visible objects or the hidden ones. *Materials* holds the
        overrides, and what it shows depends on what the file offers - the rows themselves where
        every shader set of the file is one of its objects, a second tree of shader sets where
        they are not, and the object's own material slots for a file that defines no shader sets
        at all. An override on a group reaches its whole branch, the closest one to a shader set
        winning.

        *Reset Object Hierarchy* re-reads the file: everything is shown again, the overrides are
        dropped and the material slots go back to what the proxy was imported with.

        See docs/ui_templates.md for how the rows are built and how the overrides are exported.

        Template arguments:
           file_property: the name of the string property holding the path to the proxy file.
                          Defaults to 'file'.
           mode_bound_property: the name of the enum property holding the include/exclude mode.
                          Defaults to 'visibility_lists_type'.
    """

    items: bpy.props.CollectionProperty(
        type = VRayProxyHierarchyItem
    )

    # The material each of the object's slots was imported with, in slot order - what labels the
    # rows of the material slot list and what Reset puts back. See captureImportedSlots().
    importedSlots: bpy.props.CollectionProperty(
        type = VRayProxyImportedSlot
    )

    # The 0-based index of the currently selected visibility list item, -1 for no selection
    activeItem: bpy.props.IntProperty(default = -1)

    # The 0-based index of the currently selected shader set list item, -1 for no selection
    activeShaderSet: bpy.props.IntProperty(default = -1)

    # The absolute path rebuild() last read, empty until it has run.
    sourceFile: bpy.props.StringProperty()

    # True when every shader set of the file is one of its objects. See perObjectMaterials().
    perObject: bpy.props.BoolProperty()

    # What the stored rows hold, see _LIST_SCHEMA. 0 is a list saved before they carried the
    # shader sets, and asks for one re-read.
    schema: bpy.props.IntProperty()


    def draw(self, layout: bpy.types.UILayout, context: bpy.types.Context,
                    pluginModule, propGroup, widgetAttr: dict, text):
        layout.use_property_split    = False
        layout.use_property_decorate = False

        # The file has never been read for this list - a scene saved before the rollout existed, an
        # import that set the path directly, a repath - or it was read by an add-on that recorded
        # less than this one does. Reading it here would spawn a process from a draw() procedure,
        # so queue it and let the one-shot timer do it.
        if self._getProxyFilePath() and self.needsRebuild():
            _scheduleRebuild(self)

        if len(self.items) == 0:
            # Replace an empty list with a message explaining the reason
            if not self._getProxyFilePath():
                layout.label(text="No proxy file selected")
            elif self.needsRebuild():
                layout.label(text="Reading objects from the proxy file...")
            else:
                layout.label(text="No objects in the proxy file")

        hasObjects = any(i.canToggleVisibility() for i in self.items)
        hasShaderSets = any(i.canOverrideMaterial() for i in self.items)

        # A file that defines no shader sets leaves nothing in the hierarchy to attach a material
        # to. What the proxy is shaded with is then the object's material slots, so those are what
        # the second area shows - the assignment there is the slot itself.
        slotOwner = None if hasShaderSets else self._getSlotOwner(context)

        if hasObjects:
            # The list type is a property of the plugin, not of the template, so it is bound
            # directly to the host property group. 
            header = layout.row()
            header.label(text="Visibility")

            typeCell = header.row()
            typeCell.alignment = 'RIGHT'
            typeCell.prop(propGroup, self._getModeProperty(), text="")

            layout.template_list('VRAY_UL_ProxyVisibility', _LIST_ID, self, 'items',
                                 self, 'activeItem', rows=8)

            # Regenerating the preview means running 'vraytools' over the whole file, which is too
            # much to do on every click of a checkbox - hence a button rather than an update
            # callback. The automatic reloads (a new file, a different preview type or face count)
            # apply the current list on their own.
            common.VRAY_OT_simple_button.create(layout, 'Update Preview',
                            'Regenerate the viewport preview so that it shows only the visible '
                            'objects. Affects only the preview, render always respects the list.'
                            'May not always work for "Preview" preview mode depending on the '
                            'contents of the proxy file',
                            self, 'onRefreshPreview')

            layout.separator(type='SPACE')

        if hasShaderSets and not self.perObjectMaterials():
            if hasObjects:
                layout.separator(type='LINE')

            layout.label(text="Materials")
            layout.template_list('VRAY_UL_ProxyShaderSets', _SHADERSET_LIST_ID, self, 'items',
                                 self, 'activeShaderSet', rows=4)

        elif (slotOwner is not None) and slotOwner.material_slots:
            if hasObjects:
                layout.separator(type='LINE')

            layout.label(text="Materials")
            layout.template_list('VRAY_UL_ProxyMaterialSlots', _SLOT_LIST_ID,
                                 slotOwner, 'material_slots',
                                 slotOwner, 'active_material_index', rows=4)

        common.VRAY_OT_simple_button.create(layout, 'Reset Object Hierarchy',
                        'Re-read the hierarchy from the proxy file, showing all objects again, '
                        'dropping the material overrides and putting the material slots back to '
                        'the materials the proxy was imported with',
                        self, 'onResetObjectHierarchy')


    def rebuild(self, preserveState=False):
        """ Re-read the object list from the proxy file.

            Any previous check state and material override is discarded unless preserveState is
            set.

            On failure the hierarchy is left exactly as it was: a path that fails to read - a typo,
            a file temporarily missing - must not cost the user their visibility list and material
            overrides, since restoring the file (or fixing the path) and retrying is the expected
            recovery.

            NOTE: This runs the 'vraytools' process, never call it from a draw() procedure.

            Returns:
                str | None: an error message on failure, None on success.
        """
        from vray_blender.vray_tools.vray_proxy import readProxyObjectNames

        absFilePath = self._getAbsProxyFilePath()

        if not absFilePath:
            # Not a failure to preserve against - this is the empty state itself.
            self.setObjectNames([], [], absFilePath)
            return None

        if not os.path.isfile(absFilePath):
            # Stamp the path as read so needsRebuild() does not keep re-queuing it on every
            # redraw, but do not touch the hierarchy - see the docstring.
            self.sourceFile = absFilePath
            return f"File not found: {self._getProxyFilePath()}"

        savedState = self._snapshotState() if preserveState else None

        shaderSets = []
        objectNames, err = readProxyObjectNames(absFilePath, outShaders=shaderSets)
        if err:
            self.sourceFile = absFilePath
            return err

        self.setObjectNames(objectNames, shaderSets, absFilePath, savedState)
        return None


    def captureImportedSlots(self, owner: bpy.types.Object | None):
        """ Record the material each of the object's slots was created with, and its name.

            The slots of a proxy stand for the shading groups of the asset and they have no names.
            Use the name of the material to name the slot instead. This should be pretty meaningful
            if the materials have names tied to the geometry parts, but for the case where it is not
            we cannot do anything more.
        """
        self.importedSlots.clear()

        for slot in (owner.material_slots if owner else ()):
            imported = self.importedSlots.add()
            imported.material = slot.material
            imported.name     = slot.material.name if slot.material else ""


    def restoreSlotMaterials(self, owner: bpy.types.Object | None):
        """ Put every slot back to the material it was imported with. 

            Any slot past the end of the snapshot was added by hand after the import and 
            has no imported material to go back to, and one the user deleted is not re-created. 
        """
        if owner is None:
            return

        for index, imported in enumerate(self.importedSlots):
            if index >= len(owner.material_slots):
                break

            # None covers both a slot that came in empty and one whose imported material has since
            # been deleted from the blend outright, which clears every pointer to it. Either way
            # there is no imported material any more, and empty is the honest answer.
            owner.material_slots[index].material = imported.material


    def _snapshotState(self):
        """ What the user has set on the rows, keyed by path, to carry over across a re-read. """
        return {i.fullPath: (i.enabled, i.expanded, i.material) for i in self.items}


    def setObjectNames(self, objectNames: list[str], shaderSets: list[dict], absFilePath: str,
                       savedState: dict = None):
        """ Fill the list from data that has already been read from the proxy file.

            The preview dump returns the object names and the shader sets alongside the geometry,
            so the common paths come through here and never run the tool a second time. rebuild()
            is what reads them when nobody else has.

            savedState, from _snapshotState(), carries the check marks and the material overrides
            over by path - for the re-read of the very same file that a schema bump asks for. A row
            the new read no longer produces simply loses its state, and a new one starts at the
            defaults.

            An older 'vraytools' reports no shader sets. The list then holds the objects alone and
            no row offers a material override.
        """
        global _cascading

        state = savedState or {}

        self.items.clear()
        self.activeItem      = -1
        self.activeShaderSet = -1

        self.sourceFile = absFilePath
        self.schema     = _LIST_SCHEMA

        rows, self.perObject = buildHierarchyRows(objectNames, shaderSets)

        # A group's check mark is restored along with its children's, so its cascade must not run
        _cascading = True
        try:
            for row in rows:
                item = self.items.add()
                item.name        = row['name']
                item.fullPath    = row['fullPath']
                item.depth       = row['depth']
                item.isGroup     = row['isGroup']
                item.isObject    = row['isObject']
                item.isShaderSet    = row['isShaderSet']
                item.shaderId       = row['shaderId']
                item.branchTypes    = row['branchTypes']
                item.soleChildTypes = row['soleChildTypes']

                if (saved := state.get(row['fullPath'])) is not None:
                    item.enabled, item.expanded, item.material = saved
        finally:
            _cascading = False

        # The snapshot is normally the importer's: it is the one place that knows what the slots
        # were created with. This is the fallback for a proxy imported before it existed, where
        # the slots current contents is what we are going to revert to.
        if not shaderSets and not self.importedSlots:
            self.captureImportedSlots(self._getSlotOwner(bpy.context))


    def needsRebuild(self):
        """ Retur tnrue when the list has not been read from the file it is currently pointing at, 
            or was read by an add-on that recorded less than this one does.
        """
        # The schema clause only applies to a list that was actually read once: a fresh one has no
        # schema recorded either, and with no file there is nothing to re-read.
        return self.needsRebuildFor(self._getAbsProxyFilePath()) \
               or (bool(self.sourceFile) and self.schema < _LIST_SCHEMA)


    def needsRebuildFor(self, absFilePath: str):
        """ True when the list has not been read from this exact file.

            The preview load asks with the path it is loading rather than the one on the property,
            because the import operators load the preview before assigning the property.
        """
        return self.sourceFile != absFilePath


    def _resolveMaterials(self):
        """ Retturn the material each row ends up with - its own, or the closest ancestor's - 
            in row order.

            The rows are in DFS pre-order, so the ancestors of a row are the rows before it with 
            a smaller depth, and a stack of those carrying a material has the closest at its top.
        """
        resolved  = []
        ancestors = []  # (depth, material) of the rows above the current one that carry a material

        for item in self.items:
            depth = item.depth

            # Anything at this depth or deeper is a branch that has been left behind, not an
            # ancestor of this row.
            while ancestors and (ancestors[-1][0] >= depth):
                ancestors.pop()

            if (material := item.material) is not None:
                ancestors.append((depth, material))
            elif ancestors:
                material = ancestors[-1][1]

            resolved.append(material)

        return resolved


    # Callback for VRAY_OT_simple_button
    def onResetObjectHierarchy(self, context: bpy.types.Context, selectorID: str):
        # rebuild() re-reads unconditionally, so this is also the way out of a file that failed to
        # read: the lazy path in draw() will not retry one on its own.
        if err := self.rebuild():
            debug.reportError(err)
            return

        # The rows come back fresh from the file, which is what drops the overrides they carried.
        # The material slots are the object's, not the list's, so the rebuild leaves them behind -
        # putting them back is the other half of Reset. In every mode, not only where the slots
        # are the list the rollout draws: they hold the proxy's shading whichever list it draws,
        # and the button says it puts them back. 
        self.restoreSlotMaterials(self._getSlotOwner(context))


    def getVisibilityList(self) -> tuple[list[str], int]:
        """ The visibility list as the renderer and the preview both see it: the full paths it
            holds, and what they mean (0 Exclude, 1 Include).

            The single place this is decided. The renderer gets it through exportToPluginDesc() and
            'vraytools' through the preview dump, and a preview that disagreed with the render
            would be worse than no preview filtering at all.
        """
        # Only the rows that are objects in the file can be matched. An intermediate level of the
        # hierarchy which is not an object itself contributes nothing - the group cascade has
        # already applied its check state to the rows below it.
        listedNames = [i.fullPath for i in self.items if i.isObject and i.enabled]

        # The mode is a parameter of the plugin, not of the template, so it lives on the host - the
        # same property group draw() binds the dropdown to.
        listType = int(getattr(self._getHost(), self._getModeProperty(), 0) or 0)

        if not any(i.isObject for i in self.items):
            # Nothing was read from the file - no file yet, no object info channel in it, or the
            # read failed. An empty 'include' list means 'hide the whole proxy', with nothing in
            # the UI to explain it, so fall back to the neutral 'exclude nothing'. Explicitly
            # unchecking every row is a different thing and is left alone - hiding everything is
            # then exactly what the user asked for.
            listType = 0

        return listedNames, listType


    def exportToPluginDesc(self, exporterCtx: ExporterContext, pluginDesc: PluginDesc):
        # The list holds the checked rows, and 'visibility_lists_type' - exported from its own
        # property, which the dropdown edits - says what V-Ray does with them.
        listedNames, listType = self.getVisibilityList()

        # The list holds full paths, so the renderer has to resolve the object names the same way.
        pluginDesc.setAttribute('use_full_names', True)
        pluginDesc.setAttribute('visibility_list_names', listedNames)

        # Only when the list is empty because nothing was read does the type need overriding - see
        # getVisibilityList(). Otherwise the generic parameter export writes the property as it is.
        if not any(i.isObject for i in self.items):
            pluginDesc.setAttribute('visibility_lists_type', listType)

        return True


    # Callback for VRAY_OT_simple_button
    def onRefreshPreview(self, context: bpy.types.Context, selectorID: str):
        """ Regenerate the viewport preview so that it stops showing the hidden objects.

            Not done as the checkboxes are ticked: every regeneration runs 'vraytools' over the
            whole file, which is far too much to spend on a click.
        """
        from vray_blender.vray_tools.vray_proxy import loadVRayProxyPreviewMesh

        host = self._getHost()
        filePath = self._getProxyFilePath()

        if (host is None) or (not filePath):
            return

        if err := loadVRayProxyPreviewMesh(host, filePath, context.scene.frame_current):
            debug.reportError(err)


    def perObjectMaterials(self):
        """ True when the file has shader sets and they are its objects, so an override can name
            an object.

            V-Ray shades by shader set, and AlembicMeshFile::initMeshSetsData() gives an object
            with no face sets a shader set named exactly after it. Every shader set of the file
            being one of its objects is therefore precisely the case where object-path patterns
            reach the renderer - which is most Alembic files, and no '.vrmesh', whose shader sets
            are the names baked in when it was written.

            A file with no shader sets at all is not this case: nothing in the hierarchy can carry
            a material, and the object's material slots are what the rollout offers instead.
        """
        return self.perObject


    def getMaterialOverrides(self):
        """ The (fullPath, depth, isShaderSet, material) tuples of the rows carrying an override.

            Used in the per-object case, where the paths are matched as patterns against the
            shader set names. The depth is used to order the items in the hierarchy.
            Whether the row is a shader set says whether its bare path names anything at all.
        """
        return [(i.fullPath, i.depth, i.isShaderSet, i.material) for i in self.items
                if (i.material is not None) and i.canOverrideMaterial()]


    def getMaterialOverridesByShaderId(self):
        """ Return {shader set ID: Material} for the shader sets that end up with an override.

            Used in the face-set case, where the shader set names cannot be addressed as patterns:
            the tool reads them as V-Ray builds them when  use_face_sets=1, while when 2 - a '/' is
            inserted and a literal pattern would miss. 

            A row's own override wins; failing that it inherits the closest ancestor's, which is
            how a material set on a group reaches its whole branch.
        """
        # This runs on every material export of every such proxy, and a proxy nobody has set an
        # override on is the usual case. Reading one property per row to establish that is worth
        # it - the walk below reads four, and it stops at the first row that carries one.
        if not any(item.material is not None for item in self.items):
            return {}

        overrides = {}

        for item, material in zip(self.items, self._resolveMaterials()):
            if item.isShaderSet and (item.shaderId >= 0) and (material is not None):
                overrides[item.shaderId] = material

        return overrides


    def resetToDefaults(self):
        """ Show all objects again and drop the material overrides, keeping the list itself.

            The material slots go back to what they were imported with, for the same reason Reset
            puts them back, and in every mode as it does: they hold the proxy's shading whichever
            of the two lists the rollout draws over the rows.
        """
        global _cascading

        self.restoreSlotMaterials(self._getSlotOwner(bpy.context))

        _cascading = True
        try:
            for item in self.items:
                item.enabled  = True
                item.expanded = True
                item.material = None
        finally:
            _cascading = False

        self.activeItem      = -1
        self.activeShaderSet = -1


    def copy(self, dest: TemplateProxyObjectHierarchy):
        global _cascading

        dest.items.clear()

        _cascading = True
        try:
            for item in self.items:
                newItem = dest.items.add()
                newItem.name        = item.name
                newItem.fullPath    = item.fullPath
                newItem.depth       = item.depth
                newItem.isGroup     = item.isGroup
                newItem.isObject    = item.isObject
                newItem.isShaderSet    = item.isShaderSet
                newItem.shaderId       = item.shaderId
                newItem.branchTypes    = item.branchTypes
                newItem.soleChildTypes = item.soleChildTypes
                newItem.expanded       = item.expanded
                newItem.enabled        = item.enabled
                newItem.material       = item.material
        finally:
            _cascading = False

        dest.importedSlots.clear()
        for imported in self.importedSlots:
            copied = dest.importedSlots.add()
            copied.material = imported.material
            copied.name     = imported.name

        dest.activeItem      = self.activeItem
        dest.activeShaderSet = self.activeShaderSet
        # Carry the stamp over, or the copy would look unread and re-run the tool on the same file.
        dest.sourceFile = self.sourceFile
        dest.perObject  = self.perObject
        dest.schema     = self.schema


    def _getSlotOwner(self, context: bpy.types.Context):
        """ The object whose material slots stand in for the shader sets this file does not have.

            The list lives on the mesh, and the slots on the object using it. That is the one the
            Properties editor is showing, except when it is pinned to the mesh itself - then any
            object using it will do, since the slots are the mesh's unless one of them is linked
            to the object.
        """
        mesh = self.id_data

        # The context of a render job, which Export V-Ray Proxy runs in, has no 'object'.
        if (obj := getattr(context, 'object', None)) and (obj.data is mesh):
            return obj

        return next((o for o in bpy.data.objects if o.data is mesh), None)


    def _getModeProperty(self):
        """ Return the name of the property holding the include/exclude mode. """
        return self.getTemplateAttr('mode_bound_property', 'visibility_lists_type')


    def _getHost(self):
        """ The property group the template is a property of - the GeomMeshFile holding the proxy's
            file path and its include/exclude mode. None when the template is not attached to one.
        """
        # The template is registered as a property of the plugin's property group, so the owner
        # is one level up its RNA path.
        path = self.path_from_id()

        if '.' not in path:
            return None

        return self.id_data.path_resolve(path.rsplit('.', 1)[0])


    def _getProxyFilePath(self):
        """ Return the value of the property holding the path to the proxy file. """
        host = self._getHost()

        if host is None:
            return ""

        return getattr(host, self.getTemplateAttr('file_property', 'file'), "")


    def _getAbsProxyFilePath(self):
        """ The proxy file as an absolute path, or "" when none is set.

            Not bpy.path.abspath("") - that resolves to the directory of the .blend, which would
            make an empty file property look like a path that has never been read.
        """
        filePath = self._getProxyFilePath()
        return bpy.path.abspath(filePath) if filePath else ""


def getRegClasses():
    return (
        VRayProxyImportedSlot,
        VRayProxyHierarchyItem,
        VRAY_UL_ProxyVisibility,
        VRAY_UL_ProxyShaderSets,
        VRAY_UL_ProxyMaterialSlots,
        VRAY_OT_proxy_hierarchy_toggle_expand,
        VRAY_OT_proxy_hierarchy_drop_material,
        TemplateProxyObjectHierarchy,
    )


def register():
    from vray_blender.lib.class_utils import registerClass

    for regClass in getRegClasses():
        registerClass(regClass)


def unregister():
    for regClass in reversed(getRegClasses()):
        bpy.utils.unregister_class(regClass)
