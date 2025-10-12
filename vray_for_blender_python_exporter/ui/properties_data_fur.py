
import bpy

from vray_blender.ui      import classes
from vray_blender.nodes   import utils as NodesUtils
from vray_blender.plugins import PLUGINS



 ######   #######  ##    ## ######## ######## ##     ## ########
##    ## ##     ## ###   ##    ##    ##        ##   ##     ##
##       ##     ## ####  ##    ##    ##         ## ##      ##
##       ##     ## ## ## ##    ##    ######      ###       ##
##       ##     ## ##  ####    ##    ##         ## ##      ##
##    ## ##     ## ##   ###    ##    ##        ##   ##     ##
 ######   #######  ##    ##    ##    ######## ##     ##    ##

class VRAY_PT_context_fur(classes.VRayFurPanel):
    bl_label   = ""
    bl_options = {'HIDE_HEADER'}

    def draw(self, context):
        layout = self.layout

        fur = context.object
        geomHairPropGroup = fur.data.vray.GeomHair

        if NodesUtils.treeHasNodes(fur.vray.ntree) and (outputNode := NodesUtils.getOutputNode(fur.vray.ntree)):
            geomHairPropGroup = getattr(outputNode, outputNode.vray_plugin)


        classes.drawPluginUI(context, layout, geomHairPropGroup, PLUGINS['GEOMETRY']['GeomHair'], None)


########  ########  ######   ####  ######  ######## ########     ###    ######## ####  #######  ##    ##
##     ## ##       ##    ##   ##  ##    ##    ##    ##     ##   ## ##      ##     ##  ##     ## ###   ##
##     ## ##       ##         ##  ##          ##    ##     ##  ##   ##     ##     ##  ##     ## ####  ##
########  ######   ##   ####  ##   ######     ##    ########  ##     ##    ##     ##  ##     ## ## ## ##
##   ##   ##       ##    ##   ##        ##    ##    ##   ##   #########    ##     ##  ##     ## ##  ####
##    ##  ##       ##    ##   ##  ##    ##    ##    ##    ##  ##     ##    ##     ##  ##     ## ##   ###
##     ## ########  ######   ####  ######     ##    ##     ## ##     ##    ##    ####  #######  ##    ##

def getRegClasses():
    return (
        VRAY_PT_context_fur,
    )


def register():
    from vray_blender.lib.class_utils import registerClass
    for regClass in getRegClasses():
        registerClass(regClass)


def unregister():
    for regClass in reversed(getRegClasses()):
        bpy.utils.unregister_class(regClass)
