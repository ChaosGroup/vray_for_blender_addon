# SPDX-FileCopyrightText: Chaos Software EOOD
#
# SPDX-License-Identifier: GPL-3.0-or-later

import bpy

from vray_blender.lib import color_utils
from vray_blender.lib.blender_utils import getObjectFromEditorContext
from vray_blender.lib.lib_utils import getLightPluginType
from vray_blender.plugins.templates import common


class TemplateColorTemperature(common.VRayUITemplate):
    """ UI template for color/temperature selection.
    
        Template arguments:
            color_mode: name of the enum property for selection between Color and Temperature
            color_bound_property: name of the color bound property
            temperature_bound_property: name of the temperature bound property
    """

    def draw(self, layout: bpy.types.UILayout, context: bpy.types.Context, 
                    pluginModule, propGroup, widgetAttr: dict, text, nested=False):
        
        colorModeAttr = self.getTemplateAttr('color_mode')
        colorAttr = self.getTemplateAttr('color_bound_property')
        temperatureAttr = self.getTemplateAttr('temperature_bound_property')
        
        assert colorModeAttr, "TemplateColorTemperature is missing the 'color_mode' argument"
        assert colorAttr, "TemplateColorTemperature is missing the 'color_bound_property' argument"
        assert temperatureAttr, "TemplateColorTemperature is missing the 'temperature_bound_property' argument"

        # Color Mode (Expanded row/tabs)
        row = layout.row()
        row.prop(propGroup, colorModeAttr, expand=True)

        colorMode = getattr(propGroup, colorModeAttr)

        if colorMode == '0':
            # Color
            layout.prop(propGroup, colorAttr, text="Color")
        else:
            # Temperature
            from vray_blender.ui import icons
            
            lightObj = getObjectFromEditorContext(context)
            if lightObj is None:
                return
            
            pluginType = getLightPluginType(lightObj.data)
            temperature = getattr(propGroup, temperatureAttr, 6500.0)
            
            # Range is [800, 12000] for most lights
            tempClamped = max(800.0, min(12000.0, float(temperature)))
            color = color_utils.kelvinToRGB(tempClamped)
            
            row = layout.row(align=True)
            row.prop(propGroup, temperatureAttr)
            
            # Color preview icon
            row.label(text="", icon_value=icons.getSolidColorIcon(color))
            
            # "Set as color" button
            op = row.operator("vray.set_light_color_from_temperature", text="", icon='FORWARD')
            op.color = color
            op.light_name = lightObj.name
            op.plugin_type = pluginType
            op.color_attr_name = colorAttr


    def exportToPluginDesc(self, exporterCtx, pluginDesc):
        from vray_blender.lib.defs import AColor
        
        colorModeAttr = self.getTemplateAttr('color_mode')
        colorAttr = self.getTemplateAttr('color_bound_property')
        temperatureAttr = self.getTemplateAttr('temperature_bound_property')
        
        propGroup = pluginDesc.vrayPropGroup
        colorMode = getattr(propGroup, colorModeAttr)
        
        if colorMode == '0':
            # Use color from color_colortex. 
            # Note: COLOR_TEXTURE properties are handled by the generic exporter
            # if they are not overridden. 
            pass
        else:
            # Get color from temperature.
            temperature = getattr(propGroup, temperatureAttr)
            tempClamped = max(800.0, min(12000.0, float(temperature)))
            rgb = color_utils.kelvinToRGB(tempClamped)
            
            pluginDesc.setAttribute(colorAttr, AColor(rgb))

        return True

    def copy(self, dest):
        pass


def getRegClasses():
    return (
        TemplateColorTemperature,
    )


def register():
    from vray_blender.lib.class_utils import registerClass
    for regClass in getRegClasses():
        registerClass(regClass)


def unregister():
    for regClass in reversed(getRegClasses()):
        bpy.utils.unregister_class(regClass)
