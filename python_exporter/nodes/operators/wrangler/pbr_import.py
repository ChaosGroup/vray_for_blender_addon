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
from vray_blender.nodes.slots import firstOutput, loadImage, tryLink
from vray_blender.nodes.tools import rearrangeTree
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
# `isData` = True -> Blender Non-Color colorspace + V-Ray linear transfer function / raw color space.
_SLOTS = [
    # slotKey         tokens                                                   attrName              flavor          isData
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


def _newBitmap(ntree, filepath: str, isData: bool, makeRelative: bool):
    """ Create a VRayNodeMetaImageTexture, load + assign the image. Returns (node, colorOutput). """
    node = ntree.nodes.new('VRayNodeMetaImageTexture')
    img = loadImage(filepath, makeRelative)
    if getattr(node, 'texture', None) is not None:
        node.texture.image = img
    if isData:
        img.colorspace_settings.is_data = True
        safeSet(node.BitmapBuffer, 'transfer_function', '0')
        safeSet(node.BitmapBuffer, 'rgb_color_space', 'raw')
    # Default output for TEXTURE category is "Color".
    return node, firstOutput(node, 'Color')


def _wrapNormal(ntree, bitmapOut):
    """ Wrap a bitmap in VRayNodeTexNormalBump (tangent space). Returns (wrapper, color_output). """
    wrap = ntree.nodes.new('VRayNodeTexNormalBump')
    tryLink(ntree, bitmapOut, getInputSocketByAttr(wrap, 'bump_tex_color'))
    # map_type '1' = normal map in tangent space
    safeSet(wrap.TexNormalBump, 'map_type', '1')
    return wrap, firstOutput(wrap, 'Color')


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

        # A BRDFVRayMtl socket accepts a single input, but different filename tokens can
        # resolve to the SAME target - e.g. a normal map and a bump map both target
        # bump_map, or roughness and gloss both target reflect_glossiness. Flag the first
        # match per socket to be connected (_SLOTS order makes that the more standard
        # channel: normal over bump, roughness over gloss); the rest are still imported
        # (so they land in the Textures group, ready to wire manually) but left
        # unconnected, instead of overwriting the link and orphaning a node. displacement
        # has no direct socket (attrName None) and is always connected.
        seenTargets = set()
        flagged, skippedSlots = [], []
        for slotMeta, fname in resolved:
            attrName = slotMeta[2]
            connect = not (attrName is not None and attrName in seenTargets)
            if attrName is not None and connect:
                seenTargets.add(attrName)
            if not connect:
                skippedSlots.append(slotMeta[0])
            flagged.append((slotMeta, fname, connect))
        resolved = flagged

        # rows: list of (bitmap, [wrappers]) - wrappers sit to the right of the
        # source bitmap in the layout pass at the end.
        rows: list[tuple[bpy.types.Node, list[bpy.types.Node]]] = []
        # Bitmaps imported but not wired (socket already taken); positioned into the
        # Textures group after the connected part of the tree has been arranged.
        unconnectedBitmaps: list[bpy.types.Node] = []
        # Carried out of the per-slot loop so the MtlDisplacement post-pass
        # can reach it.
        displacementBitmap: bpy.types.Node | None = None

        for (slotKey, _tokens, attrName, flavor, isData), fname, connect in resolved:
            # Displacement has no direct BRDF socket - handled as a post-pass
            # below that wraps the whole material in a MtlDisplacement.
            target = None
            if connect and flavor != 'DISPLACEMENT':
                target = getInputSocketByAttr(mtl, attrName)
                if target is None:
                    continue   # material lacks this socket - nothing to import

            filepath = path.join(importDir, fname)
            bitmap, bitmapOut = _newBitmap(ntree, filepath, isData, self.relative_path)
            bitmap.label = slotKey.replace('_', ' ').title()
            wrappers: list[bpy.types.Node] = []
            rows.append((bitmap, wrappers))

            # Imported-but-unconnected: keep it in the Textures group, wire nothing.
            if not connect:
                unconnectedBitmaps.append(bitmap)
                continue

            if bitmapOut is None:
                continue

            if flavor == 'DISPLACEMENT':
                displacementBitmap = bitmap
                continue

            if flavor == 'NORMAL':
                wrap, wrapOut = _wrapNormal(ntree, bitmapOut)
                wrappers.append(wrap)
                tryLink(ntree, wrapOut, target)
                # Tell the material this is a normal map (bump_type == 6).
                safeSet(mtl.BRDFVRayMtl, 'bump_type', '6')
                continue

            if flavor == 'BUMP':
                tryLink(ntree, bitmapOut, target)
                safeSet(mtl.BRDFVRayMtl, 'bump_type', '0')
                continue

            # Flip the material's roughness/gloss mode to match the texture
            # so the bitmap can wire straight into `reflect_glossiness`
            # without a TexInvertFloat in between.
            if flavor == 'ROUGHNESS':
                safeSet(mtl.BRDFVRayMtl, 'option_use_roughness', True)
            elif flavor == 'GLOSS':
                safeSet(mtl.BRDFVRayMtl, 'option_use_roughness', False)

            tryLink(ntree, bitmapOut, target)

        if not rows:
            self.report({'INFO'}, "No compatible sockets on the active material")
            return {'CANCELLED'}

        # Shared UVW mapping node wired into every CONNECTED bitmap's uvwgen input.
        # A reroute branches the mapping vector so all bitmaps fan out from one point,
        # keeping the link graph readable. Unconnected imports are left fully standalone
        # (no uvwgen either), so the layout doesn't pull them into the graph and strand
        # them next to the material - they are placed into the Textures group below.
        connectedBitmaps = [bitmap for bitmap, _ in rows if bitmap not in unconnectedBitmaps]
        uvwNode = ntree.nodes.new('VRayNodeUVWMapping')
        mappingOut = uvwNode.outputs.get('Mapping') or (uvwNode.outputs[0] if uvwNode.outputs else None)
        uvwReroute = ntree.nodes.new('NodeReroute')
        if mappingOut:
            tryLink(ntree, mappingOut, uvwReroute.inputs[0])
        rerouteOut = uvwReroute.outputs[0]
        for bitmap in connectedBitmaps:
            tryLink(ntree, rerouteOut, getInputSocketByAttr(bitmap, 'uvwgen'))

        # Displacement post-pass: wrap the BRDF in a MtlDisplacement so the
        # height map feeds the engine's subdivision pipeline. Existing
        # consumers of the BRDF's output are redirected onto the wrapper's
        # Material output, matching the merge-downstream-rewire pattern.
        displacementNode = None
        if displacementBitmap is not None:
            displacementNode = self._wrapInDisplacement(ntree, mtl, displacementBitmap)

        # Lay the whole tree out with the shared auto-arrange algorithm rather than fixed
        # offsets: the old row step overlapped tall preview nodes (a TexBitmap with its
        # thumbnail is far taller than the old step) and ignored DPI scaling. Force a
        # redraw first so the freshly created nodes report their real node.dimensions.
        try:
            bpy.ops.wm.redraw_timer(type='DRAW_WIN_SWAP', iterations=1)
        except RuntimeError:
            pass

        rearrangeTree(ntree, getOutputNode(ntree, 'MATERIAL') or mtl)

        # Stack any imported-but-unconnected bitmaps into the texture column, just below
        # the connected ones, so they sit inside the Textures group rather than being left
        # at the origin. Real node.dimensions are available now (we forced a redraw above).
        if unconnectedBitmaps:
            dpiFac = bpy.context.preferences.system.dpi / 72.0
            def _uiHeight(node):
                dimY = node.dimensions.y
                return dimY / dpiFac if dimY > 1.0 else 200.0

            if connectedBitmaps:
                colX = min(b.location.x for b in connectedBitmaps)
                y = min(b.location.y - _uiHeight(b) for b in connectedBitmaps) - 40.0
            else:
                colX, y = mtl.location.x - 900.0, mtl.location.y
            for bitmap in unconnectedBitmaps:
                bitmap.location = (colX, y)
                y -= _uiHeight(bitmap) + 40.0

        # Group the source bitmaps (connected and unconnected) under a labeled "Textures"
        # frame so they read as a unit. Parent AFTER the layout: the frame stays at (0,0),
        # so each bitmap's stored (now frame-relative) location is unchanged on screen and
        # Blender shrinks the frame to fit.
        texturesFrame = ntree.nodes.new('NodeFrame')
        texturesFrame.label = 'Textures'
        for bitmap, _ in rows:
            bitmap.parent = texturesFrame

        ntree.update_tag()

        # Frame the result in the editor.
        try:
            bpy.ops.node.view_all('INVOKE_DEFAULT')
        except RuntimeError:
            pass
        summary = f"Imported {len(rows)} texture(s)"
        if displacementNode is not None:
            summary += "; wrapped material in MtlDisplacement"
        if skippedSlots:
            summary += (f"; {', '.join(skippedSlots)} left unconnected in the Textures "
                        "group (target socket already used - wire manually if needed)")
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

        tryLink(ntree, brdfOutput, getInputSocketByAttr(wrap, 'base_material'))
        tryLink(ntree, firstOutput(heightBitmap, 'Color'), getInputSocketByAttr(wrap, 'displacement_tex_color'))

        materialOutput = firstOutput(wrap, 'Material')
        for toSocket in preserved:
            tryLink(ntree, materialOutput, toSocket)

        # Fresh material trees often leave the BRDF disconnected from the
        # Material Output. Without this fallback the wrapper would float
        # unconnected and displacement wouldn't reach the renderer.
        if not preserved:
            treeOutput = getOutputNode(ntree, 'MATERIAL')
            if treeOutput is not None:
                materialInput = treeOutput.inputs.get('Material')
                if materialInput is not None:
                    tryLink(ntree, materialOutput, materialInput)

        return wrap


def getRegClasses():
    return (VRAY_OT_WR_add_pbr_setup,)


def register():
    for regClass in getRegClasses():
        bpy.utils.register_class(regClass)


def unregister():
    for regClass in reversed(getRegClasses()):
        bpy.utils.unregister_class(regClass)
