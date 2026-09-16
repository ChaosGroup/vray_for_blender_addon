# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later


import bpy

from vray_blender.ui      import classes
from vray_blender.ui      import node_nav
from vray_blender.ui      import node_slots
from vray_blender.ui      import ui_operators
from vray_blender.lib     import draw_utils
from vray_blender.lib     import lib_utils
from vray_blender.nodes   import navigation as NodesNav
from vray_blender.nodes   import utils as NodesUtils
from vray_blender.plugins import PLUGINS, VRayLight, getPluginModule
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

        # The property values are stored in different places for light with node trees and such without
        outputNode = None
        lightPropGroup = None

        if NodesUtils.treeHasNodes(light.node_tree) and (outputNode := NodesUtils.getLightOutputNode(light.node_tree)):
            lightPropGroup = getattr(outputNode, outputNode.vray_plugin)
        else:
            lightPropGroup = getattr(vrayLight, lightPluginType)

        # The panel follows the tree's selection, so a texture entered from one of the light's slot
        # rows shows its own parameters here - the same rule the Material and World tabs use. Without
        # this the panel was pinned to outputNode and 'enter the texture' appeared to do nothing.
        activeNode = NodesNav.getPanelNode(light.node_tree, 'LIGHT') if outputNode else None

        headerRow = layout.row(align=True)
        headerRow.label(text=activeNode.bl_label if (activeNode and activeNode != outputNode) else lightPluginModule.NAME)
        ui_operators.drawPropertyPageButtons(headerRow, context, 'LIGHT')

        if lightPropGroup:
            layout.separator()
            # A node-mode light drives its colour through a socket on outputNode, so it gets the
            # texture picker. A light without a node tree has no sockets and simply draws as before.
            slotContext = node_slots.makeSlotContext(context, light, light.node_tree) if outputNode else None

            if slotContext is not None:
                navCol = layout.column(align=True)
                navCol.use_property_split = False
                node_nav.drawNavigation(navCol, context, slotContext.ownerType, slotContext.ownerName,
                                        light.node_tree, 'LIGHT', activeNode)
                layout.separator()

            with draw_utils.slotEditing(slotContext):
                if (activeNode is None) or (activeNode == outputNode):
                    classes.drawPluginUI(context, layout, lightPropGroup, lightPluginModule, outputNode)
                else:
                    classes.drawActiveNodePanel(context, layout, activeNode, PLUGINS)



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
