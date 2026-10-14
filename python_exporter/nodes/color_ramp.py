# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy

from vray_blender.lib.blender_utils import getFullPathToNode, resolveNodeFromPath, tagUsersForUpdate
from vray_blender.lib.defs import AColor, ExporterContext, PluginDesc
from vray_blender.lib.export_utils import exportPluginCommon
from vray_blender.nodes import utils as NodeUtils
from vray_blender.nodes.specials import gradient_ramp
from vray_blender.plugins.texture import TexSoftbox


# Instances of template_color_ramp() for which updates from the ramp itself have to be processed.
# This list works with registerColorRamps() and syncColorRamps() callbacks called from events.py
# Item format: {tuple(node_name, texture_name) : tuple(Node, texAttrName, Texture)
COLOR_RAMPS: dict[tuple[str, str], tuple[bpy.types.Node, str, bpy.types.Texture]] = {}

_COLOR_RAMP_ELEMENT_OWNER = object()


def copyColorRamp(ramp: bpy.types.ColorRamp, rampCopy: bpy.types.ColorRamp):
    """ Copy the properties of a color ramp. """

    rampCopy.color_mode = ramp.color_mode
    rampCopy.interpolation = ramp.interpolation

    # Create ramp elements
    # A new ramp already has 2 elements
    elementsToCreate = len(ramp.elements) - 2
    for i in range(elementsToCreate):
        # We will setup proper position later
        rampCopy.elements.new(0.0)

    for i, rampElement in enumerate(ramp.elements):
        el = rampCopy.elements[i]
        el.color    = rampElement.color
        el.position = rampElement.position


def createRampTexture(node: bpy.types.Node, attrName: str = 'texture'):
    """ Create a new Texture attribute with a color ramp enabled and add it to the node. """
    texName = NodeUtils.createFakeName()

    tex = bpy.data.textures.new(texName, 'NONE')
    tex.use_color_ramp = True
    tex.use_fake_user = True

    setattr(node, attrName, tex)
    setattr(node, f'{attrName}_name', texName)

    tex.color_ramp.color_mode = 'RGB'
    tex.color_ramp.interpolation = 'LINEAR'
    tex.color_ramp.elements[0].color = (0.0, 0.0, 0.0, 1.0)
    tex.color_ramp.elements[1].color = (1.0, 1.0, 1.0, 1.0)

    _subscribeToRampUpdates(tex, attrName)
    return tex


def _onRampUpdate(node: bpy.types.Node):
    from vray_blender.preview.thumbs import service as thumbService
    thumbService.invalidateNode(node)

    # If the node does not reach the tree's output node, its ramp does not affect the render
    # result, so there is nothing to re-export.
    if not NodeUtils.isNodeConnectedToTreeOutput(node):
        return

    node.id_data.update_tag()
    tagUsersForUpdate(node.id_data)


def _onRampTextureUpdate(tex: bpy.types.Texture, attrName: str):
    """ msgbus notify for a ramp texture. Finds the node by its texture because a node kept in the
        subscription args goes stale (see iterValidColorRamps).
    """
    from vray_blender.nodes.tools import iterVRayNodeTrees
    for ntree in iterVRayNodeTrees():
        for node in ntree.nodes:
            if getattr(node, attrName, None) == tex:
                _onRampUpdate(node)
                return


def _onAnyElementPositionChanged():
    for node, _attrName, _tex in iterValidColorRamps():
        _onRampUpdate(node)


def _subscribeToRampUpdates(tex: bpy.types.Texture, attrName: str):
    """Subscribes the message bus for changes to the texture."""

    # Knot drag: COLORBAND widget publishes at (Texture, color_ramp_prop) level.
    # The prop=NULL fallback in WM_msg_publish_rna_params matches this subscription.
    bpy.msgbus.subscribe_rna(
        key=tex,
        owner=tex,
        args=(tex, attrName),
        notify=_onRampTextureUpdate
    )

    # Interpolation, color_mode: published at (ColorRamp, prop) level.
    # key=tex.color_ramp creates (RNA_ColorRamp, owner_id=tex, data=&coba, prop=NULL),
    # matched via the prop=NULL fallback when any ramp property changes.
    bpy.msgbus.subscribe_rna(
        key=tex.color_ramp,
        owner=tex,
        args=(tex, attrName),
        notify=_onRampTextureUpdate
    )


def registerColorRamp(node: bpy.types.Node, rampAttrTexName: str, rampTexture: bpy.types.Texture):
    """ Add ColorRamp to a global list of ramps for which an update callback
        will be called in the module of their host plugin.
    """
    COLOR_RAMPS[(getFullPathToNode(node), rampTexture.name)] = (node, rampAttrTexName, rampTexture)
    # Clear any existing subscription before adding a new one to avoid duplicate
    # subscriptions when both createRampTexture and registerColorRamp are called together.
    bpy.msgbus.clear_by_owner(rampTexture)
    _subscribeToRampUpdates(rampTexture, rampAttrTexName)


def unregisterColorRamp(node: bpy.types.Node, rampAttrTexName: str, rampTexture: bpy.types.Texture):
    """ Remove ColorRamp from a global list of ramps for which an update callback
        will be called in the module of their host plugin.
    """
    ramp = (getFullPathToNode(node), rampTexture.name)
    if ramp in COLOR_RAMPS:
        bpy.msgbus.clear_by_owner(rampTexture)
        del COLOR_RAMPS[ramp]


def registerColorRamps():
    """ Register all color ramps in the scene for receiving updates. """

    # This function is only called when the scene gets (re)loaded.
    # It is safe to clear any existing registration data
    # NOTE: The msgbus subscriptions are cleared automatically in this case
    COLOR_RAMPS.clear()

    TexSoftbox.registerColorRamps()
    gradient_ramp.registerColorRamps()

    # In Blender 5.0 ColorRampElement instances can't be used as subscribe_rna keys,
    # so the type class is used instead to catch all element position changes.
    bpy.msgbus.clear_by_owner(_COLOR_RAMP_ELEMENT_OWNER)
    try:
        bpy.msgbus.subscribe_rna(
            key=(bpy.types.ColorRampElement, 'position'),
            owner=_COLOR_RAMP_ELEMENT_OWNER,
            args=(),
            notify=_onAnyElementPositionChanged,
        )
    except Exception:
        pass


def syncColorRamps():
    """ Sync (update) all color ramps in the scene. """
    TexSoftbox.syncColorRamps()
    gradient_ramp.syncColorRamps()


def pruneColorRamps():
    ramps = list(COLOR_RAMPS.keys())

    for ramp in ramps:
        nodePath = ramp[0]
        textureName = ramp[1]
        if (not resolveNodeFromPath(nodePath)) or (textureName not in bpy.data.textures):
            if ramp in COLOR_RAMPS:
                del COLOR_RAMPS[ramp]


def iterValidColorRamps():
    """ Yield (node, texAttrName, texture) for each registered ramp, looked up by the
        (nodePath, textureName) key. Do not read the node and texture stored in COLOR_RAMPS.
        They go stale after undo/redo, file reload, scene upgrade or addon re-enable, and
        touching a stale one crashes Blender.
    """
    for (nodePath, textureName), (_node, texAttrName, _tex) in list(COLOR_RAMPS.items()):
        node = resolveNodeFromPath(nodePath)
        texture = bpy.data.textures.get(textureName)
        if (node is not None) and (texture is not None):
            yield node, texAttrName, texture


def getColorRampsForPlugin(pluginType: str):
    """ Get all (node, texAttrName, texture) tuples registered for this plugin type. """
    return [r for r in iterValidColorRamps() if r[0].vray_plugin == pluginType]


def exportRampAttributes(
        ctx: ExporterContext,
        pluginDesc: PluginDesc,
        texAttrName: str,
        colAttrName: str,
        posAttrName: str,
        interpAttrName=""
):
    """ Fills plugin color ramp attributes from blender Texture.color_ramp and
        exports TexAColor plugins for the ramp stops.
    """

    node = NodeUtils.getNodeOfPropGroup(pluginDesc.vrayPropGroup)
    tex = getattr(node, texAttrName, None)

    if not tex:
        return

    pluginName = f"{pluginDesc.name}@{texAttrName}"
    ramp = tex.color_ramp

    colors = []
    positions = []

    elemNum = 0
    for elem in ramp.elements:
        colPluginName = f"{pluginName}Pos{elemNum}"
        pos = elem.position

        texturePlugin = PluginDesc(colPluginName, "TexAColor")
        texturePlugin.setAttribute('texture', AColor(elem.color))

        colors.append(exportPluginCommon(ctx, texturePlugin))
        positions.append(pos)
        elemNum += 1

    pluginDesc.setAttribute(colAttrName, colors)
    pluginDesc.setAttribute(posAttrName, positions)

    if interpAttrName:
        # Toon material requires a list of values for compatibility.
        interpolation = {
            'CONSTANT': 0,
            'LINEAR': 1,
            'EASE': 2,
            'CARDINAL': 3,
            'B_SPLINE': 4
        }.get(ramp.interpolation, 1)

        interpolation = [interpolation] * len(positions)

        pluginDesc.setAttribute(interpAttrName, interpolation)
