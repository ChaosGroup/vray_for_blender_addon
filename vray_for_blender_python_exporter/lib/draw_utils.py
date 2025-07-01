
import bpy
import importlib
import json

from vray_blender import debug
from vray_blender.lib import attribute_types, attribute_utils, plugin_utils
from vray_blender.nodes.utils import getInputSocketByVRayAttr
from vray_blender.plugins import getPluginAttr


def getContextType(context):
    if hasattr(context, 'node') and context.node:
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


def subPanel(layout: bpy.types.UILayout):
    split = layout.split(factor=0.05, align=True)
    split.column()
    panel = split.column(align=True)

    # Right-align labels, left-align the corresponding fields around a vertical line
    panel.use_property_split = True 
    return panel


def rollout(layout: bpy.types.UILayout, uniqueID: str, label: str, defaultClosed=True, usePropDataSrc=None, usePropName: str = None):
    """" Draw a dynamic rollout with an optional enable/disable checkbox.

    Args:
        layout (bpy.types.UILayout): the parent layout
        uniqueID (str): a unique ID which will be associated with the panel's state
        label (str): the label to show for the panel
        defaultClosed(bool): the initial state of the rollout is closed
        usePropDataSrc: data source for the checkbox property
        usePropName: the name of he checkbox property

    Returns:
        body (bpy.types.UILayout) | None: the layout for the body of the panel or None if the panel is collapsed.
    """
    header, body = layout.panel(uniqueID, default_closed=defaultClosed)
    header.alignment = 'LEFT'
    
    if usePropDataSrc and usePropName:
        header.prop(usePropDataSrc, usePropName, text=label)
    else:
        header.label(text=label)

    if not body:
        # Panel is closed
        return None
    
    # Create a container for the children of the rollout.
    # The empty left column of the split will be used for visual offset of the items 
    # in the container.
    return subPanel(body)
    


class UIPainter:
    def __init__(self, context: bpy.types.Context, pluginModule, propGroup, node: bpy.types.Node = None):
        self.context = context
        self.node = node
        self.propGroup = propGroup
        self.pluginModule = pluginModule


    def _drawAttr(self, layout: bpy.types.UILayout, attrName, label: str):
        """ Draw a single attribute of the plugin. This method will select between drawing node sockets
            and fields from the property group depending on whether the plugin is part of a
            nodetree.
        """
        
        if self.node and hasattr(self.node, "inputs") and (socket := getInputSocketByVRayAttr(self.node, attrName)):
            # We're drawing a node and the property is exposed as a node socket, let the socket draw itself  
            # in both the node and the property pages. This is necessary because, due to Blender limitations, 
            # only the values of sockets can be animated, and not the fields of the prop group of the node.
            if hasattr(socket, "draw_property"):
                socket.draw_property(self.context, layout, self.node, label)
            else:
                socket.draw(self.context, layout, self.node, label)
        elif hasattr(self.propGroup, attrName):
            # Draw property of a plugin that is not part of a nodetree
            layout.prop(self.propGroup, attrName, text=label)


    def _drawAttrWidget(self, layout: bpy.types.UILayout, widgetAttr):
        """ Draw a single attribute from the property group """
        attrName = widgetAttr['name']

        # TODO Delete the 'custom_draw' attribute if it is unused
        if fnDraw := self.getCustomDrawFunction(widgetAttr):
            # Use a custom draw function for the attribute
            fnDraw(self.context, layout, self.propGroup, widgetAttr)
        else:
            expand = widgetAttr.get('expand', False)
            slider = widgetAttr.get('slider', False)
            label  = self._getAttrLabel(widgetAttr)

            if (searchType := widgetAttr.get('search_bar_for')):
                layout.prop_search(self.propGroup, attrName,  bpy.data, searchType, text=label)
            elif (template := getPluginAttr(self.pluginModule, attrName).get('options', {}).get('template')):
                self._drawTemplate(layout, widgetAttr, template)
            elif self.node and hasattr(self.node, "inputs") and (socket := getInputSocketByVRayAttr(self.node, attrName)):
                # If there is a socket for the attribute, draw it instead of the value from the property 
                # group. In this way  what is shown in self.node and in the property pages will always stay in sync.
                label = label if (label is not None) else socket.name
                
                if hasattr(socket, "draw_property"):
                    socket.draw_property(self.context, layout, self.node, label, expand=expand, slider=slider)
                else:
                    socket.draw(self.context, layout, self.node, label)
            else:
                # Call Blender to draw the default widget for the attribute's data type
                layout.prop(self.propGroup, attrName, slider=slider, expand=expand, text=label)
    

    def _drawCustomAttr(self, layout, attrName, widgetAttr):
        """ Draw customized attribute UI depending on the atribute's data type.
            @returns True if the attribute was drawn, False if no custom draw procedure was found for it.
        """
        if not (attrDesc := attribute_utils.getAttrDesc(self.pluginModule, attrName)):
            return False

        if attrDesc['type'] in attribute_types.MetaPropertyTypes:
            label = self._getAttrLabel(widgetAttr)
            self._drawAttr(layout, attrName, label)
            return True
            
        return False


    def _drawTemplate(self, layout, widgetAttr, template):
        from vray_blender.plugins import templates

        templateInst = getattr(self.propGroup, widgetAttr['name'])
        templateInst.draw(layout, self.context, self.pluginModule, self.propGroup, widgetAttr, self._getAttrLabel(widgetAttr))


    def _renderDefault(self, layout: bpy.types.UILayout):
        """ Render plugin's parameters from their descriptions in the Parameters section.
            Use this method for plugins without a 'widgets' section.
        """
        parameters = self.pluginModule.Parameters

        for attrDesc in sorted(parameters, key=lambda x: x['attr']):
            if attrDesc['type'] in attribute_types.SkippedTypes:
                continue
            if attrDesc['type'] in attribute_types.NodeOutputTypes:
                continue
            if attrDesc['type'] in attribute_types.NodeInputTypes:
                continue
            if not attrDesc.get('options', {}).get("visible", True):
                continue

            attrName = attrDesc['attr']
            self._drawAttr(layout, attrName, attribute_utils.getAttrDisplayName(attrDesc))


    def _evaluateCondition(self, cond):
        """ Evaluates whether a condition set for an attribute is fulfilled.

            Conditions can be applied to any attribute of a widget. This function only
            evaluates the condition to True or False. The semantics of the condition
            itself (i.e. what will happen if it is true) are implemented by the caller.
        """

        if type(cond) is not dict:
            return bool(cond)
        
        if cond.get('cond') and ('evaluate' in cond):
            return cond['evaluate'](self.propGroup, self.node)
        
        # NOTE: 'cond' may contain quote chars so don't use an f-string to construct the message  
        errMsg = "Incorrect condition definition [" + cond + f"] in {self.propGroup.bl_rna.name}."
        if 'evaluate' not in cond:
            errMsg += " Evaluation function not compiled."

        raise Exception(errMsg)


    def getCustomDrawFunction(self, widgetAttr):
        if not (drawFnName := widgetAttr.get('custom_draw')):
            return None

        fnDraw = None
        pythonModule = self.pluginModule

        if ':' in drawFnName:
            moduleName, fnName = drawFnName.split(':')
            modulePath = f"vray_blender.{moduleName}"

            try:
                pythonModule = importlib.import_module(modulePath)
            except ImportError as e:
                debug.printError(f"Failed to load python module: {modulePath}")
                raise e

            if hasattr(pythonModule, fnName):
                fnDraw = getattr(pythonModule, fnName)
        else:
            fnDraw = getattr(self.pluginModule, drawFnName)

        return fnDraw
        
        
    def _setActive(self, layout, active):
        if active is not None:
            isActive = self._evaluateCondition(active)
            layout.active = isActive
            layout.enabled = isActive


    def _renderItem(self, layout: bpy.types.UILayout, widgetAttr: dict):
        """ Render a single plugin parameter and set its enabled state. If the parameter 
            has an associated input self.node socket, render the socket, else render the parameter itself.
        """ 
        container = layout
        attrName = widgetAttr['name']

        if active := widgetAttr.get('active', None):
            # Individual layout properties cannot be enabled/disabled. Here we create a 
            # sub-layout which will host the attribute and will be enabled/disabled
            # instead. 
            container = layout.row()
            self._setActive(container, active)

        # Draw non-default UI look for the attribute, if defined.
        if self._drawCustomAttr(container, attrName, widgetAttr):
            return
        
        self._drawAttrWidget(container, widgetAttr)
    

    def _renderRollout(self, layout: bpy.types.UILayout, widget) -> bpy.types.UILayout | None:
        """ Render a rollout widget.

        :return A container to render rollout's properties into if the rollout is open, None if
                the rollout is closed.
        """         

        label = widget.get('label', "")
        useProp = widget.get('use_prop', None)
        uniqueID = f"{self.propGroup.as_pointer()}_{widget['name']}"
        defaultClosed = widget.get('default_closed', True)

        return rollout(layout, uniqueID, label, defaultClosed, self.propGroup, useProp)

    
    def _renderContainer(self, layout: bpy.types.UILayout, widget):
        """ Create a sub-layout(container) for the widget in an existing layout 
            
            :param layout - the existing layout
            :param widget - the widget for which to create the sub-layout 
            :return the newly created layout
        """ 
        container = layout

        containerType   = widget.get('layout', 'COLUMN')
        align           = widget.get('align', True)
        label           = widget.get('label', "")
        active          = widget.get('active', None)
        
        match containerType:
            case 'COLUMN':
                container = layout.column(align=align)
            case 'ROW':
                container = layout.row(align=align)
            case 'SEPARATOR':
                layout.separator()
            case 'BOX':
                container = layout.box()
            case 'ROLLOUT':
                container = self._renderRollout(layout, widget)
                label = "" # The rollout is a special case and it shows its own label. Do not show the container label.
        
        # Container may be None if we are rendering a closed Rollout widget
        if container:
            if label != "":
                container.label(text=f"{label}:")

            if useProp := widget.get('use_prop', ""):
                # Container is enabled by a checkbox tied to the 'use_prop' property
                container.active = getattr(self.propGroup, useProp)
                container.enabled = getattr(self.propGroup, useProp)
            else:
                self._setActive(container, active)
            
        return container


    def renderWidgetAttributes(self, widget, container):
        # Render widget attributes
        widgetAttributes = widget.get('attrs', {})

        for widgetAttr in widgetAttributes:
            if "layout" in widgetAttr:
                # Widgets can be nested. A widget is recognized by the presence of the 'layout' field.
                # Render the nested widget in its parent widget's container.
                # Note: container flags are inherited. The subcontainer is created for the purpose of 
                # resetting parent container flags which should not be active in the child container. 
                subContainer = container.column()
                subContainer.use_property_split = False
                self.renderWidget(subContainer, widgetAttr)
            else:
                # Render a simple (non-widget) attribute
                if ('visible' not in widgetAttr) or self._evaluateCondition(widgetAttr['visible']):
                    self._renderItem(container, widgetAttr)


    def renderWidget(self, layout: bpy.types.UILayout, widget: dict):
        """ Render a single widget onto the supplied layout """
        # If the widget has a 'visible' condition, check it first
        
        if (showCond := widget.get('visible')) and not self._evaluateCondition(showCond):
            return None

        # Create a container to put widget's attributes in
        if ('visible' not in widget) or self._evaluateCondition(widget['visible']):
            container = self._renderContainer(layout, widget)

        if not container:
            # The rest of the widget is hidden ( e.g. a closed rollout ). 
            # Do not render its attributes.
            return None
        
        container.use_property_split = widget.get('use_property_split', True)
        
        # Show animation dots for animatable properties (the ones which have the 'ANIMATABLE' option set).
        container.use_property_decorate = True

        self.renderWidgetAttributes(widget, container)

        if widget.get('layout', '') == 'ROLLOUT':
            # Add some space at the end of the rollout because it is
            # sometings difficult to separate its contents visually from 
            # the contents that follow.
            container.separator()
        
        return container



    def _getAttrLabel(self, widgetAttr: dict):
        """ Get the label to show for an attribute, searching the definitions at all 
            possible levels.
        """
        attrName = widgetAttr['name']

        if label := widgetAttr.get('label', None):
            return attribute_utils.formatAttributeName(label)
        
        attrDesc = getPluginAttr(self.pluginModule, attrName)
        if not attrDesc:
            debug.printError(f"Failed to draw unknown plugin parameter {self.pluginModule.ID}::{attrName}")
            
        if displayName := attribute_utils.getAttrDisplayName(attrDesc):
            return attribute_utils.formatAttributeName(displayName)
            
        return attribute_utils.formatAttributeName(attrName)
        

    def _getWidgets(self):
        jsonTemplate = self.pluginModule.Widget
        widgetDesc = jsonTemplate if type(jsonTemplate) is dict else json.loads(jsonTemplate)
        return widgetDesc['widgets']


    def renderPluginUI(self, layout: bpy.types.UILayout):
        """ Render the custom widget UI defined in a plugin description (if found), or all visible
            plugin parameters.

            @param self.node - if the plugin has a self.nodetree, the self.node in the tree to render.
        """
        if widgets := self._getWidgets():
            self.renderWidgets(layout, widgets)
        else:
            self._renderDefault(layout)


    def renderWidgets(self, layout: bpy.types.UILayout, widgets):
        """ Render a list of widgets onto the supplied layout. This need not be a the complete
            list for a plugin, so this function allows for drawing the UI in pieces.
        """
        for widget in widgets:
            self.renderWidget(layout, widget)

    
    def renderWidgetsSection(self, layout: bpy.types.UILayout, sectionName: str):
        """ Render a subsection of PluginDescription::Widget by its name  """
        self.renderWidgets(layout, self.pluginModule.Widget[sectionName])


    

