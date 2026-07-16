# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

# Displacement section. Three groups:
#   - Object Displacement: VRayNodeDisplacement on the object output's Displacement socket
#     (edits GeomDisplacedMesh).
#   - Subdivision: VRayNodeGeomStaticSmoothedMesh on the Subdivision socket
#     (edits GeomStaticSmoothedMesh).
#   - Material Displacement: VRayNodeMtlDisplacement on the material output
#     (edits MtlDisplacement).
# An object can have both displacement and subdivision nodes, so each is wrapped per role
# (_ObjEntity) and listed in both groups. Object rows use their own select/name cells
# operating on the wrapped object (the shared core cells need a real bpy_struct).

import bpy

from vray_blender.ui.lister.core import (
    ListerCategory, ColumnSpec, drawSelectCell, drawNameCell, drawTextureRef, drawMaterialUsersCell,
)


class _ObjEntity:
    """ Wraps an object for a single role ('OBJECT' = displacement, 'SUBDIV' = subdivision)
        so the same object can appear in both groups. """
    __slots__ = ('obj', 'role')

    def __init__(self, obj, role):
        self.obj = obj
        self.role = role

    @property
    def locator(self):
        """ Stable per-row id (object name + role) used for pinning. _ObjEntity instances are
            rebuilt on every enumerate(), so without this core._entityId falls back to the
            default object repr, whose address changes each redraw and breaks pin persistence. """
        return f"{self.obj.name}|{self.role}"


def _objectOutputLink(obj: bpy.types.Object, socketName: str, nodeType: str):
    """ The node feeding the object output's <socketName> input through a <nodeType> node,
        or None. Same node path the exporter uses (exporting/obj_export.py). """
    vrayObj = getattr(obj, 'vray', None)
    ntree = getattr(vrayObj, 'ntree', None) if vrayObj is not None else None
    if ntree is None:
        return None

    from vray_blender.nodes.utils import getOutputNode
    from vray_blender.exporting.tools import getNodeLinkToNode

    nodeOutput = getOutputNode(ntree, 'OBJECT')
    if nodeOutput is None:
        return None
    link = getNodeLinkToNode(nodeOutput, socketName, nodeType)
    return link.from_node if link is not None else None


def _displacementNode(obj: bpy.types.Object):
    """ The object's VRayNodeDisplacement node (object output's Displacement socket), or None. """
    return _objectOutputLink(obj, "Displacement", "VRayNodeDisplacement")


def _subdivNode(obj: bpy.types.Object):
    """ The object's VRayNodeGeomStaticSmoothedMesh node (Subdivision socket), or None. """
    return _objectOutputLink(obj, "Subdivision", "VRayNodeGeomStaticSmoothedMesh")


def _displacementMtlNode(mtl: bpy.types.Material):
    """ Return the material's VRayNodeMtlDisplacement node connected to its output, or None.
        Mirrors the exporter's detection in plugins/material/MtlDisplacement.py. """
    ntree = getattr(mtl, 'node_tree', None)
    if ntree is None:
        return None

    from vray_blender.nodes.utils import getOutputNode, areNodesInterconnected

    nodeOutput = getOutputNode(ntree, 'MATERIAL')
    if nodeOutput is None:
        return None
    for node in ntree.nodes:
        if node.bl_idname == "VRayNodeMtlDisplacement" and areNodesInterconnected(node, nodeOutput):
            return node
    return None


def _drawWaterLevel(row, entity, propGroup):
    # Water level only applies when the water-level clamp is enabled
    row.enabled = getattr(propGroup, 'use_water_level', False)
    row.prop(propGroup, 'water_level', text="")


def _makeGlobalOverrideCell(attr):
    """ A subdivision-quality override (view dependence, edge length, max subdivs) that is
        greyed out when the displacement/subdivision uses the global settings. """
    def draw(row, entity, propGroup):
        row.enabled = not getattr(propGroup, 'use_globals', False)
        row.prop(propGroup, attr, text="")
    return draw


def _makeBoundCell(attr):
    """ A displacement bound (min/max) that is greyed out unless bounds are enabled. """
    def draw(row, entity, propGroup):
        row.enabled = getattr(propGroup, 'use_bounds', False)
        row.prop(propGroup, attr, text="")
    return draw


# --- Object-row cells (entities are _ObjEntity wrappers, so use entity.obj) ---

def _drawObjSelect(row, entity, propGroup):
    # Reuse the shared cell against the wrapped object
    drawSelectCell(row, entity.obj, propGroup)


def _drawObjName(row, entity, propGroup):
    drawNameCell(row, entity.obj, propGroup)


def _drawDisplacementTexture(row, entity, propGroup):
    # Show whatever texture is linked to the node's "Displacement Texture" input.
    node = _displacementNode(entity.obj)
    sock = next((s for s in node.inputs if s.name == "Displacement Texture"), None) if node is not None else None
    if sock is not None and sock.is_linked and sock.links:
        drawTextureRef(row, sock.links[0].from_node)
    else:
        row.label(text="")


_COL_OBJ_SELECT = ColumnSpec('select', "", draw=_drawObjSelect, fixedWidth=1.5, center=True)
_COL_OBJ_NAME   = ColumnSpec('name', "Name", draw=_drawObjName, width=2.0)


class DisplacementCategory(ListerCategory):
    id = 'DISPLACEMENT'
    label = "Displacement"
    icon = 'MOD_DISPLACE'
    enumIndex = 9
    # Object rows are _ObjEntity wrappers, not bpy objects, so they can't use the shared
    # selectable machinery; they carry their own select cell instead.
    selectable = False

    def enumerate(self, context):
        entities = []
        for o in context.scene.objects:
            # List the object in each group it qualifies for (it may have both)
            if _displacementNode(o) is not None:
                entities.append(_ObjEntity(o, 'OBJECT'))
            if _subdivNode(o) is not None:
                entities.append(_ObjEntity(o, 'SUBDIV'))
        entities += [m for m in bpy.data.materials if m.users > 0 and _displacementMtlNode(m) is not None]
        return entities

    def entityName(self, entity):
        return entity.obj.name if isinstance(entity, _ObjEntity) else entity.name

    def groupKey(self, entity):
        return entity.role if isinstance(entity, _ObjEntity) else 'MATERIAL'

    def groupLabel(self, key):
        return {
            'OBJECT':   "Object Displacement",
            'SUBDIV':   "Subdivision",
            'MATERIAL': "Material Displacement",
        }.get(key, key)

    def groupOrder(self):
        return ['OBJECT', 'SUBDIV', 'MATERIAL']

    def pickerSections(self, context, state):
        # One labelled section per group
        return self.pickerSectionsByGroup()

    def getPropGroup(self, entity):
        if isinstance(entity, _ObjEntity):
            if entity.role == 'OBJECT':
                node = _displacementNode(entity.obj)
                return getattr(node, 'GeomDisplacedMesh', None) if node is not None else None
            node = _subdivNode(entity.obj)
            return getattr(node, 'GeomStaticSmoothedMesh', None) if node is not None else None
        node = _displacementMtlNode(entity)
        return getattr(node, 'MtlDisplacement', None) if node is not None else None

    def columns(self, key):
        if key == 'SUBDIV':
            # Distinct ids (subdiv_*) so visibility toggles are independent from the Object
            # Displacement group, where the same attrs are default-hidden.
            return [
                _COL_OBJ_SELECT, _COL_OBJ_NAME,
                ColumnSpec('subdiv_use_globals', "Use Globals", attr='use_globals', width=0.9),
                ColumnSpec('subdiv_view_dep', "View Dep.", draw=_makeGlobalOverrideCell('view_dep'), width=0.9),
                ColumnSpec('subdiv_edge_length', "Edge Length", draw=_makeGlobalOverrideCell('edge_length'), width=1.4),
                ColumnSpec('subdiv_max_subdivs', "Max Subdivs", draw=_makeGlobalOverrideCell('max_subdivs'), width=1.4),
                ColumnSpec('preserve_map_borders', "Map Borders", attr='preserve_map_borders', width=1.8, defaultHidden=True),
                ColumnSpec('preserve_geometry_borders', "Geom Borders", attr='preserve_geometry_borders', width=0.9, defaultHidden=True),
                ColumnSpec('classic_catmark', "Classic Catmark", attr='classic_catmark', width=0.9, defaultHidden=True),
            ]

        if key == 'MATERIAL':
            # MtlDisplacement carries the same subdivision-quality settings (use_globals /
            # edge_length / max_subdivs), exposed here too. Distinct ids (mtl_*) keep their
            # visibility independent from the geometry groups. MtlDisplacement has no view_dep.
            return [
                ColumnSpec('select', "", draw=drawMaterialUsersCell, fixedWidth=1.5, center=True),
                ColumnSpec('name', "Name", draw=drawNameCell, width=2.0),
                ColumnSpec('displacement_amount', "Amount", attr='displacement_amount', width=1.4),
                ColumnSpec('displacement_shift', "Shift", attr='displacement_shift', width=1.4),
                ColumnSpec('mtl_use_globals', "Use Globals", attr='use_globals', width=0.9),
                ColumnSpec('mtl_edge_length', "Edge Length", draw=_makeGlobalOverrideCell('edge_length'), width=1.4),
                ColumnSpec('mtl_max_subdivs', "Max Subdivs", draw=_makeGlobalOverrideCell('max_subdivs'), width=1.4),
                ColumnSpec('use_bounds', "Use Bounds", attr='use_bounds', width=0.9, defaultHidden=True),
                ColumnSpec('min_bound_float', "Min Bound", draw=_makeBoundCell('min_bound_float'), width=1.2, defaultHidden=True),
                ColumnSpec('max_bound_float', "Max Bound", draw=_makeBoundCell('max_bound_float'), width=1.2, defaultHidden=True),
            ]

        return [
            _COL_OBJ_SELECT, _COL_OBJ_NAME,
            ColumnSpec('type', "Type", attr='type', width=1.8),
            ColumnSpec('displacement_texture', "Texture", draw=_drawDisplacementTexture, width=2.0, defaultHidden=True),
            ColumnSpec('displacement_amount', "Amount", attr='displacement_amount', width=1.4),
            ColumnSpec('displacement_shift', "Shift", attr='displacement_shift', width=1.4),
            ColumnSpec('use_water_level', "Water Lvl", attr='use_water_level', width=0.9),
            ColumnSpec('water_level', "Water Level", draw=_drawWaterLevel, width=1.4),
            ColumnSpec('keep_continuity', "Keep Cont.", attr='keep_continuity', width=0.9),
            ColumnSpec('use_globals', "Use Globals", attr='use_globals', width=0.9, defaultHidden=True),
            ColumnSpec('view_dep', "View Dep.", draw=_makeGlobalOverrideCell('view_dep'), width=0.9, defaultHidden=True),
            ColumnSpec('edge_length', "Edge Length", draw=_makeGlobalOverrideCell('edge_length'), width=1.4, defaultHidden=True),
            ColumnSpec('max_subdivs', "Max Subdivs", draw=_makeGlobalOverrideCell('max_subdivs'), width=1.4, defaultHidden=True),
            ColumnSpec('static_displacement', "Generation", attr='static_displacement', width=1.6, defaultHidden=True),
        ]


category = DisplacementCategory()
