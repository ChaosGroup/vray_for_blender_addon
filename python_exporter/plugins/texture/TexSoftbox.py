# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy

from vray_blender.lib import plugin_utils, export_utils
from vray_blender.lib.defs import  ExporterContext, PluginDesc
from vray_blender.nodes.tools import isVrayNodeTree
from vray_blender.nodes import color_ramp, utils as NodeUtils

plugin_utils.loadPluginOnModule(globals(), __name__)


_COLOR_RAMP_ATTRS = ("grad_vert", "grad_horiz", "grad_rad", "frame")


def nodeInit(node: bpy.types.Node):
    for attrName in _COLOR_RAMP_ATTRS:
        rampTexAttrName = _getRampTexAttrName(attrName)
        texture = color_ramp.createRampTexture(node, rampTexAttrName)
        color_ramp.registerColorRamp(node, rampTexAttrName, texture) 
    

def nodeCopy(nodeCopy: bpy.types.Node, nodeOrig: bpy.types.Node):
    for attrName in _COLOR_RAMP_ATTRS:
        rampTexAttrName = _getRampTexAttrName(attrName)
        origTex = getattr(nodeOrig, rampTexAttrName)
        if (ownTex := getattr(nodeCopy, rampTexAttrName)) and ownTex != origTex:
            color_ramp.copyColorRamp(origTex.color_ramp, ownTex.color_ramp)
        elif bpy.app.version >= (5, 1, 0):
            NodeUtils.copyFakeTexture(nodeCopy, rampTexAttrName, origTex)

    NodeUtils.fixCopiedTexturesLater()


def nodeFree(node: bpy.types.Node):
    for attrName in _COLOR_RAMP_ATTRS:
        attrTexName = _getRampTexAttrName(attrName)
        tex = getattr(node, attrTexName)
        color_ramp.unregisterColorRamp(node, attrTexName, tex)
        if tex and tex.users - tex.use_fake_user == 1:
            tex.use_fake_user = False
            bpy.data.textures.remove(tex)


# Custom function that draws color ramp
def widgetDrawGradientRamp(context: bpy.types.Context, layout, propGroup, widgetAttr):
    node = NodeUtils.getNodeOfPropGroup(propGroup)
    
    if not (texture := getattr(node, widgetAttr['name'], None)):
        # This will be the case right after the node has been copied from another node
        return
    
    box = layout.box()
    col = box.column()
    col.template_color_ramp(texture, 'color_ramp', expand=True)


def exportCustom(ctx: ExporterContext, pluginDesc: PluginDesc):
    for gradient in _COLOR_RAMP_ATTRS:
        if not getattr(pluginDesc.vrayPropGroup, f"{gradient}_on"):
            continue

        texAttrName = f"ramp_{gradient}"
        colAttrName = f"{gradient}_col"
        posAttrName = f"{gradient}_pos"

        color_ramp.exportRampAttributes(ctx, pluginDesc, texAttrName, colAttrName, posAttrName)
    
    return export_utils.exportPluginCommon(ctx, pluginDesc) 


def syncColorRamps():
    """ Called from Update Depsgraph Pre event handler """
    
    for node, _, _ in color_ramp.getColorRampsForPlugin('TexSoftbox'):
        NodeUtils.selectedObjectTagUpdate(node, bpy.context)


def registerColorRamps():
    """ Called from the Load Post event handler to register all color ramps for which
        update notifications will be processed in syncColorRamps().
    """
    from vray_blender.nodes.tools import iterVRayNodeTrees
    for ntree in iterVRayNodeTrees():
        for node in ntree.nodes:
            if hasattr(node, 'TexSoftbox'):
                registerNodeColorRamps(node)
                

def registerNodeColorRamps(node: bpy.types.Node):
    for attrName in _COLOR_RAMP_ATTRS:
        attrTexName = _getRampTexAttrName(attrName)
        color_ramp.registerColorRamp(node, attrTexName, getattr(node, attrTexName)) 


def _getRampTexAttrName(attrName):
    return f'ramp_{attrName}'


