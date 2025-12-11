
import bpy

from vray_blender.exporting.tools import getLinkedFromSocket, getActiveFarNodeLinks
from vray_blender.lib.blender_utils import isCollection
from vray_blender.lib.mixin import VRayNodeBase
from vray_blender.nodes.sockets import addInput, addOutput
from vray_blender.nodes.tools import getLinkInfo, isVrayNode, isCompatibleNode
from vray_blender.plugins.templates import common
from vray_blender import debug


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


    def getSelected(self, context: bpy.types.Context):
        """ Get the list of the objects and collections selected in this node and 
            in any other selector nodes connected to its input socket.

            Returns:
            list[bpy.types.Object|bpy.types.Collection]: a mixed list of objects and collection 
        """

        result = set()

        # Recursively collect the items from connected object selector nodes
        for link in [l for l in self.inputs[0].links if not l.is_muted and not l.is_hidden]:
            
            if sockFrom := getLinkedFromSocket(link.to_socket):
                match sockFrom.node.bl_idname:
                    case 'VRayNodeMultiSelect':
                        result = result | sockFrom.node.getSelected(context)
                    case 'VRayNodeSelectObject':
                        if selected := sockFrom.node.getSelected(context):
                            result.add(selected)
        
        collectionName = getattr(self, 'collection', '')

        return result | set(o for o in self.getSelectedItems(context, collectionName))
                

    def draw_buttons(self, context: bpy.types.Context, layout: bpy.types.UILayout):
        self.drawSelectorUI(context, layout, dataProvider=context.scene, dataProperty='objects', listLabel="Object List")
        

    def onFilterObject(self: bpy.types.Node, obj):
        # Return the poll (filter) function for the Object Select field
        sockOut = self.outputs[0]
        
        activeLinks = getActiveFarNodeLinks(sockOut)
        if len(activeLinks) != 1:
            return True
        
        sockTo = activeLinks[0].to_socket
        if not isVrayNode(sockTo.node):
            return True
        
        if linkInfo := getLinkInfo(sockTo.node.vray_plugin, sockTo.vray_attr):
            return linkInfo.fnFilter(obj)
        
        
    def onSelectionChanged(self, context: bpy.types.Context):
        self.update()
        
        # Mark the nodetree as updated because we cannot mark the node itself
        self.id_data.update_tag()
        
        if obj := getattr(context, 'active_object', None):
            obj.update_tag()


    # Node.update() override
    def update(self):
        # Mark as disabled the items that are not compatible with the socket(s) the output of 
        # this node is linked to.
        inputSockets = [l.to_socket for l in getActiveFarNodeLinks(self.outputs[0])]
        
        filters = [getLinkInfo(toSocket.node.vray_plugin, toSocket.vray_attr).fnFilter \
                    for toSocket in inputSockets \
                    if isCompatibleNode(toSocket.node)]

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

    def onFilterObject(self: bpy.types.Node, obj):
        # Return the poll (filter) function for the Object Select field
        sockOut = self.outputs[0]
        
        activeLinks = getActiveFarNodeLinks(sockOut)
        if len(activeLinks) != 1:
            return True
        
        sockTo = activeLinks[0].to_socket
        if not isVrayNode(sockTo.node):
            return True
        
        if linkInfo := getLinkInfo(sockTo.node.vray_plugin, sockTo.vray_attr):
            return linkInfo.fnFilter(obj)
        
        return True
    
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
        update = onUpdateName,
        poll = onFilterObject
    )

    def _clearIncorrectObjectPtr(self, context: bpy.types.Context):
        """ Checks if the not in the scene collection. If so, clears the object pointer. """
        if self.objectPtr and self.objectPtr.name not in context.scene.objects:
            self.objectPtr = None
    
    def init(self, context):
        addOutput(self, 'VRaySocketObject', "Object")

    def draw_buttons(self, context: bpy.types.Context, layout: bpy.types.UILayout):
        layout.prop_search(self, 'objectPtr',
                           context.scene, 'objects',
                           text="")

    def removeDeletedItems(self, context: bpy.types.Context):
        if self.objectName and self.objectName not in context.scene.objects:
            self.objectPtr = None
            self.objectName = ""
            return True

        return False

    def getSelected(self, context: bpy.types.Context):
        if not self.objectName:
            return None
        
        return self.objectPtr.original if self.objectPtr.original in context.scene.objects.values() else None



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
