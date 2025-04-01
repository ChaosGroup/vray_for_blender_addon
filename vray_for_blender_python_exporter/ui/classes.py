
import bpy

from vray_blender.lib import lib_utils
from vray_blender.lib import draw_utils
from vray_blender     import plugins, debug
from vray_blender.ui  import icons
from vray_blender.nodes.utils import getVrayPropGroup


########  ######## ######## #### ##    ## ########  ######
##     ## ##       ##        ##  ###   ## ##       ##    ##
##     ## ##       ##        ##  ####  ## ##       ##
##     ## ######   ######    ##  ## ## ## ######    ######
##     ## ##       ##        ##  ##  #### ##             ##
##     ## ##       ##        ##  ##   ### ##       ##    ##
########  ######## ##       #### ##    ## ########  ######

VRayEngines = {
    'VRAY_RENDER',
    'VRAY_RENDER_PREVIEW',
    'VRAY_RENDER_RT',
    'VRAY_RENDERER'
}

# Lists of panels available for each 'tab' in the main Render panel. The actual visibility of the panels
# is determined by the return value of the corresponding class' poll() method
RenderPanelGroups = {
    # Render
    # Sampler
    '0' : (
        'VRAY_PT_GpuTextureOptions',
        'VRAY_PT_ImageSampler',
        'VRAY_PT_BucketSize'
    ),
    # GI
    '1' : (
        'VRAY_PT_GI',
        'VRAY_PT_GI_BruteForce',
        'VRAY_PT_GI_LightCache',
        'VRAY_PT_SettingsCaustics',
    ),
    # Globals
    '2' : (
        'VRAY_PT_Globals',
        'VRAY_PT_ColorManagement'
    ),
    # System
    '3' : (
        'VRAY_PT_Exporter',
        'VRAY_PT_SceneExporter',
        'VRAY_PT_SettingsSystem',
        'VRAY_PT_DR',
    ),
}

narrowui = 200


##     ## ######## #### ##        ######
##     ##    ##     ##  ##       ##    ##
##     ##    ##     ##  ##       ##
##     ##    ##     ##  ##        ######
##     ##    ##     ##  ##             ##
##     ##    ##     ##  ##       ##    ##
 #######     ##    #### ########  ######

def getContextType(context):
    if hasattr(context, 'node'):
        return 'NODE'
    if hasattr(context, 'material'):
        return 'MATERIAL'
    return None


def getRegionWidthFromContext(context):
    contextType = getContextType(context)
    if contextType == 'NODE':
        return context.node.width
    elif hasattr(context, 'region'):
        return context.region.width
    # Assume wide region width
    return 1024


def pollBase(cls, context):
    poll_engine = context.scene.render.engine in cls.COMPAT_ENGINES
    poll_custom = True
    if hasattr(cls, 'poll_custom'):
        poll_custom = cls.poll_custom(context)
    poll_group = True
    if hasattr(cls, 'poll_group'):
        poll_group = cls.poll_group(context)
    return poll_engine and poll_custom and poll_group


def pollEngine(context):
    return context.scene.render.engine in VRayEngines


def pollTreeType(cls, context):
    is_vray      = pollEngine(context)
    is_vray_tree = context.space_data.tree_type.startswith('VRayNodeTree')
    return is_vray and is_vray_tree


########  ########     ###    ##      ##
##     ## ##     ##   ## ##   ##  ##  ##
##     ## ##     ##  ##   ##  ##  ##  ##
##     ## ########  ##     ## ##  ##  ##
##     ## ##   ##   ######### ##  ##  ##
##     ## ##    ##  ##     ## ##  ##  ##
########  ##     ## ##     ##  ###  ###


def drawPluginUI(context, layout, propGroup, pluginModule, node = None):
    """ Draw property pages for the plugin in the supplied container. 

        @param layout - container to draw into
        @param propGroup - Blender PropertyGroup for the plugin
        @param vrayPlugin - 
        @param node - optional. If valid, draw node input sockets as properties in the property page
    """
    uiPainter = draw_utils.UIPainter(context, pluginModule, propGroup, node)
    uiPainter.renderPluginUI(layout)
    

def drawNodePanel(context, layout, node, PLUGINS):
    """ Draw the main property panel for the node ( the one in the main
        Properties tab; the node sidebar panel is drawn by nodes.py::VRayNodeDrawSide() )
    """   
    pluginModule = None
    show         = True

    if node.bl_idname.startswith('VRayNodeMeta'):
        node.draw_buttons_ext(context, layout)
        return

    show = getattr(node, 'vray_type', 'NONE') != 'NONE'
    show = show and (getattr(node, 'vray_plugin', 'NONE') != 'NONE')
    
    if show:
        pluginTypes = PLUGINS[node.vray_type]
        if node.vray_plugin in pluginTypes:
            pluginModule = pluginTypes[node.vray_plugin]

    layout.label(text=f"Node:  {node.name}")
    layout.separator()
    
    if show and pluginModule and (propGroup := getVrayPropGroup(node)):
        drawPluginUI(context, layout, propGroup, pluginModule, node)
    else:
        layout.label(text="Selected node has no properties to show.")


def ntreeWidget(layout, propGroup, label, addOp, addOpContext):
    row = layout.row(align=True)

    row.prop_search(propGroup, 'ntree', bpy.data, 'node_groups', text=label)
    if not propGroup.ntree:
        row.operator(addOp, icon='ADD', text="")


def drawListWidget(layout, propGroupPath, listType, defItemName, itemAddOp='DEFAULT', itemRenderFunc=None):
    listPropGroup = lib_utils.getPropGroup(bpy.context.scene, propGroupPath)

    row = layout.row()
    row.template_list(listType, "",
        listPropGroup, 'list_items',
        listPropGroup, 'list_item_selected',
        rows=5)

    col = row.column()
    sub = col.row()
    subsub = sub.column(align=True)
    if itemAddOp in {'DEFAULT'}:
        op = subsub.operator('vray.ui_list_item_add', icon="ADD", text="")
        op.list_attr     = propGroupPath
        op.def_item_name = defItemName
    else:
        subsub.operator(itemAddOp, icon="ADD", text="")

    sub= col.row()
    op = subsub.operator('vray.ui_list_item_del', icon="REMOVE", text="")
    op.list_attr   = propGroupPath
    subsub = sub.column(align=True)
    op = subsub.operator("vray.ui_list_item_up",   icon='TRIA_UP',   text="")
    op.list_attr   = propGroupPath
    op = subsub.operator("vray.ui_list_item_down", icon='TRIA_DOWN', text="")
    op.list_attr   = propGroupPath

    if itemRenderFunc:
        if listPropGroup.list_item_selected >= 0 and len(listPropGroup.list_items) > 0:
            listItem = listPropGroup.list_items[listPropGroup.list_item_selected]

            layout.separator()
            itemRenderFunc(layout, listItem)


########     ###     ######  ########     ######  ##          ###     ######   ######  ########  ######
##     ##   ## ##   ##    ## ##          ##    ## ##         ## ##   ##    ## ##    ## ##       ##    ##
##     ##  ##   ##  ##       ##          ##       ##        ##   ##  ##       ##       ##       ##
########  ##     ##  ######  ######      ##       ##       ##     ##  ######   ######  ######    ######
##     ## #########       ## ##          ##       ##       #########       ##       ## ##             ##
##     ## ##     ## ##    ## ##          ##    ## ##       ##     ## ##    ## ##    ## ##       ##    ##
########  ##     ##  ######  ########     ######  ######## ##     ##  ######   ######  ########  ######

class VRayPanel(bpy.types.Panel):
    COMPAT_ENGINES = VRayEngines
    bl_icon = "" # Icon from "vray_blender.ui.icons" to be drawn in front of V-Ray rollouts.

    # A list of V-Ray plugin type names to be shown on the panel. 
    # The order in the UI is the same as in the list.
    vrayPlugins = [] 

    def drawPanelCheckBox(self, context):
        """ Override this function to draw a rollout checkbox. """
        pass

    def draw_header(self, context):
        if self.bl_icon:
            self.layout.label(icon_value=icons.getIcon(self.bl_icon), text="")
        self.drawPanelCheckBox(context)

    @classmethod
    def poll(cls, context):
        return pollBase(cls, context)
    
        
    def draw(self, context: bpy.types.Context):
        for vrayPlugin in self.vrayPlugins:
            self.drawPlugin(context, self.layout, vrayPlugin)


    def drawPlugin(self, context: bpy.types.Context, layout: bpy.types.UILayout, pluginType):
        """ Draw the whole Widget description for a plugin.  """
        if hasattr(context.scene.vray, pluginType):
            propGroup = getattr(context.scene.vray, pluginType)
            pluginModule = plugins.getPluginModule(pluginType)
            
            uiPainter = draw_utils.UIPainter(context, pluginModule, propGroup)
            uiPainter.renderWidgets(layout, pluginModule.Widget.get('widgets', []))
    

    def drawSection(self, context: bpy.types.Context, layout: bpy.types.UILayout, pluginType, widgetName):
        """ Draw a top-level widget from a plugin description. This method is 
            used for fine-grain control over the widget placement. To draw the whole
            widget for a plugin, add the plugin type to the vrayPlugins list instead.
        """
        pluginModule = plugins.getPluginModule(pluginType)
        propGroup = getattr(context.scene.vray, pluginType)
        
        if widget := next((w for w in pluginModule.Widget['widgets'] if w.get('name') == widgetName), None):
            painter = draw_utils.UIPainter(context, pluginModule, propGroup)
            return painter.renderWidget(layout, widget)
        else:
            debug.printError(f"Plugin {pluginType} has no widget with name '{widgetName}'")
        
        return None


class VRayDataPanel(VRayPanel):
    bl_space_type  = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context     = 'data'


class VRayGeomPanel(VRayDataPanel):
    bl_icon = "VRAY_PLACEHOLDER"
    incompatTypes  = {'LIGHT', 'CAMERA', 'SPEAKER', 'ARMATURE', 'EMPTY', 'META'}

    @classmethod
    def poll(cls, context):
        return context.object and context.object.type not in cls.incompatTypes and pollBase(cls, context)


class VRayCameraPanel(VRayDataPanel):
    bl_icon = "VRAY_PLACEHOLDER"

    @classmethod
    def poll(cls, context):
        return context.camera and pollBase(cls, context)


class VRayLampPanel(VRayDataPanel):
    @classmethod
    def poll(cls, context):
        return context.light and pollBase(cls, context)


class VRayMaterialPanel(VRayPanel):
    bl_space_type  = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context     = 'material'

    @classmethod
    def poll(cls, context):
        return context.material and pollBase(cls, context)


class VRayObjectPanel(VRayPanel):
    bl_space_type  = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context     = 'object'
    bl_icon        = "VRAY_PLACEHOLDER"

    incompatTypes  = {'LIGHT', 'CAMERA', 'SPEAKER', 'ARMATURE'}

    @classmethod
    def poll(cls, context):
        return context.object and context.object.type not in cls.incompatTypes and pollBase(cls, context)


class VRayParticlePanel(VRayPanel):
    bl_space_type  = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context     = 'particle'

    @classmethod
    def poll(cls, context):
        return context.particle_system and pollBase(cls, context)


class VRayRenderPanel(VRayPanel):
    bl_space_type  = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context     = 'render'

    @classmethod
    def poll_group(cls, context):
        VRayExporter = context.scene.vray.Exporter
        
        activeGroup = VRayExporter.ui_render_context
        # 'activeGroup' may no longer be valid  if there are changes to the render context group
        # and we are loadig an older scene.
        if cls.__name__ in cls.bl_panel_groups.get(activeGroup, tuple()):
            return True

        return False

class VRayOutputPanel(VRayPanel):
    bl_space_type  = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context     = 'output'

    @classmethod
    def poll(cls, context):
        return pollBase(cls, context)
    

class VRayRenderLayersPanel(VRayPanel):
    bl_space_type  = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context     = 'view_layer'

    @classmethod
    def poll(cls, context):
        return pollBase(cls, context)


class VRayScenePanel(VRayPanel):
    bl_space_type  = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context     = 'scene'

    @classmethod
    def poll(cls, context):
        return pollBase(cls, context)


class VRayTexturePanel(VRayPanel):
    bl_space_type  = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context     = 'texture'

    @classmethod
    def poll(cls, context):
        return context.texture and pollBase(cls, context)


class VRayWorldPanel(VRayPanel):
    bl_space_type  = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context     = 'world'

    @classmethod
    def poll(cls, context):
        return context.world and pollBase(cls, context)


##       ####  ######  ########
##        ##  ##    ##    ##
##        ##  ##          ##
##        ##   ######     ##
##        ##        ##    ##
##        ##  ##    ##    ##
######## ####  ######     ##

class VRayOpListBase:
    list_attr    : bpy.props.StringProperty()
    def_item_name: bpy.props.StringProperty()


class VRAY_OT_ui_list_item_add(VRayOpListBase, bpy.types.Operator):
    bl_idname      = 'vray.ui_list_item_add'
    bl_label       = "Add Item"
    bl_description = "Add list item"
    bl_options     = {'INTERNAL'}

    def execute(self, context):
        listAttr = lib_utils.getPropGroup(context.scene, self.list_attr)
        listAttr.list_items.add()
        listAttr.list_items[-1].name = self.def_item_name
        listAttr.list_item_selected = len(listAttr.list_items) - 1
        return {'FINISHED'}


class VRAY_OT_ui_list_item_del(VRayOpListBase, bpy.types.Operator):
    bl_idname      = 'vray.ui_list_item_del'
    bl_label       = "Delete Item"
    bl_description = "Delete list item"
    bl_options     = {'INTERNAL'}

    def execute(self, context):
        listAttr = lib_utils.getPropGroup(context.scene, self.list_attr)

        if listAttr.list_item_selected >= 0:
           listAttr.list_items.remove(listAttr.list_item_selected)
           listAttr.list_item_selected -= 1

        if len(listAttr.list_items):
            if listAttr.list_item_selected < 0:
               listAttr.list_item_selected = 0
        else:
            listAttr.list_item_selected = -1

        return {'FINISHED'}


class VRAY_OT_ui_list_item_up(VRayOpListBase, bpy.types.Operator):
    bl_idname      = 'vray.ui_list_item_up'
    bl_label       = "Move Item Up"
    bl_description = "Move list item up"
    bl_options     = {'INTERNAL'}

    def execute(self, context):
        listAttr = lib_utils.getPropGroup(context.scene, self.list_attr)
        if listAttr.list_item_selected <= 0:
            return {'CANCELLED'}

        listAttr.list_items.move(listAttr.list_item_selected,
                                 listAttr.list_item_selected-1)
        listAttr.list_item_selected -= 1

        return {'FINISHED'}


class VRAY_OT_ui_list_item_down(VRayOpListBase, bpy.types.Operator):
    bl_idname      = 'vray.ui_list_item_down'
    bl_label       = "Move Item Down"
    bl_description = "Move list item down"
    bl_options     = {'INTERNAL'}
    
    def execute(self, context):
        listAttr = lib_utils.getPropGroup(context.scene, self.list_attr)
        if listAttr.list_item_selected < 0:
            return {'CANCELLED'}
        if listAttr.list_item_selected >= len(listAttr.list_items)-1:
            return {'CANCELLED'}

        listAttr.list_items.move(listAttr.list_item_selected,
                                 listAttr.list_item_selected+1)
        listAttr.list_item_selected += 1

        return {'FINISHED'}


# The draw_item function is called for each item of the collection that is visible in the list.
#   data is the RNA object containing the collection,
#   item is the current drawn item of the collection,
#   icon is the "computed" icon for the item (as an integer, because some objects like materials or textures
#     have custom icons ID, which are not available as enum items).
#   active_data is the RNA object containing the active property for the collection (i.e. integer pointing to the
#     active item of the collection).
#   active_propname is the name of the active property (use 'getattr(active_data, active_propname)').
#   index is index of the current item in the collection.

class VRAY_UL_Use(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        layout.label(text=item.name)
        layout.prop(item, 'use', text="")


class VRAY_UL_ListBase(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        layout.label(text=item.name)


class VRAY_UL_DR(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        port_override = ":%s" % item.port if item.port_override else ""

        layout.label(text="%s [%s%s]" % (item.name, item.address, port_override))
        layout.prop(item, 'use', text="")


class VRAY_UL_MaterialSlots(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        ob   = data
        slot = item
        ma   = slot.material

        split = layout.split(factor=0.75)

        if ma:
            split.label(text=ma.name, translate=False, icon_value=icon)
            split.prop(slot, 'link', text="", emboss=False, translate=False)
        else:
            split.label(text="")


class VRAY_UL_NodeTrees(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        layout.label(text="", icon=item.bl_icon)
        layout.label(text=item.name, translate=False)


class VRAY_UL_Materials(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        split = layout.split(factor=0.1)

        split.column().prop(item, 'diffuse_color', text="")
        split.column().label(text=item.name, translate=False)
        if hasattr(item, 'vray'):
            icon = item.node_tree.bl_icon if item.node_tree else 'NONE'
            split.column().prop(item, 'node_tree', text="", icon=icon)


########  ########  ######   ####  ######  ######## ########     ###    ######## ####  #######  ##    ##
##     ## ##       ##    ##   ##  ##    ##    ##    ##     ##   ## ##      ##     ##  ##     ## ###   ##
##     ## ##       ##         ##  ##          ##    ##     ##  ##   ##     ##     ##  ##     ## ####  ##
########  ######   ##   ####  ##   ######     ##    ########  ##     ##    ##     ##  ##     ## ## ## ##
##   ##   ##       ##    ##   ##        ##    ##    ##   ##   #########    ##     ##  ##     ## ##  ####
##    ##  ##       ##    ##   ##  ##    ##    ##    ##    ##  ##     ##    ##     ##  ##     ## ##   ###
##     ## ########  ######   ####  ######     ##    ##     ## ##     ##    ##    ####  #######  ##    ##

def getRegClasses():
    return (
        VRAY_UL_NodeTrees,
        VRAY_UL_MaterialSlots,
        VRAY_UL_Use,
        VRAY_UL_DR,
        VRAY_UL_ListBase,
        VRAY_UL_Materials,

        VRAY_OT_ui_list_item_add,
        VRAY_OT_ui_list_item_del,
        VRAY_OT_ui_list_item_up,
        VRAY_OT_ui_list_item_down,
    )


def register():
    for regClass in getRegClasses():
        bpy.utils.register_class(regClass)


def unregister():
    for regClass in getRegClasses():
        bpy.utils.unregister_class(regClass)
