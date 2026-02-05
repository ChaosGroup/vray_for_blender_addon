import bpy

from vray_blender.lib.blender_utils import getFullPathToNode, resolveNodeFromPath
from vray_blender.lib.defs import AColor, ExporterContext, PluginDesc
from vray_blender.lib.export_utils import exportPluginCommon
from vray_blender.nodes import utils as NodeUtils
from vray_blender.nodes.specials import gradient_ramp
from vray_blender.plugins.texture import TexSoftbox


# Instances of template_color_ramp() for which updates from the ramp itself have to be processed.
# This list works with registerColorRamps() and syncColorRamps() callbacks called from events.py
# Item format: {tuple(node_name, texture_name) : tuple(Node, texAttrName, Texture)
COLOR_RAMPS: dict[tuple[str, str], tuple[bpy.types.Node, str, bpy.types.Texture]] = {}

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

    for i,rampElement in enumerate(ramp.elements):
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

    _subscribeToRampUpdates(node, tex)
    return tex


def _onRampUpdate(node: bpy.types.Node):
    node.id_data.update_tag()


def _subscribeToRampUpdates(node: bpy.types.Node, tex: bpy.types.Texture):
    """Subscribes the message bus for changes to the texture."""

    # In Blender versions < 5.0 this subscription is redundant, but
    # it does affect the operation.
    bpy.msgbus.subscribe_rna(
        key=tex,
        owner=tex,
        args=(node,),
        notify=_onRampUpdate
    )


def registerColorRamp(node: bpy.types.Node, rampAttrTexName: str, rampTexture: bpy.types.Texture):
    """ Add ColorRamp to a global list of ramps for which an update callback
        will be called in the module of their host plugin.
    """
    COLOR_RAMPS[(getFullPathToNode(node), rampTexture.name)] = (node, rampAttrTexName, rampTexture)
    _subscribeToRampUpdates(node, rampTexture)


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


def getColorRampsForPlugin(pluginType: str):
    """ Get all (node,texture) tuples registered for this plugin type. """
    return [r for r in COLOR_RAMPS.values() if r[0].vray_plugin == pluginType]


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
