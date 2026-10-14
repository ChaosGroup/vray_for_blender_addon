# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

""" User-defined material categories for the V-Ray Material Manager window.

    The list pane draws a row of tabs above the materials - "All Materials", "Cosmos", one tab
    per category the user has defined, and "Uncategorized" - and the tab in front filters the
    list. A material can be filed under any number of categories at once.

    "Cosmos" is predefined and never filed by hand: it holds every material imported from
    Chaos Cosmos, recognized by the package id the importer stamps on it.

    Two pieces of state, deliberately kept apart:

      * The tags, on the material itself, as the ID custom property 'vray_categories'
        (newline-separated, because a category name may contain spaces). A custom property
        rather than a field on mtl.vray, so the tags survive a V-Ray update regenerating the
        plugin property groups, and so non-V-Ray materials - which the manager lists too -
        can be filed as well.

      * The tab set, on the scene (VRayListerState.material_categories). Kept apart from the
        tags, or a category nothing is filed under yet would have no tab.

    The category tabs are always shown in alphabetical order.

    Materials appended from another file bring their tags along, so the tabs shown are the
    defined set plus any tag found on a material that is not defined here yet (tabKeys).
    Folding those into the scene's list is a write, which a draw callback may not do, so it
    happens in the operators (adoptTags) and never in the draw path.

    Materials linked from a library are read-only here: a tag written on one would never be
    saved, so setCategoriesOf() leaves them alone.

    No UI in here - this is the data side only.
"""

import bpy


# ID custom property holding a material's categories, newline-separated
CATEGORY_PROP = "vray_categories"

# The built-in tabs. Prefixed so they cannot collide with a user's category name.
ALL = '__ALL__'
COSMOS = '__COSMOS__'
UNCATEGORIZED = '__UNCAT__'

_BUILTIN_LABELS = {
    ALL:           "All Materials",
    COSMOS:        "Cosmos",
    UNCATEGORIZED: "Uncategorized",
}


def isBuiltin(key: str) -> bool:
    return key in _BUILTIN_LABELS


def label(key: str) -> str:
    return _BUILTIN_LABELS.get(key, key)


def _splitNames(value) -> list[str]:
    return [n.strip() for n in str(value or "").split("\n") if n.strip()]


############################################################
# Tags (per material)
############################################################

def categoriesOf(mat) -> list[str]:
    """ The categories this material is filed under, in the order they were added. """
    return _splitNames(mat.get(CATEGORY_PROP, ""))


def setCategoriesOf(mat, names):
    """ Replace a material's categories. An empty list removes the property rather than
        leaving it blank, so an unfiled material carries nothing extra. """
    if not mat.is_editable:
        return
    unique = list(dict.fromkeys(n.strip() for n in names if n.strip()))
    if unique:
        mat[CATEGORY_PROP] = "\n".join(unique)
    elif CATEGORY_PROP in mat:
        del mat[CATEGORY_PROP]


def hasCategory(mat, name: str) -> bool:
    return name in categoriesOf(mat)


def isCosmos(mat) -> bool:
    return bool(mat.vray.cosmos_package_id)


def fileMaterials(materials, name: str, add: bool):
    """ Add or remove one category on every given material. """
    name = name.strip()
    if not name:
        return
    for mat in materials:
        names = categoriesOf(mat)
        if add and name not in names:
            setCategoriesOf(mat, names + [name])
        elif not add and name in names:
            setCategoriesOf(mat, [n for n in names if n != name])


def toggleMaterials(materials, name: str) -> bool:
    """ One toggle for a whole selection: if any of them is missing the category they all get
        it; only when every one already has it does the click take it away. Returns whether
        the materials ended up filed. """
    materials = list(materials)
    add = any(not hasCategory(m, name) for m in materials)
    fileMaterials(materials, name, add)
    return add


def unfileMaterials(materials):
    """ Take the given materials out of every category. """
    for mat in materials:
        setCategoriesOf(mat, [])


############################################################
# The tab set (per scene)
############################################################

def definedCategories(view) -> list[str]:
    return _splitNames(view.material_categories) if view is not None else []


def _setDefinedCategories(view, names):
    if view is not None:
        view.material_categories = "\n".join(names)


def _usedCategories(materials) -> list[str]:
    """ Every category found on the given materials, in first-seen order. """
    return list(dict.fromkeys(n for m in materials for n in categoriesOf(m)))


def tabKeys(view, materials) -> list[str]:
    """ The tabs to draw: All Materials, Cosmos, then A-Z the defined categories and any a
        material carries that the scene does not know yet (appended materials), and
        Uncategorized - only while some listed material is unfiled. Read-only, draw-safe. """
    defined = definedCategories(view)
    foreign = [n for n in _usedCategories(materials) if n not in defined]

    keys = [ALL, COSMOS] + sorted(defined + foreign, key=str.lower)
    if any(matches(m, UNCATEGORIZED) for m in materials):
        keys.append(UNCATEGORIZED)
    return keys


def userCategories(view, materials) -> list[str]:
    """ The tabs a material can be filed under - tabKeys() minus the built-in views. """
    return [k for k in tabKeys(view, materials) if not isBuiltin(k)]


def adoptTags(view):
    """ Fold the categories of appended materials into the scene's tab set, so they can be
        renamed and deleted like the ones defined here. Writes - operators only. """
    defined = definedCategories(view)
    foreign = [n for n in _usedCategories(bpy.data.materials) if n not in defined]
    if foreign:
        _setDefinedCategories(view, defined + foreign)


def uniqueName(view, name: str, ignore: str = "") -> str:
    """ A category name not already taken (case-insensitively), '.001'-suffixed if it is. """
    name = (name or "").strip().replace("\n", " ") or "Category"
    # The built-in labels too, or a user's "Cosmos" would draw as a second Cosmos tab
    taken = {n.lower() for n in definedCategories(view) if n != ignore}
    taken |= {n.lower() for n in _BUILTIN_LABELS.values()}
    if name.lower() not in taken:
        return name
    for i in range(1, 1000):
        candidate = f"{name}.{i:03d}"
        if candidate.lower() not in taken:
            return candidate
    return name


def defineCategory(view, name: str) -> str:
    """ Add a category to the tab set, filing nothing yet. Returns the name it got. """
    name = uniqueName(view, name)
    _setDefinedCategories(view, definedCategories(view) + [name])
    return name


def deleteCategory(view, name: str):
    """ Drop a category: its tab goes and every material in the file is taken out of it. The
        materials themselves are untouched - this is filing, not cleanup. """
    _setDefinedCategories(view, [n for n in definedCategories(view) if n != name])
    fileMaterials(bpy.data.materials, name, add=False)
    if view.material_active_category == name:
        view.material_active_category = ALL


def renameCategory(view, old: str, new: str) -> str:
    """ Rename a category, on its tab and on every material filed under it. Returns the name
        it got. """
    new = uniqueName(view, new, ignore=old)
    if new == old:
        return old

    _setDefinedCategories(view, [new if n == old else n for n in definedCategories(view)])
    for mat in bpy.data.materials:
        names = categoriesOf(mat)
        if old in names:
            setCategoriesOf(mat, [new if n == old else n for n in names])

    if view.material_active_category == old:
        view.material_active_category = new
    return new


############################################################
# Filtering
############################################################

def activeKey(view, materials) -> str:
    """ The tab in front, falling back to All Materials when the stored one is gone (deleted,
        or a stale name in a loaded file). Read-only. """
    key = view.material_active_category if view is not None else ALL
    return key if key in tabKeys(view, materials) else ALL


def matches(mat, key: str) -> bool:
    if key == ALL:
        return True
    if key == COSMOS:
        return isCosmos(mat)
    if key == UNCATEGORIZED:
        # Cosmos materials are already sorted into their own tab
        return not categoriesOf(mat) and not isCosmos(mat)
    return hasCategory(mat, key)


def filterMaterials(materials, key: str) -> list:
    if key == ALL:
        return list(materials)
    return [m for m in materials if matches(m, key)]


def countFor(materials, key: str) -> int:
    if key == ALL:
        return len(materials)
    return sum(1 for m in materials if matches(m, key))
