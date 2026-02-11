# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy
import functools
import operator

from vray_blender import debug
from vray_blender.exporting.tools import (getInputSocketByName, getFarNodeLink, removeSocketLinks, 
                                          getInputSocketByAttr)
from vray_blender.exporting.update_tracker import UpdateTracker, UpdateFlags, UpdateTarget
from vray_blender.lib import attribute_types, lib_utils, color_utils
from vray_blender.lib.attribute_types import AllNodeInputTypes, NodeOutputTypes, getSocketType
from vray_blender.lib.attribute_utils import getAttrDisplayName, formatAttributeName
from vray_blender.lib.condition_processor import evaluateCondition, isCondition
from vray_blender.lib.sys_utils import importFunction
from vray_blender.nodes.tools import isVrayNode, getSocketPanel
from vray_blender.lib.blender_utils import tagUsersForUpdate


class PropertyContext:
    """ Provides access to the plugin properties' underlying Blender data. This class is needed
        because the properties shown in the UI may be backed up by either fields 
        in a property group, or by input node sockets. The socket always takes precedence.
    """
    def __init__(self, propGroup, node: bpy.types.Node = None):
        """ @param propGroup - VRay plugin property group 
            @param node - (optional) the parent node of the property group
        """
        self.node = node
        self.propGroup = propGroup

    def get(self, attrName: str):
        if sock := getInputSocketByAttr(self.node, attrName) if self.node else None:
            return sock.value
        return getattr(self.propGroup, attrName)

    def set(self, attrName, newValue):
        """ Set a new value to the property. If the new value is the same as the old one,
            the property is not set. This allows to break the infinite updates sequence
            when updating linked properties.
        """
        if sock := getInputSocketByAttr(self.node, attrName) if self.node else None:
            if newValue != sock.value:
                sock.value = newValue
        else:
            if newValue != getattr(self.propGroup, attrName): 
                setattr(self.propGroup, attrName, newValue)

    def safeSetColor(self, attrName, newColor):
        """ Color format (RGB or RGBA) may not match between sockets and property groups.
            This method will convert the color to the target format before setting the new value.

            @param newColor - 3 or 4 component color tuple
        """
        color = self.get(attrName)
        safeColor = color_utils.toRGB(newColor) if len(color) == 3 else color_utils.toRGBA(newColor)
        self.set(attrName, safeColor)



def getNodeOfPropGroup(propGroup: dict):
    """ Return the parent node of a VRay property group. """

    if isinstance(propGroup.id_data, bpy.types.NodeTree) and hasattr(propGroup,'parent_node_id'):
        parentNtree = propGroup.id_data
        return next((n for n in parentNtree.nodes if hasattr(n, "unique_id") and (n.unique_id == propGroup.parent_node_id)), None)

    return None


def getPropGroupOfNode(node: bpy.types.Node):
    assert isVrayNode(node)

    if node.vray_plugin != '':
        return getattr(node, node.vray_plugin)

    return None


def getPluginTypeOfNode(node: bpy.types.Node):
    """ Returms the vray plugin type associated with the node """
    if node.vray_plugin and node.vray_plugin != 'NONE':
        return node.vray_plugin
    return None

def getUpdateCallbackPropertyContext(updateSource, pluginType) -> PropertyContext:
    """ Create a PropertyContext for the update source 

    @param updateSource - the 'self' parameter to an update callback, either a VRay property group 
                            or an input node socket
    @param pluginType - the type of the plugin
    """
    node = None
    propGroup = None

    if isinstance(updateSource, bpy.types.NodeSocket):
        propGroup = getattr(updateSource.node, pluginType)
        node = updateSource.node
    else:
        propGroup = updateSource
        node = getNodeOfPropGroup(updateSource)

    return PropertyContext(propGroup, node)


def getNodeByType(ntree, nodeType):
    if not ntree:
        return None
    for n in ntree.nodes:
        if n.bl_idname == nodeType:
            return n
    return None

def areNodesInterconnected(fromNode: bpy.types.Node, toNode: bpy.types.Node, visited=None):
    """
    Check if two nodes are linked directly or indirectly (through other nodes). The check is
    done for any connection starting at an output socket of fromNode and ending at an input
    socket of toNode that is enabled for export.

    @param fromNode (bpy.types.Node) - The node from whose output sockets to start the search
    @param toNode (bpy.types.Node) - The end node for the search.
    @param visited (set of bpy.types.Node) - A set of visited nodes, used to avoid revisiting 
    """
    assert fromNode != toNode, "A node cannot be connected to itself."

    if (fromNode is None) or (toNode is None):
        return False

    if visited is None:
        visited = set()

    visited.add(fromNode)

    for output in fromNode.outputs:
        for link in output.links:
            nextNode = link.to_node

            if not link.to_socket.hasActiveFarLink():
                return False

            if nextNode == toNode:
                return True

            if (nextNode not in visited) and areNodesInterconnected(nextNode, toNode, visited):
                return True

    return False


def getNearLink(fromSock: bpy.types.NodeSocket, toSock: bpy.types.NodeSocket):
    """ Return the direct link between two sockets, if any. """
    return next((l for l in toSock.links if l.from_socket == fromSock), None)


def createFakeName():
    return ".VRayFakeTexture@%s" % lib_utils.getUUID()


def createFakeTextureAttribute(cls, attrName='texture', updateFunc=None):
    cls.__annotations__[attrName] = bpy.props.PointerProperty(
        name = "Texture",
        type = bpy.types.Texture,
        description = "Fake texture for internal usage",
        update = updateFunc
    )

    # NOTE: We will store associated texture name for further possible
    # refactor to find the texture used by this datablock simply by name
    # and restore pointers
    #
    cls.__annotations__[f'{attrName}_name'] = bpy.props.StringProperty(
        name = "Texture Name",
        options = {'HIDDEN'},
        description = "Associated texture name",
        default = 'NONE'
    )


def createBitmapTexture(self, attrName='texture'):
    texName = createFakeName()

    tex = bpy.data.textures.new(texName, 'IMAGE')
    tex.use_fake_user = True

    setattr(self, attrName, tex)
    setattr(self, f'{attrName}_name', texName)

    return tex


# Cache for non-exportable plugin parameters
_NON_EXPORTABLE_PLUIGN_PARAMS = {} # dict[pluginModule, excludededParams[]]

def getNonExportablePluginProperties(pluginModule):
    """ Return a list of the parameters that should not be exported for a plugin. """
    global _NON_EXPORTABLE_PLUIGN_PARAMS

    if result := _NON_EXPORTABLE_PLUIGN_PARAMS.get(pluginModule):
        return result

    suppressedByMeta = []

    suppressedByMeta = set([p['color_prop'] for p in pluginModule.Parameters if p['type'] == "COLOR_TEXTURE"])
    suppressedByMeta.update([p['tex_prop'] for p in pluginModule.Parameters if p['type'] == "COLOR_TEXTURE"])

    derivedParams = [p['attr'] for p in pluginModule.Parameters if not p.get('options', {}).get('derived', True)]
    templateParams = [p['attr'] for p in pluginModule.Parameters if p['type'] == 'TEMPLATE']

    excludedParams = set(pluginModule.Options.get('excluded_parameters', []))
    excludedParams.update(derivedParams)
    excludedParams.update(templateParams)
    excludedParams.update(suppressedByMeta)

    # Cache the result
    _NON_EXPORTABLE_PLUIGN_PARAMS[pluginModule] = excludedParams

    return excludedParams


def _getPluginInputSockets(pluginModule, nodeType):
    sockets = {}

    # Collect the names of all bound propertes in a flat list
    metaProperties = [p for p in pluginModule.Parameters if 'bound_props' in p]
    overriddenByMetaLists = [p['bound_props'].values() for p in metaProperties]
    overriddenByMeta = functools.reduce(operator.iconcat, overriddenByMetaLists, [])

    excludedParams = set(pluginModule.Options.get('excluded_parameters', []))
    excludedParams = excludedParams.union(overriddenByMeta)

    # socketOverrides
    #   None means that there are no overrides
    #   [] means that no sockets should be shown
    inputSocketsList = None
    pluginParams = pluginModule.Parameters

    # Order the parameters of the plugin the same way as the ones included in the 'input_sockets' list.
    if getattr(pluginModule, 'Node') and (inputSocketsList := pluginModule.Node.get('input_sockets')):
        # There is no other way to order the node sockets except create them in the desired order.
        # The parameters that are not on the list are moved to the back without keeping any order. The order 
        # doesn't matter in this case becuase they will not be visible in the node.
        lastItem = len(inputSocketsList)
        orderedNames = [n['name'] for n in inputSocketsList]
        pluginParams = sorted(pluginParams, key=lambda item: lastItem if item['attr'] not in orderedNames else orderedNames.index(item['attr']))

    createdPanels = []

    for attr in [p for p in pluginParams if p['attr'] not in excludedParams]:

        if not (socketType := _getInputSocketType(nodeType, attr)):
            # Some socket types may need to be skipped depending on the attribute type
            continue

        attrName = attr['attr']
        attrType = attr['type']
        socketName = getAttrDisplayName(attr)
        sockDesc = None
        isVisible = True  # The 'default' of a condition on the 'visible' property of the socket

        # If an input socket list is found in the plugin description, use it to determine how and 
        # which sockets to show
        if inputSocketsList is not None:
            if sockDesc := next((s for s in inputSocketsList if s['name'] == attrName), None):
                attrType = sockDesc.get('type', attrType)
                visibleAttr = sockDesc.get('visible')

                if (visibleAttr is not None) and isCondition(visibleAttr):
                    isVisible = visibleAttr.get('default', True)

        isTypeVisible    = attrType not in attribute_types.HiddenNodeInputTypes
        isListExplicit   = inputSocketsList is not None
        isSocketExplicit = sockDesc is not None

        visible =  isVisible and \
                    ((isListExplicit and isSocketExplicit) or ((not isListExplicit) and isTypeVisible))\
                    and attr.get('options', {}).get('visible', True)

        # Add a panel for the socket, if necessary. The panel is added when the first of its sockets is added.
        if inputSocketsList is not None:
            panelAttrName = next((name for name in pluginModule.SocketPanels if attrName in pluginModule.SocketPanels[name]), None)

            if panelAttrName is not None:
                # Socket is placed on a panel
                if panelAttrName not in createdPanels:
                    # This is the first socket on this panel, create the panel
                    panelSockDesc = getSocketPanel(pluginModule, panelAttrName)
                    assert panelSockDesc is not None

                    sockets[panelAttrName] = {
                        'type': panelSockDesc['type'],
                        'name': formatAttributeName(panelSockDesc['name']),
                        'visible':  True
                    }

                    createdPanels.append(panelAttrName)

        sockets[attrName] = {
            'type': socketType,
            'name': socketName,
            'default': attr.get('default'), # The meta sockets may not have a default value
            'visible':  visible
        }

    # Insert any panel sockets at the correct place
    return sockets


def addInputs(node: bpy.types.Node, pluginModule):
    """ Add input sockets to the node. Use Node::input_sockets list from the plugin description to determine
        for which plugin propeties to create sockets. If the list is not specified, create sockets for all
        properties of types included in the AllNodeInputTypes list.

        Sockets are added for all parameters depending on the type of the parameter and visibility options.
    """
    from vray_blender.nodes.sockets import addInput

    socketsData = _getPluginInputSockets(pluginModule, pluginModule.ID)

    for attrName in socketsData:
        sockInfo = socketsData[attrName]
        sockName = sockInfo['name']
        addInput(node, sockInfo['type'], sockName, attrName, pluginModule.ID, visible=sockInfo['visible'])


def _getInputSocketType(nodeType, attrDesc):
    """ Return the V-Ray socket type based on the plugin property type and any additional options.  """
    attrType = attrDesc['type']
    attrOptions = attrDesc.get('options', {})

    if attrType in AllNodeInputTypes:
        isLinkedOnly = attrOptions.get('linked_only', False)
        return getSocketType(attrDesc['type'], attrDesc.get('subtype'), isLinkedOnly)

    return None


def _getOutputSocketType(attrType):
    return getSocketType(attrType)


def _addDefaultOutputForPluginCategory(node: bpy.types.Node, pluginModule):
    """ Add the output sockets that are mandatory for the given node type. 

        The plugin descriptions do not explicitly include properties for the default output type.
        This function adds the default output socket based on the plugin category.
    """
    from vray_blender.nodes.sockets import addOutput

    match pluginModule.Category:
        case 'TEXTURE':
            addOutput(node, 'VRaySocketColor', "Color")
        case 'FLOAT_TEXTURE':
            addOutput(node, 'VRaySocketFloat', "Float")
        case 'UVWGEN':
            addOutput(node, 'VRaySocketCoords', "Mapping", 'uvwgen')
        case 'BRDF':
            addOutput(node, 'VRaySocketBRDF', "BRDF")
        case 'GEOMETRY':
            addOutput(node, 'VRaySocketGeom', "Geometry")
        case 'MATERIAL':
            if node.vray_plugin != 'MtlOSL':
                addOutput(node, 'VRaySocketMtl', "Material")
        case 'EFFECT':
            addOutput(node, 'VRaySocketEffectOutput', "Effect")
        case 'RENDERCHANNEL':
            addOutput(node, 'VRaySocketRenderChannelOutput', "Channel")


def addOutputs(node: bpy.types.Node, pluginModule):
    """ Add all output sockets to the node. If a Node::output_sockets list is found, add only the 
        outputs from the list. Otherwise, add outputs for all output property types.
        The default output for the plugin category is always added.
    """
    from vray_blender.nodes.sockets import addOutput
    from vray_blender.lib.defs import AttrPlugin

    pluginOutputParams = [a for a in pluginModule.Parameters if a['type'] in NodeOutputTypes]

    # Add default output socket at the top. Add if either:
    #  - it is explicitly defined as '_default_' in Node::output_sockets, or
    #  - no output properties are defined for the plugin.
    if outputSockets := pluginModule.Node.get('output_sockets', []):
        if defaultOutput := next((o for o in outputSockets if o.get('name', '') == '_default_'), None):
            # There is no parameter defined in the plugin description for this socket,
            # take all data from the atribute widget entry in Node.output_sockets
            overrideName = defaultOutput.get('label', '')
            description = defaultOutput.get('desc', overrideName)

            if outputType := defaultOutput.get('type', ''):
                addOutput(node, getSocketType(outputType), socketName=overrideName, attrName=AttrPlugin.OUTPUT_DEFAULT, description=description)

            # Some plugins define output properties but still need to show a default output.
            # We just created the default output, remove it from the list of the sockets to be craeted.
            outputSockets = [s for s in outputSockets if s['name'] != '_default_']
    elif not pluginOutputParams:
        # If any output parameters are defined, we should not add the default output for the plugin type
        _addDefaultOutputForPluginCategory(node, pluginModule)

    # Order the parameters of the plugin the same way as the ones included in the 'output_sockets' list.
    if outputSockets and getattr(pluginModule, 'Node'):
        # There is no other way to order the node sockets except create them in the desired order.
        # The parameters that are not on the list are moved to the back without keeping any order. The order
        # doesn't matter in this case becuase they will not be visible in the node.
        lastItem = len(outputSockets)
        orderedNames = [n['name'] for n in outputSockets]
        pluginOutputParams = sorted(pluginOutputParams, key=lambda item: lastItem if item['attr'] not in orderedNames else orderedNames.index(item['attr']))

    for attr in pluginOutputParams:
        attrName = attr['attr']
        attrType = attr['type']
        attrOptions = attr.get('options', {})
        socketName = getAttrDisplayName(attr)
        description = socketName
        sockVisible = True

        if not (socketType := _getOutputSocketType(attrType)):
            # Some socket types may need to be skipped depending on the attribute type
            continue

        # If an output socket list is found in the plugin description, use it to determine how and 
        # which sockets to show
        if overrideDesc := next((s for s in outputSockets if 'name' in s and s['name'] == attrName), None):
            attrType    = overrideDesc.get('type', attrType)
            socketName  = overrideDesc.get('label', socketName)
            description = overrideDesc.get('desc', socketName)
            sockVisible = overrideDesc.get('visible', True)

        if not attrOptions.get('visible', True) or (outputSockets and (overrideDesc is None)):
            continue

        sock = addOutput(node, socketType, socketName, attrName, description)
        sock.enabled = sockVisible
        sock.hide = not sockVisible


def addInputsOutputs(node, vrayPlugin):
    """ Add all input and output sockets to the node """
    addInputs(node, vrayPlugin)
    addOutputs(node, vrayPlugin)


def createNode(ntree, nodeType, nodeName=None, unique=True):
    if nodeName is None:
        nodeName = f"{nodeType}_{lib_utils.getUUID()}"

    if nodeName in ntree.nodes:
        if not unique:
            return ntree.nodes[nodeName]
        else:
            nodeName = lib_utils.getUUID() + nodeName

    node = ntree.nodes.new(nodeType)
    node.name  = nodeName

    return node


MATERIAL_OPTION_PLUGIN_TYPES = (
    "VRayObjectProperties",
    "VRayMtlRenderStats",
    "VRayMtlRoundEdges"
)

def _nodeConnectedToObjOutput(node: bpy.types.Node, objTree: bpy.types.NodeTree):
    """ Check if the node is connected to the output node of the object node tree. """

    # Note: V-Ray Fur objects can have multiple output nodes, so we need to check all of them
    for treeType in ['OBJECT', 'FUR', 'DECAL']:
        if objTreeOutput := getOutputNode(objTree, treeType):
            if (node == objTreeOutput) or areNodesInterconnected(node, objTreeOutput):
                return True

    return False


def selectedObjectTagUpdate(self, context: bpy.types.Context):
    """ Triggers an update of the currently selected object or node tree and a redraw of the 
        property editor. This function is set as the 'update' callback of node sockets 
        and properties. It is needed because Blender does not generate update events 
        for changes to custom node trees.
    """
    activeEditor = context.scene.vray.ActiveNodeEditorType
    
    if isinstance(self, bpy.types.Node):
        # For manually defined nodes (e.g. V-Ray Transform), property updates have 'self' set to the node, not to the node tree.
        # Tag an update on the node tree.

        # Nodes from object node trees are tagging for update the object otherwise the changes won't be exported.
        if (activeEditor == "OBJECT") and _nodeConnectedToObjOutput(self, self.id_data):
            context.active_object.update_tag()
        else:
            self.id_data.update_tag()

        return

    if hasattr( context, "active_object"):
        ob = context.active_object

        if not hasattr(ob, "type"):
            return

        match ob.type:
            case 'MESH'| 'META' | 'SURFACE' | 'FONT' | 'CURVE' | 'CURVES' | 'POINTCLOUD' | 'VOLUME':
                if activeEditor == "OBJECT":
                    # If the opened node tree editor is 'OBJECT'
                    # and the node of the property group is linked to output node,
                    # the active object is tagged for geometry updates
                    if ob.vray and ob.vray.ntree:
                        node = self.node if isinstance(self, bpy.types.NodeSocket) else getNodeOfPropGroup(self)

                        if _nodeConnectedToObjOutput(node, ob.vray.ntree): 
                            tagUsersForUpdate(ob.vray.ntree)

                elif (activeEditor == 'SHADER') and (mtl := ob.active_material) and mtl.node_tree: # We are parsing material node tree

                    srcType = type(self).__name__

                    # For changes to material option plugins, tag the respective material topologies
                    # as updated. We might do this with better resolution, but need to track the specific
                    # property that has changed which is not convenient in this function.
                    if srcType == 'VRayMtlMaterialID':
                        UpdateTracker.tagMtlTopology(context, mtl)

                    elif srcType in MATERIAL_OPTION_PLUGIN_TYPES:
                        parentType = type(self.id_data).__name__

                        match parentType:
                            case 'Material':
                                # Tag both material and object's material options, because both should be exported
                                UpdateTracker.tagMtlTopology(context, mtl)
                            case 'Object':
                                UpdateTracker.tagUpdate(ob, UpdateTarget.OBJECT_MTL_OPTIONS, UpdateFlags.TOPOLOGY)
                            case 'ShaderNodeTree':
                                # A node's property has changed
                                UpdateTracker.tagUpdate(mtl, UpdateTarget.MATERIAL, UpdateFlags.DATA)
                    else:
                        UpdateTracker.tagUpdate(mtl, UpdateTarget.MATERIAL, UpdateFlags.DATA)


            case 'LIGHT':
                if activeEditor == 'SHADER':
                    # A Light node's property has changed
                    UpdateTracker.tagUpdate(ob.data, UpdateTarget.LIGHT, UpdateFlags.DATA)

                    # For lights, it is currently not possible to tag the node trees, so tag the object
                    # itself. This will trigger a node tree update
                    ob.update_tag()

    tagRedrawPropertyEditor()


def activeAttributeUpdateCallback(propGroup, pluginModule, attrName: str):
    """ Callback for 'active' socket attributes' (i.e. attributes with conditions) """
    inputSockets = pluginModule.Node.get('input_sockets', [])

    node = getNodeOfPropGroup(propGroup)
    for sockDesc in inputSockets:
        if (visibleCond := sockDesc.get('visible', None)) and isCondition(visibleCond):
            visible = evaluateCondition(propGroup, node, visibleCond)
            sock = getInputSocketByAttr(node, sockDesc['name'])

            if (sock.hide != (not visible)) or (sock.enabled != visible):
                if sock.hide:
                    removeSocketLinks(sock)
                sock.hide = not visible
                sock.enabled = visible


def customAttributeUpdateCallback(propGroup, context: bpy.types.Context, pluginModule, attrName, updateFuncPath: str):
    """ Callback for custom update functions on plugin attributes.

        @param updateFuncPath - the path to the custom function to call before selectedObjectTagUpdate.
                            By default, this is the name of a function in the plugin module.
                            If the path contains a '.', this is a path of any package relative to vray_blender.
    """
    if fnUpdate := importFunction(updateFuncPath, pluginModule):
        fnUpdate(propGroup, context, attrName)


def selectedObjectTagUpdateWrapper(propGroup, context: bpy.types.Context, pluginModule, attrName, updateCallbacks: list):
    """ Wrap invocation of the selectedObjectTagUpdate function, calling in addition a custom function.

        @param modulePath - the path to the custom function to call before selectedObjectTagUpdate.
                            By default, this is the name of a function in the plugin module.
                            If the path contains a '.', this is a path of any package relative to vray_blender.
    """
    from vray_blender.nodes.utils import selectedObjectTagUpdate

    for fnUpdate in updateCallbacks:
        fnUpdate(propGroup, context, pluginModule, attrName)

    selectedObjectTagUpdate(propGroup, context)


def findDataObjFromNode(coll, node: bpy.types.Node, isObjTreeNode=False):
    """ Get the parent data object of a node in a nodetree """
    ntree = node.id_data
    assert isinstance(ntree, bpy.types.NodeTree), "Node should be attached to a nodetree"

    if isObjTreeNode:
         return next((o for o in coll if o.vray.ntree == ntree), None)

    return next((o for o in coll if o.node_tree == ntree), None)


def tagObjectsForMaterial(mtl: bpy.types.Material):
    """ Tag for update all objects that are using the material """ 
    for obj in bpy.context.scene.objects:
        match obj.type:
            case 'MESH'| 'META' | 'SURFACE' | 'FONT' | 'CURVE' | 'CURVES' | 'POINTCLOUD' | 'VOLUME':
                # For geometry, tag the active material, if any
                if any([s.material for s in obj.material_slots if s.material == mtl]):
                    UpdateTracker.tagUpdate(obj, UpdateTarget.OBJECT_MTL_OPTIONS, UpdateFlags.TOPOLOGY)



def tagRedrawArea(areaType):
    """ Tag area for redraw """
    if not hasattr(bpy.context.screen, 'areas'):
        return

    for area in bpy.context.screen.areas:
        if area.type == areaType:
            area.tag_redraw()

def tagRedrawPropertyEditor():
    """ Tag property pages area for redraw """
    tagRedrawArea('PROPERTIES')


def tagRedrawNodeEditor():
    """ Tag property pages area for redraw """
    tagRedrawArea('NODE_EDITOR')


def tagRedrawViewport():
    """ Tag 3D Viewport for redraw """
    tagRedrawArea('VIEW3D')



def getObjectsFromSelector(node: bpy.types.Node, context: bpy.types.Context):
    """ Get the list of objects from a selector node.

        @param node - a selector node
        @return - a list of all objects selected by the selector node
    """

    sceneObjects = bpy.context.scene.objects

    match node.bl_idname:
        case "VRayNodeSelectObjectGeometry":
            if hasattr(node, "objectName") and node.objectName in sceneObjects:
                ob = sceneObjects[node.objectName]
                if ob.type == 'MESH':
                    return [ob]

        case  "VRayNodeSelectObject":
            selected = node.getSelected(context)
            return [selected] if selected else []

        case  "VRayNodeMultiSelect":
            return node.getSelected(context)

    # We don't care if a non-selector node is plugged into the socket or nothing is returned 
    # by the selector. In both cases, no error is show and an empty list is returned.
    return []

def treeHasNodes(ntree):
    return ntree and ntree.nodes


_OUTPUT_NODE_TYPES = {
        'WORLD'     : 'VRayNodeWorldOutput',
        'MATERIAL'  : 'VRayNodeOutputMaterial',
        'OBJECT'    : 'VRayNodeObjectOutput',
        'FUR'       : 'VRayNodeFurOutput',
        'DECAL'     : 'VRayNodeDecalOutput',

        'SHADER'    : 'ShaderNodeOutputMaterial' # Cycles Material Output
    }


def getOutputNode(ntree, treeType = '')-> bpy.types.Node | None:
    """ Get the root node of a node tree """
    if not treeType:
        treeType = ntree.vray.tree_type

    if not (outputNodeType := _OUTPUT_NODE_TYPES.get(treeType)):
        return None

    return getNodeByType(ntree, outputNodeType)


def getChannelsOutputNode(worldTree: bpy.types.NodeTree):
    if outputNode := getOutputNode(worldTree, 'WORLD'):
        channelsSocket = getInputSocketByName(outputNode, 'Channels')
        if channelsLink := getFarNodeLink(channelsSocket):
            return channelsLink.from_node
    return None


def getLightOutputNode(lightNtree: bpy.types.NodeTree):
    """ Return the root node of a light node tree. Lights node trees are different from 
        those of other types in that there is no single output node type but each light type
        has its own output node type.
    """
    return next((n for n in lightNtree.nodes if isVrayNode(n) and (n.vray_type == 'LIGHT')), None)


def getActiveTreeNode(ntree: bpy.types.NodeTree, treeType: str):
    """ Return the active (selected) node in a tree.

        @param ntree - the node tree to search
        @param treeType - the type of the tree (see getOutputNode())
        @return Return the selected node or None if there is no selection. If more than 1 nodes are
                selected, return the Output node with priority, or the last node in the list.
    """

    if not treeHasNodes(ntree):
        return None

    selectedNodes = [x for x in ntree.nodes if x.select]
    if not selectedNodes:
        return None

    if len(selectedNodes) == 1:
        return selectedNodes[-1]
    else:
        outputNode = getOutputNode(ntree, treeType)
        isOutputNodeSelected = any(n for n in selectedNodes if n.bl_idname == outputNode.bl_idname) if outputNode else False
        return outputNode if isOutputNodeSelected else selectedNodes[-1]


def getVrayPropGroup(node: bpy.types.Node):
    return getattr(node, getattr(node, 'vray_plugin', ''), None)