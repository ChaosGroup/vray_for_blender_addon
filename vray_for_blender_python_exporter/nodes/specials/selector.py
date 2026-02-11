# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later


import bpy

from vray_blender.exporting.tools import getActiveInputFarNodeLinks, getActiveOutputFarNodeLinks
from vray_blender.lib.blender_utils import isCollection
from vray_blender.lib.mixin import VRayNodeBase
from vray_blender.nodes.nodes import updateNodeMutedState
from vray_blender.nodes.sockets import addInput, addOutput
from vray_blender.nodes.tools import getLinkInfo, isVrayNode, isCompatibleNode
from vray_blender.plugins.templates import common

    
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
        for link in [l for l in getActiveInputFarNodeLinks(self.inputs[0])]:
            node = link.from_node
            match node.bl_idname:
                case 'VRayNodeMultiSelect':
                    result = result | node.getSelected(context)
                case 'VRayNodeSelectObject':
                    if selected := node.getSelected(context):
                        result.add(selected)
    
        collectionName = getattr(self, 'collection', '')

        if not self.mute:
            result = result | set(o for o in self.getSelectedItems(context, collectionName))

        return result
                

    def draw_buttons(self, context: bpy.types.Context, layout: bpy.types.UILayout):
        self.drawSelectorUI(context, layout, dataProvider=context.scene, dataProperty='objects', listLabel="Object List")
        

    def onFilterObject(self: bpy.types.Node, obj):
        # Return the poll (filter) function for the Object Select field
        sockOut = self.outputs[0]
        
        activeLinks = getActiveOutputFarNodeLinks(sockOut)
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
        inputSockets = [l.to_socket for l in getActiveOutputFarNodeLinks(self.outputs[0])]
        
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

        updateNodeMutedState(self)


    def resolveInternalLink(self, outSock: bpy.types.NodeSocket):
        inSock = outSock.node.inputs[0]
        return inSock, inSock


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
        
        activeLinks = getActiveOutputFarNodeLinks(sockOut)
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
        if (not self.objectName) or self.mute:
            return None
        
        return self.objectPtr.original if self.objectPtr.original in context.scene.objects.values() else None


def resolveSelectorNode(toSock: bpy.types.NodeSocket):
    """ Get a connected Selector node resolving Reroute nodes only.
        This is specific to Selectors because they implement their own resolution
        and we only need to find the first of the subtree of connected selectors.
    """
    if not toSock.is_linked:
        return None
    
    fromNode = toSock.links[0].from_node
    if fromNode.bl_idname == 'NodeReroute':
        return resolveSelectorNode(fromNode.inputs[0])
    elif fromNode.bl_idname in ('VRayNodeMultiSelect', 'VRayNodeSelectObject'):
        return fromNode
    

########  ########  ######   ####  ######  ######## ########     ###    ######## ####  #######  ##    ##
##     ## ##       ##    ##   ##  ##    ##    ##    ##     ##   ## ##      ##     ##  ##     ## ###   ##
##     ## ##       ##         ##  ##          ##    ##     ##  ##   ##     ##     ##  ##     ## ####  ##
########  ######   ##   ####  ##   ######     ##    ########  ##     ##    ##     ##  ##     ## ## ## ##
##   ##   ##       ##    ##   ##        ##    ##    ##   ##   #########    ##     ##  ##     ## ##  ####
##    ##  ##       ##    ##   ##  ##    ##    ##    ##    ##  ##     ##    ##     ##  ##     ## ##   ###
##     ## ########  ######   ####  ######     ##    ##     ## ##     ##    ##    ####  #######  ##    ##

def getRegClasses():
    return (
        VRayNodeMultiSelect,
        VRayNodeSelectObject,
    )


def register():
    for regClass in getRegClasses():
        bpy.utils.register_class(regClass)


def unregister():
    for regClass in getRegClasses():
        bpy.utils.unregister_class(regClass)
