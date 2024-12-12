
import bpy

from vray_blender.lib.blender_utils import isCollection
from vray_blender.nodes.mixin import VRayNodeBase
from vray_blender.nodes.sockets import addInput, addOutput
from vray_blender.nodes.tools import getLinkInfo
from vray_blender.plugins.templates import common


class VRayNodeSelectNodeTree(VRayNodeBase):
    bl_idname = 'VRayNodeSelectNodeTree'
    bl_label  = 'V-Ray Node Tree Select'
    bl_icon   = 'NODETREE'

    vray_type   = 'NONE'
    vray_plugin = 'NONE'

    treeName: bpy.props.StringProperty(
        name        = "Tree",
        description = "Tree name",
        default     = ""
    )

    def init(self, context):
        addOutput(self, 'VRaySocketObject', "Tree")

    def draw_buttons(self, context, layout):
        layout.prop_search(self, 'treeName',
                           bpy.data, 'node_groups',
                           text="")


    
class VRayNodeMultiSelect(VRayNodeBase, common.VRayObjectSelector):
    """ Multi object selector """
    bl_idname = 'VRayNodeMultiSelect'
    bl_label  = 'V-Ray Group Select'
    bl_icon   = 'OBJECT_DATA'

    vray_type   = 'NONE'
    vray_plugin = 'NONE'

    def init(self, context: bpy.types.Context):
        addOutput(self, 'VRaySocketObjectList', "Objects")
        addInput(self, 'VRaySocketObjectList', "Objects", isMultiInput=True)
        
        self.onSelectionChanged(context)


    def getSelected(self, context: bpy.types.Context):# -> list:
        """ Get the list of the objects and collections selected in this node and 
            in any other selector nodes connected to its input socket.

            Returns:
            list[bpy.types.Object|bpy.types.Collection]: a mixed list of objects and collection 
        """

        result = set()

        # Recursively collect the items from connected object selector nodes
        for link in self.inputs[0].links:
            sockFrom = link.from_socket

            match sockFrom.node.bl_idname:
                case 'VRayNodeMultiSelect':
                    result = sockFrom.node.getSelected(context)
                case 'VRayNodeSelectObject':
                    if selected := sockFrom.node.getSelected(context):
                        result.add(selected)
        
        collectionName = getattr(self, 'collection', '')

        return result | set(o for o in self.getSelectedItems(context, collectionName))
                

    def draw_buttons(self, context: bpy.types.Context, layout: bpy.types.UILayout):
        self.drawSelectorUI(context, layout, dataProvider=context.scene, dataProperty='objects', listLabel="Object List")
        

    def onSelectionChanged(self, context: bpy.types.Context):
        self.update()
        
        # Mark the nodetree as updated because we cannot mark the node itself
        self.id_data.update_tag()


    # Node.update() override
    def update(self):
        # Mark as disabled the items that are not compatible with the socket(s) the output of 
        # this node is linked to.
        filters = [getLinkInfo(link.to_socket.node.vray_plugin, link.to_socket.vray_attr).fnFilter for link in self.outputs[0].links]

        selectedObjects = [item for item in self.selectedItems if item.objectPtr and (not isCollection(item.objectPtr))]

        # If no filter function is defined for a link, the entry in 'filters' will be None
        if any(f for f in filters):
            for item in selectedObjects:
                item.enabled = any(filter(item.objectPtr) for filter in filters if filter)
        else:
            for item in selectedObjects:
                item.enabled = True


class VRayNodeSelectObject(VRayNodeBase):
    """ Single object selector """
    bl_idname = 'VRayNodeSelectObject'
    bl_label  = 'V-Ray Object Select'
    bl_icon   = 'OBJECT_DATA'

    vray_type   = 'NONE'
    vray_plugin = 'NONE'

    def onUpdateName(self, context):
        if self.objectPtr:
            self.objectName = self.objectPtr.name
        else:
            self.objectName = ""

    objectName: bpy.props.StringProperty(
        name        = "Object name",
        description = "Object name",
        default     = "",
        options     = {'HIDDEN'}
    )

    objectPtr: bpy.props.PointerProperty(
        type = bpy.types.Object,
        name = "Object",
        description = "Selected object",
        update = onUpdateName
    )

    def init(self, context):
        addOutput(self, 'VRaySocketObject', "Object")

    def draw_buttons(self, context: bpy.types.Context, layout: bpy.types.UILayout):
        layout.prop_search(self, 'objectPtr',
                           context.scene, 'objects',
                           text="")

    def getSelected(self, context: bpy.types.Context):
        return self.objectPtr if self.objectPtr in context.scene.objects.values() else None



########  ########  ######   ####  ######  ######## ########     ###    ######## ####  #######  ##    ##
##     ## ##       ##    ##   ##  ##    ##    ##    ##     ##   ## ##      ##     ##  ##     ## ###   ##
##     ## ##       ##         ##  ##          ##    ##     ##  ##   ##     ##     ##  ##     ## ####  ##
########  ######   ##   ####  ##   ######     ##    ########  ##     ##    ##     ##  ##     ## ## ## ##
##   ##   ##       ##    ##   ##        ##    ##    ##   ##   #########    ##     ##  ##     ## ##  ####
##    ##  ##       ##    ##   ##  ##    ##    ##    ##    ##  ##     ##    ##     ##  ##     ## ##   ###
##     ## ########  ######   ####  ######     ##    ##     ## ##     ##    ##    ####  #######  ##    ##

def getRegClasses():
    return (
        VRayNodeSelectNodeTree,
        VRayNodeMultiSelect,
        VRayNodeSelectObject,
    )


def register():
    for regClass in getRegClasses():
        bpy.utils.register_class(regClass)


def unregister():
    for regClass in getRegClasses():
        bpy.utils.unregister_class(regClass)
