# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""Quick-setup operators for object and world node trees, plus a topology-sort
arrange operator that works in every V-Ray editor.

Operators
---------
vray.wr_arrange_tree        - Topo-sort from the output node (all tree types).
vray.wr_add_disp_subdiv     - Drop Displacement + Subdivision nodes (OBJECT).
vray.wr_add_shadow_catcher  - Shadow-catcher preset on the active object (OBJECT).
vray.wr_add_disp_texture    - File-browser: wire a height map into Displacement (OBJECT).
vray.wr_add_hdri            - File-browser: wire an HDRI into World Environment (WORLD).
vray.wr_add_dome_hdri       - File-browser: wire an HDRI into a Dome light texture (LIGHT).
vray.wr_wrap_selected       - Wrap ≥2 selected nodes in MtlMulti or BRDFLayered (MATERIAL).
vray.wr_add_beauty_channels - Add all beauty render elements to the world tree (WORLD).
"""

import bpy
from bpy.props import BoolProperty, StringProperty
from bpy_extras.io_utils import ImportHelper

from vray_blender.lib.mixin import VRayOperatorBase
from vray_blender.exporting.tools import getInputSocketByAttr
from vray_blender.nodes.tools import rearrangeTree, calculateTreeBounds
from vray_blender.nodes.sockets import addInput, moveExtendSocketToBottom
from vray_blender.nodes.specials.material import getMaterialSockets
from vray_blender.nodes.utils import getNodeByType, getOutputNode, getLightOutputNode, DisableAutoConnect
from vray_blender.nodes.operators.wrangler.helpers import _OUTPUT_BY_TREE_TYPE
from vray_blender.nodes.operators.wrangler.poll import isVrayEditor, hasEditTree, hasSelection
from vray_blender.nodes.operators.wrangler.pbr_import import (
    _newBitmap, _tryLink,
)


# Beauty render-element bl_idnames derived from customRenderChannelNodesDesc.
# Computed once here so the execute path stays allocation-free.
def _buildBeautyIdnames():
    from vray_blender.nodes.customRenderChannelNodes import customRenderChannelNodesDesc
    return [
        "VRayNodeRenderChannel" + d['params']['name'].replace(' ', '').replace('-', '')
        for d in customRenderChannelNodesDesc
        if d['Subtype'] == 'BEAUTY'
    ]

_BEAUTY_CHANNEL_IDNAMES = None   # populated lazily on first use


# ---------- shared helpers ----------

def _treeType(context) -> str:
    ntree = context.space_data.edit_tree
    return getattr(getattr(ntree, 'vray', None), 'tree_type', '')


def _getOrCreateOutput(ntree: bpy.types.NodeTree) -> bpy.types.Node | None:
    """Return the tree's output node, creating one if the tree type supports it."""
    treeType = getattr(getattr(ntree, 'vray', None), 'tree_type', '')
    if node := getOutputNode(ntree, treeType):
        return node
    bl_idname = _OUTPUT_BY_TREE_TYPE.get(treeType)
    if not bl_idname:
        return None
    try:
        return ntree.nodes.new(bl_idname)
    except RuntimeError:
        return None


def _newHdriBitmap(ntree: bpy.types.NodeTree, filepath: str, makeRelative: bool):
    """ Create an HDRI bitmap fed by an Environment (spherical) UVW mapping node.

        Returns (bitmapNode, bitmapColorOutput, uvwNode). HDRI files are already
        linear, so isData=False lets Blender auto-detect the colour space
        (.hdr/.exr -> Linear).
    """
    bitmapNode, bitmapOut = _newBitmap(ntree, filepath, isData=False, makeRelative=makeRelative)
    uvwNode = ntree.nodes.new('VRayNodeUVWMapping')
    uvwNode.mapping_node_type = 'ENVIRONMENT'   # triggers _mappingTypeUpdate
    _tryLink(ntree, uvwNode.outputs.get('Mapping'), getInputSocketByAttr(bitmapNode, 'uvwgen'))
    return bitmapNode, bitmapOut, uvwNode


# ---------- Arrange Tree ----------

class VRAY_OT_WR_arrange_tree(VRayOperatorBase):
    """Topology-sort the node tree from its output node outward"""
    bl_idname = "vray.wr_arrange_tree"
    bl_label = "Arrange Tree"
    bl_options = {'REGISTER', 'UNDO', 'INTERNAL'}

    @classmethod
    def poll(cls, context):
        return isVrayEditor(context) and hasEditTree(context)

    def execute(self, context):
        ntree = context.space_data.edit_tree
        treeType = getattr(getattr(ntree, 'vray', None), 'tree_type', '')
        outputNode = getOutputNode(ntree, treeType)
        if outputNode is None:
            self.report({'WARNING'}, "No output node found to arrange from")
            return {'CANCELLED'}
        bounds = calculateTreeBounds(ntree)
        rearrangeTree(ntree, outputNode, bounds=bounds)
        ntree.update_tag()
        return {'FINISHED'}


# ---------- Add Displacement + Subdivision (OBJECT) ----------

class VRAY_OT_WR_add_disp_subdiv(VRayOperatorBase):
    """Add a Displacement and Subdivision node pair wired into the Object Output"""
    bl_idname = "vray.wr_add_disp_subdiv"
    bl_label = "Add Displacement + Subdivision"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return isVrayEditor(context) and hasEditTree(context) and _treeType(context) == 'OBJECT'

    def execute(self, context):
        ntree = context.space_data.edit_tree
        outputNode = _getOrCreateOutput(ntree)
        if outputNode is None:
            self.report({'WARNING'}, "No object output node")
            return {'CANCELLED'}

        dispSock   = outputNode.inputs.get('Displacement')
        subdivSock = outputNode.inputs.get('Subdivision')

        # Place new nodes to the left of the output: displacement above, subdivision below.
        gapX = outputNode.location.x - 320.0

        # Only spawn a node when its output socket is empty. Otherwise re-running
        # the operator drops disconnected orphans into the tree alongside the
        # existing chain.
        if dispSock and not dispSock.is_linked:
            with DisableAutoConnect():
                dispNode = ntree.nodes.new('VRayNodeDisplacement')
            _tryLink(ntree, dispNode.outputs.get('Displacement'), dispSock)
            dispNode.location = (gapX, outputNode.location.y + 100.0)

        if subdivSock and not subdivSock.is_linked:
            with DisableAutoConnect():
                subdivNode = ntree.nodes.new('VRayNodeGeomStaticSmoothedMesh')
            # Plugin-backed geometry nodes expose their geometry on outputs[0].
            subdivOut = subdivNode.outputs[0] if subdivNode.outputs else None
            _tryLink(ntree, subdivOut, subdivSock)
            subdivNode.location = (gapX, outputNode.location.y - 140.0)

        ntree.update_tag()
        return {'FINISHED'}


# ---------- Shadow Catcher (OBJECT) ----------

class VRAY_OT_WR_add_shadow_catcher(VRayOperatorBase):
    """Wire a Matte Properties node and apply shadow-catcher defaults to the active object"""
    bl_idname = "vray.wr_add_shadow_catcher"
    bl_label = "Make Shadow Catcher"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return (
            isVrayEditor(context)
            and hasEditTree(context)
            and _treeType(context) == 'OBJECT'
            and bool(context.active_object)
        )

    def execute(self, context):
        ntree = context.space_data.edit_tree
        obj   = context.active_object

        outputNode = _getOrCreateOutput(ntree)
        if outputNode is None:
            self.report({'WARNING'}, "No object output node")
            return {'CANCELLED'}

        # Don't create the matte node here. Assigning matte_surface = True triggers
        # mattePropsSetter (plugins/misc/VRayObjectProperties.py), which builds and
        # wires the VRayNodeObjectMatteProps node itself; doing it manually first
        # leaves an orphan node behind when the setter replaces the link. Guard the
        # assignment so re-running the operator doesn't make the setter add another
        # duplicate.
        try:
            p = obj.vray.VRayObjectProperties
            if not p.matte_surface:
                p.matte_surface = True
            p.affect_alpha       = True
            p.shadows            = True
            p.alpha_contribution = -1.0
        except AttributeError:
            self.report({'WARNING'}, "Could not set matte properties on " + obj.name)

        ntree.update_tag()
        self.report({'INFO'}, f"Shadow catcher applied to '{obj.name}'")
        return {'FINISHED'}


# ---------- Import Displacement Texture (OBJECT) ----------

class VRAY_OT_WR_add_disp_texture(VRayOperatorBase, ImportHelper):
    """Pick a height / displacement map and wire it into a Displacement node"""
    bl_idname = "vray.wr_add_disp_texture"
    bl_label = "Import Displacement Texture"
    bl_options = {'REGISTER', 'UNDO'}

    filter_glob: StringProperty(
        default="*.png;*.jpg;*.jpeg;*.tif;*.tiff;*.exr;*.hdr;*.tga;*.bmp",
        options={'HIDDEN'},
    )
    relative_path: BoolProperty(
        name="Relative Path",
        description="Make the filepath relative to the blend file when possible",
        default=True,
    )

    def draw(self, context):
        self.layout.prop(self, 'relative_path')

    @classmethod
    def poll(cls, context):
        return isVrayEditor(context) and hasEditTree(context) and _treeType(context) == 'OBJECT'

    def execute(self, context):
        if not self.filepath:
            self.report({'INFO'}, "No file selected")
            return {'CANCELLED'}

        ntree      = context.space_data.edit_tree
        outputNode = _getOrCreateOutput(ntree)
        if outputNode is None:
            self.report({'WARNING'}, "No object output node")
            return {'CANCELLED'}

        # Find or create a VRayNodeDisplacement.
        dispNode = getNodeByType(ntree, 'VRayNodeDisplacement')
        if dispNode is None:
            with DisableAutoConnect():
                dispNode = ntree.nodes.new('VRayNodeDisplacement')
            dispSock = outputNode.inputs.get('Displacement')
            if dispSock and not dispSock.is_linked:
                _tryLink(ntree, dispNode.outputs.get('Displacement'), dispSock)
            dispNode.location = (outputNode.location.x - 550.0, outputNode.location.y)

        # Create the bitmap node (marked as data / non-colour for displacement maps).
        bitmapNode, bitmapOut = _newBitmap(ntree, self.filepath, isData=True,
                                           makeRelative=self.relative_path)
        texSock = dispNode.inputs.get('Displacement Texture')
        _tryLink(ntree, bitmapOut, texSock)
        bitmapNode.location = (dispNode.location.x - 380.0, dispNode.location.y)

        ntree.update_tag()
        self.report({'INFO'}, "Displacement texture imported")
        return {'FINISHED'}


# ---------- Import HDRI (WORLD) ----------

class VRAY_OT_WR_add_hdri(VRayOperatorBase, ImportHelper):
    """Pick an HDRI / EXR and wire it into the World Environment background"""
    bl_idname = "vray.wr_add_hdri"
    bl_label = "Import HDRI"
    bl_options = {'REGISTER', 'UNDO'}

    filter_glob: StringProperty(
        default="*.hdr;*.exr;*.png;*.jpg;*.jpeg;*.tif;*.tiff",
        options={'HIDDEN'},
    )
    relative_path: BoolProperty(
        name="Relative Path",
        description="Make the filepath relative to the blend file when possible",
        default=True,
    )
    link_gi: BoolProperty(
        name="Also link to GI",
        description="Drive the GI environment override with the same HDRI",
        default=False,
    )
    link_reflection: BoolProperty(
        name="Also link to Reflection",
        description="Drive the Reflection environment override with the same HDRI",
        default=False,
    )

    def draw(self, context):
        layout = self.layout
        layout.prop(self, 'relative_path')
        layout.separator()
        layout.prop(self, 'link_gi')
        layout.prop(self, 'link_reflection')

    @classmethod
    def poll(cls, context):
        return isVrayEditor(context) and hasEditTree(context) and _treeType(context) == 'WORLD'

    def execute(self, context):
        if not self.filepath:
            self.report({'INFO'}, "No file selected")
            return {'CANCELLED'}

        ntree      = context.space_data.edit_tree
        outputNode = _getOrCreateOutput(ntree)
        if outputNode is None:
            self.report({'WARNING'}, "No world output node")
            return {'CANCELLED'}

        # Find or create VRayNodeEnvironment.
        envNode = getNodeByType(ntree, 'VRayNodeEnvironment')
        if envNode is None:
            envNode = ntree.nodes.new('VRayNodeEnvironment')
            envSock = outputNode.inputs.get('Environment')
            if envSock and not envSock.is_linked:
                _tryLink(ntree, envNode.outputs.get('Environment'), envSock)
            envNode.location = (outputNode.location.x - 380.0, outputNode.location.y + 80.0)

        # HDRI bitmap fed by an Environment (spherical) UVW mapping node.
        bitmapNode, bitmapOut, uvwNode = _newHdriBitmap(ntree, self.filepath, self.relative_path)

        # Background is always wired; enable the use checkbox so the override is active.
        bgSock = getInputSocketByAttr(envNode, 'bg_tex')
        _tryLink(ntree, bitmapOut, bgSock)
        if bgSock:
            bgSock.use = True

        if self.link_gi:
            giSock = getInputSocketByAttr(envNode, 'gi_tex')
            _tryLink(ntree, bitmapOut, giSock)
            if giSock:
                giSock.use = True
        if self.link_reflection:
            reflectSock = getInputSocketByAttr(envNode, 'reflect_tex')
            _tryLink(ntree, bitmapOut, reflectSock)
            if reflectSock:
                reflectSock.use = True

        # Layout: uvw ← bitmap ← envNode ← outputNode
        bitmapNode.location = (envNode.location.x - 380.0, envNode.location.y + 40.0)
        uvwNode.location    = (bitmapNode.location.x - 280.0, bitmapNode.location.y)

        ntree.update_tag()
        self.report({'INFO'}, "HDRI imported")
        return {'FINISHED'}


# ---------- Import HDRI into a Dome light (LIGHT) ----------

class VRAY_OT_WR_add_dome_hdri(VRayOperatorBase, ImportHelper):
    """Pick an HDRI / EXR and wire it into the Dome light texture"""
    bl_idname = "vray.wr_add_dome_hdri"
    bl_label = "Import HDRI"
    bl_options = {'REGISTER', 'UNDO'}

    filter_glob: StringProperty(
        default="*.hdr;*.exr;*.png;*.jpg;*.jpeg;*.tif;*.tiff",
        options={'HIDDEN'},
    )
    relative_path: BoolProperty(
        name="Relative Path",
        description="Make the filepath relative to the blend file when possible",
        default=True,
    )

    def draw(self, context):
        self.layout.prop(self, 'relative_path')

    @classmethod
    def poll(cls, context):
        if not (isVrayEditor(context) and hasEditTree(context) and _treeType(context) == 'LIGHT'):
            return False
        domeNode = getLightOutputNode(context.space_data.edit_tree)
        return (domeNode is not None) and (getattr(domeNode, 'vray_plugin', '') == 'LightDome')

    def execute(self, context):
        if not self.filepath:
            self.report({'INFO'}, "No file selected")
            return {'CANCELLED'}

        ntree    = context.space_data.edit_tree
        domeNode = getLightOutputNode(ntree)
        if domeNode is None or getattr(domeNode, 'vray_plugin', '') != 'LightDome':
            self.report({'WARNING'}, "No dome light node")
            return {'CANCELLED'}

        bitmapNode, bitmapOut, uvwNode = _newHdriBitmap(ntree, self.filepath, self.relative_path)

        # Wire the bitmap into the dome's "Dome Color" texture socket. The
        # VRaySocketColorTexture meta socket sets use_dome_tex=True on export
        # whenever it is linked, so no explicit toggle is required here.
        _tryLink(ntree, bitmapOut, getInputSocketByAttr(domeNode, 'color_colortex'))

        # Layout: uvw <- bitmap <- domeNode
        bitmapNode.location = (domeNode.location.x - 380.0, domeNode.location.y + 40.0)
        uvwNode.location    = (bitmapNode.location.x - 280.0, bitmapNode.location.y)

        ntree.update_tag()
        self.report({'INFO'}, "HDRI imported")
        return {'FINISHED'}


# ---------- Wrap Selected in MtlMulti or BRDFLayered (MATERIAL) ----------

def _firstEnabledOutput(node, *preferred_types):
    """Return the first enabled, visible output matching one of the preferred
    bl_idnames, falling back to outputs[0]."""
    for type_id in preferred_types:
        s = next((s for s in node.outputs
                  if s.bl_idname == type_id and s.enabled and not s.hide), None)
        if s:
            return s
    return next((s for s in node.outputs if s.enabled and not s.hide), None)


class VRAY_OT_WR_wrap_selected(VRayOperatorBase):
    """Wrap the selected node(s) in a material/BRDF wrapper"""
    bl_idname = "vray.wr_wrap_selected"
    bl_label = "Wrap Selected"
    bl_options = {'REGISTER', 'UNDO'}

    wrapper_type: bpy.props.EnumProperty(
        name="Wrapper",
        items=[
            ('MTL_MULTI',        "Switch Material (MtlMulti)",     "Stack materials in a V-Ray Switch Material node"),
            ('BRDF_LAYERED',     "Layered BRDF (BRDFLayered)",      "Stack BRDFs in a V-Ray Layered BRDF coat stack"),
            ('BRDF_BUMP',        "Bump / Normal (BRDFBump)",        "Add bump mapping to the selected BRDF"),
            ('MTL_DISPLACEMENT', "Displacement (MtlDisplacement)",  "Add displacement to the selected material"),
            ('MTL_OVERRIDE',     "Override (MtlOverride)",          "Wrap the selected material in an MtlOverride"),
        ],
        default='MTL_MULTI',
    )

    @classmethod
    def poll(cls, context):
        if not (isVrayEditor(context) and hasEditTree(context) and _treeType(context) == 'MATERIAL'):
            return False
        return any(
            getattr(n, 'vray_type', '') in {'BRDF', 'MATERIAL'}
            for n in (context.selected_nodes or [])
        )

    def execute(self, context):
        ntree    = context.space_data.edit_tree
        selected = sorted(
            [n for n in ntree.nodes if n.select],
            key=lambda n: n.location.y,
            reverse=True,
        )
        match self.wrapper_type:
            case 'MTL_MULTI':
                return self._buildMtlMulti(ntree, selected)
            case 'BRDF_LAYERED':
                return self._buildBRDFLayered(ntree, selected)
            case 'BRDF_BUMP':
                return self._buildSingleWrapper(ntree, selected[0], 'VRayNodeBRDFBump',
                                                ('VRaySocketBRDF',))
            case 'MTL_DISPLACEMENT':
                return self._buildSingleWrapper(ntree, selected[0], 'VRayNodeMtlDisplacement',
                                                ('VRaySocketMtl', 'VRaySocketBRDF'))
            case 'MTL_OVERRIDE':
                return self._buildSingleWrapper(ntree, selected[0], 'VRayNodeMtlOverride',
                                                ('VRaySocketMtl', 'VRaySocketBRDF'))
        return {'CANCELLED'}

    # ---- single-node wrappers (BRDFBump / MtlDisplacement / MtlOverride) ----

    def _rewireDownstream(self, ntree, preserved, wrapOut):
        """Reconnect captured downstream sockets to wrapOut, falling back to the
        tree's Material output socket if there were no downstream consumers."""
        if wrapOut is None:
            return
        for toSock in preserved:
            _tryLink(ntree, wrapOut, toSock)
        if not preserved:
            if outNode := _getOrCreateOutput(ntree):
                mtlSock = outNode.inputs.get('Material')
                if mtlSock and not mtlSock.is_linked:
                    _tryLink(ntree, wrapOut, mtlSock)

    def _buildSingleWrapper(self, ntree, srcNode, nodeType, preferredTypes):
        srcOut = _firstEnabledOutput(srcNode, *preferredTypes)
        if srcOut is None:
            self.report({'WARNING'}, f"No usable output on '{srcNode.name}'")
            return {'CANCELLED'}

        preserved = [lk.to_socket for lk in list(srcOut.links)]

        with DisableAutoConnect():
            wrapNode = ntree.nodes.new(nodeType)

        _tryLink(ntree, srcOut, wrapNode.inputs.get('Base Material'))

        wrapOut = wrapNode.outputs[0] if wrapNode.outputs else None
        self._rewireDownstream(ntree, preserved, wrapOut)

        wrapNode.location = (srcNode.location.x + 300.0, srcNode.location.y)
        ntree.update_tag()
        self.report({'INFO'}, f"Wrapped '{srcNode.name}' in {wrapNode.bl_label}")
        return {'FINISHED'}

    # ---- MtlMulti ----

    def _buildMtlMulti(self, ntree, selected):
        sources = [
            (n, _firstEnabledOutput(n, 'VRaySocketBRDF', 'VRaySocketMtl'))
            for n in selected
        ]
        sources = [(n, s) for n, s in sources if s is not None]
        if not sources:
            self.report({'WARNING'}, "No material / BRDF outputs found in selection")
            return {'CANCELLED'}

        # Preserve downstream links from the first node before touching anything.
        firstOut   = sources[0][1]
        selectedSet = {n for n, _ in sources}
        preserved  = [lk.to_socket for lk in list(firstOut.links)
                      if lk.to_node not in selectedSet]

        with DisableAutoConnect():
            wrapNode = ntree.nodes.new('VRayNodeMtlMulti')

        # VRayNodeMtlMulti starts with 2 slots; add more as needed.
        for _ in range(len(sources) - 2):
            wrapNode.addMaterial()

        # Link each source to a material socket by position; the ID in the 'Material X' name
        # need not be consecutive.
        for (_, srcOut), mtlSock in zip(sources, getMaterialSockets(wrapNode)):
            _tryLink(ntree, srcOut, mtlSock)

        wrapOut = wrapNode.outputs.get('Material') or wrapNode.outputs[0]
        self._rewireDownstream(ntree, preserved, wrapOut)

        wrapNode.location = (
            max(n.location.x for n in selected) + 300.0,
            sum(n.location.y for n in selected) / len(selected),
        )
        ntree.update_tag()
        self.report({'INFO'}, f"Wrapped {len(sources)} material(s) in Switch Material")
        return {'FINISHED'}

    # ---- BRDFLayered ----

    def _buildBRDFLayered(self, ntree, selected):
        sources = [
            (n, _firstEnabledOutput(n, 'VRaySocketBRDF'))
            for n in selected
        ]
        sources = [(n, s) for n, s in sources if s is not None]
        if not sources:
            self.report({'WARNING'}, "No BRDF outputs found in selection")
            return {'CANCELLED'}

        firstOut    = sources[0][1]
        selectedSet = {n for n, _ in sources}
        preserved   = [lk.to_socket for lk in list(firstOut.links)
                       if lk.to_node not in selectedSet]

        with DisableAutoConnect():
            wrapNode = ntree.nodes.new('VRayNodeBRDFLayered')

        # Wire base material (bottom of coat stack = first / topmost selected).
        _tryLink(ntree, sources[0][1], wrapNode.inputs.get('Base Material'))

        # Wire coat layers.  Layer 1 sockets are already created by nodeInit;
        # layers 2+ are added via the shared helper.
        from vray_blender.plugins.BRDF.BRDFLayered import addCoatLayer
        for layerIdx, (_, srcOut) in enumerate(sources[1:], start=1):
            if layerIdx > 1:
                addCoatLayer(wrapNode)
            _tryLink(ntree, srcOut, wrapNode.inputs.get(f"Coat Material {layerIdx}"))

        wrapOut = wrapNode.outputs.get('BRDF') or wrapNode.outputs[0]
        self._rewireDownstream(ntree, preserved, wrapOut)

        wrapNode.location = (
            max(n.location.x for n in selected) + 300.0,
            sum(n.location.y for n in selected) / len(selected),
        )
        ntree.update_tag()
        self.report({'INFO'}, f"Wrapped {len(sources)} BRDF(s) in BRDFLayered")
        return {'FINISHED'}


# ---------- Beauty Render Channel Preset (WORLD) ----------

def _addChannelToContainer(ntree, channelNode, containerNode):
    """Wire channelNode's output into the next free slot of containerNode,
    growing the socket list if needed."""
    channelOut = next(
        (s for s in channelNode.outputs if s.bl_idname == 'VRaySocketRenderChannelOutput'),
        channelNode.outputs[0] if channelNode.outputs else None,
    )
    if channelOut is None:
        return
    freeSock = next(
        (s for s in containerNode.inputs
         if s.bl_idname == 'VRaySocketRenderChannel' and not s.is_linked),
        None,
    )
    if freeSock is None:
        humanIdx = sum(1 for s in containerNode.inputs
                       if s.bl_idname == 'VRaySocketRenderChannel') + 1
        freeSock = addInput(containerNode, 'VRaySocketRenderChannel', f"Channel {humanIdx}")
        moveExtendSocketToBottom(containerNode)
    _tryLink(ntree, channelOut, freeSock)


class VRAY_OT_WR_add_beauty_channels(VRayOperatorBase):
    """Add all V-Ray beauty render element channels to the world tree at once"""
    bl_idname = "vray.wr_add_beauty_channels"
    bl_label = "Add Beauty Render Elements"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return isVrayEditor(context) and hasEditTree(context) and _treeType(context) == 'WORLD'

    def execute(self, context):
        global _BEAUTY_CHANNEL_IDNAMES
        if _BEAUTY_CHANNEL_IDNAMES is None:
            _BEAUTY_CHANNEL_IDNAMES = _buildBeautyIdnames()

        ntree = context.space_data.edit_tree

        # Find or create the channels container and wire it to the world output.
        channelsNode = getNodeByType(ntree, 'VRayNodeRenderChannels')
        if channelsNode is None:
            channelsNode = ntree.nodes.new('VRayNodeRenderChannels')
            outputNode   = _getOrCreateOutput(ntree)
            if outputNode:
                chanSock = outputNode.inputs.get('Channels')
                chanOut  = channelsNode.outputs.get('Channels')
                if chanSock and not chanSock.is_linked:
                    _tryLink(ntree, chanOut, chanSock)
            channelsNode.location = (
                outputNode.location.x - 380.0 if outputNode else 0.0,
                outputNode.location.y - 220.0 if outputNode else 0.0,
            )

        # Skip channels already present so the op is idempotent.
        existing = {n.bl_idname for n in ntree.nodes}

        added   = 0
        skipped = 0
        startX  = channelsNode.location.x - 380.0
        startY  = channelsNode.location.y + 80.0

        # Suppress per-node auto-connect; init() would otherwise duplicate
        # the explicit wiring below.
        newNodes = []
        with DisableAutoConnect():
            for bl_idname in _BEAUTY_CHANNEL_IDNAMES:
                if bl_idname in existing:
                    skipped += 1
                    continue
                try:
                    chanNode = ntree.nodes.new(bl_idname)
                except RuntimeError:
                    skipped += 1
                    continue
                chanNode.location = (startX, startY - added * 100.0)
                _addChannelToContainer(ntree, chanNode, channelsNode)
                newNodes.append(chanNode)
                added += 1

        if newNodes:
            beautyFrame = ntree.nodes.new('NodeFrame')
            beautyFrame.label = "Beauty Pass"
            for n in newNodes:
                n.parent = beautyFrame

        ntree.update_tag()
        msg = f"Added {added} beauty channel(s)"
        if skipped:
            msg += f", {skipped} already present"
        self.report({'INFO'}, msg)
        return {'FINISHED'}


# ---------- Registration ----------

def getRegClasses():
    return (
        VRAY_OT_WR_arrange_tree,
        VRAY_OT_WR_add_disp_subdiv,
        VRAY_OT_WR_add_shadow_catcher,
        VRAY_OT_WR_add_disp_texture,
        VRAY_OT_WR_add_hdri,
        VRAY_OT_WR_add_dome_hdri,
        VRAY_OT_WR_wrap_selected,
        VRAY_OT_WR_add_beauty_channels,
    )


def register():
    for regClass in getRegClasses():
        bpy.utils.register_class(regClass)


def unregister():
    for regClass in reversed(getRegClasses()):
        bpy.utils.unregister_class(regClass)
