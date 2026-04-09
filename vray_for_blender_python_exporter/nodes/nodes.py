# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy

from vray_blender import debug
from vray_blender.nodes.customRenderChannelNodes import customRenderChannelNodesDesc
from vray_blender.nodes import utils as NodeUtils, specials
from vray_blender.nodes.tools import isVrayNode
from vray_blender.plugins import PLUGINS, getPluginModule
from vray_blender.exporting.tools import resolveInternalLink
from vray_blender.exporting.update_tracker import UpdateFlags, UpdateTracker, UpdateTarget
from vray_blender.lib import class_utils, draw_utils, blender_utils, lib_utils
from vray_blender.lib.names import syncObjectUniqueName
from vray_blender.lib.mixin import VRayNodeBase
from vray_blender.ui import classes
from vray_blender.plugins.skipped_plugins import MANUALLY_CREATED_PLUGINS, SKIPPED_PLUGINS, HIDDEN_PLUGINS


VRayNodeTypes = {
    'BRDF'          : [],
    'EFFECT'        : [],
    'GEOMETRY'      : [],
    'LIGHT'         : [],
    'MATERIAL'      : [],
    'TEXTURE'       : [],
    'UVWGEN'        : [],
    'RENDERCHANNEL' : [],
    'MISC'          : [],
}


VRayNodeTypeIcon = {
    'BRDF'          : 'SHADING_TEXTURE',
    'EFFECT'        : 'GHOST_ENABLED',
    'GEOMETRY'      : 'MESH_DATA',
    'LIGHT'         : 'LIGHT',
    'MATERIAL'      : 'MATERIAL',
    'TEXTURE'       : 'TEXTURE',
    'UVWGEN'        : 'GROUP_UVS',
    'RENDERCHANNEL' : 'SCENE_DATA',
}


from vray_blender.lib.attribute_utils import getAttrDesc

def _getSearchableEnumVariants(nodePlugin, attrName, labelIdx=1):
    ''' Extract (Value, Label) from an ENUM parameter in the plugin description. '''
    pluginModule = getPluginModule(nodePlugin)
    if not pluginModule:
        return []

    if attrDesc := getAttrDesc(pluginModule, attrName):
        return [(item[0], item[labelIdx]) for item in attrDesc.get('items', [])]
    return []

# Wrapper for the blender swap operator to disable auto connect
# which causes problems with render elements and effects.
class VRAY_OT_swap_node(bpy.types.Operator):
    bl_idname = 'vray.swap_node'
    bl_label = 'Swap V-Ray Node'

    type: bpy.props.StringProperty(
        name="Node Type",
        description="Node type",
    )

    @classmethod
    def poll(cls, context):
        return bpy.ops.node.swap_node.poll()

    def execute(self, context):
        with NodeUtils.DisableAutoConnect():
            bpy.ops.node.swap_node('INVOKE_DEFAULT', type=self.type)

        return {'FINISHED'}

def _nodeOperator(layout, nodeType, label=None, icon=None, searchWeight=0.0, isInSwapMenu = False):
    if label is None:
        bl_rna = bpy.types.Node.bl_rna_get_subclass(nodeType)
        label = bl_rna.name if bl_rna else nodeType

    # Make sure all V-Ray nodes' labels are prefixed with 'V-Ray'
    if not label.startswith('V-Ray') and nodeType.startswith('VRayNode'):
        label = f'V-Ray {label}'

    iconVal = 0
    if icon:
        from vray_blender.ui import icons
        iconVal = icons.getIcon(icon)

    opName = 'vray.swap_node' if isInSwapMenu else 'node.add_node'
    props = layout.operator(opName, text=label, search_weight=searchWeight, icon_value=iconVal)
    props.type = nodeType
    if not isInSwapMenu:
        props.use_transform = True
    return props


def _nodeOperatorWithSearchableEnumSocket(context: bpy.types.Context, layout: bpy.types.UILayout, nodeIdName: str, socketIdentifier: str, enumItems: list, callbackFunc = None, searchWeight=-1.0, isInSwapMenu = False):
    if not isInSwapMenu and getattr(context, 'is_menu_search', False):
        bl_rna = bpy.types.Node.bl_rna_get_subclass(nodeIdName)
        nodeLabel = bl_rna.name if bl_rna else nodeIdName

        for item in enumItems:
            if isinstance(item, tuple):
                value, labelText = item
            else:
                value, labelText = item, item

            label = f'{nodeLabel} \u25B8 {labelText}'
            if not label.startswith('V-Ray') and nodeIdName.startswith('VRayNode'):
                label = f'V-Ray {label}'

            props = layout.operator('node.add_node', text=label, search_weight=searchWeight)
            props.type = nodeIdName
            props.use_transform = True

            if socketIdentifier:
                prop = props.settings.add()
                prop.name = socketIdentifier
                # Blender calls eval(value), so we pass it as escaped string...
                prop.value = repr(value)

            if callbackFunc:
                callbackFunc(props, value)


def buildItemsList(nodeType, subType=None):
    def _hidePlugin(pluginName):
        if pluginName in SKIPPED_PLUGINS or pluginName in HIDDEN_PLUGINS:
            return True
        _name_filter = ('Maya', 'TexMaya', 'MtlMaya', 'TexModo', 'TexXSI', 'texXSI', 'volumeXSI')
        if pluginName.startswith(_name_filter):
            return True
        if any(x in pluginName for x in ('ASGVIS', 'C4D', 'Modo')):
            return True
        return False

    menuItems = []
    for t in VRayNodeTypes[nodeType]:
        pluginName = t.bl_rna.identifier.replace('VRayNode', '')
        if _hidePlugin(pluginName):
            continue

        pluginSubtype = getattr(t, 'vray_menu_subtype', '')
        if subType is None:
            if pluginSubtype: continue
        else:
            if subType != pluginSubtype: continue

        menuItems.append((t.bl_rna.identifier, t.bl_label))
    return menuItems


class VRayRenderChannelMenu(bpy.types.Menu):
    bl_options = { 'SEARCH_ON_KEY_PRESS' }

    @staticmethod
    def drawMenu(layout, context, menu_type, isInSwapMenu = False):
        nodesList = buildItemsList('RENDERCHANNEL', menu_type)

        # Use a higher weight so render elements appear first when searching.
        for item_idname, item_label in nodesList:
            _nodeOperator(layout, item_idname, label=item_label, searchWeight=5.0, isInSwapMenu = isInSwapMenu)

    def draw(self, context):
        self.drawMenu(self.layout, context, self.menu_type)


_channelMenuClasses = []
_channelSwapMenuClasses = []

# Generates Node Menu classes for the render nodes
def _getRenderChannelMenuClasses(isInSwapMenu = False):
    global _channelMenuClasses
    global _channelSwapMenuClasses

    channelList = _channelSwapMenuClasses if isInSwapMenu else _channelMenuClasses

    if not channelList:
        from vray_blender.plugins.channel.RenderChannelsPanel import VRayChannelNodeSubtypes

        def makeDraw(st, sw):
            def draw(self, context):
                self.drawMenu(self.layout, context, st, isInSwapMenu = sw)
            return draw

        for channelSubtype in VRayChannelNodeSubtypes:
            channelSubtypeName = channelSubtype.title()

            prefix = 'Swap' if isInSwapMenu else ''
            idnameInfix = '_VRAY_SWAP_' if isInSwapMenu else '_VRAY_'

            vRayRenderChannelType = type(
                f'VRay{prefix}{channelSubtypeName}ChannelsMenu',
                (VRayRenderChannelMenu,),
                {
                    'bl_label': f'{channelSubtypeName}',
                    'bl_idname': f'NODE_MT{idnameInfix}{channelSubtypeName.upper()}_CHANNELS_MENU',
                    'menu_type': channelSubtype,
                    'bl_options': { 'SEARCH_ON_KEY_PRESS' },
                    'draw': makeDraw(channelSubtype, isInSwapMenu)
                }
            )
            channelList.append(vRayRenderChannelType)

    return channelList

##     ## ######## ##    ## ##     ##
###   ### ##       ###   ## ##     ##
#### #### ##       ####  ## ##     ##
## ### ## ######   ## ## ## ##     ##
##     ## ##       ##  #### ##     ##
##     ## ##       ##   ### ##     ##
##     ## ######## ##    ##  #######


def _pollObjectNodeTreeSelected(context):
    return context.scene.vray.ActiveNodeEditorType == "OBJECT"


def _pollFurNodeTreeSelected(context):
    return _pollObjectNodeTreeSelected(context) and _pollObjectOfContextIsFur(context)

def _pollDecalNodeTreeSelected(context):
    return context.scene.vray.ActiveNodeEditorType == "OBJECT" and _pollObjectOfContextIsDecal(context)

def _pollMaterialNodeTreeSelected(context):
    return context.object and \
        context.object.type in blender_utils.TypesThatSupportMaterial and \
        context.scene.vray.ActiveNodeEditorType == "SHADER"


def _pollWorldNodeTreeSelected(context):
    return context.scene.vray.ActiveNodeEditorType == "WORLD"


def _pollObjectOfContextIsFur(context):
    return context.object and hasattr(context.object, "vray") and context.object.vray.isVRayFur

def _pollObjectOfContextIsDecal(context):
    return context.object and hasattr(context.object, "vray") and context.object.vray.isVRayDecal


def _vrayMenuPoll(context):
    if not (spaceData := context.space_data):
        return False
    if spaceData.tree_type != 'VRayNodeTreeEditor':
        return False

    vrayType = context.scene.vray.ActiveNodeEditorType
    if vrayType == 'WORLD':
        return bool(context.scene.world and context.scene.world.node_tree)
    if vrayType == 'OBJECT':
        ob = context.object
        return bool(ob and ob.vray.ntree)

    return True


class NODE_MT_vray_add_material(bpy.types.Menu):
    bl_label = 'Material'
    bl_idname = 'NODE_MT_vray_add_material'
    bl_options = { 'SEARCH_ON_KEY_PRESS' }

    @classmethod
    def poll(cls, context):
        return _pollMaterialNodeTreeSelected(context)

    @staticmethod
    def drawMenu(layout, context, isInSwapMenu = False):
        def _addMtl(type, label, icon=None):
            for t in VRayNodeTypes[type]:
                if t.bl_label == label:
                    _nodeOperator(layout, t.bl_rna.identifier, label=t.bl_label, icon=icon, isInSwapMenu = isInSwapMenu)

        _addMtl('BRDF', 'V-Ray Mtl',                   icon='MTL_VRAY')
        _addMtl('BRDF', 'V-Ray AL Surface Mtl',        icon='MTL_AL_SURFACE')
        _addMtl('BRDF', 'V-Ray Fast SSS2',             icon='MTL_FAST_SSS2')
        _addMtl('BRDF', 'V-Ray Blend Mtl',             icon='MTL_BLEND')
        _addMtl('BRDF', 'V-Ray Bump Mtl',              icon='MTL_BUMP')
        _addMtl('BRDF', 'V-Ray Light Mtl',             icon='MTL_LIGHT')
        _addMtl('BRDF', 'V-Ray Hair Next Mtl',         icon='MTL_HAIR_NEXT')
        _addMtl('BRDF', 'V-Ray Car Paint 2 Mtl',       icon='MTL_CAR_PAINT2')
        _addMtl('BRDF', 'V-Ray Flakes 2 Mtl',          icon='MTL_FLAKES')
        _addMtl('BRDF', 'V-Ray Scanned Mtl',           icon='MTL_SCANNED')
        _addMtl('BRDF', 'V-Ray Stochastic Flakes Mtl', icon='MTL_STOCHASTIC_FLAKES')
        _addMtl('BRDF', 'V-Ray Toon Mtl',              icon='MTL_TOON')
        _addMtl('MATERIAL', 'V-Ray Switch Mtl',        icon='MTL_SWITCH')
        _addMtl('MATERIAL', 'V-Ray Mtl 2Sided',        icon='MTL_2SIDED')
        _addMtl('MATERIAL', 'V-Ray Mtl Override',      icon='MTL_OVERRIDE')
        _addMtl('MATERIAL', 'V-Ray Displacement Mtl',  icon='MTL_DISPLACEMENT')
        _addMtl('MATERIAL', 'V-Ray VRmat Mtl',         icon='MTL_VRMAT')

    def draw(self, context):
        self.drawMenu(self.layout, context)


class NODE_MT_vray_add_textures(bpy.types.Menu):
    bl_label = 'Textures'
    bl_idname = 'NODE_MT_vray_add_textures'
    bl_options = { 'SEARCH_ON_KEY_PRESS' }

    @staticmethod
    def drawMenu(layout, context, isInSwapMenu = False):
        _nodeOperator(layout, 'VRayNodeMetaImageTexture', isInSwapMenu = isInSwapMenu)
        _nodeOperator(layout, 'VRayNodeColorRamp', isInSwapMenu = isInSwapMenu)

        # TexSampler with searchable outputs, outputs are enabled by the internal_sampler update callback.
        _nodeOperatorWithSearchableEnumSocket(
            context, layout, 'VRayNodeTexSampler', 'TexSampler.internal_sampler',
            _getSearchableEnumVariants('TexSampler', 'samplers'), isInSwapMenu = isInSwapMenu
        )

        _nodeOperatorWithSearchableEnumSocket(
            context, layout, 'VRayNodeTexUserColor', 'TexUserColor.mode',
            _getSearchableEnumVariants('TexUserColor', 'mode'), isInSwapMenu = isInSwapMenu
        )

        _nodeOperatorWithSearchableEnumSocket(
            context, layout, 'VRayNodeTexAColorOp', 'TexAColorOp.mode',
            _getSearchableEnumVariants('TexAColorOp', 'mode'), isInSwapMenu = isInSwapMenu
        )

        _nodeOperatorWithSearchableEnumSocket(
            context, layout, 'VRayNodeTexFloatOp', 'TexFloatOp.mode',
            _getSearchableEnumVariants('TexFloatOp', 'mode'), isInSwapMenu = isInSwapMenu
        )

        for idname, label in buildItemsList('TEXTURE'):
            _nodeOperator(layout, idname, label=label, isInSwapMenu = isInSwapMenu)

    def draw(self, context):
        self.drawMenu(self.layout, context)


class NODE_MT_vray_add_texture_utilities(bpy.types.Menu):
    bl_label = 'Texture Utilities'
    bl_idname = 'NODE_MT_vray_add_texture_utilities'
    bl_options = { 'SEARCH_ON_KEY_PRESS' }

    @staticmethod
    def drawMenu(layout, context, isInSwapMenu = False):
        for idname, label in buildItemsList('TEXTURE', 'UTILITY'):
            _nodeOperator(layout, idname, label=label, isInSwapMenu = isInSwapMenu)

    def draw(self, context):
        self.drawMenu(self.layout, context)


class NODE_MT_vray_add_mapping(bpy.types.Menu):
    bl_label = 'Mapping'
    bl_idname = 'NODE_MT_vray_add_mapping'
    bl_options = { 'SEARCH_ON_KEY_PRESS' }

    @staticmethod
    def drawMenu(layout, context, isInSwapMenu = False):
        _nodeOperator(layout, 'VRayNodeUVWMapping', isInSwapMenu = isInSwapMenu)
        _nodeOperator(layout, 'VRayNodeUVWGenRandomizer', isInSwapMenu = isInSwapMenu)

        _nodeOperatorWithSearchableEnumSocket(
            context, layout, 'VRayNodeUVWMapping', 'mapping_node_type',
            (
                ('UV',          'UV mapping'),
                ('PROJECTION',  'Projection mapping'),
                ('OBJECT',      'Object mapping'),
                ('ENVIRONMENT', 'Environment mapping')
            ), isInSwapMenu = isInSwapMenu
        )

        def _projMappingFunc(props: bpy.types.OperatorProperties, value: str):
            prop = props.settings.add()
            prop.name = 'mapping_node_type'
            prop.value = '\'PROJECTION\''

        _nodeOperatorWithSearchableEnumSocket(
            context, layout, 'VRayNodeUVWMapping', 'UVWGenProjection.type',
            (
                ('1', 'Planar mapping'),
                ('2', 'Spherical mapping'),
                ('3', 'Cylindrical mapping'),
                ('5', 'Cubic mapping'),
                ('8', 'Perspective mapping'),
            ),
            callbackFunc=_projMappingFunc, isInSwapMenu = isInSwapMenu
        )

    def draw(self, context):
        self.drawMenu(self.layout, context)


class NODE_MT_vray_add_geometry(bpy.types.Menu):
    bl_label = 'Geometry'
    bl_idname = 'NODE_MT_vray_add_geometry'
    bl_options = { 'SEARCH_ON_KEY_PRESS' }

    @classmethod
    def poll(cls, context):
        return _pollObjectNodeTreeSelected(context) and not _pollObjectOfContextIsFur(context)

    @staticmethod
    def drawMenu(layout, context, isInSwapMenu = False):
        _nodeOperator(layout, 'VRayNodeDisplacement', isInSwapMenu = isInSwapMenu)
        _nodeOperator(layout, 'VRayNodeGeomStaticSmoothedMesh', isInSwapMenu = isInSwapMenu)

        _nodeOperatorWithSearchableEnumSocket(context, layout, 'VRayNodeDisplacement', 'GeomDisplacedMesh.type',
            _getSearchableEnumVariants('GeomDisplacedMesh', 'type', 2), isInSwapMenu = isInSwapMenu
        )

    def draw(self, context):
        self.drawMenu(self.layout, context)


class NODE_MT_vray_add_object_properties(bpy.types.Menu):
    bl_label = 'Object Properties'
    bl_idname = 'NODE_MT_vray_add_object_properties'
    bl_options = { 'SEARCH_ON_KEY_PRESS' }

    @classmethod
    def poll(cls, context):
        return _pollObjectNodeTreeSelected(context)

    @staticmethod
    def drawMenu(layout, context, isInSwapMenu = False):
        _nodeOperator(layout, 'VRayNodeObjectMatteProps', isInSwapMenu = isInSwapMenu)
        _nodeOperator(layout, 'VRayNodeObjectSurfaceProps', isInSwapMenu = isInSwapMenu)
        _nodeOperator(layout, 'VRayNodeObjectVisibilityProps', isInSwapMenu = isInSwapMenu)

        if not _pollDecalNodeTreeSelected(context) and not _pollFurNodeTreeSelected(context):
            _nodeOperatorWithSearchableEnumSocket(
                context, layout, 'VRayNodeObjectMatteProps', 'make_shadow_catcher',
                ((True, 'Shadow Catcher'),), isInSwapMenu = isInSwapMenu
            )

    def draw(self, context):
        self.drawMenu(self.layout, context)


class NODE_MT_vray_add_math(bpy.types.Menu):
    bl_label = 'Math'
    bl_idname = 'NODE_MT_vray_add_math'
    bl_options = { 'SEARCH_ON_KEY_PRESS' }

    @staticmethod
    def drawMenu(layout, context, isInSwapMenu = False):
        _nodeOperator(layout, 'VRayNodeTransform', isInSwapMenu = isInSwapMenu)
        _nodeOperator(layout, 'VRayNodeMatrix', isInSwapMenu = isInSwapMenu)
        _nodeOperator(layout, 'VRayNodeVector', isInSwapMenu = isInSwapMenu)

    def draw(self, context):
        self.drawMenu(self.layout, context)


class NODE_MT_vray_add_output(bpy.types.Menu):
    bl_label = 'Output'
    bl_idname = 'NODE_MT_vray_add_output'
    bl_options = { 'SEARCH_ON_KEY_PRESS' }

    @staticmethod
    def drawMenu(layout, context, isInSwapMenu = False):
        if _pollMaterialNodeTreeSelected(context):
            _nodeOperator(layout, 'VRayNodeOutputMaterial', isInSwapMenu = isInSwapMenu)
        if _pollWorldNodeTreeSelected(context):
            _nodeOperator(layout, 'VRayNodeWorldOutput', isInSwapMenu = isInSwapMenu)
        if _pollObjectNodeTreeSelected(context) and not _pollDecalNodeTreeSelected(context):
            _nodeOperator(layout, 'VRayNodeObjectOutput', isInSwapMenu = isInSwapMenu)
        if _pollFurNodeTreeSelected(context):
            _nodeOperator(layout, 'VRayNodeFurOutput', isInSwapMenu = isInSwapMenu)
        if _pollDecalNodeTreeSelected(context):
            _nodeOperator(layout, 'VRayNodeDecalOutput', isInSwapMenu = isInSwapMenu)

    def draw(self, context):
        self.drawMenu(self.layout, context)


class NODE_MT_vray_add_selectors(bpy.types.Menu):
    bl_label = 'Selectors'
    bl_idname = 'NODE_MT_vray_add_selectors'
    bl_options = { 'SEARCH_ON_KEY_PRESS' }

    @staticmethod
    def drawMenu(layout, context, isInSwapMenu = False):
        _nodeOperator(layout, 'VRayNodeSelectObject', isInSwapMenu = isInSwapMenu)
        _nodeOperator(layout, 'VRayNodeMultiSelect', isInSwapMenu = isInSwapMenu)

    def draw(self, context):
        self.drawMenu(self.layout, context)


class NODE_MT_vray_add_environment(bpy.types.Menu):
    bl_label = 'Environment'
    bl_idname = 'NODE_MT_vray_add_environment'
    bl_options = { 'SEARCH_ON_KEY_PRESS' }

    @classmethod
    def poll(cls, context):
        return _pollWorldNodeTreeSelected(context)

    @staticmethod
    def drawMenu(layout, context, isInSwapMenu = False):
        _nodeOperator(layout, 'VRayNodeEnvironment', isInSwapMenu = isInSwapMenu)

    def draw(self, context):
        self.drawMenu(self.layout, context)


class NODE_MT_vray_add_effects(bpy.types.Menu):
    bl_label = 'Effects'
    bl_idname = 'NODE_MT_vray_add_effects'
    bl_options = { 'SEARCH_ON_KEY_PRESS' }

    @classmethod
    def poll(cls, context):
        return _pollWorldNodeTreeSelected(context)

    @staticmethod
    def drawMenu(layout, context, isInSwapMenu = False):
        _nodeOperator(layout, 'VRayNodeEffectsHolder', isInSwapMenu = isInSwapMenu)
        for idname, label in buildItemsList('EFFECT'):
            _nodeOperator(layout, idname, label=label, isInSwapMenu = isInSwapMenu)

    def draw(self, context):
        self.drawMenu(self.layout, context)


class NODE_MT_vray_add_render_channels(bpy.types.Menu):
    bl_label = 'Render Channels'
    bl_idname = 'NODE_MT_vray_add_render_channels'
    bl_options = { 'SEARCH_ON_KEY_PRESS' }

    @classmethod
    def poll(cls, context):
        return _pollWorldNodeTreeSelected(context)

    @staticmethod
    def drawMenu(layout, context, isInSwapMenu = False):
        # Use a higher weight so render elements appear first when searching.
        if not isInSwapMenu:
            _nodeOperator(layout, 'VRayNodeRenderChannels', label='V-Ray Channels Container', searchWeight=5.0, isInSwapMenu = isInSwapMenu)

        for channelMenuClass in _getRenderChannelMenuClasses(isInSwapMenu = isInSwapMenu):
            layout.menu(channelMenuClass.bl_idname)

    def draw(self, context):
        self.drawMenu(self.layout, context)


class NODE_MT_vray_add_layout(bpy.types.Menu):
    bl_label = 'Layout'
    bl_idname = 'NODE_MT_vray_add_layout'
    bl_options = { 'SEARCH_ON_KEY_PRESS' }

    @staticmethod
    def drawMenu(layout, context, isInSwapMenu = False):
        _nodeOperator(layout, 'VRayPluginListHolder', isInSwapMenu = isInSwapMenu)
        _nodeOperator(layout, 'NodeFrame', searchWeight=-1, isInSwapMenu = isInSwapMenu)
        _nodeOperator(layout, 'NodeReroute', isInSwapMenu = isInSwapMenu)

    def draw(self, context):
        self.drawMenu(self.layout, context)


class NODE_MT_vray_add(bpy.types.Menu):
    bl_label = 'Add'
    bl_idname = 'NODE_MT_vray_add'
    bl_options = { 'SEARCH_ON_KEY_PRESS' }

    @classmethod
    def poll(cls, context):
        return _vrayMenuPoll(context)

    def draw(self, context):
        layout = self.layout
        layout.operator_context = 'INVOKE_DEFAULT'
        _drawVRayAddMenuContents(layout)


 ######  ##          ###     ######   ######     ##     ## ######## ######## ##     ##  #######  ########   ######
##    ## ##         ## ##   ##    ## ##    ##    ###   ### ##          ##    ##     ## ##     ## ##     ## ##    ##
##       ##        ##   ##  ##       ##          #### #### ##          ##    ##     ## ##     ## ##     ## ##
##       ##       ##     ##  ######   ######     ## ### ## ######      ##    ######### ##     ## ##     ##  ######
##       ##       #########       ##       ##    ##     ## ##          ##    ##     ## ##     ## ##     ##       ##
##    ## ##       ##     ## ##    ## ##    ##    ##     ## ##          ##    ##     ## ##     ## ##     ##       ##
 ######  ######## ##     ##  ######   ######     ##     ## ########    ##    ##     ##  #######  ########   ######


def vrayNodeDraw(self, context, layout):
    """ Draw widgets on a node. These widgets are positioned after the output sockets
        and before the input sockets.
    """
    if not hasattr(self, 'vray_type') or not hasattr(self, 'vray_plugin'):
        return

    vrayPlugin = PLUGINS[self.vray_type][self.vray_plugin]
    propGroup  = getattr(self, self.vray_plugin)

    # Draw node properties using 'nodeDraw'
    #
    if hasattr(vrayPlugin, 'nodeDraw'):
        vrayPlugin.nodeDraw(context, layout, self)

    elif hasattr(vrayPlugin, 'Node') and 'widgets' in vrayPlugin.Node:
        uiPainter = draw_utils.UIPainter(context, vrayPlugin, propGroup, node = self)
        uiPainter.renderWidgets(layout, vrayPlugin.Node['widgets'], True)


def vrayNodeDrawSide(self: bpy.types.Node, context: bpy.types.Context, layout: bpy.types.UILayout):
    """ Draw widgets in the node's sidebar """
    if not hasattr(self, 'vray_type') or not hasattr(self, 'vray_plugin'):
        return

    vrayPlugin = PLUGINS[self.vray_type][self.vray_plugin]

    if hasattr(vrayPlugin, 'nodeDrawSide'):
        vrayPlugin.nodeDrawSide(context, layout, self)
    else:
        classes.drawPluginUI(
            context,
            layout,
            getattr(self, self.vray_plugin), # PropertyGroup
            vrayPlugin,                      # Plugin module
            self                             # node
        )


def setUniqueRenderChannelName(node: bpy.types.Node, isNewNode: bool):
    """ Set a unique channel name. By default, the name of the channel is set to be
        the name as the name of the render channel. If this name is already in use for another
        render channel however, create a new unique name by adding a numeric suffix.
    """
    propGroup = NodeUtils.getVrayPropGroup(node)
    channelName = propGroup.name

    attempt = 1
    while any([n for n in node.id_data.nodes if isVrayNode(n) and (n.vray_type == 'RENDERCHANNEL') \
                                                and n != node \
                                                and NodeUtils.getVrayPropGroup(n).name == channelName]):
        channelName = f"{propGroup.name} {attempt}"
        attempt += 1

    # Prevent infinite recursion
    if propGroup.name != channelName:
        propGroup.name = channelName


def _initTemplates(node: bpy.types.Node, pluginModule):
    if not (propGroup := NodeUtils.getVrayPropGroup(node)):
        return

    for attrDesc in [a for a in pluginModule.Parameters if a['type'] == 'TEMPLATE']:
        attrName = attrDesc['attr']
        templateInst = getattr(propGroup, attrName)
        if hasattr(templateInst, 'init'):
            templateInst.init(pluginModule, attrDesc)


def vrayNodeInit(self: bpy.types.Node, context):
    """ This is the init() function for automatically created VRay nodes """
    if not hasattr(self, 'vray_type') or self.vray_type == 'NONE':
        return
    if not hasattr(self, 'vray_plugin') or self.vray_plugin == 'NONE':
        return

    try:
        pluginModule = PLUGINS[self.vray_type][self.vray_plugin]

        # Set the unique ID of the node to the propety group. It will be
        # used to reach the node from the property group in callbacks which
        # do not supply node info. PointerProperty type cannot be used for the
        # node reference because it only supports types derived from bpy.types.ID
        # This function will also set the parent_node_id on the node's property group.
        syncObjectUniqueName(self, reset=True)

        NodeUtils.addInputsOutputs(self, pluginModule)

        if self.vray_type == 'RENDERCHANNEL':
            setUniqueRenderChannelName(self, isNewNode = True)

        if hasattr(pluginModule, "nodeInit"):
            # Give the node a chance to initialize plugin-specific state
            pluginModule.nodeInit(self)

        _initTemplates(self, pluginModule)

        NodeUtils.autoConnectNode(self)
    except Exception as ex:
        debug.printExceptionInfo(ex, f"Failed to init node: {self.vray_plugin}")
        raise ex


def vrayNodeCopy(self: bpy.types.Node, node: bpy.types.Node):
    """ Copy 'node' to 'self' """

    # NOTE: Do not try to access the values of the 'node's sockets in this function.
    # The reference from the socket to the node has not yet been set and any custom
    # setter/getter function will cause an access violation.

    # Create a new unique ID for the node and set the link to it in the property group
    syncObjectUniqueName(self, reset=True)

    if self.vray_plugin == 'NONE': # No properties for copying
        return

    propGroupCopy = getattr(self, self.vray_plugin)
    propGroupCopy.parent_node_id = self.unique_id

    pluginModule = PLUGINS[self.vray_type][self.vray_plugin]
    if hasattr(pluginModule, "nodeCopy"):
        # Give the node a chance to de-initialize plugin-specific state
        pluginModule.nodeCopy(self, node)

    # Assign unique names to copied render channel nodes.
    if hasattr(node, 'vray_type') and node.vray_type == "RENDERCHANNEL":
        setUniqueRenderChannelName(self, isNewNode = True)


def vrayNodeFree(self: bpy.types.Node):
    pluginModule = PLUGINS[self.vray_type][self.vray_plugin]
    if hasattr(pluginModule, "nodeFree"):
        # Give the node a chance to de-initialize plugin-specific state
        pluginModule.nodeFree(self)


@classmethod
def vrayNodePoll(cls: bpy.types.Node, nodetree: bpy.types.NodeTree):
    return hasattr(cls, 'vray') and hasattr(nodetree, 'vray')


def vrayNodeUpdate(self: VRayNodeBase):
    # This callback is invoked when the topology of a nodetree changes. For VRay custom nodes,
    # node tree updates are not generated by Blender for some reason (it seems that they are only
    # generated if any of the node's sockets is a stock Blender socket). This is why we need
    # to manually tag the nodetree as having been updated
    super(type(self), self).update()
    parentNodeTree = self.id_data

    # Call custom nodeUpdate() function, if defined
    pluginModule = getPluginModule(self.vray_plugin)
    if hasattr(pluginModule, "nodeUpdate"):
        pluginModule.nodeUpdate(self)

    # For V-Ray nodes, update the object whose nodetree has changed
    if hasattr(parentNodeTree, 'vray'):
        match parentNodeTree.vray.tree_type:
            case 'MATERIAL':
                if mtl := NodeUtils.findDataObjFromNode(bpy.data.materials, self):
                    UpdateTracker.tagMtlTopology(bpy.context, mtl)
            case 'OBJECT':
                if obj := NodeUtils.findDataObjFromNode(bpy.data.objects, self, isObjTreeNode=True):
                    obj.update_tag()
            case 'LIGHT':
                if light := NodeUtils.findDataObjFromNode(bpy.data.lights, self):
                    UpdateTracker.tagUpdate(light, UpdateTarget.LIGHT, UpdateFlags.TOPOLOGY)
            case 'WORLD':
                if self.vray_type == 'RENDERCHANNEL':
                    # Suppress updates for render channel nodes.
                    # They should be exported only once during the first "full" export
                    pass

    parentNodeTree.update_tag()
    updateNodeMutedState(self)


def updateNodeMutedState(node: bpy.types.Node):
    """ Show different shape for the sockets that are part of the
        currently active internal connections. This is a replacement
        for the built-in way of showing internal links which we cannot
        use because it is only applied to Cycles socket types, and
        V-Ray socket types are not derived from Cycles socket types.
    """

    if lib_utils.isRestrictedContext(bpy.context) or not bpy.context.region or not bpy.context.space_data:
        return

    # Reset all sockets to the original shape
    for s in list(node.outputs.values()) + list(node.inputs.values()):
        s.display_shape = 'CIRCLE'

    if not node.mute:
        return

    shape = 'DIAMOND_DOT'

    for outSock in node.outputs:
        if outSock.is_linked:
            inSock, _ = resolveInternalLink(outSock)
            if inSock:
                outSock.display_shape = shape
                inSock.display_shape = shape


########  ##    ## ##    ##    ###    ##     ## ####  ######     ##    ##  #######  ########  ########  ######
##     ##  ##  ##  ###   ##   ## ##   ###   ###  ##  ##    ##    ###   ## ##     ## ##     ## ##       ##    ##
##     ##   ####   ####  ##  ##   ##  #### ####  ##  ##          ####  ## ##     ## ##     ## ##       ##
##     ##    ##    ## ## ## ##     ## ## ### ##  ##  ##          ## ## ## ##     ## ##     ## ######    ######
##     ##    ##    ##  #### ######### ##     ##  ##  ##          ##  #### ##     ## ##     ## ##             ##
##     ##    ##    ##   ### ##     ## ##     ##  ##  ##    ##    ##   ### ##     ## ##     ## ##       ##    ##
########     ##    ##    ## ##     ## ##     ## ####  ######     ##    ##  #######  ########  ########  ######

# This list is used for registration/unregistration of the dynamic classes
_dynamicClasses = []


def createDynamicNodeClass(pluginType, pluginCategory, className, vrayPlugin, menuType = None, nodeLabel = '', pluginSubtype = ''):
    """ Creates the class for a custom node representing a VRay plugin. The class
        is created with only boilerplate code. Plugin-specific data and functions
        are added by the caller.
    """
    if not nodeLabel:
        nodeLabel = vrayPlugin.NAME

    DynNodeClassAttrs = {
        'bl_idname' : className,
        'bl_label'  : nodeLabel,
        # 'bl_icon'   : VRayNodeTypeIcon.get(pluginCategory, 'NONE'),
        # 'bl_menu'   : textureMenuType,

        'vray_menu_subtype' : pluginSubtype,

        'init'             : vrayNodeInit,
        'copy'             : vrayNodeCopy,
        'free'             : vrayNodeFree,
        'draw_buttons'     : vrayNodeDraw,
        'draw_buttons_ext' : vrayNodeDrawSide,
        'poll'             : vrayNodePoll,
        'update'           : vrayNodeUpdate,

        '__annotations__' : {
            'vray_type'   : bpy.props.StringProperty(default=pluginCategory),
            'vray_plugin' : bpy.props.StringProperty(default=pluginType),
        }
    }

    # Allow each plugin to add its own functions to the registered class attributes.
    # This is necessary in order to use the node in scenarios where only registered
    # functions are allowed, e.g. as callbacks passed in operator context. In order to
    # use this functionality, the plugin must define a 'nodeRegister' function which
    # should return a dictionary of functionName => function
    pluginModule = getPluginModule(pluginType)
    if fnRegister := getattr(pluginModule, 'nodeRegister', None):
        DynNodeClassAttrs.update(fnRegister())

    if menuType:
        DynNodeClassAttrs['bl_menu'] = menuType

    return type(
        className,          # Name
        (VRayNodeBase,),    # Inheritance
        DynNodeClassAttrs   # Attributes
    )


def _createAdditionalRenderChannelNodes():
    """ Register Node classes for the render channels with non-default 'alias' property set
        (channel variants).

        The nodes for the render channels with the default 'alias' value have been already
        created from the respective plugin descriptions, registered and added to the
        VRayNodeTypes["RENDERCHANNEL"] list.
    """
    global _dynamicClasses

    import copy
    channels = VRayNodeTypes["RENDERCHANNEL"]

    for customNode in customRenderChannelNodesDesc:
        # Make a copy of the original (default) plugin module and set the properties required
        # by the particular override
        pluginType = customNode["base_plugin_type"]
        vrayPlugin  = copy.deepcopy(PLUGINS["RENDERCHANNEL"][pluginType])

        # Set the overriding properties for the variant
        for param in vrayPlugin.Parameters:
            if param["attr"] in customNode["params"].keys():
                param["default"] = customNode["params"][param["attr"]]

        # Create the boilerplate for the node class
        nodeLabel = customNode['params']['name']
        variantClassName = f"VRayNodeRenderChannel{customNode['params']['name'].replace(' ', '').replace('-', '')}"

        # Make sure the names of all channels in the list are unique. Re-registering a class results in a
        # very hard to debug error.
        if any((c for c in channels if c.bl_idname ==  variantClassName)):
            debug.printError(f"Failed to register render channel variant {variantClassName} because it has" \
                              " the same class name as an already created channel")

        nodeClass = createDynamicNodeClass(pluginType, "RENDERCHANNEL", variantClassName, vrayPlugin,
                                            nodeLabel = nodeLabel, pluginSubtype = customNode["Subtype"])

        # Register the variant render channel class
        class_utils.registerPluginPropertyGroup(nodeClass, vrayPlugin, overridePropGroup = True)

        channels.append(nodeClass)

        _dynamicClasses.append(nodeClass)


def _loadDynamicNodes():
    """ Dynamic nodes are the ones that are created based only on their JSON descriptions.
        In contrast, there are a number of custom nodes for which node classes are defined explicitly
        in the ./nodes/specials/*.py files
    """
    global _dynamicClasses
    global VRayNodeTypes

    _dynamicClasses = []

    # Runtime Node classes generation
    #
    for pluginCategory in VRayNodeTypes:
        VRayNodeTypes[pluginCategory] = []

        for pluginType in sorted(PLUGINS[pluginCategory]):
            # Skip manually created nodes
            if pluginType in MANUALLY_CREATED_PLUGINS:
                continue

            if pluginType in  SKIPPED_PLUGINS:
                continue

            vrayPlugin  = PLUGINS[pluginCategory][pluginType]
            textureMenuType = getattr(vrayPlugin, 'MENU', None)

            DynNodeClassName = f'VRayNode{pluginType}'

            # Create the boilerplate for the node class
            DynNodeClass = createDynamicNodeClass(
                pluginType, pluginCategory, DynNodeClassName,
                vrayPlugin, textureMenuType,
                pluginSubtype=getattr(vrayPlugin, 'SUBTYPE', ''))

            if pluginType in { 'BitmapBuffer' }:
                NodeUtils.createFakeTextureAttribute(DynNodeClass, updateFunc=NodeUtils.selectedObjectTagUpdate)

            if pluginType == 'TexSoftbox':
                NodeUtils.createFakeTextureAttribute(DynNodeClass, 'ramp_grad_vert')
                NodeUtils.createFakeTextureAttribute(DynNodeClass, 'ramp_grad_horiz')
                NodeUtils.createFakeTextureAttribute(DynNodeClass, 'ramp_grad_rad')
                NodeUtils.createFakeTextureAttribute(DynNodeClass, 'ramp_frame')

            # Add fake texture attribute for TexGradRamp nodes to support legacy nodes
            # (created from plugins with UPGRADE_NUMBER < 31).
            if pluginType == 'TexGradRamp':
                NodeUtils.createFakeTextureAttribute(DynNodeClass, updateFunc=NodeUtils.selectedObjectTagUpdate)


            # Add a property group with all plugin's properties
            class_utils.registerPluginPropertyGroup(DynNodeClass, vrayPlugin)

            VRayNodeTypes[pluginCategory].append(DynNodeClass)

            _dynamicClasses.append(DynNodeClass)

    # Add manually defined classes
    VRayNodeTypes['MATERIAL'].append(specials.material.VRayNodeMtlMulti)
    VRayNodeTypes['MATERIAL'].append(specials.material.VRayNodeMtlOSL)

    VRayNodeTypes['TEXTURE'].append(specials.texture.VRayNodeTexLayeredMax)
    VRayNodeTypes['TEXTURE'].append(specials.material.VRayNodeTexOSL)

    # Sort texture list alphabetically. This will be the order the Texture nodes
    # will show on the Node Add list.
    VRayNodeTypes['TEXTURE'].sort(key=lambda e: e.bl_label)

    _createAdditionalRenderChannelNodes()


########  ########  ######   ####  ######  ######## ########     ###    ######## ####  #######  ##    ##
##     ## ##       ##    ##   ##  ##    ##    ##    ##     ##   ## ##      ##     ##  ##     ## ###   ##
##     ## ##       ##         ##  ##          ##    ##     ##  ##   ##     ##     ##  ##     ## ####  ##
########  ######   ##   ####  ##   ######     ##    ########  ##     ##    ##     ##  ##     ## ## ## ##
##   ##   ##       ##    ##   ##        ##    ##    ##   ##   #########    ##     ##  ##     ## ##  ####
##    ##  ##       ##    ##   ##        ##    ##    ##    ##  ##     ##    ##     ##  ##     ## ##   ###
##     ## ########  ######   ####  ######     ##    ##     ## ##     ##    ##    ####  #######  ##    ##


class NodeSidePropertiesDraw:
    """ Hook for the sidebar node properties panel draw function.
        When V-Ray is the active render engine, the panel will not show the
        'Inputs:' section.
    """
    original = None   # The original 'draw' function of NODE_PT_active_node_properties

    @staticmethod
    def hook():
        from bl_ui.space_node import NODE_PT_active_node_properties
        NodeSidePropertiesDraw.original =  NODE_PT_active_node_properties.draw
        NODE_PT_active_node_properties.draw = NodeSidePropertiesDraw._drawSidebarProps

    @staticmethod
    def unhook():
        from bl_ui.space_node import NODE_PT_active_node_properties
        NODE_PT_active_node_properties.draw = NodeSidePropertiesDraw.original

    @staticmethod
    def _drawSidebarProps(self, context):
        if context.engine == 'VRAY_RENDER_RT':
            layout = self.layout
            node = context.active_node
            # set 'node' context pointer for the panel layout
            layout.context_pointer_set("node", node)

            if hasattr(node, "draw_buttons_ext"):
                node.draw_buttons_ext(context, layout)
            elif hasattr(node, "draw_buttons"):
                node.draw_buttons(context, layout)
        else:
            NodeSidePropertiesDraw.original(self, context)


def _drawVRayAddMenuContents(layout, isInSwapMenu = False):
    prefix = 'NODE_MT_vray_swap_' if isInSwapMenu else 'NODE_MT_vray_add_'

    def menu(suffix):
        layout.menu(f'{prefix}{suffix}')

    menu('material')
    layout.separator()

    menu('textures')
    menu('texture_utilities')
    layout.separator()

    menu('mapping')
    menu('geometry')
    menu('object_properties')
    menu('math')
    layout.separator()

    menu('environment')
    menu('effects')
    menu('render_channels')
    layout.separator()

    menu('output')
    layout.separator()

    menu('selectors')
    menu('layout')


def _drawVRayAddMenuHook(self, context):
    if _vrayMenuPoll(context):
        _drawVRayAddMenuContents(self.layout)


def _drawVRaySwapMenuHook(self, context: bpy.types.Context):
    if not _vrayMenuPoll(context):
        return

    node = context.active_node
    if not (node and isVrayNode(node)):
        return

    # Disable swap for output nodes and containers
    if node.bl_idname in (
        'VRayNodeOutputMaterial',
        'VRayNodeWorldOutput',
        'VRayNodeObjectOutput',
        'VRayNodeFurOutput',
        'VRayNodeDecalOutput',
        'VRayNodeRenderChannels',
        'VRayNodeEffectsHolder',
        'VRayNodeEnvironment'
    ):
        return

    layout = self.layout

    # If multiple nodes are selected, show all swap menus
    if len(context.selected_nodes) > 1:
        _drawVRayAddMenuContents(layout, isInSwapMenu=True)
        return

    vrayType = getattr(node, 'vray_type', None)

    if vrayType in {'BRDF', 'MATERIAL'}:
        layout.menu('NODE_MT_vray_swap_material')
    elif vrayType == 'TEXTURE':
        layout.menu('NODE_MT_vray_swap_textures')
        layout.menu('NODE_MT_vray_swap_texture_utilities')
    elif vrayType == 'UVWGEN':
        layout.menu('NODE_MT_vray_swap_mapping')
    elif vrayType == 'GEOMETRY':
        layout.menu('NODE_MT_vray_swap_geometry')
    elif vrayType == 'EFFECT':
        layout.menu('NODE_MT_vray_swap_effects')
    elif vrayType == 'RENDERCHANNEL':
        layout.menu('NODE_MT_vray_swap_render_channels')
    elif node.bl_idname in {'VRayNodeTransform', 'VRayNodeMatrix', 'VRayNodeVector'}:
        layout.menu('NODE_MT_vray_swap_math')
    elif node.bl_idname in {'VRayNodeObjectMatteProps', 'VRayNodeObjectSurfaceProps', 'VRayNodeObjectVisibilityProps'}:
        layout.menu('NODE_MT_vray_swap_object_properties')
    else:
        _drawVRayAddMenuContents(layout, isInSwapMenu=True)


_swapMenuClasses = []
def _getSwapMenuClasses():
    global _swapMenuClasses
    if not _swapMenuClasses:
        baseMenus = [
            (NODE_MT_vray_add_material, 'Material'),
            (NODE_MT_vray_add_textures, 'Textures'),
            (NODE_MT_vray_add_texture_utilities, 'Texture Utilities'),
            (NODE_MT_vray_add_mapping, 'Mapping'),
            (NODE_MT_vray_add_geometry, 'Geometry'),
            (NODE_MT_vray_add_object_properties, 'Object Properties'),
            (NODE_MT_vray_add_math, 'Math'),
            (NODE_MT_vray_add_output, 'Output'),
            (NODE_MT_vray_add_selectors, 'Selectors'),
            (NODE_MT_vray_add_environment, 'Environment'),
            (NODE_MT_vray_add_effects, 'Effects'),
            (NODE_MT_vray_add_render_channels, 'Render Channels'),
            (NODE_MT_vray_add_layout, 'Layout'),
        ]

        def makeDraw(b):
            def draw(self, context):
                b.drawMenu(self.layout, context, isInSwapMenu = True)
            return draw

        for cls, label in baseMenus:
            attrs = {
                'bl_label': label,
                'bl_idname': cls.bl_idname.replace('_add_', '_swap_'),
                'bl_options': {'SEARCH_ON_KEY_PRESS'},
                'draw': makeDraw(cls)
            }
            if 'poll' in cls.__dict__:
                attrs['poll'] = cls.__dict__['poll']
            swap_cls = type(
                cls.__name__.replace('_add_', '_swap_'),
                (bpy.types.Menu,),
                attrs
            )
            _swapMenuClasses.append(swap_cls)

    return _swapMenuClasses


def _getMenuClasses():
    return (
        NODE_MT_vray_add_material,
        NODE_MT_vray_add_textures,
        NODE_MT_vray_add_texture_utilities,
        NODE_MT_vray_add_mapping,
        NODE_MT_vray_add_geometry,
        NODE_MT_vray_add_object_properties,
        NODE_MT_vray_add_math,
        NODE_MT_vray_add_output,
        NODE_MT_vray_add_selectors,
        NODE_MT_vray_add_environment,
        NODE_MT_vray_add_effects,
        NODE_MT_vray_add_render_channels,
        NODE_MT_vray_add_layout,
        NODE_MT_vray_add,
    )


def register():
    _loadDynamicNodes()
    for cls in _dynamicClasses:
        bpy.utils.register_class(cls)

    NodeSidePropertiesDraw.hook()

    for menuClass in _getRenderChannelMenuClasses(isInSwapMenu = False):
        bpy.utils.register_class(menuClass)
    for menuClass in _getRenderChannelMenuClasses(isInSwapMenu = True):
        bpy.utils.register_class(menuClass)

    for cls in _getMenuClasses():
        bpy.utils.register_class(cls)
    for cls in _getSwapMenuClasses():
        bpy.utils.register_class(cls)

    bpy.types.NODE_MT_add.append(_drawVRayAddMenuHook)
    # Node swap is not fully available in 4.5
    if hasattr(bpy.types, 'NODE_MT_swap'):
        bpy.types.NODE_MT_swap.append(_drawVRaySwapMenuHook)

    bpy.utils.register_class(VRAY_OT_swap_node)


def unregister():
    if hasattr(bpy.types, 'NODE_MT_swap'):
        bpy.types.NODE_MT_swap.remove(_drawVRaySwapMenuHook)
    bpy.types.NODE_MT_add.remove(_drawVRayAddMenuHook)

    for cls in reversed(_getSwapMenuClasses()):
        bpy.utils.unregister_class(cls)
    for cls in reversed(_getMenuClasses()):
        bpy.utils.unregister_class(cls)

    for menuClass in reversed(_getRenderChannelMenuClasses(isInSwapMenu = True)):
        bpy.utils.unregister_class(menuClass)
    for menuClass in reversed(_getRenderChannelMenuClasses(isInSwapMenu = False)):
        bpy.utils.unregister_class(menuClass)

    NodeSidePropertiesDraw.unhook()

    for regClass in reversed(_dynamicClasses):
        bpy.utils.unregister_class(regClass)

    bpy.utils.unregister_class(VRAY_OT_swap_node)
