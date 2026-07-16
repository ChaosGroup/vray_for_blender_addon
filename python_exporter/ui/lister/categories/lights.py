# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

# Lights category: lists every light grouped by V-Ray plugin type, with type-specific
# columns. Light data is edited through lib_utils.getLightPropGroup(), covering both
# non-node and node-mode lights; native Blender lights get their own group.

import bpy

from vray_blender.lib import lib_utils
from vray_blender.ui.lister.core import (
    ListerCategory, ColumnSpec, drawSelectCell, drawNameCell, makeStatusCell, makeFileCell, drawTextureRef,
)


_GROUP_LABELS = {
    'LightRectangle':  "Rectangle Lights",
    'LightSphere':     "Sphere Lights",
    'LightDome':       "Dome Lights",
    'LightMesh':       "Mesh Lights",
    'LightIES':        "IES Lights",
    'SunLight':        "Sun Lights",
    'LightSpot':       "Spot Lights",
    'LightOmni':       "Omni Lights",
    'MayaLightDirect': "Direct Lights",
    'LightAmbient':    "Ambient Lights",
}

_GROUP_ORDER = [
    'LightRectangle', 'LightSphere', 'LightDome', 'LightMesh', 'LightIES',
    'SunLight', 'LightSpot', 'LightOmni', 'MayaLightDirect', 'LightAmbient',
]

# Per-pass memos of each light's propgroup and color socket (resolving either scans the
# node tree, and both are queried several times per redraw). Cleared at the start of
# every pass (LightsCategory.enumerate).
_propGroupCache: dict = {}
_colorSocketCache: dict = {}


def _lightColorSocket(light):
    """ The color_colortex input socket on a node-mode light's V-Ray node, or None.
        This meta socket (VRaySocketColorTexture) is what actually drives the light
        color: its own value when unlinked, or a connected texture when linked. """
    key = light.name_full
    if key in _colorSocketCache:
        return _colorSocketCache[key]

    sock = None
    ntree = getattr(light, 'node_tree', None)
    if ntree is not None:
        from vray_blender.nodes.utils import getNodeByType
        from vray_blender.exporting.tools import getInputSocketByAttr
        node = getNodeByType(ntree, f'VRayNode{lib_utils.getLightPluginType(light)}')
        sock = getInputSocketByAttr(node, 'color_colortex') if node is not None else None

    _colorSocketCache[key] = sock
    return sock


def _drawColor(row, obj, propGroup):
    light = obj.data
    if propGroup is None:
        return

    props = propGroup.bl_rna.properties

    # Mode toggle and RGB color attribute mirror what the light's own UI panel binds
    # (its templateColorTemperature args). Most lights use 'color_mode' + the
    # 'color_colortex' meta; SunLight uses 'color_temp_mode' + 'filter_color'.
    modeAttr  = 'color_temp_mode' if 'color_temp_mode' in props else 'color_mode'
    colorAttr = 'color_colortex'  if 'color_colortex'  in props else 'filter_color'

    # Color / Temperature toggle in a fixed-width box (wide enough for the longest label, so
    # the value that follows starts at the same x in every row regardless of mode). A fixed
    # width - rather than a fraction of the column - keeps the labels readable even as the
    # column is narrowed. Lights without a mode toggle draw the value into the whole cell.
    valueArea = row
    if modeAttr in props:
        sub = row.row(align=True)
        sub.ui_units_x = 6.0
        sub.prop(propGroup, modeAttr, text="")

    if modeAttr in props and getattr(propGroup, modeAttr) != '0':
        # Kelvin temperature: the value plus a swatch of the resolved color, mirroring the
        # light panel's temperature mode (minus its "set as color" button).
        if 'temperature' in props:
            from vray_blender.lib import color_utils
            from vray_blender.ui import icons
            valueArea.prop(propGroup, 'temperature', text="")
            temp = max(800.0, min(12000.0, float(getattr(propGroup, 'temperature'))))
            valueArea.label(text="", icon_value=icons.getSolidColorIcon(color_utils.kelvinToRGB(temp)))
        return

    # RGB. A node-mode light drives its color through the color_colortex socket, not the
    # propgroup, so edit the socket and show a linked texture's name read-only.
    sock = _lightColorSocket(light)
    if sock is not None:
        if sock.is_linked and sock.links:
            drawTextureRef(valueArea, sock.links[0].from_node)
        else:
            valueArea.prop(sock, 'value', text="")
        return

    # Non-node light: the color lives in the meta/filter property the panel binds and the
    # exporter reads. The raw 'color' attribute only backs the color_colortex meta and is
    # suppressed from export, so editing it would have no effect on the light.
    if colorAttr in props:
        valueArea.prop(propGroup, colorAttr, text="")


def _intensityAttr(light):
    """ The propgroup attribute that holds a light's intensity-like value, per type. """
    key = lib_utils.getLightPluginType(light)
    return {'SunLight': 'intensity_multiplier', 'LightIES': 'power'}.get(key, 'intensity')


def _drawIntensity(row, obj, propGroup):
    """ One shared 'Intensity' cell across light types: Sun edits its intensity multiplier,
        IES its power, every other light its intensity - so the value lines up in a single
        aligned column instead of a separate, mostly-blank column per type. """
    attr = _intensityAttr(obj.data)
    if propGroup is not None and attr in propGroup.bl_rna.properties:
        row.prop(propGroup, attr, text="")


def _intensityValue(obj, propGroup):
    """ Sort key for the merged Intensity column: the dispatched attr's value per light type. """
    attr = _intensityAttr(obj.data)
    return getattr(propGroup, attr, None) if propGroup is not None else None


# Shared column descriptors.
_COL_SELECT = ColumnSpec('select', "", draw=drawSelectCell, fixedWidth=1.5, center=True)
_COL_ON     = ColumnSpec('on', "On", attr='enabled', width=0.6)
_COL_NAME   = ColumnSpec('name', "Name", draw=drawNameCell, width=1.4)
_COL_COLOR  = ColumnSpec('color', "Color", draw=_drawColor, width=0.6)
# Type-dispatched intensity column: Sun multiplier / IES power / others intensity, in one slot.
_COL_INTENSITY = ColumnSpec('intensity', "Intensity", draw=_drawIntensity, sortValue=_intensityValue, width=1.2)
_COL_SHADOWS   = ColumnSpec('shadows', "Shadows", attr='shadows', width=0.7)
_COL_INVISIBLE = ColumnSpec('invisible', "Invisible", attr='invisible', width=0.7)
_COL_DIFFUSE   = ColumnSpec('affectDiffuse', "Diffuse", attr='affectDiffuse', width=0.7)
_COL_SPECULAR  = ColumnSpec('affectSpecular', "Specular", attr='affectSpecular', width=0.7)
_COL_REFLECT   = ColumnSpec('affectReflections', "Reflect", attr='affectReflections', width=0.7)
_COL_ATMOS     = ColumnSpec('affectAtmospherics', "Atmos", attr='affectAtmospherics', width=0.7)

# Default-hidden parameters; switch them on from the column picker.
_COL_SHADOWBIAS  = ColumnSpec('shadowBias', "Shadow Bias", attr='shadowBias', width=1.4, defaultHidden=True)
_COL_SHADOWCOLOR = ColumnSpec('shadowColor', "Shadow Color", attr='shadowColor', width=1.4, defaultHidden=True)
_COL_DOUBLESIDED = ColumnSpec('doubleSided', "2-Sided", attr='doubleSided', width=0.8, defaultHidden=True)
_COL_CAUSTICSUBDIVS = ColumnSpec('causticSubdivs', "Caustic Subdivs", attr='causticSubdivs', width=1.5, defaultHidden=True)
_COL_CAUSTICMULT    = ColumnSpec('causticMult', "Caustic Mult", attr='causticMult', width=1.3, defaultHidden=True)


class VRAY_MT_lister_convert_lights(bpy.types.Menu):
    bl_idname = "VRAY_MT_lister_convert_lights"
    bl_label = "Convert to V-Ray"

    def draw(self, context):
        layout = self.layout
        op = layout.operator("vray.convert_lights", text="Convert Selected to V-Ray", icon='LIGHT_DATA')
        op.selected_only = True
        op = layout.operator("vray.convert_lights", text="Convert All to V-Ray", icon='LIGHT_DATA')
        op.selected_only = False


def getRegClasses():
    return (VRAY_MT_lister_convert_lights,)


class LightsCategory(ListerCategory):
    id = 'LIGHTS'
    label = "Lights"
    icon = 'LIGHT'
    enumIndex = 0

    def enumerate(self, context):
        # New draw pass: drop the memos so light edits/deletions are re-resolved
        _propGroupCache.clear()
        _colorSocketCache.clear()
        return [o for o in context.scene.objects if o.type == 'LIGHT']

    def drawHeader(self, layout, context, entities):
        # Convert native Blender lights to V-Ray. Drawn into the shared right-aligned header
        # row (see core.drawLister); the fixed width keeps the label from truncating.
        sub = layout.row()
        sub.ui_units_x = 9.5
        sub.menu("VRAY_MT_lister_convert_lights", text="Convert to V-Ray", icon='LIGHT_DATA')

    def groupKey(self, obj):
        # Native Blender lights map to a V-Ray plugin type (e.g. POINT -> LightOmni)
        # and carry a full propgroup, so they group and edit like explicit V-Ray lights.
        return lib_utils.getLightPluginType(obj.data)

    def groupLabel(self, key):
        return _GROUP_LABELS.get(key, key)

    def groupOrder(self):
        return _GROUP_ORDER

    def pickerSections(self, context, state):
        # Column picker: a common "Lights" section, type-specific extras (Sun sky params,
        # IES file/power), and the caustic params.
        from vray_blender.ui.lister import core
        caustic = {'causticSubdivs', 'causticMult'}
        allCommon = [c for c in core._effectiveColumns(self, 'LightRectangle')
                     if c.id not in ('select', 'name')]
        common = [c for c in allCommon if c.id not in caustic]
        commonIds = {c.id for c in allCommon}  # includes caustics, so type sections skip them

        sections = [("Lights", common)]
        for key, label in (('SunLight', "Sun"), ('LightIES', "IES")):
            extra = [c for c in self.columns(key)
                     if c.id not in commonIds and c.id not in ('select', 'name')]
            if extra:
                sections.append((label, extra))
        causticCols = [c for c in allCommon if c.id in caustic]
        if causticCols:
            sections.append(("Caustics", causticCols))
        return sections

    def getPropGroup(self, obj):
        light = obj.data
        key = light.name_full
        if key in _propGroupCache:
            return _propGroupCache[key]
        propGroup = lib_utils.getLightPropGroup(light, lib_utils.getLightPluginType(light))
        _propGroupCache[key] = propGroup
        return propGroup

    def columns(self, key):
        # Shared type-dispatched 'Intensity' column (Sun multiplier / IES power / others).
        cols = [_COL_SELECT, _COL_ON, _COL_NAME, _COL_COLOR, _COL_INTENSITY]

        if key == 'SunLight':
            cols += [
                ColumnSpec('size_multiplier', "Size Mult", attr='size_multiplier', width=1.4, defaultHidden=True),
                ColumnSpec('turbidity', "Turbidity", attr='turbidity', width=1.4, defaultHidden=True),
                ColumnSpec('ozone', "Ozone", attr='ozone', width=1.2, defaultHidden=True),
                ColumnSpec('sky_model', "Sky Model", attr='sky_model', width=1.8, defaultHidden=True),
                ColumnSpec('filter_color', "Filter", attr='filter_color', width=1.4, defaultHidden=True),
            ]
        elif key == 'LightIES':
            cols += [
                ColumnSpec('ies_status', "Status", draw=makeStatusCell('IES', 'ies_file'), width=1.0, center=True),
                # Fixed width, not relative: the IES File column is exclusive to this group, so
                # a relative width would overflow the IES table here and compress its other
                # columns out of alignment. A fixed width reserves the same space everywhere.
                ColumnSpec('ies_file', "IES File", attr='ies_file', draw=makeFileCell('IES', 'ies_file'), fixedWidth=16.0),
                ColumnSpec('filter_color', "Filter", attr='filter_color', width=1.4, defaultHidden=True),
            ]
        else:
            # Fixed width, same reason as IES File above: the Units column is absent from the
            # IES group, so a relative width would collapse in its blank cell.
            cols.append(ColumnSpec('units', "Units", attr='units', fixedWidth=11.0))

        cols += [
            _COL_SHADOWS, _COL_INVISIBLE,
            _COL_DIFFUSE, _COL_SPECULAR, _COL_REFLECT, _COL_ATMOS,
            _COL_SHADOWBIAS, _COL_SHADOWCOLOR, _COL_DOUBLESIDED,
            _COL_CAUSTICSUBDIVS, _COL_CAUSTICMULT,
        ]
        return cols


category = LightsCategory()
