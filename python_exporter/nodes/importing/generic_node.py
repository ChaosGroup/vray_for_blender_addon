# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

""" Import of V-Ray plugins for which the addon generates no node class.

    Nothing here is per-plugin: one VRayNodeGenericPlugin node is built from the parameters
    the .vrscene actually set, with a socket per value, a socket per list element, and an
    input socket for every referenced plugin. See nodes/specials/generic_plugin.py for why
    the sockets rather than a property group hold the values.

    A plugin description is used when V-Ray ships one - it gives the socket its label, its
    exact type and its enum items - but nothing here requires one.
"""

import bpy

from vray_blender import debug
from vray_blender.lib import attribute_types, attribute_utils
from vray_blender.nodes import utils as NodeUtils
from vray_blender.nodes.sockets import addInput, addOutput
from vray_blender.plugins import findPluginModule


GENERIC_NODE_TYPE = 'VRayNodeGenericPlugin'

# Plugin categories that live in a shader node tree. Everything else (geometry, lights,
# settings, render channels) is imported by dedicated code and never falls back here.
SHADER_CATEGORIES = ('TEXTURE', 'UVWGEN', 'MATERIAL', 'BRDF')

# The output socket a generic node of each category exposes. The names match
# engine._getDefaultOutputSocketName so that referencing plugins find them.
_CATEGORY_OUTPUTS = {
    'TEXTURE':  ("Color",    'VRaySocketColor'),
    'UVWGEN':   ("Mapping",  'VRaySocketCoords'),
    'BRDF':     ("BRDF",     'VRaySocketBRDF'),
    'MATERIAL': ("Material", 'VRaySocketMtl'),
}

# The input socket used for a reference, keyed on the category of the referenced plugin. Each
# matches the output socket above, which link validation enforces for BRDF references
# (nodes/links.isConnectionAllowed).
_CATEGORY_INPUTS = {
    'TEXTURE':  'VRaySocketGenericColor',
    'UVWGEN':   'VRaySocketCoords',
    'BRDF':     'VRaySocketBRDF',
    'MATERIAL': 'VRaySocketMtl',
}

# Socket types safe to use on a generic node: they store their own value. The ones left out
# (VRaySocketEnum, VRaySocketColorTexture, VRaySocketPluginUse, VRaySocketColorUse,
# VRaySocketColorRamp, the list sockets) read a property group, which a generic node has none
# of; ENUM is served by VRaySocketGenericEnum instead.
_VALUE_SOCKET_TYPES = frozenset({
    'VRaySocketAColor',
    'VRaySocketBool',
    'VRaySocketColor',
    'VRaySocketFloat',
    'VRaySocketInt',
    'VRaySocketString',
    'VRaySocketTransform',
    'VRaySocketVector',
})

# A generated node overrides a socket's limits per attribute with a class of its own
# (nodes/sockets.registerDynamicSocketClass), so the stock classes carry the limits of a
# hand-authored UI. Three of them would silently clamp an imported value - measured: Int at
# +/-1024 (a 3000-pixel bitmap width came back 1024) and Color/AColor at 0..1 (any HDR colour
# crushed to white). Float and Vector declare only soft limits, which do not clamp.
_WIDENED_SOCKETS = {
    'VRaySocketAColor': 'VRaySocketGenericAColor',
    'VRaySocketColor':  'VRaySocketGenericColor',
    'VRaySocketInt':    'VRaySocketGenericInt',
}

# Types not drawn on the node body; they are reachable from the sidebar. Mirrors
# attribute_types.HiddenNodeInputTypes for generated nodes.
_HIDDEN_SOCKET_TYPES = frozenset({
    'VRaySocketBool',
    'VRaySocketGenericEnum',
    'VRaySocketGenericInt',
    'VRaySocketString',
})

# Beyond this a list is bulk data (per-hair root colours, gradient tables), not something
# anyone would edit socket by socket. Such attributes are dropped with a warning.
_MAX_LIST_SOCKETS = 32

# The V-Ray attribute type each socket stands for. Export hands every socket a description
# built from this, so a generic node marshals its values through exactly the same socket code
# as a generated one - see exporting/node_export._exportGenericPluginNode.
_SOCKET_VRAY_TYPES = {
    'VRaySocketBRDF':          'BRDF',
    'VRaySocketBool':          'BOOL',
    'VRaySocketCoords':        'UVWGEN',
    'VRaySocketFloat':         'FLOAT_TEXTURE',
    'VRaySocketGenericAColor': 'TEXTURE',
    'VRaySocketGenericColor':  'TEXTURE',
    'VRaySocketGenericEnum':   'ENUM',
    'VRaySocketGenericInt':    'INT',
    'VRaySocketMtl':           'MATERIAL',
    'VRaySocketString':        'STRING',
    'VRaySocketTransform':     'TRANSFORM',
    'VRaySocketVector':        'VECTOR_TEXTURE',
}


def pluginCategory(pluginType: str):
    """ The category of a plugin: from its description when V-Ray ships one, otherwise from
        the naming convention every V-Ray plugin follows. Returns None when neither applies.

        A plugin with no description at all is left alone rather than guessed at - the
        MaterialX node definitions ('ND_add_vector3' and the like) land here, and inventing a
        category for them would put an unverifiable node in the tree.
    """
    if pluginModule := findPluginModule(pluginType):
        return getattr(pluginModule, 'TYPE', None)

    name = pluginType.lower()
    if 'uvwgen' in name:
        return 'UVWGEN'
    if name.startswith('brdf'):
        return 'BRDF'
    if name.startswith('mtl'):
        return 'MATERIAL'
    if name.startswith('tex'):
        return 'TEXTURE'
    return None


def isImportableAsGenericNode(pluginType: str):
    return pluginCategory(pluginType) in SHADER_CATEGORIES


def defaultOutputName(pluginType: str):
    """ The output a generic node built for this plugin exposes, or None if it would not be
        imported as one. Callers that resolve an output socket name need this for a plugin
        V-Ray ships no description for, since there is nothing else to read a name from.
    """
    outputDesc = _CATEGORY_OUTPUTS.get(pluginCategory(pluginType))
    return outputDesc[0] if outputDesc else None


def ensureOutput(node: bpy.types.Node, socketName: str):
    """ Add an output socket a referencing plugin asked for by name. A generic node only
        carries its category's default output until something links to another one.
    """
    if socketName in node.outputs:
        return node.outputs[socketName]

    # Export appends the '::selector' of a non-default output from the socket's attribute
    # (exporting/tools.getOutSocketSelector), so the attribute the name was derived from has
    # to be recovered. Only a described plugin can name an output in the first place.
    attrName, sockType = '', 'VRaySocketColor'

    if pluginModule := findPluginModule(node.vray_plugin):
        for outDesc in getattr(pluginModule, 'Outputs', ()):
            if attribute_utils.formatAttributeName(outDesc['attr']) == socketName:
                attrName = outDesc['attr']
                sockType = attribute_types.getSocketType(outDesc['type']) or sockType
                break

    return addOutput(node, sockType, socketName, attrName)


def createGenericNode(importContext, pluginDesc: dict):
    """ Build a VRayNodeGenericPlugin for one .vrscene plugin. """
    from vray_blender.nodes.importing.engine import _createLinkedNode

    pluginType = pluginDesc['ID']
    category = pluginCategory(pluginType)

    node = NodeUtils.createNode(importContext.nodeTree, GENERIC_NODE_TYPE, pluginDesc['Name'])
    node.vray_plugin = pluginType
    node.vray_type = category
    # The node's name is the plugin instance name, so show the type in the header instead.
    node.label = pluginType

    if outputDesc := _CATEGORY_OUTPUTS.get(category):
        addOutput(node, outputDesc[1], outputDesc[0])

    pluginModule = findPluginModule(pluginType)

    # Sockets are created for every attribute first, and only then linked: creating a link
    # recurses into the referenced plugin, which may reference this one back.
    pendingLinks = []

    for attrName, attrValue in pluginDesc['Attributes'].items():
        attrDesc = attribute_utils.getAttrDesc(pluginModule, attrName) if pluginModule else None

        if attrDesc and (attrDesc['type'] in attribute_types.NodeOutputTypes):
            continue

        try:
            _addAttribute(importContext, node, attrName, attrValue, attrDesc, pendingLinks)
        except Exception as ex:
            debug.printExceptionInfo(ex, f"nodes.importing.generic_node ({pluginType}.{attrName})")

    for sock, outputName, targetPlugin in pendingLinks:
        _createLinkedNode(importContext, sock, outputName, targetPlugin)

    return node


def _addAttribute(importContext, node, attrName, attrValue, attrDesc, pendingLinks):
    label = attribute_utils.getAttrDisplayName(attrDesc) if attrDesc \
                else attribute_utils.formatAttributeName(attrName)

    # Bulk data (bitmap pixels, mesh buffers) arrives as a numpy view and is never editable
    # as sockets. The plugin still imports; only this attribute is left at its V-Ray default.
    if hasattr(attrValue, 'shape'):
        debug.printWarning(f"Import: '{node.vray_plugin}.{attrName}' holds bulk data and was not imported")
        return

    if not isinstance(attrValue, list):
        _addValueSocket(importContext, node, attrName, label, attrValue, attrDesc, pendingLinks)
        return

    # A V-Ray List: one socket per element, all sharing the attribute name.
    if len(attrValue) > _MAX_LIST_SOCKETS:
        debug.printWarning(f"Import: '{node.vray_plugin}.{attrName}' has {len(attrValue)} entries "
                           f"(limit {_MAX_LIST_SOCKETS}) and was not imported")
        return

    for i, item in enumerate(attrValue):
        _addValueSocket(importContext, node, attrName, f"{label} {i + 1}", item, attrDesc, pendingLinks)

    # Only once a socket exists: a list of references to plugins that are not part of a shader
    # graph (a Node, a geometry) produces none, and must not be exported as an empty list.
    if any(s.vray_attr == attrName for s in node.inputs):
        node.markListAttr(attrName)


def _addValueSocket(importContext, node, attrName, socketName, value, attrDesc, pendingLinks):
    if _isReferenceValue(importContext, value, attrDesc):
        # A reference the scene does not define, or one to something that is not part of a
        # shader graph (a Node, a geometry), has no socket to land in. It must not fall
        # through to the value path either - a plugin name is not a string attribute.
        if reference := _resolveReference(importContext, value):
            targetPlugin, outputName, refCategory = reference
            if sockType := _CATEGORY_INPUTS.get(refCategory):
                sock = _addSocket(node, sockType, socketName, attrName)
                pendingLinks.append((sock, outputName, targetPlugin))
        return

    if (sockType := _socketTypeForValue(value, attrDesc)) is None:
        debug.printWarning(f"Import: '{node.vray_plugin}.{attrName}' has an unsupported value "
                           f"of type {type(value).__name__} and was not imported")
        return

    # Values are deliberately not converted to Blender units here. A generic node is a
    # passthrough: export writes its sockets back verbatim, so converting on one side only
    # would scale every distance by the scene unit.
    sock = _addSocket(node, sockType, socketName, attrName)
    _setSocketValue(node, sock, value, sockType)


def _addSocket(node, sockType: str, socketName: str, attrName: str):
    # addInput resolves a per-attribute socket class when passed an attribute name. Those
    # classes exist only while V-Ray ships a description for the plugin, so a generic node
    # uses the base classes, which are always registered, and carries the attribute itself.
    sock = addInput(node, sockType, socketName, visible = sockType not in _HIDDEN_SOCKET_TYPES)
    sock.vray_attr = attrName
    return sock


def _isReferenceValue(importContext, value, attrDesc):
    """ Whether a value is a reference to another plugin rather than a string attribute. Both
        are plain strings in the parsed scene.
    """
    if not isinstance(value, str):
        return False

    # A description settles it: only the linkable types carry a reference. Without one, a
    # value that names a plugin the scene defines is taken to be a reference.
    if attrDesc:
        return attrDesc['type'] in attribute_types.NodeInputTypes

    return _resolveReference(importContext, value) is not None


def _resolveReference(importContext, value):
    """ Resolve a plugin reference to (pluginDesc, outputSocketName, category), or None when
        the scene does not define the referenced plugin.
    """
    from vray_blender.nodes.importing.engine import _getPluginFromLink

    targetPlugin, outputName = _getPluginFromLink(importContext, value)
    if targetPlugin is None:
        return None

    return targetPlugin, outputName, pluginCategory(targetPlugin['ID'])


def _socketTypeForValue(value, attrDesc):
    """ The socket class for a value. The description is preferred - it distinguishes a colour
        from a vector and knows about enums - and the value's own shape is the fallback.
    """
    sockType = None

    if attrDesc:
        if attrDesc['type'] == 'ENUM':
            return 'VRaySocketGenericEnum'

        candidate = attribute_types.getSocketType(attrDesc['type'], attrDesc.get('subtype'))
        if candidate in _VALUE_SOCKET_TYPES:
            # A colour socket holds three components, so a source that spelled the value out
            # with an alpha would lose it. Keep what the scene set.
            if (candidate == 'VRaySocketColor') and isinstance(value, tuple) and (len(value) == 4):
                candidate = 'VRaySocketAColor'
            sockType = candidate

    if sockType is None:
        sockType = _inferSocketType(value)

    return _WIDENED_SOCKETS.get(sockType, sockType)


def _inferSocketType(value):
    """ The socket class for a value V-Ray ships no description for, from its shape alone. """
    if isinstance(value, bool):
        return 'VRaySocketBool'
    if isinstance(value, int):
        return 'VRaySocketInt'
    if isinstance(value, float):
        return 'VRaySocketFloat'
    if isinstance(value, str):
        return 'VRaySocketString'

    if isinstance(value, tuple):
        # ((c0, c1, c2), offset) is a transform and ((c0, c1, c2)) a matrix; both land in a
        # transform socket. A flat tuple is a colour or a vector.
        if value and isinstance(value[0], tuple):
            return 'VRaySocketTransform'
        if len(value) == 4:
            return 'VRaySocketAColor'
        if len(value) == 3:
            return 'VRaySocketColor'
        if len(value) == 2:
            return 'VRaySocketVector'

    return None


def _setSocketValue(node, sock, value, sockType):
    from vray_blender.nodes.importing.engine import _assignSocketValue, _setMatrixSocketValue

    if sockType == 'VRaySocketTransform':
        _setMatrixSocketValue(node, sock.name, value)
    elif sockType == 'VRaySocketGenericEnum':
        # V-Ray enum values are ints in the .vrscene and strings in the descriptions.
        sock.value = str(value)
    else:
        _assignSocketValue(sock, value)


def socketAttrDesc(sock: bpy.types.NodeSocket):
    """ The attribute description a socket of a generic node exports through. The sockets do
        their own marshalling (a colour with alpha wraps in AColor, a transform converts to a
        matrix, ...), and all they need from a description is the name and the type.
    """
    return {
        'attr': sock.vray_attr,
        'type': _SOCKET_VRAY_TYPES.get(sock.vray_socket_base_type, 'TEXTURE'),
    }
