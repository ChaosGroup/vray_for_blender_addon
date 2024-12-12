import bpy

from vray_blender.lib.defs import AColor, ExporterContext, PluginDesc
from vray_blender.lib.export_utils import exportPluginCommon
from vray_blender.nodes import utils as NodeUtils
from vray_blender.plugins.texture import TexGradRamp, TexSoftbox



# Instances of template_color_ramp() for which updates from the ramp itself have to be processed.
# This list works with registerColorRamps() and syncColorRamps() callbacks called from events.py
# Item format: tuple(Node, texAttrName, Texture)
COLOR_RAMPS: list[tuple[bpy.types.Node, str, bpy.types.Texture]] = []

def copyColorRamp(ramp, rampCopy):
    """ Copy the properties of a color ramp """

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


def createRampTexture(node, attrName='texture'):
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

    return tex


def registerColorRamp(node: bpy.types.Node, rampAttrTexName: str, rampTexture: bpy.types.Texture):
    """ Add ColorRamp to a global list of ramps for which an update callback will be called in the
        module of their host plugin.
    """ 
    global COLOR_RAMPS
    COLOR_RAMPS.append((node, rampAttrTexName, rampTexture))


def unregisterColorRamp(node: bpy.types.Node, rampAttrTexName: str, rampTexture: bpy.types.Texture):
    """ Remove ColorRamp from a global list of ramps for which an update callback will be called in the
        module of their host plugin.
    """
    global COLOR_RAMPS
    
    ramp = (node, rampAttrTexName, rampTexture)
    if ramp in COLOR_RAMPS:
        COLOR_RAMPS.remove(ramp)


def clearColorRampRegistrations():
    global COLOR_RAMPS
    COLOR_RAMPS.clear()

    
def registerColorRamps():
    """ Register all color ramps in the scene for receiving updates """
    clearColorRampRegistrations()
    TexGradRamp.registerColorRamps()
    TexSoftbox.registerColorRamps()


def syncColorRamps():
    """ Sync (update) all color ramps in the scene """
    TexGradRamp.syncColorRamps()
    TexSoftbox.syncColorRamps()


def getColorRampsForPlugin(pluginType: str):
    """ Get all (node,texture) tuples registered for this plugin type """
    global COLOR_RAMPS
    return [r for r in COLOR_RAMPS if r[0].vray_plugin == pluginType]


def exportRampAttributes(ctx: ExporterContext, pluginDesc, texAttrName, colAttrName, posAttrName, interpAttrName=""):
    """ Fills plugin color ramp attributes from blender Texture.color_ramp and exports
        TexAColor plugins for the ramp stops.
    """

    node = NodeUtils.getNodeOfPropGroup(pluginDesc.vrayPropGroup)
    tex = getattr(node, texAttrName)

    if tex:
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
            interpolation = {
                'CONSTANT': 0,
                'LINEAR': 1,
                'EASE': 2,
                'CARDINAL': 3,
                'B_SPLINE': 4
            }.get(ramp.interpolation, 1)
            pluginDesc.setAttribute(interpAttrName, interpolation)