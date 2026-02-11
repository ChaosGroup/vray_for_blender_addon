# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import functools
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
    def reattachTexture(node, texAttrName, tex):
        setattr(node, texAttrName, tex)
    
    for attrName in _COLOR_RAMP_ATTRS:
        rampTexAttrName = _getRampTexAttrName(attrName)
        texOrig = getattr(nodeOrig, rampTexAttrName)
        texCopy = color_ramp.createRampTexture(nodeCopy, rampTexAttrName)
        
        color_ramp.copyColorRamp(texOrig.color_ramp, texCopy.color_ramp)
        color_ramp.registerColorRamp(nodeCopy, rampTexAttrName, texCopy) 

        bpy.app.timers.register(functools.partial(reattachTexture, nodeCopy, rampTexAttrName, texCopy))


def nodeFree(node: bpy.types.Node):
    for attrName in _COLOR_RAMP_ATTRS:
        attrTexName = _getRampTexAttrName(attrName)
        color_ramp.unregisterColorRamp(node, attrTexName, getattr(node, attrTexName)) 


# Custom function that draws color ramp
def widgetDrawGradientRamp(context: bpy.types.Context, layout, propGroup, widgetAttr):
    node = NodeUtils.getNodeOfPropGroup(propGroup)
    
    if not (texture := getattr(node, widgetAttr['name'], None)):
        # This will be the case right after the node has been copied from anoter node
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
    nodeTrees = (
        (bpy.data.materials, 'MATERIAL'), 
        (bpy.data.worlds, 'WORLD'), 
        (bpy.data.lights, 'LIGHT')
    )

    for tree in nodeTrees: 
        for item in [it for it in tree[0] if isVrayNodeTree(it.node_tree, tree[1])]:
            for node in [ n for n in item.node_tree.nodes if hasattr(n, 'TexSoftbox')]:
                _registerRampsForNode(node)
                

def _registerRampsForNode(node: bpy.types.Node):
    for attrName in _COLOR_RAMP_ATTRS:
        attrTexName = _getRampTexAttrName(attrName)
        color_ramp.registerColorRamp(node, attrTexName, getattr(node, attrTexName)) 


def _getRampTexAttrName(attrName):
    return f'ramp_{attrName}'


