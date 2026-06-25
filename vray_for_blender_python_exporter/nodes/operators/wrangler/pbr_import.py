# SPDX-FileCopyrightText: Blender Foundation
# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later
#
# The filename-token matcher is adapted from Blender's Node Wrangler
# add-on (GPL-2.0-or-later, `utils/paths.py`). The plumbing below is
# V-Ray-specific.

""" Add PBR Texture Setup.

    The user picks a folder of PBR textures; filenames are matched against
    well-known tokens (diffuse, rough, normal, etc.) and each match is
    created as a `VRayNodeMetaImageTexture` (TexBitmap + BitmapBuffer) wired
    into the appropriate `BRDFVRayMtl` socket.

    V-Ray-specific behavior:
    - Normal maps route through `VRayNodeTexNormalBump` into `bump_map` and
      set `BRDFVRayMtl.bump_type` to '6' (explicit normal map).
    - Roughness and gloss both target `reflect_glossiness`. Instead of
      inserting an invert node, the material's `option_use_roughness` flag
      is flipped to match the texture so the bitmap can wire straight in.
    - Displacement wraps the BRDF in a `MtlDisplacement` post-pass; the
      BRDF's existing downstream consumers are redirected through the wrap.
"""

from os import path
import re

import bpy
from bpy.props import BoolProperty, CollectionProperty, StringProperty
from bpy_extras.io_utils import ImportHelper

from vray_blender.lib.mixin import VRayOperatorBase
from vray_blender.exporting.tools import getInputSocketByAttr
from vray_blender.nodes.utils import getOutputNode, getPluginTypeOfNode
from vray_blender.nodes.operators.wrangler.helpers import safeSet
from vray_blender.nodes.operators.wrangler.poll import isVrayEditor, hasEditTree


# ---------- filename -> token matching ----------

def _splitIntoComponents(fname: str) -> list[str]:
    """'WallTexture_diff_2k.002.jpg' -> ['wall', 'texture', 'diff', 'k']"""
    fname = path.splitext(fname)[0]
    fname = "".join(char for char in fname if not char.isdigit())
    fname = re.sub(r"([a-z])([A-Z])", r"\g<1> \g<2>", fname)
    for sep in ("_", ".", "-", "__", "--", "#"):
        fname = fname.replace(sep, " ")
    return [token.lower() for token in fname.split(" ") if token]


def _stripCommonEdges(nameToTags: dict[str, list[str]]):
    """Strip the common leading/trailing component shared by every filename.
       Those carry no classification signal and can only confuse the matcher
       (e.g. all files start with 'mytexture_').
    """
    changed = True
    while changed and len(nameToTags) > 1:
        changed = False
        sample = next(iter(nameToTags.values()))
        if sample:
            head = sample[0]
            if all(tags and tags[0] == head for tags in nameToTags.values()):
                for name in nameToTags:
                    nameToTags[name] = nameToTags[name][1:]
                changed = True
        sample = next(iter(nameToTags.values()))
        if sample:
            tail = sample[-1]
            if all(tags and tags[-1] == tail for tags in nameToTags.values()):
                for name in nameToTags:
                    nameToTags[name] = nameToTags[name][:-1]
                changed = True


def _matchFilesToSlots(files, slots):
    """slots: list of [slotKey, [tokens], None]. Mutates slot[2] with a matched filename."""
    nameToTags = {file.name: _splitIntoComponents(file.name) for file in files}
    _stripCommonEdges(nameToTags)

    for slot in slots:
        for name, tags in nameToTags.items():
            if slot[0] == 'normal' and ('dx' in tags or 'directx' in tags):
                # Prefer GL-convention normals; skip DirectX maps.
                continue
            if set(slot[1]) & set(tags):
                slot[2] = name
                break


# ---------- slot -> BRDFVRayMtl plumbing ----------

# One entry per texture kind we recognise. `flavor` drives the wiring:
#   'COLOR'        - straight bitmap -> target socket (color data)
#   'FLOAT'        - straight bitmap -> float socket
#   'ROUGHNESS'    - straight -> reflect_glossiness, flip option_use_roughness=True
#   'GLOSS'        - straight -> reflect_glossiness, flip option_use_roughness=False
#   'NORMAL'       - wrap in TexNormalBump, connect to bump_map, set bump_type='6'
#   'BUMP'         - straight -> bump_map, set bump_type='0'
#   'DISPLACEMENT' - no BRDF socket; the BRDF is wrapped in a MtlDisplacement in a post-pass.
# `isData` = True -> colorspace_settings.is_data (Non-Color) on the loaded image.
_SLOTS = [
    # slotKey         tokens                                                attrName              flavor          isData
    ('base_color',    'diffuse diff albedo base col color basecolor'.split(),  'diffuse',            'COLOR',        False),
    ('metallic',      'metallic metalness metal mtl'.split(),                  'metalness',          'FLOAT',        True),
    ('roughness',     'roughness rough rgh'.split(),                           'reflect_glossiness', 'ROUGHNESS',    True),
    ('gloss',         'gloss glossy glossiness'.split(),                       'reflect_glossiness', 'GLOSS',        True),
    ('normal',        'normal nor nrm nrml norm'.split(),                      'bump_map',           'NORMAL',       True),
    ('bump',          'bump bmp'.split(),                                      'bump_map',           'BUMP',         True),
    ('emission',      'emission emissive emit'.split(),                        'self_illumination',  'COLOR',        False),
    ('alpha',         'alpha opacity'.split(),                                 'opacity_color',      'FLOAT',        True),
    ('displacement',  'displacement displace disp dsp height heightmap'.split(), None,               'DISPLACEMENT', True),
]


def _loadImage(filepath: str, makeRelative: bool):
    """Load an image, re-using an existing datablock if one with the same name is loaded."""
    blockName = bpy.path.display_name_from_filepath(filepath)
    img = bpy.data.images.get(blockName)
    if img is None:
        img = bpy.data.images.load(filepath, check_existing=True)
        img.name = blockName
    if makeRelative and bpy.data.filepath:
        try:
            img.filepath = bpy.path.relpath(img.filepath)
        except ValueError:
            pass
    return img


def _newBitmap(ntree, filepath: str, isData: bool, makeRelative: bool):
    """ Create a VRayNodeMetaImageTexture, load + assign the image. Returns (node, colorOutput). """
    node = ntree.nodes.new('VRayNodeMetaImageTexture')
    img = _loadImage(filepath, makeRelative)
    if getattr(node, 'texture', None) is not None:
        node.texture.image = img
    if isData:
        img.colorspace_settings.is_data = True
    # Default output for TEXTURE category is "Color".
    return node, _firstOutput(node, 'Color')


def _firstOutput(node, name: str):
    return node.outputs.get(name) or (node.outputs[0] if node.outputs else None)


def _tryLink(ntree, fromSocket, toSocket):
    if fromSocket is None or toSocket is None:
        return
    try:
        ntree.links.new(fromSocket, toSocket)
    except RuntimeError:
        pass


def _wrapNormal(ntree, bitmapOut):
    """ Wrap a bitmap in VRayNodeTexNormalBump (tangent space). Returns (wrapper, color_output). """
    wrap = ntree.nodes.new('VRayNodeTexNormalBump')
    _tryLink(ntree, bitmapOut, getInputSocketByAttr(wrap, 'bump_tex_color'))
    # map_type '1' = normal map in tangent space
    safeSet(wrap.TexNormalBump, 'map_type', '1')
    return wrap, _firstOutput(wrap, 'Color')


# ---------- operator ----------

class VRAY_OT_WR_add_pbr_setup(VRayOperatorBase, ImportHelper):
    """Pick a folder of PBR textures and wire them into the active BRDFVRayMtl"""
    bl_idname = "vray.wr_add_pbr_setup"
    bl_label = "Add PBR Texture Setup"
    bl_description = (
        "Select a set of PBR textures (diffuse / roughness / normal / ...) "
        "and wire them into the active V-Ray material"
    )
    bl_options = {'REGISTER', 'UNDO'}

    directory: StringProperty(
        name='Directory',
        subtype='DIR_PATH',
        default='',
        description='Folder containing the PBR texture set',
    )
    files: CollectionProperty(
        type=bpy.types.OperatorFileListElement,
        options={'HIDDEN', 'SKIP_SAVE'},
    )
    relative_path: BoolProperty(
        name='Relative Path',
        description='Make the image filepaths relative to the blend file when possible',
        default=True,
    )

    order = ["filepath", "files"]

    def draw(self, context):
        self.layout.prop(self, 'relative_path')

    @classmethod
    def poll(cls, context):
        if not (isVrayEditor(context) and hasEditTree(context)):
            return False
        active = context.space_data.edit_tree.nodes.active
        return bool(active and getPluginTypeOfNode(active) == 'BRDFVRayMtl')

    def execute(self, context):
        if not self.directory:
            self.report({'INFO'}, "No folder selected")
            return {'CANCELLED'}
        if not self.files[:]:
            self.report({'INFO'}, "No files selected")
            return {'CANCELLED'}

        ntree = context.space_data.edit_tree
        mtl = ntree.nodes.active
        if not mtl or getPluginTypeOfNode(mtl) != 'BRDFVRayMtl':
            self.report({'WARNING'}, "Active node is not a BRDFVRayMtl")
            return {'CANCELLED'}

        slots = [[slot[0], slot[1], None] for slot in _SLOTS]
        _matchFilesToSlots(self.files, slots)

        directory = bpy.path.abspath(self.directory)
        importDir = self.directory
        if self.relative_path and bpy.data.filepath:
            try:
                importDir = bpy.path.relpath(self.directory)
            except ValueError:
                pass

        # Discard slots with no filename match or a missing file on disk.
        resolved = []
        for slotMeta, slotRes in zip(_SLOTS, slots):
            if not slotRes[2]:
                continue
            if not path.exists(path.join(directory, slotRes[2])):
                continue
            resolved.append((slotMeta, slotRes[2]))

        if not resolved:
            self.report({'INFO'}, "No matching images found")
            return {'CANCELLED'}

        # rows: list of (bitmap, [wrappers]) - wrappers sit to the right of the
        # source bitmap in the layout pass at the end.
        rows: list[tuple[bpy.types.Node, list[bpy.types.Node]]] = []
        # Carried out of the per-slot loop so the MtlDisplacement post-pass
        # can reach it.
        displacementBitmap: bpy.types.Node | None = None

        for (_slotKey, _tokens, attrName, flavor, isData), fname in resolved:
            # Displacement has no direct BRDF socket - handled as a post-pass
            # below that wraps the whole material in a MtlDisplacement.
            if flavor != 'DISPLACEMENT':
                target = getInputSocketByAttr(mtl, attrName)
                if target is None:
                    continue
            else:
                target = None

            filepath = path.join(importDir, fname)
            bitmap, bitmapOut = _newBitmap(ntree, filepath, isData, self.relative_path)
            bitmap.label = _slotKey.replace('_', ' ').title()
            wrappers: list[bpy.types.Node] = []
            rows.append((bitmap, wrappers))

            if bitmapOut is None:
                continue

            if flavor == 'DISPLACEMENT':
                displacementBitmap = bitmap
                continue

            if flavor == 'NORMAL':
                wrap, wrapOut = _wrapNormal(ntree, bitmapOut)
                wrappers.append(wrap)
                _tryLink(ntree, wrapOut, target)
                # Tell the material this is a normal map (bump_type == 6).
                safeSet(mtl.BRDFVRayMtl, 'bump_type', '6')
                continue

            if flavor == 'BUMP':
                _tryLink(ntree, bitmapOut, target)
                safeSet(mtl.BRDFVRayMtl, 'bump_type', '0')
                continue

            # Flip the material's roughness/gloss mode to match the texture
            # so the bitmap can wire straight into `reflect_glossiness`
            # without a TexInvertFloat in between.
            if flavor == 'ROUGHNESS':
                safeSet(mtl.BRDFVRayMtl, 'option_use_roughness', True)
            elif flavor == 'GLOSS':
                safeSet(mtl.BRDFVRayMtl, 'option_use_roughness', False)

            _tryLink(ntree, bitmapOut, target)

        if not rows:
            self.report({'INFO'}, "No compatible sockets on the active material")
            return {'CANCELLED'}

        # Shared UVW mapping node wired into every bitmap's uvwgen input.
        # A reroute branches the mapping vector so all bitmaps fan out from
        # one point, keeping the link graph readable.
        uvwNode = ntree.nodes.new('VRayNodeUVWMapping')
        mappingOut = uvwNode.outputs.get('Mapping') or (uvwNode.outputs[0] if uvwNode.outputs else None)
        uvwReroute = ntree.nodes.new('NodeReroute')
        if mappingOut:
            _tryLink(ntree, mappingOut, uvwReroute.inputs[0])
        rerouteOut = uvwReroute.outputs[0]
        for bitmap, _ in rows:
            _tryLink(ntree, rerouteOut, getInputSocketByAttr(bitmap, 'uvwgen'))

        # Displacement post-pass: wrap the BRDF in a MtlDisplacement so the
        # height map feeds the engine's subdivision pipeline. Existing
        # consumers of the BRDF's output are redirected onto the wrapper's
        # Material output, matching the merge-downstream-rewire pattern.
        displacementNode = None
        if displacementBitmap is not None:
            displacementNode = self._wrapInDisplacement(ntree, mtl, displacementBitmap)

        # Layout: UVWgen on the far left, reroute branching to the bitmap
        # column, then wrappers cascading right of each bitmap. The whole
        # block sits to the left of the BRDF.
        bitmapX = mtl.location.x - 900.0
        wrapStep = 280.0
        rowStep = 200.0
        by = mtl.location.y
        for row, (bitmap, wrappers) in enumerate(rows):
            y = by - row * rowStep
            bitmap.location = (bitmapX, y)
            for col, wrap in enumerate(wrappers):
                wrap.location = (bitmapX + (col + 1) * wrapStep, y)
        # Bitmaps are anchored at their top-left, so the row centers sit
        # rowStep/2 below `.location.y`. Average top + bottom row centers
        # to land the reroute/UVW on the vertical midline of the column.
        midY = by - len(rows) * rowStep / 2.0
        uvwNode.location = (bitmapX - 300.0, midY)
        uvwReroute.location = (bitmapX - 60.0, midY)

        # Group bitmaps under a labeled "Textures" frame so they read as a
        # unit, matching the layout produced by Blender's stock Node Wrangler.
        # Parent is assigned after absolute positioning - the frame stays at
        # (0,0) so each bitmap's stored location (relative to the frame) keeps
        # the same on-screen position.
        texturesFrame = ntree.nodes.new('NodeFrame')
        texturesFrame.label = 'Textures'
        for bitmap, _ in rows:
            bitmap.parent = texturesFrame

        if displacementNode is not None:
            wrapX = mtl.location.x + mtl.width + 80.0
            displacementNode.location = (wrapX, mtl.location.y)
            # If the tree's Material Output is in the way (overlapping or to
            # the left of the wrapper), shift it past so the chain reads
            # BRDF -> MtlDisplacement -> Output instead of stacking on top.
            outputNode = getOutputNode(ntree, 'MATERIAL')
            if outputNode is not None:
                minOutputX = wrapX + displacementNode.width + 80.0
                if outputNode.location.x < minOutputX:
                    outputNode.location.x = minOutputX

        ntree.update_tag()
        summary = f"Imported {len(rows)} texture(s)"
        if displacementNode is not None:
            summary += "; wrapped material in MtlDisplacement"
        self.report({'INFO'}, summary)
        return {'FINISHED'}

    def _wrapInDisplacement(self, ntree, mtl, heightBitmap):
        """ Insert a MtlDisplacement between the BRDF and its downstream consumers.
            Returns the new node, or None if creation fails.
        """
        try:
            wrap = ntree.nodes.new('VRayNodeMtlDisplacement')
        except RuntimeError:
            return None

        # Capture the BRDF's downstream before we disturb anything.
        brdfOutput = next((output for output in mtl.outputs if output.enabled and not output.hide), None)
        preserved = [link.to_socket for link in list(brdfOutput.links)] if brdfOutput else []

        _tryLink(ntree, brdfOutput, getInputSocketByAttr(wrap, 'base_material'))
        _tryLink(ntree, _firstOutput(heightBitmap, 'Color'), getInputSocketByAttr(wrap, 'displacement_tex_color'))

        materialOutput = _firstOutput(wrap, 'Material')
        for toSocket in preserved:
            _tryLink(ntree, materialOutput, toSocket)

        # Fresh material trees often leave the BRDF disconnected from the
        # Material Output. Without this fallback the wrapper would float
        # unconnected and displacement wouldn't reach the renderer.
        if not preserved:
            treeOutput = getOutputNode(ntree, 'MATERIAL')
            if treeOutput is not None:
                materialInput = treeOutput.inputs.get('Material')
                if materialInput is not None:
                    _tryLink(ntree, materialOutput, materialInput)

        return wrap


def getRegClasses():
    return (VRAY_OT_WR_add_pbr_setup,)


def register():
    for regClass in getRegClasses():
        bpy.utils.register_class(regClass)


def unregister():
    for regClass in reversed(getRegClasses()):
        bpy.utils.unregister_class(regClass)
