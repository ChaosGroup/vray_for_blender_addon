
import bpy

from vray_blender.ui      import classes
from vray_blender.lib     import lib_utils
from vray_blender.nodes   import utils as NodesUtils
from vray_blender.plugins import PLUGINS, VRayLight
from vray_blender.plugins.templates.common import VRayObjectSelector


##     ## ######## #### ##        ######
##     ##    ##     ##  ##       ##    ##
##     ##    ##     ##  ##       ##
##     ##    ##     ##  ##        ######
##     ##    ##     ##  ##             ##
##     ##    ##     ##  ##       ##    ##
 #######     ##    #### ########  ######

def lightIsSun(lamp):
    if lamp.type == 'SUN' and lamp.vray.direct_type == 'SUN':
        return True
    return False


def lightIsAmbient(lamp):
    if lib_utils.getLightPluginName(lamp) in {'LightAmbient'}:
        return True
    return False



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
        lightPluginName = lib_utils.getLightPluginName(light)

        layout.label(text=f"V-Ray Type:  {lightPluginName}", icon='LIGHT_DATA')
        layout.separator()

        # We support adding both Blender and VRay lights to the scene. Both are eventually exported
        # as VRay lights, but they are shown as separate types in the UI. For a Blender lights, allow 
        # changing the type of the light only if it doesn't have a node tree.
        if vrayLight.light_type == "BLENDER" and (not light.node_tree):
            layout.prop(light, 'type', expand=True)
        
        # The property values are stored in different places for light with node trees and such without
        outputNode = None
        lightPropGroup = None

        if NodesUtils.treeHasNodes(light.node_tree):
            if outputNode := NodesUtils.getLightOutputNode(light.node_tree):
                # The output node might have been deleted
                lightPropGroup = getattr(outputNode, outputNode.vray_plugin)
        else:
            lightPropGroup = getattr(vrayLight, lightPluginName)
            
        if lightPropGroup:
            layout.separator()
            classes.drawPluginUI(context, layout, lightPropGroup, PLUGINS['LIGHT'][lightPluginName], outputNode)



######## ##     ##  ######  ##       ##     ## ########  ########
##        ##   ##  ##    ## ##       ##     ## ##     ## ##
##         ## ##   ##       ##       ##     ## ##     ## ##
######      ###    ##       ##       ##     ## ##     ## ######
##         ## ##   ##       ##       ##     ## ##     ## ##
##        ##   ##  ##    ## ##       ##     ## ##     ## ##
######## ##     ##  ######  ########  #######  ########  ########

class VRAY_PT_include_exclude(classes.VRayLampPanel):
    bl_label   = "Include / Exclude"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout

        vrayLight: VRayLight= context.light.vray

        if vrayLight.light_type == 'MESH':
            layout.label(text="Not applicable to V-Ray Light Mesh")
            layout.active = False
            return
        
        layout.prop(vrayLight, 'include_exclude', text="Type", expand=True)

        col = layout.column()
        col.active = col.enabled = vrayLight.include_exclude != '0'
        col.prop(vrayLight, 'illumination_shadow', text="From")
        
        VRayObjectSelector.drawSelectorUI(vrayLight.objectList, context, col, 
                                          dataProvider=context.scene, dataProperty='objects', 
                                          listLabel='Objects List')


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
    )


def register():
    from vray_blender.lib.class_utils import registerClass
    for regClass in getRegClasses():
        registerClass(regClass)


def unregister():
    for regClass in reversed(getRegClasses()):
        bpy.utils.unregister_class(regClass)
