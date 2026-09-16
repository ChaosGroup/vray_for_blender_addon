# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

""" Icons for V-Ray nodes shown in navigation UI (breadcrumb, shading tree).

    Materials reuse the custom MTL_* icons the Add Material menus already use, and textures reuse
    their texture_groups group icon - so the icon on a node in the tree is the same one the user
    saw on the group they picked it from.

    NOTE: ui/properties_material.VRAY_MT_material_add_popup and nodes.NODE_MT_vray_add_material
    each carry their own copy of the material icon mapping, one keyed by bl_idname and the other
    by bl_label. Unifying all three is worth doing, but it is a separate cleanup.
"""

import bpy

from vray_blender.nodes.texture_groups import TEXTURE_GROUP_ICONS, getTextureGroup
from vray_blender.ui import icons


# bl_idname -> custom icon name in ui/icons.py
_MATERIAL_ICONS = {
    'VRayNodeBRDFVRayMtl':          'MTL_VRAY',
    'VRayNodeBRDFLayered':          'MTL_BLEND',
    'VRayNodeMtlDisplacement':      'MTL_DISPLACEMENT',
    'VRayNodeBRDFLight':            'MTL_LIGHT',
    'VRayNodeBRDFAlSurface':        'MTL_AL_SURFACE',
    'VRayNodeBRDFSSS2Complex':      'MTL_FAST_SSS2',
    'VRayNodeBRDFBump':             'MTL_BUMP',
    'VRayNodeBRDFHair4':            'MTL_HAIR_NEXT',
    'VRayNodeBRDFCarPaint2':        'MTL_CAR_PAINT2',
    'VRayNodeBRDFFlakes2':          'MTL_FLAKES',
    'VRayNodeBRDFScanned':          'MTL_SCANNED',
    'VRayNodeBRDFStochasticFlakes': 'MTL_STOCHASTIC_FLAKES',
    'VRayNodeBRDFToonMtl':          'MTL_TOON',
    'VRayNodeMtlMulti':             'MTL_SWITCH',
    'VRayNodeMtl2Sided':            'MTL_2SIDED',
    'VRayNodeMtlOverride':          'MTL_OVERRIDE',
    'VRayNodeMtlVRmat':             'MTL_VRMAT',
}

# Node types whose stock icon says more than their group's would.
_EXPLICIT_ICONS = {
    'VRayNodeMetaImageTexture': 'IMAGE_DATA',
    'VRayNodeEnvironment':      'WORLD',
    'VRayNodeOutputMaterial':   'NODE_MATERIAL',
    'VRayNodeWorldOutput':      'WORLD',
}


def getNodeIcon(node) -> tuple[str, int]:
    """ (icon, iconValue) for a node, ready to hand straight to layout.operator().

        Returned as a pair because a stock icon goes in `icon` while a custom one goes in
        `icon_value`, and only one of them may be set.
    """
    if node is None:
        return ('NONE', 0)

    blIdname = node.bl_idname

    if stockIcon := _EXPLICIT_ICONS.get(blIdname):
        return (stockIcon, 0)

    if iconName := _MATERIAL_ICONS.get(blIdname):
        return ('NONE', icons.getIcon(iconName))

    if blIdname.startswith('VRayNodeUVWGen'):
        return ('UV', 0)

    if getattr(node, 'vray_type', 'NONE') == 'TEXTURE':
        return (TEXTURE_GROUP_ICONS.get(getTextureGroup(blIdname), 'TEXTURE'), 0)

    return ('NODE', 0)
