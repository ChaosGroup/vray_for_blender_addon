# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

""" Grouping of the user-creatable V-Ray texture nodes into themed submenus.

    One taxonomy drives both places a texture can be created - the Node Editor's Add > Texture menu
    and the texture picker on a property page - so the user sees the same grouping in both.

    The set of textures itself still comes from buildItemsList(), so a plugin becoming available or
    hidden needs no change here; this table only decides which submenu an already-exposed texture
    lands in. Anything the table does not mention falls into the explicit 'OTHER' group rather than
    being folded into 'UTILITY', so a newly exposed plugin is visible instead of silently
    disappearing into a junk drawer.

    Run scripts/check_texture_groups.py to list dead entries and ungrouped textures.
"""

# Icon shown on each group's row. Only the group rows carry an icon - the textures inside a group
# are drawn without one, matching how the Node Editor's Add menu lists nodes.
TEXTURE_GROUP_ICONS = {
    'PROCEDURAL': 'FORCE_TURBULENCE',
    'PATTERN':    'MESH_GRID',
    'IMAGE':      'IMAGE_DATA',
    'COLOR':      'COLOR',
    'MATH':       'DRIVER',
    'BLEND':      'RENDERLAYERS',
    'UTILITY':    'TOOL_SETTINGS',
    'OTHER':      'DOT',
}

# (group id, menu label, member node bl_idnames). Order defines the order of the submenus.
TEXTURE_GROUPS = (
    ('PROCEDURAL', "Noise & Procedural", (
        'VRayNodeTexNoise',
        'VRayNodeTexNoiseMax',
        'VRayNodeTexCyclesNoise',
        'VRayNodeTexCellular',
        'VRayNodeTexGranite',
        'VRayNodeTexMarble',
        'VRayNodeTexMarbleMax',
        'VRayNodeTexRock',
        'VRayNodeTexSmoke',
        'VRayNodeTexSnow',
        'VRayNodeTexSpeckle',
        'VRayNodeTexSplat',
        'VRayNodeTexStucco',
        'VRayNodeTexSwirl',
        'VRayNodeTexWater',
        'VRayNodeTexWood',
        'VRayNodeTexBulge',
        'VRayNodeTexBerconNoise',
        'VRayNodeTexBerconWood',
    )),
    ('PATTERN', "Patterns", (
        'VRayNodeTexChecker',
        'VRayNodeTexGrid',
        'VRayNodeTexTiles',
        'VRayNodeTexCloth',
        'VRayNodeTexLeather',
        'VRayNodeTexBerconTile',
    )),
    ('IMAGE', "Image & Environment", (
        'VRayNodeMetaImageTexture',
        'VRayNodeTexSky',
        'VRayNodeTexSoftbox',
        'VRayNodeTexTriPlanar',
        'VRayNodeTexParallax',
        'VRayNodeTexPtex',
        'VRayNodeTexOpenVDB',
    )),
    ('COLOR', "Color", (
        'VRayNodeTexGradRamp',
        'VRayNodeTexGradient',
        'VRayNodeTexTemperature',
        'VRayNodeTexTemperatureToColor',
        'VRayNodeColorCorrection',
        'VRayNodeColorCorrect',
        'VRayNodeTexInvert',
        'VRayNodeTexRGBToHSV',
        'VRayNodeTexHSVToRGB',
        'VRayNodeTexLuminance',
    )),
    ('MATH', "Math", (
        'VRayNodeTexAColorOp',
        'VRayNodeTexFloatOp',
        'VRayNodeTexRemap',
        'VRayNodeTexRemapFloat',
        'VRayNodeTexClamp',
        'VRayNodeTexSetRange',
        'VRayNodeTexInvertFloat',
        'VRayNodeTexFloatComposite',
        'VRayNodeTexFloat',
        'VRayNodeFloat3ToAColor',
        'VRayNodeTexColorToFloat',
        'VRayNodeTexFloatToColor',
        'VRayNodeTexCondition',
        'VRayNodeTexVectorOp',
        'VRayNodeTexVectorToColor',
    )),
    ('BLEND', "Blend & Layer", (
        'VRayNodeTexLayered',
        'VRayNodeTexLayeredMax',
        'VRayNodeTexMulti',
        'VRayNodeTexMix',
        'VRayNodeTexFalloff',
        'VRayNodeTexComposite',
        'VRayNodeTexCompMax',
        'VRayNodeTexMaskMax',
        'VRayNodeTexColorSwitch',
    )),
    ('UTILITY', "Utility", (
        'VRayNodeTexOCIO',
        'VRayNodeTexDirt',
        'VRayNodeTexCurvature',
        'VRayNodeTexEdges',
        'VRayNodeTexNormalBump',
        'VRayNodeTexDistance',
        'VRayNodeTexSampler',
        'VRayNodeTexUserColor',
        'VRayNodeTexUserScalar',
        'VRayNodeTexOutput',
        'VRayNodeTexOSL',
        'VRayNodeTexSurfaceLuminance',
        'VRayNodeTexSurfIncidence',
        'VRayNodeTexHairSampler',
    )),
)

# Meta nodes that stand in for a plugin rather than being one. buildItemsList() does not return
# them because they have no plugin of their own, so they are injected explicitly - the image
# texture is by far the most common texture to reach for and must not be missing from the picker.
META_TEXTURE_ITEMS = (
    ('VRayNodeMetaImageTexture', "V-Ray Bitmap"),
)

# Group collecting any exposed texture this table does not mention. Deliberately its own bucket:
# folding the unknowns into UTILITY is what let an earlier table quietly lose ~15 textures.
FALLBACK_GROUP = 'OTHER'
FALLBACK_GROUP_LABEL = "Other"

# bl_idname -> group id
_GROUP_OF_NODE = {
    idname: groupId
    for groupId, _label, members in TEXTURE_GROUPS
    for idname in members
}


def getTextureGroup(blIdname: str) -> str:
    """ The group id a texture node belongs to, or FALLBACK_GROUP. """
    return _GROUP_OF_NODE.get(blIdname, FALLBACK_GROUP)


_itemsByGroupCache = []


def clearItemsCache():
    _itemsByGroupCache.clear()


def getTextureItemsByGroup():
    """ [(groupId, groupLabel, [(bl_idname, label), ...]), ...] for the exposed textures.

        Both the plain textures and the ones marked with the 'UTILITY' menu subtype are included -
        together they are what the Node Editor offers. Empty groups are omitted so a group whose
        plugins are all unavailable does not show up blank.
    """
    # Menus redraw as the pointer moves, and every group submenu asks for this list, so rebuilding
    # it would re-scan every registered texture node type once per submenu per redraw. The exposed
    # plugin set is fixed after _loadPlugins(); clearItemsCache() covers an addon reload.
    if _itemsByGroupCache:
        return _itemsByGroupCache

    # Imported here: nodes.py imports this module, so a module-level import would be circular.
    from vray_blender.nodes.nodes import buildItemsList

    items = buildItemsList('TEXTURE') + buildItemsList('TEXTURE', 'UTILITY') + list(META_TEXTURE_ITEMS)

    byGroup = {}
    for idname, label in items:
        byGroup.setdefault(getTextureGroup(idname), []).append((idname, label))

    result = []
    for groupId, groupLabel, _members in TEXTURE_GROUPS:
        if group := byGroup.get(groupId):
            result.append((groupId, groupLabel, sorted(group, key=lambda x: x[1].lower())))

    if fallback := byGroup.get(FALLBACK_GROUP):
        result.append((FALLBACK_GROUP, FALLBACK_GROUP_LABEL, sorted(fallback, key=lambda x: x[1].lower())))

    _itemsByGroupCache.extend(result)
    return result


def getTextureItemsOfGroup(groupId: str):
    """ Just one group's items, without the caller scanning the whole grouped list. """
    return next((items for gid, _label, items in getTextureItemsByGroup() if gid == groupId), ())
