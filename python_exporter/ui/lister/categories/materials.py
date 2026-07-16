# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

# Materials category: lists every material grouped by its main shader (V-Ray Mtl,
# Light Mtl, Toon, Scanned, VRmat, ...) and edits that shader's propgroup. Non-V-Ray
# materials go under "Other Materials".

import bpy

from vray_blender.nodes.utils import getOutputNode
from vray_blender.ui.lister.core import ListerCategory, ColumnSpec, drawNameCell, drawTextureRef, drawMaterialUsersCell


def _useRoughness() -> bool:
    """ The global 'Use roughness' preference - the per-material default for
        option_use_roughness. The BRDFVRayMtl reflection-sharpness column follows it, so its
        header reads 'Roughness' when on and 'Glossiness' when off. """
    try:
        from vray_blender.lib.blender_utils import getVRayPreferences
        return bool(getVRayPreferences(bpy.context).mtl_use_roughness)
    except Exception:
        return False


_GROUP_LABELS = {
    'BRDFVRayMtl':          "V-Ray Material",
    'BRDFLight':            "Light Material",
    'BRDFToonMtl':          "Toon Material",
    'BRDFCarPaint2':        "Car Paint",
    'BRDFStochasticFlakes': "Stochastic Flakes",
    'BRDFFlakes2':          "Metal Flakes",
    'BRDFHair4':            "Hair",
    'BRDFSSS2Complex':      "Subsurface (SSS)",
    'BRDFSkinComplex':      "Skin",
    'BRDFAlSurface':        "AlSurface",
    'BRDFLayered':          "Layered Material",
    'BRDFScanned':          "Scanned Material",
    'MtlVRmat':             "V-Ray Mtl File (.vrmat)",
    'STANDARD':             "Other Materials",
}

_GROUP_ORDER = [
    'BRDFVRayMtl', 'BRDFLight', 'BRDFToonMtl', 'BRDFCarPaint2', 'BRDFStochasticFlakes',
    'BRDFFlakes2', 'BRDFHair4', 'BRDFSSS2Complex', 'BRDFSkinComplex', 'BRDFAlSurface',
    'BRDFLayered', 'BRDFScanned', 'MtlVRmat', 'STANDARD',
]

# Fallback columns for material types without a hand-written set: first attr matching
# a diffuse-like colour, then a reflection-like value.
_DIFFUSE_CANDIDATES = ('diffuse', 'color', 'diffuse_color', 'base_color', 'base_diffuse', 'coat_color', 'flake_color')
_REFLECT_CANDIDATES = ('reflect', 'reflection', 'reflect_color', 'metalness')

# Per-pass memo of each material's main shader node, cleared at the start of every
# pass (MaterialsCategory.enumerate).
_shaderNodeCache: dict = {}


def _mainShaderNode(mat: bpy.types.Material):
    """ The node feeding the material output, i.e. the material's main shader.

        Resolving it scans the whole node tree, and it is queried several times for the same
        material in one redraw (groupKey, getPropGroup and once per texturable cell), so the
        result is memoized for the duration of a draw pass (see _shaderNodeCache).
    """
    key = mat.name_full
    if key in _shaderNodeCache:
        return _shaderNodeCache[key]

    node = None
    nodeTree = getattr(mat, 'node_tree', None)
    if nodeTree is not None:
        outputNode = getOutputNode(nodeTree, 'MATERIAL')
        if outputNode is not None:
            for sock in outputNode.inputs:
                if sock.is_linked and sock.links:
                    node = sock.links[0].from_node
                    break
        if node is None:
            for candidate in nodeTree.nodes:
                if getattr(candidate, 'vray_type', 'NONE') in ('BRDF', 'MATERIAL'):
                    node = candidate
                    break

    _shaderNodeCache[key] = node
    return node


def _linkedTextureNode(mtl, attr):
    """ If the main shader's input socket for 'attr' is driven by another node (a
        texture), return that node; otherwise None. V-Ray sockets carry the propgroup
        attribute they map to in socket.vray_attr. """
    node = _mainShaderNode(mtl)
    if node is None:
        return None
    for sock in node.inputs:
        if getattr(sock, 'vray_attr', None) == attr and sock.is_linked and sock.links:
            return sock.links[0].from_node
    return None


def _makeTexturableCell(attr):
    """ Draw the attribute's value, unless a texture is plugged into its socket - in
        that case the value is overridden by the texture, so show the texture's name
        (read-only) and a texture icon instead of an editable value that does nothing. """
    def draw(row, mtl, propGroup):
        texNode = _linkedTextureNode(mtl, attr)
        if texNode is not None:
            drawTextureRef(row, texNode)
        elif propGroup is not None and attr in propGroup.bl_rna.properties:
            row.prop(propGroup, attr, text="")
    return draw


def _drawBlenderDiffuse(row, mtl, propGroup):
    """ Non-V-Ray materials have no V-Ray propgroup; show Blender's own viewport diffuse
        colour so the row is not empty. """
    row.prop(mtl, 'diffuse_color', text="")


_COL_USERS = ColumnSpec('select', "", draw=drawMaterialUsersCell, fixedWidth=1.5, center=True)
_COL_NAME = ColumnSpec('name', "Name", draw=drawNameCell, width=2.5)


class MaterialsCategory(ListerCategory):
    id = 'MATERIALS'
    label = "Materials"
    icon = 'MATERIAL'
    enumIndex = 10
    selectable = False  # entities are materials, not scene objects
    # Shader types have very different columns; a Unified Grid would leave mostly-blank
    # cells, so each type's table uses its own column widths.
    forceIndependentAlignment = True
    hasFakeUserColumn = True
    hasCleanup = True

    def enumerate(self, context):
        # New draw pass: drop the memo so material edits/deletions are re-resolved
        _shaderNodeCache.clear()

        state = getattr(context.scene, 'vray_lister', None)
        # "Selected Only" can't use the generic object filter here: list the materials
        # used by the selected objects, otherwise every material in the file.
        if state is not None and state.show_selected_only:
            materials = []
            for obj in context.selected_objects:
                for slot in obj.material_slots:
                    if slot.material is not None and slot.material not in materials:
                        materials.append(slot.material)
        else:
            materials = [m for m in bpy.data.materials if m.users > 0]

        # "V-Ray Materials Only": drop the STANDARD group. groupKey() reuses the per-pass
        # shader-node memo, so this is the same lookup the grouping does next.
        if state is not None and getattr(state, 'show_vray_materials_only', False):
            materials = [m for m in materials if self.groupKey(m) != 'STANDARD']
        return materials

    def groupKey(self, mtl):
        node = _mainShaderNode(mtl)
        if node is None:
            return 'STANDARD'
        return getattr(node, 'vray_plugin', 'STANDARD') or 'STANDARD'

    def groupLabel(self, key):
        return _GROUP_LABELS.get(key, key)

    def groupOrder(self):
        return _GROUP_ORDER

    def getPropGroup(self, mtl):
        node = _mainShaderNode(mtl)
        if node is None:
            return None
        pluginType = getattr(node, 'vray_plugin', '')
        return getattr(node, pluginType, None) if pluginType else None

    def columns(self, key):
        base = [_COL_USERS, _COL_NAME]

        if key == 'BRDFVRayMtl':
            # Header follows the global 'Use roughness' preference. An OpenPBR material
            # forces option_use_roughness=True, so its roughness value can still show under
            # a 'Glossiness' header - accepted as a rare edge case.
            glossLabel = "Roughness" if _useRoughness() else "Glossiness"
            return base + [
                ColumnSpec('diffuse', "Diffuse", attr='diffuse', draw=_makeTexturableCell('diffuse'), width=1.4),
                ColumnSpec('reflect', "Reflection", attr='reflect', draw=_makeTexturableCell('reflect'), width=1.4),
                ColumnSpec('reflect_glossiness', glossLabel, attr='reflect_glossiness', draw=_makeTexturableCell('reflect_glossiness'), width=1.4),
                ColumnSpec('metalness', "Metalness", attr='metalness', draw=_makeTexturableCell('metalness'), width=1.4),
                ColumnSpec('refract', "Refraction", attr='refract', draw=_makeTexturableCell('refract'), width=1.4, defaultHidden=True),
                ColumnSpec('self_illumination', "Self-Illum", attr='self_illumination', draw=_makeTexturableCell('self_illumination'), width=1.4, defaultHidden=True),
            ]

        if key == 'BRDFLight':
            return base + [
                ColumnSpec('color', "Color", attr='color', draw=_makeTexturableCell('color'), width=1.6),
                ColumnSpec('colorMultiplier', "Intensity", attr='colorMultiplier', draw=_makeTexturableCell('colorMultiplier'), width=1.6),
                ColumnSpec('emitOnBackSide', "Emit Back Side", attr='emitOnBackSide', width=1.4, defaultHidden=True),
                ColumnSpec('compensateExposure', "Compensate Exp.", attr='compensateExposure', width=1.4, defaultHidden=True),
            ]

        # Non-V-Ray materials have no V-Ray propgroup; show Blender's viewport colour
        if key == 'STANDARD':
            return base + [ColumnSpec('color', "Color", draw=_drawBlenderDiffuse, width=1.6)]

        # Any other V-Ray material type: show the first diffuse-like and reflection-like
        # attribute the shader exposes. File-only shaders (Scanned / VRmat) show neither.
        from vray_blender.plugins import PLUGIN_MODULES
        pluginModule = PLUGIN_MODULES.get(key)
        attrs = {p['attr'] for p in getattr(pluginModule, 'Parameters', ())} if pluginModule is not None else set()

        cols = list(base)
        diffuseAttr = next((a for a in _DIFFUSE_CANDIDATES if a in attrs), None)
        if diffuseAttr is not None:
            cols.append(ColumnSpec('diffuse', "Color", attr=diffuseAttr, draw=_makeTexturableCell(diffuseAttr), width=1.6))
        reflectAttr = next((a for a in _REFLECT_CANDIDATES if a in attrs), None)
        if reflectAttr is not None:
            cols.append(ColumnSpec('reflect', "Reflection", attr=reflectAttr, draw=_makeTexturableCell(reflectAttr), width=1.6))
        return cols

    def pickerSections(self, context, state):
        # Split the column picker by shader type, one heading per type
        return self.pickerSectionsByGroup()


category = MaterialsCategory()
