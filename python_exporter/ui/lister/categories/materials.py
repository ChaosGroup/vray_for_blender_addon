# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

# Materials category: enumerates every material and identifies its main shader (V-Ray Mtl,
# Light Mtl, Toon, Scanned, VRmat, ...). Materials with no V-Ray shader are 'STANDARD'.
#
# This is the Material Lister's model, not a Scene Lister section (hasTable = False), so it
# defines no ColumnSpecs and none of the table hooks. The window draws it through
# core.drawMaterialListerMain / ...Params.

import bpy

from vray_blender.nodes.navigation import getTreeRootNode
from vray_blender.ui.lister.core import ListerCategory


# Per-pass memo of each material's main shader node, cleared at the start of every
# pass (MaterialsCategory.enumerate).
_shaderNodeCache: dict = {}


def _mainShaderNode(mat: bpy.types.Material):
    """ The node feeding the material output, i.e. the material's main shader.

        Resolving it scans the whole node tree, and it is queried once per listed material in a
        redraw (groupKey, for the 'V-Ray Materials Only' filter), so the result is memoized for
        the duration of a draw pass (see _shaderNodeCache).
    """
    key = mat.name_full
    if key in _shaderNodeCache:
        return _shaderNodeCache[key]

    # fallbackScan keeps materials whose output link is missing - imported or half-built ones -
    # grouped by their real shader instead of dropping into "Other Materials". The property pages
    # deliberately leave it off, which is why this is not just getPanelNode().
    node = getTreeRootNode(getattr(mat, 'node_tree', None), 'MATERIAL', fallbackScan=True)

    _shaderNodeCache[key] = node
    return node


class MaterialsCategory(ListerCategory):
    id = 'MATERIALS'
    label = "Materials"
    icon = 'MATERIAL'
    enumIndex = 10
    selectable = False  # entities are materials, not scene objects
    hasTable = False    # drawn by the Material Lister's panes, not as a column table
    hasCleanup = True

    def enumerate(self, context):
        # New draw pass: drop the memo so material edits/deletions are re-resolved
        _shaderNodeCache.clear()

        state = getattr(context.scene, 'vray_lister', None)
        # "Selected Only" can't use the generic object filter here: list the materials
        # used by the selected objects, otherwise every material in the file.
        if state is not None and state.show_selected_only:
            # dict.fromkeys de-duplicates while keeping the slot order stable
            materials = list(dict.fromkeys(slot.material for obj in context.selected_objects
                                           for slot in obj.material_slots if slot.material is not None))
        else:
            # Unused materials are listed too - a just-created one has no users yet.
            materials = list(bpy.data.materials)

        # "V-Ray Materials Only": drop the ones with no V-Ray shader. groupKey() reuses the
        # per-pass shader-node memo, so this costs one tree scan per material.
        if state is not None and state.show_vray_materials_only:
            materials = [m for m in materials if self.groupKey(m) != 'STANDARD']
        return materials

    def groupKey(self, mtl):
        node = _mainShaderNode(mtl)
        if node is None:
            return 'STANDARD'
        return getattr(node, 'vray_plugin', 'STANDARD') or 'STANDARD'

    def shaderLabel(self, mtl) -> str:
        """ Display name of the material's main shader, for the parameters pane header. Taken
            from the node's own bl_label rather than a table here, so every shader is named -
            and named the same as in the node editor - without a list to keep in sync. """
        node = _mainShaderNode(mtl)
        return node.bl_label if node is not None else "No Shader"

    # Solo: rows are materials (not scene objects), but soloing hides the scene objects
    # that don't use the soloed material.
    def supportsSolo(self, key=""):
        return True

    def soloScope(self, context):
        # A material can be used by any geometry object, so scope the whole view layer - but
        # only mesh-like objects that can carry a material. Lights, cameras, empties etc. are
        # left visible so lighting and framing are unaffected by a material solo. Gaussian
        # splats (Empties) and hair / V-Ray fur (Curves objects) don't carry a material slot
        # the usual way, so include them explicitly - a material solo hides them too.
        from vray_blender.exporting import tools
        return [o for o in context.view_layer.objects
                if hasattr(o.data, 'materials') or o.type == 'CURVES' or tools.isObjectVRayGaussian(o)]

    def soloMatches(self, obj, targetName):
        # Same predicate as VRAY_OT_lister_select_material_users
        return any(slot.material == bpy.data.materials.get(targetName) for slot in obj.material_slots)


category = MaterialsCategory()
