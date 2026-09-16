# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

""" Reference prepass: discover the objects referenced by V-Ray object selectors so they export even
    when hidden / disabled in renders (otherwise the reference resolves to an empty plugin). Selector
    nodes and object-selector property groups are scanned generically, on plugin nodes and on datablock
    V-Ray data. Runs before the object pass; GeometryExporter._exportObjects exports the hidden ones.

    Also collects the attribute names the scene refers to, so mesh and per-instance attributes
    nothing can read are not exported.
"""

import bpy
import re

from itertools import chain

from vray_blender.exporting.plugin_tracker import getObjTrackId
from vray_blender.lib.defs import ExporterContext
from vray_blender.nodes import utils as NodesUtils
from vray_blender.nodes.tools import iterVRayNodeTrees
from vray_blender.plugins import getPluginModule


# Selector nodes that point at scene objects. NodesUtils.getObjectsFromSelector resolves each.
_SELECTOR_NODE_TYPES = ('VRayNodeSelectObject', 'VRayNodeMultiSelect', 'VRayNodeSelectObjectGeometry')

# Plugin params naming a V-Ray user attribute. GeomInstancer.user_attributes can supply one of
# these per instance, so a geometry-nodes instance attribute is a valid source for them. The plural
# 'user_attributes' params define attributes rather than reference them, so they are excluded.
_USER_ATTR_PARAMS = ('user_attribute', 'random_user_attribute', 'random_user_attr_name')

# V-Ray looks uv_set_name up in map_channels_names, so it can name a UV layer or a color attribute
# on the mesh, but never a user attribute - a map channel is per-vertex data, not per-instance.
_MAP_CHANNEL_PARAMS = ('uv_set_name',)

# Plugin params naming an attribute V-Ray resolves at render time.
_ATTR_NAME_PARAMS = _USER_ATTR_PARAMS + _MAP_CHANNEL_PARAMS

# UVWGenExplicit/UVWGenProjection read this fixed name instead of a user supplied one.
_UVW_SCALE_PARAM = 'user_attribute_scale_enabled'
_UVW_SCALE_ATTR  = '__vray_uvwscale'

# V-Ray substitutes any <token> in a bitmap path that is not one of its own tags with the
# like-named user attribute. Mirrors TextureTokenResolver::registerUserAttributes in C4D.
_FILE_PARAM = 'file'
_PATH_TOKEN = re.compile(r'<([^<>]+)>')
_RESERVED_PATH_TOKENS = frozenset(('UDIM', 'UVTILE', 'u', 'v', 'U', 'V', 'frameNum'))

# cycles_export turns the Attribute and Color Attribute nodes into a TexUserColor.
_CYCLES_ATTR_PARAMS = ('attribute_name', 'layer_name')

# The UV Map, Normal Map and Tangent nodes become plugins carrying uv_set_name, so their uv_map is
# a map channel lookup too - and it has to be collected, or a color attribute named there is
# dropped from the mesh export.
_CYCLES_UV_PARAMS = ('uv_map',)

# Plugin types declaring any of the above. Built once - the schema is fixed.
_attrNamePlugins = None


def _attrNamePluginTypes():
    global _attrNamePlugins
    if _attrNamePlugins is None:
        from vray_blender.plugins import PLUGIN_MODULES
        params = set(_ATTR_NAME_PARAMS) | {_UVW_SCALE_PARAM, _FILE_PARAM}
        _attrNamePlugins = [
            pluginType for pluginType, module in PLUGIN_MODULES.items()
            if any(p.get('attr') in params for p in getattr(module, 'Parameters', ()))
        ]
    return _attrNamePlugins


def _pathTokenAttrs(filePath: str):
    """ The <token>s of a bitmap path that name attributes; V-Ray resolves its own tags. """
    return (t for t in _PATH_TOKEN.findall(filePath) if t not in _RESERVED_PATH_TOKENS)


def _newNames() -> dict:
    """ The collected names, split by what V-Ray resolves them against - see
        collectReferencedAttrNames(). 'userAttrs' is always a subset of 'all'.
    """
    return {'all': set(), 'userAttrs': set()}


def _addUserAttr(names: dict, *attrNames):
    """ Names read through a V-Ray user attribute, which GeomInstancer can supply per instance. """
    names['all'].update(attrNames)
    names['userAttrs'].update(attrNames)


def _collectFromPlugin(propGroup, names: dict):
    """ Add the attribute names this plugin's parameters point at. """
    for param in _USER_ATTR_PARAMS:
        if name := getattr(propGroup, param, None):
            _addUserAttr(names, name)

    for param in _MAP_CHANNEL_PARAMS:
        if name := getattr(propGroup, param, None):
            names['all'].add(name)

    if getattr(propGroup, _UVW_SCALE_PARAM, False):
        _addUserAttr(names, _UVW_SCALE_ATTR)

    if filePath := getattr(propGroup, _FILE_PARAM, None):
        _addUserAttr(names, *_pathTokenAttrs(filePath))


def _collectFromPluginPropGroups(owner, names: dict, pluginsPerOwnerType: dict):
    """ Names pointed at by every V-Ray plugin property group on a node or a datablock's .vray. """
    if owner is None:
        return

    if (pluginTypes := pluginsPerOwnerType.get(type(owner))) is None:
        pluginTypes = [p for p in _attrNamePluginTypes() if hasattr(owner, p)]
        pluginsPerOwnerType[type(owner)] = pluginTypes

    for pluginType in pluginTypes:
        _collectFromPlugin(getattr(owner, pluginType), names)


def _collectFromNodeTree(ntree, names: dict, pluginsPerOwnerType: dict):
    if ntree is None:
        return

    for node in ntree.nodes:
        _collectFromPluginPropGroups(node, names, pluginsPerOwnerType)

        for param in _CYCLES_ATTR_PARAMS:
            if name := getattr(node, param, None):
                _addUserAttr(names, name)

        for param in _CYCLES_UV_PARAMS:
            if name := getattr(node, param, None):
                names['all'].add(name)


def _collectFromObject(obj, names: dict, pluginsPerOwnerType: dict):
    _collectFromPluginPropGroups(getattr(obj, 'vray', None), names, pluginsPerOwnerType)
    if obj.data is not None:
        _collectFromPluginPropGroups(getattr(obj.data, 'vray', None), names, pluginsPerOwnerType)


def _collectAlwaysScanned(exporterCtx: ExporterContext, names: dict, pluginsPerOwnerType: dict):
    """ The sources re-read in full on every export: scene, world and the image list. """
    scene = exporterCtx.ctx.scene
    _collectFromPluginPropGroups(getattr(scene, 'vray', None), names, pluginsPerOwnerType)
    if scene.world is not None:
        _collectFromPluginPropGroups(getattr(scene.world, 'vray', None), names, pluginsPerOwnerType)

    # A bitmap fed by an Image datablock forwards that datablock's filepath, tokens included.
    for image in bpy.data.images:
        if filePath := image.filepath:
            _addUserAttr(names, *_pathTokenAttrs(filePath))


def collectReferencedAttrNames(exporterCtx: ExporterContext) -> dict:
    """ The attribute names referenced anywhere in the scene, split by how V-Ray resolves them.

        V-Ray looks both map channels and user attributes up by name, so a name that appears nowhere
        cannot affect the render. Over-collects on purpose: a spare name costs one exported map
        channel, a missed one silently drops the attribute.

        'all'       - every name found. Gates the mesh attribute export.
        'userAttrs' - only the names read through a V-Ray user attribute (TexUserColor and friends).
                      Gates the per-instance export, since GeomInstancer.user_attributes is the only
                      thing that can serve those.

        The split matters because Blender copies a mesh's own attributes onto the geometry-nodes
        instance domain: scatter a UV-mapped grid and every instance ends up carrying a 'UVMap'. A
        bitmap naming that UV layer in uv_set_name is a map channel lookup, which can never read a
        user attribute, so exporting one per instance would run the whole dupli-tree walk for
        nothing. A TexUserColor naming it in user_attribute genuinely can, and keeps it.

        A prepass rather than collected during plugin export, because the object and instance
        passes both run before materials (renderer_ipr_base._export exports materials last).

        The full walk - incremental exports go through syncReferencedAttrNames() instead.
    """
    names = _newNames()
    pluginsPerOwnerType = {}  # owner class -> the attribute-naming plugin groups on it

    # Node groups cover the object, fur and decal trees, and nested groups' own nodes.
    for ntree in iterVRayNodeTrees():
        _collectFromNodeTree(ntree, names, pluginsPerOwnerType)

    # allObjects, not sceneObjects: an object living only in a linked collection is not in the
    # scene's depsgraph, and collection instancing is what gets scattered.
    for obj in exporterCtx.allObjects:
        _collectFromObject(obj, names, pluginsPerOwnerType)

    _collectAlwaysScanned(exporterCtx, names, pluginsPerOwnerType)

    return names


def _collectFromUpdatedDatablock(datablock, names: dict, pluginsPerOwnerType: dict):
    """ Re-read the names of one datablock the depsgraph reported.
        A source missed here silently drops an attribute - keep this generous.
    """
    if isinstance(datablock, bpy.types.NodeTree):
        _collectFromNodeTree(datablock, names, pluginsPerOwnerType)
    elif isinstance(datablock, bpy.types.Object):
        _collectFromObject(datablock, names, pluginsPerOwnerType)
        _collectFromNodeTree(getattr(getattr(datablock, 'vray', None), 'ntree', None), names, pluginsPerOwnerType)
    elif isinstance(datablock, (bpy.types.Material, bpy.types.World, bpy.types.Light)):
        _collectFromNodeTree(getattr(datablock, 'node_tree', None), names, pluginsPerOwnerType)
        _collectFromPluginPropGroups(getattr(datablock, 'vray', None), names, pluginsPerOwnerType)
    elif hasattr(datablock, 'vray'):
        # Geometry data - Mesh, Curves, ...
        _collectFromPluginPropGroups(datablock.vray, names, pluginsPerOwnerType)


def syncReferencedAttrNames(ctx: ExporterContext):
    """ Bring ctx.referencedAttrNames up to date and re-export the geometry that has to follow it.

        Only the datablocks dg.updates named are re-read, merged into the previous export's set -
        so between full exports the set grows but never shrinks.
    """
    if ctx.fullExport or ((previous := ctx.persistedState.referencedAttrNames) is None):
        # The full walk also drops the names that went away.
        ctx._referencedAttrNames = collectReferencedAttrNames(ctx)
        return

    names = {key: set(prevNames) for key, prevNames in previous.items()}
    pluginsPerOwnerType = {}

    for update in ctx.dg.updates:
        _collectFromUpdatedDatablock(update.id.original, names, pluginsPerOwnerType)

    _collectAlwaysScanned(ctx, names, pluginsPerOwnerType)

    ctx._referencedAttrNames = names

    if newNames := names['all'] - previous['all']:
        _tagOwnersOfAttrs(ctx, newNames)


def _tagOwnersOfAttrs(ctx: ExporterContext, newNames: set):
    """ Mark as geometry-updated everything that would now export one of these attributes. """
    geometryUpdates = ctx.dgUpdates['geometry']
    allUpdates      = ctx.dgUpdates['all']

    def hasNewAttr(obj):
        # Curves / text / metaballs only gain attributes once evaluated into a mesh.
        attrs = getattr(obj.data, 'attributes', None)
        return (attrs is not None) and any(attrs.get(name) is not None for name in newNames)

    # allObjects adds the linked-collection members that never show up in dg.objects.
    for obj in chain(ctx.dg.objects, ctx.allObjects):
        if hasNewAttr(obj):
            trackId = getObjTrackId(obj)
            geometryUpdates.add(trackId)
            allUpdates.add(trackId)

    # Rebuild every instancer - which one has an instance-domain attribute is not knowable cheaply.
    geometryUpdates.update(ctx.activeInstancers)
    allUpdates.update(ctx.activeInstancers)


# TEMPLATE attribute names per plugin type. The schema is fixed for the session.
_templateAttrsCache: dict[str, tuple[str, ...]] = {}


def _templateAttrs(pluginType: str) -> tuple[str, ...]:
    """ Names of the TEMPLATE attributes a plugin type declares. Empty for most plugins. """
    attrs = _templateAttrsCache.get(pluginType)
    if attrs is None:
        pluginModule = getPluginModule(pluginType)
        attrs = tuple(p['attr'] for p in (getattr(pluginModule, 'Parameters', None) or []) if p.get('type') == 'TEMPLATE')
        _templateAttrsCache[pluginType] = attrs
    return attrs


def _objectsReferencedByPropGroup(propGroup, pluginType: str):
    """ Yield the scene objects referenced by any object-selector TEMPLATE attribute on the given
        plugin property group (an include/exclude list, a splat's clip object, ...). """
    if not pluginType or pluginType == 'NONE':
        return

    for attrName in _templateAttrs(pluginType):
        template = getattr(propGroup, attrName, None)
        if template is None:
            continue

        # Read selectedItems directly, not getSelectedItems(): the latter is not uniform across
        # template types (include/exclude returns the complement), which over-registers every object.
        if hasattr(template, 'selectedItems'):
            for item in template.selectedItems:
                if not getattr(item, 'enabled', True):
                    continue
                ptr = getattr(item, 'objectPtr', None)
                if isinstance(ptr, bpy.types.Object):
                    yield ptr
                elif isinstance(ptr, bpy.types.Collection):
                    yield from ptr.all_objects
        elif isinstance(getattr(template, 'boundPropObj', None), bpy.types.Object):
            # Single-object selector (e.g. the Gaussian splat clip object).
            yield template.boundPropObj


# Plugin types whose descriptor declares a TEMPLATE attribute. Built once - the schema is fixed.
_pluginsWithTemplates = None

# Per V-Ray data property-group type, the subset of the above hosted on it. Cached for the session.
_hostTemplatePluginsCache = {}


def _templatePluginTypes():
    global _pluginsWithTemplates
    if _pluginsWithTemplates is None:
        from vray_blender.plugins import PLUGIN_MODULES
        _pluginsWithTemplates = [
            pluginType for pluginType, module in PLUGIN_MODULES.items()
            if any(p.get('type') == 'TEMPLATE' for p in getattr(module, 'Parameters', ()))
        ]
    return _pluginsWithTemplates


def _collectDataHostReferences(ctx: ExporterContext, vrayData):
    """ Register objects referenced by object-selector property groups hosted on a datablock's V-Ray
        data (obj.vray / obj.data.vray / scene.vray / world.vray) rather than on a node. """
    if vrayData is None:
        return

    hostKey = vrayData.bl_rna.identifier
    hostPlugins = _hostTemplatePluginsCache.get(hostKey)
    if hostPlugins is None:
        hostPlugins = [p for p in _templatePluginTypes() if hasattr(vrayData, p)]
        _hostTemplatePluginsCache[hostKey] = hostPlugins

    for pluginType in hostPlugins:
        for obj in _objectsReferencedByPropGroup(getattr(vrayData, pluginType), pluginType):
            ctx.registerReferencedObject(obj)


def _canReuseCollectedReferences(ctx: ExporterContext) -> bool:
    """ Whether the references collected on the current motion blur interval's first frame still apply. """
    return (not ctx.fullExport) \
            and ctx.commonSettings.exportMotionData \
            and ctx.isAnimation \
            and not ctx.motionBlurBuilder.isIntervalStart(ctx.currentFrame)


def _collectChaosScatterReferences(ctx: ExporterContext):
    """ Register the objects a Chaos Scatter setup references (distribution targets, model
        hierarchies) so their Node - or, for a light model, light - plugins export even when the
        objects are hidden; models typically live in a hidden prototypes collection.
        Splines/look-at/camera need no registration: scatter_export serializes them into
        GeomScatter params directly.
    """
    from vray_blender.exporting import tools

    for obj in ctx.ctx.scene.objects:
        if not tools.isObjectChaosScatter(obj):
            continue
        cs = obj.chaos_scatter
        for item in cs.targets:
            if item.object is not None:
                ctx.registerReferencedObject(item.object)
        for item in cs.models:
            if item.object is not None:
                ctx.registerReferencedObject(item.object)
                for child in item.object.children_recursive:
                    ctx.registerReferencedObject(child)


def collectReferencedObjects(ctx: ExporterContext):
    """ Discover objects referenced by selectors and mark them for export even when hidden, so the
        references resolve. Re-run whenever the scene may have changed, so moving a hidden referenced
        object updates the render. The hidden ones are exported by GeometryExporter's geometry loop.
    """
    if ctx.preview:
        # Preview scenes are self-contained; nothing to force-export.
        return

    if _canReuseCollectedReferences(ctx):
        return

    context = ctx.ctx

    # Node-graph references: selector nodes + object-selector property groups hosted on plugin nodes.
    for ntree in iterVRayNodeTrees():
        for node in ntree.nodes:
            if node.bl_idname in _SELECTOR_NODE_TYPES:
                for obj in NodesUtils.getObjectsFromSelector(node, context):
                    ctx.registerReferencedObject(obj)

            pluginType = getattr(node, 'vray_plugin', 'NONE')
            if (propGroup := getattr(node, pluginType, None)) is not None:
                for obj in _objectsReferencedByPropGroup(propGroup, pluginType):
                    ctx.registerReferencedObject(obj)

    # References hosted on datablock V-Ray data rather than nodes (e.g. a splat's clip object).
    for obj in context.scene.objects:
        _collectDataHostReferences(ctx, getattr(obj, 'vray', None))
        if obj.data is not None:
            _collectDataHostReferences(ctx, getattr(obj.data, 'vray', None))
    _collectDataHostReferences(ctx, getattr(context.scene, 'vray', None))
    if context.scene.world is not None:
        _collectDataHostReferences(ctx, getattr(context.scene.world, 'vray', None))

    _collectChaosScatterReferences(ctx)
