# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later


import bpy

from vray_blender.ui      import classes
from vray_blender.ui      import ui_operators
from vray_blender.lib     import lib_utils
from vray_blender.nodes   import utils as NodesUtils
from vray_blender.plugins import VRayLight, getPluginModule
from vray_blender.plugins.templates.common import VRayObjectSelector


##     ## ######## #### ##        ######
##     ##    ##     ##  ##       ##    ##
##     ##    ##     ##  ##       ##
##     ##    ##     ##  ##        ######
##     ##    ##     ##  ##             ##
##     ##    ##     ##  ##       ##    ##
 #######     ##    #### ########  ######

def lightIsSun(lamp):
    return lamp.type == 'SUN' and lamp.vray.direct_type == 'SUN'


def lightIsAmbient(lamp):
    return lib_utils.getLightPluginType(lamp) == 'LightAmbient'



 ######   #######  ##    ## ######## ######## ##     ## ########
##    ## ##     ## ###   ##    ##    ##        ##   ##     ##
##       ##     ## ####  ##    ##    ##         ## ##      ##
##       ##     ## ## ## ##    ##    ######      ###       ##
##       ##     ## ##  ####    ##    ##         ## ##      ##
##    ## ##     ## ##   ###    ##    ##        ##   ##     ##
 ######   #######  ##    ##    ##    ######## ##     ##    ##

class VRAY_PT_context_lamp(classes.VRayLampPanel):
    bl_label   = ""
    bl_options = {'HIDE_HEADER'}

    def draw(self, context):
        layout = self.layout

        light  = context.light
        vrayLight = light.vray
        lightPluginType = lib_utils.getLightPluginType(light)
        lightPluginModule = getPluginModule(lightPluginType)

        # Light selector dropdown    
        if context.object:
            layout.template_ID(context.object, "data")
        elif light:
            # No light is selected, show the pinned light
            layout.template_ID(context.space_data, "pin_id")

        headerRow = layout.row(align=True)
        headerRow.label(text=lightPluginModule.NAME)
        ui_operators.drawPropertyPageButtons(headerRow, context, 'LIGHT')

        # The property values are stored in different places for light with node trees and such without
        outputNode = None
        lightPropGroup = None

        if NodesUtils.treeHasNodes(light.node_tree) and (outputNode := NodesUtils.getLightOutputNode(light.node_tree)):
            lightPropGroup = getattr(outputNode, outputNode.vray_plugin)
        else:
            lightPropGroup = getattr(vrayLight, lightPluginType)


        if lightPropGroup:
            layout.separator()
            classes.drawPluginUI(context, layout, lightPropGroup, lightPluginModule, outputNode)



######## ##     ##  ######  ##       ##     ## ########  ########
##        ##   ##  ##    ## ##       ##     ## ##     ## ##
##         ## ##   ##       ##       ##     ## ##     ## ##
######      ###    ##       ##       ##     ## ##     ## ######
##         ## ##   ##       ##       ##     ## ##     ## ##
##        ##   ##  ##    ## ##       ##     ## ##     ## ##
######## ##     ##  ######  ########  #######  ########  ########

def drawIncludeExclude(context: bpy.types.Context, layout: bpy.types.UILayout):
    """ Draw the light's Include / Exclude controls. Shared between the Properties
        editor panel and the node editor sidebar panel.
    """
    vrayLight: VRayLight = context.light.vray

    layout.prop(vrayLight, 'include_exclude', text="Type", expand=True)

    col = layout.column()
    col.active = col.enabled = vrayLight.include_exclude != '0'
    col.prop(vrayLight, 'illumination_shadow', text="From")

    VRayObjectSelector.drawSelectorUI(vrayLight.objectList, context, col,
                                      dataProvider=context.scene, dataProperty='objects',
                                      listLabel='Objects List')


class VRAY_PT_include_exclude(classes.VRayLampPanel):
    bl_label   = "Include / Exclude"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        drawIncludeExclude(context, self.layout)


class VRAY_PT_node_include_exclude(bpy.types.Panel):
    """ Include / Exclude controls shown in the node editor sidebar, alongside the
        light characteristics drawn for the active light node.
    """
    bl_space_type  = 'NODE_EDITOR'
    bl_region_type = 'UI'
    bl_category    = 'Node'
    bl_label       = "Include / Exclude"
    bl_options     = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        activeNode = context.active_node
        return classes.pollEngine(context) \
            and context.light is not None \
            and activeNode is not None \
            and getattr(activeNode, 'vray_type', None) == 'LIGHT'

    def draw(self, context):
        drawIncludeExclude(context, self.layout)


########  ########  ######   ####  ######  ######## ########     ###    ######## ####  #######  ##    ##
##     ## ##       ##    ##   ##  ##    ##    ##    ##     ##   ## ##      ##     ##  ##     ## ###   ##
##     ## ##       ##         ##  ##          ##    ##     ##  ##   ##     ##     ##  ##     ## ####  ##
########  ######   ##   ####  ##   ######     ##    ########  ##     ##    ##     ##  ##     ## ## ## ##
##   ##   ##       ##    ##   ##        ##    ##    ##   ##   #########    ##     ##  ##     ## ##  ####
##    ##  ##       ##    ##   ##  ##    ##    ##    ##    ##  ##     ##    ##     ##  ##     ## ##   ###
##     ## ########  ######   ####  ######     ##    ##     ## ##     ##    ##    ####  #######  ##    ##

def getRegClasses():
    return (
        VRAY_PT_context_lamp,
        VRAY_PT_include_exclude,
        VRAY_PT_node_include_exclude,
    )


def register():
    from vray_blender.lib.class_utils import registerClass
    for regClass in getRegClasses():
        registerClass(regClass)


def unregister():
    for regClass in reversed(getRegClasses()):
        bpy.utils.unregister_class(regClass)
