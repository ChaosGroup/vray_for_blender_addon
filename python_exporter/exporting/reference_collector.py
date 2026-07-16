# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

""" Reference prepass: discover the objects referenced by V-Ray object selectors so they export even
    when hidden / disabled in renders (otherwise the reference resolves to an empty plugin). Selector
    nodes and object-selector property groups are scanned generically, on plugin nodes and on datablock
    V-Ray data. Runs before the object pass; GeometryExporter._exportObjects exports the hidden ones.
"""

import bpy

from vray_blender.lib.defs import ExporterContext
from vray_blender.nodes import utils as NodesUtils
from vray_blender.plugins import getPluginModule


# Selector nodes that point at scene objects. NodesUtils.getObjectsFromSelector resolves each.
_SELECTOR_NODE_TYPES = ('VRayNodeSelectObject', 'VRayNodeMultiSelect', 'VRayNodeSelectObjectGeometry')


def _vrayNodeTrees():
    """ All node trees that may contain V-Ray selector nodes: material, world and light trees plus
        standalone node groups (object/fur trees are stored as node groups). """
    trees = set()
    for collection in (bpy.data.materials, bpy.data.worlds, bpy.data.lights):
        for block in collection:
            if (nt := getattr(block, 'node_tree', None)) is not None:
                trees.add(nt)
    trees.update(bpy.data.node_groups)
    return trees


def _objectsReferencedByPropGroup(propGroup, pluginType: str):
    """ Yield the scene objects referenced by any object-selector TEMPLATE attribute on the given
        plugin property group (an include/exclude list, a splat's clip object, ...). """
    pluginModule = getPluginModule(pluginType) if pluginType and pluginType != 'NONE' else None
    for attrDesc in (getattr(pluginModule, 'Parameters', None) or []):
        if attrDesc.get('type') != 'TEMPLATE':
            continue
        template = getattr(propGroup, attrDesc['attr'], None)
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


def collectReferencedObjects(ctx: ExporterContext):
    """ Discover objects referenced by selectors and mark them for export even when hidden, so the
        references resolve. Re-run every export (incl. IPR), so moving a hidden referenced object
        updates the render. The hidden ones are exported by GeometryExporter's geometry loop.
    """
    if ctx.preview:
        # Preview scenes are self-contained; nothing to force-export.
        return

    context = ctx.ctx

    # Node-graph references: selector nodes + object-selector property groups hosted on plugin nodes.
    for ntree in _vrayNodeTrees():
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
