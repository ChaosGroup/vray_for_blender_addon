# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

""" Apply a Chaos Scatter preset to a ChaosScatterSettings propgroup.

    The preset arrives as the parameters of a GeomScatter plugin the server filled from a Cosmos
    .mbc config (GeomUtils::readScatterPreset), i.e. exactly the dict params.buildScatterParams
    produces - so this module is its inverse and reads the same classification sets, which is what
    keeps the two from drifting.

    Not applied here, because they are not the preset's to decide:
      - what to scatter on (targets) and what to scatter (models) - the importer resolves those
        from the scene and from the packages the preset references
      - params.PINNED_PARAMS: host conventions (Blender is Z-up, UV-aligned tangents)
      - params.COMPAT_FLAGS: handled through cs.preset_compat instead, so one flag covers them
      - params.OWNED_ATTRS: list-typed attributes both emitters resolve from Blender data
"""

from chaos_scatter import params


def applyPresetParams(cs, attrs: dict) -> tuple[int, list[str]]:
    """ Write the preset's parameter values onto the propgroup.

        Returns (applied count, list of notes worth showing the user).
    """
    notes = []
    index = _fieldIndex(cs)
    applied = 0

    for name, value in attrs.items():
        if name in params.PINNED_PARAMS or name in params.COMPAT_FLAGS \
                or name in params.OWNED_ATTRS:
            continue

        entry = index.get(name)
        if entry is None:
            # Either an attribute the propgroup does not model, or one of the few the core leaves
            # to the integration. Not an error: the server sends every GeomScatter parameter.
            continue

        group, prop = entry
        coerced, ok = _coerce(prop, value)
        if not ok:
            notes.append(f"'{name}' has a value this version cannot represent ({value!r})")
            continue

        setattr(group, prop.identifier, coerced)
        applied += 1

    # A preset authored with a custom falloff curve would need the curve widget rebuilt, not just
    # the serialized samples, so it is not imported yet. Say so instead of silently flattening it.
    if _hasCustomCurve(attrs):
        notes.append("the preset's falloff curve is not imported; the default curve is used")

    # The preset's own compatibility semantics, see params.PRESET_COMPAT_FLAGS.
    cs.preset_compat = True

    return applied, notes


def _fieldIndex(cs) -> dict:
    """ GeomScatter attribute name -> (propgroup, property) over the same groups and with the same
        exclusions as params._emitGroup, so only fields the exporter emits can be written back.
    """
    index = {}
    for group in (cs, *(getattr(cs, name) for name in params.EMITTED_GROUPS)):
        for prop in group.bl_rna.properties:
            name = prop.identifier
            if prop.is_readonly or name in ('rna_type', 'name'):
                continue
            if name in params._SKIP_RESOLVED or name in params._SKIP_UI:
                continue
            if prop.type in ('POINTER', 'COLLECTION'):
                continue
            index.setdefault(name, (group, prop))
    return index


def _coerce(prop, value):
    """ Turn a plugin value into what the propgroup field takes - the inverse of the type dispatch
        in params._emitGroup. Returns (value, ok).
    """
    match prop.type:
        case 'ENUM':
            # The propgroup models GeomScatter's int enums as their decimal identifiers ('0', '1',
            # ...). A preset may still carry a mode this version does not offer.
            identifier = str(int(value))
            return identifier, (identifier in prop.enum_items)
        case 'BOOLEAN':
            return bool(value), True
        case 'INT':
            return int(value), True
        case 'FLOAT':
            if getattr(prop, 'is_array', False):
                items = tuple(float(v) for v in value)
                return items, len(items) == prop.array_length
            if prop.identifier in params.PERCENT_FIELDS:
                return float(value) * params.PERCENT_SCALE, True
            return float(value), True
        case 'STRING':
            return str(value), True
    return None, False


def _hasCustomCurve(attrs: dict) -> bool:
    """ True when a falloff curve in the preset is anything but the neutral constant-1 curve. """
    for name in ('look_at_falloff_curve', 'surface_altitude_limit_falloff_curve'):
        points = attrs.get(name)
        if not points:
            continue
        if any(abs(float(point[1]) - 1.0) > 1e-4 for point in points):
            return True
    return False
