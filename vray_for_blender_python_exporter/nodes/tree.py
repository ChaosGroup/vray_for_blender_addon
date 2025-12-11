
import bpy

from vray_blender.lib.mixin import VRayEntity
from vray_blender.lib import blender_utils
from vray_blender.ui import classes
from vray_blender.plugins import VRayNodeTreeSettings
from vray_blender.lib.blender_utils import tagUsersForUpdate


##      ##  #######  ########  ##       ########
##  ##  ## ##     ## ##     ## ##       ##     ##
##  ##  ## ##     ## ##     ## ##       ##     ##
##  ##  ## ##     ## ########  ##       ##     ##
##  ##  ## ##     ## ##   ##   ##       ##     ##
##  ##  ## ##     ## ##    ##  ##       ##     ##
 ###  ###   #######  ##     ## ######## ########

def _getVRayWordNTreeData(context):
    if (world := getattr(context.scene, 'world')) and world.node_tree:
        return world.node_tree, world, None
    
    return (None, None, None)


 #######  ########        ## ########  ######  ########
##     ## ##     ##       ## ##       ##    ##    ##
##     ## ##     ##       ## ##       ##          ##
##     ## ########        ## ######   ##          ##
##     ## ##     ## ##    ## ##       ##          ##
##     ## ##     ## ##    ## ##       ##    ##    ##
 #######  ########   ######  ########  ######     ##

def _getVRayObjectNTreeData(context):
    ob = context.active_object
    if ob and ob.type not in blender_utils.NonGeometryTypes:
        if ob.vray.ntree:
            return ob.vray.ntree, ob, None

    return (None, None, None)


class VRayNodeTreeObjectBase(VRayEntity, bpy.types.NodeTree):
    bl_label  = "V-Ray Node Tree"
    bl_idname = 'VRayNodeTreeObjectBase'
    bl_icon   = 'OBJECT_DATA'

    bl_update_preview = True

    vray: bpy.props.PointerProperty(
        name = "VRay Node Tree Settings",
        type = VRayNodeTreeSettings,
        description = "VRay Node Tree Settings"
    )

    @classmethod
    def poll(cls, context):
        # Do not show in Node Tree Editors list
        return False
    
    @classmethod
    def get_from_context(cls, context):
        return _getVRayObjectNTreeData(context)

    def update(self):
        tagUsersForUpdate(self)
    

class VRayNodeTreeObject(VRayNodeTreeObjectBase):
    bl_label  = "V-Ray Object Node Tree"
    bl_idname = 'VRayNodeTreeObject'



class VRayNodeTreeFur(VRayNodeTreeObjectBase):
    bl_label  = "V-Ray Fur Node Tree"
    bl_idname = 'VRayNodeTreeFur'
    
    
    


######## ########  #### ########  #######  ########
##       ##     ##  ##     ##    ##     ## ##     ##
##       ##     ##  ##     ##    ##     ## ##     ##
######   ##     ##  ##     ##    ##     ## ########
##       ##     ##  ##     ##    ##     ## ##   ##
##       ##     ##  ##     ##    ##     ## ##    ##
######## ########  ####    ##     #######  ##     ##

def _getVRayShaderTreeData(context):
    """ Returns the active nodetree depending the current context. The result is 
        used e.g. to populate the shader node editor window or to show the nodetree name in 
        active object's property page.
    """
    if ob:= context.active_object:
        if ob.type == 'LIGHT':
            if ob.data.node_tree:
                return ob.data.node_tree, ob.data, None
        elif material := ob.active_material:
            return material.node_tree, None, None

    return (None, None, None)


class VRayNodeTreeEditor(bpy.types.NodeTree):
    """ A custom node tree type that will show up in the editor type list.
    
        This class is never instantiated and therefore can have no instance data. 
        Its sole purpose is to be added as an entry in the list of available
        node tree types and to return the actual active node tree object when
        queried by Blender.
    """
    bl_label  = "V-Ray Node Editor"
    bl_idname = 'VRayNodeTreeEditor'
    bl_icon   = 'MATERIAL'
    bl_description = "V-Ray Nodes"

    @classmethod
    def poll(cls, context):
        # Return True to show the editor entry in the editor types list
        return context.scene.render.engine in classes.VRayEngines
    
    @classmethod
    def get_from_context(cls, context):
        # This method is called by Blender when it needs to show a nodetree in 
        # a node editor.
        match context.scene.vray.ActiveNodeEditorType: 
            case 'SHADER':
                return _getVRayShaderTreeData(context)
            case 'OBJECT':
                return _getVRayObjectNTreeData(context)
            case 'WORLD':
                return _getVRayWordNTreeData(context)
        
        return (None, None, None)

        


########  ########  ######   ####  ######  ######## ########     ###    ######## ####  #######  ##    ##
##     ## ##       ##    ##   ##  ##    ##    ##    ##     ##   ## ##      ##     ##  ##     ## ###   ##
##     ## ##       ##         ##  ##          ##    ##     ##  ##   ##     ##     ##  ##     ## ####  ##
########  ######   ##   ####  ##   ######     ##    ########  ##     ##    ##     ##  ##     ## ## ## ##
##   ##   ##       ##    ##   ##        ##    ##    ##   ##   #########    ##     ##  ##     ## ##  ####
##    ##  ##       ##    ##   ##  ##    ##    ##    ##    ##  ##     ##    ##     ##  ##     ## ##   ###
##     ## ########  ######   ####  ######     ##    ##     ## ##     ##    ##    ####  #######  ##    ##

def getRegClasses():
    return (
        VRayNodeTreeObject,
        VRayNodeTreeFur,
        VRayNodeTreeEditor
    )


def register():    
    for regClass in getRegClasses():
        bpy.utils.register_class(regClass)


def unregister():
    for regClass in getRegClasses():
        bpy.utils.unregister_class(regClass)
