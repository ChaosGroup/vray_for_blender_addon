
import bpy

from vray_blender.lib.sys_utils import isGPUEngine
from vray_blender.lib.mixin import VRayNodeBase
from vray_blender.nodes.sockets import RGBA_SOCKET_COLOR, addInput, addOutput, VRayValueSocket
from vray_blender.nodes.tools import isInputSocketLinked
from vray_blender.plugins import getPluginModule, getPluginAttr


class VRayNodeWorldOutput(VRayNodeBase):
    bl_idname = 'VRayNodeWorldOutput'
    bl_label  = 'V-Ray World Output'
    bl_width_default  = 150
    # bl_icon   = 'VRAY_LOGO'

    vray_type   = 'NONE'
    vray_plugin = 'NONE'

    def init(self, context):
        addInput(self, 'VRaySocketObject', "Environment")
        addInput(self, 'VRaySocketObjectList', "Effects")
        addInput(self, 'VRaySocketObjectList', "Channels")



class VRaySocketEnvironmentOverride(VRayValueSocket):
    bl_idname = 'VRaySocketEnvironmentOverride'
    bl_label  = 'Environment override socket'

    value: bpy.props.FloatVectorProperty(
        name = "Color",
        description = "Color",
        subtype = 'COLOR',
        min = 0.0,
        max = 1.0,
        soft_min = 0.0,
        soft_max = 1.0,
        default = (0.0, 0.0, 0.0)
    )

    use: bpy.props.BoolProperty(
        name        = "Use",
        description = "Use override",
        default     = False
    )

    multiplier: bpy.props.FloatProperty(
        name        = "Multiplier",
        description = "Color / texture multiplier",
        min         = 0.0,
        default     = 1.0,
        soft_min    = 0.0,
        soft_max    = 1.0,
    )

    def draw(self, context, layout, node, text):
        if isGPUEngine(context.scene):
            self._drawGPU(context, layout, node, text)
        else:
            self._drawCPU(context, layout, node, text)


    def _drawCPU(self, context, layout, node, text):
        row = layout.split(factor=0.3)
        row.prop(self, 'value', text="")
        col = row.split(factor=0.9)
        col.prop(self, 'multiplier', text=text)
        colUse = col.column()
        colUse.prop(self, 'use', text="")
    

    def _drawGPU(self, context, layout: bpy.types.UILayout, node, text):
        
        if isInputSocketLinked(self):
            # For GPU renders, the blend between texture and color does not work. The _mult property
            # is a simple multiplier for the color. Do not show the color in this case.
            row = layout.split(factor=0.935, align=True)
            row.prop(self, 'multiplier', text=text)
        else :
            row = layout.split(factor=0.3, align=True)
            row.prop(self, 'value', text="")
            row = row.split(factor=0.9)
            row.prop(self, 'multiplier', text=text)
            
        colUse = row.column()
        colUse.alignment = 'RIGHT'
        colUse.prop(self, 'use', text="")


    @classmethod
    def draw_color_simple(cls):
        return RGBA_SOCKET_COLOR


class VRaySocketEnvironment(VRaySocketEnvironmentOverride):
    # This socket type was originally used for the background color and was different 
    # from VRaySocketEnvironmentOverride. After a refactoring it has the same functionality
    # as VRaySocketEnvironmentOverride. Changing the class of the socket breaks the backward
    # compatibility with existing scenes and we have no path to automatically upgrade it.
    # This is why we keep the class as an alias.
    bl_idname = 'VRaySocketEnvironment'
    bl_label  = 'Environment override socket'


class VRayNodeEnvironment(VRayNodeBase):
    bl_idname = 'VRayNodeEnvironment'
    bl_label  = 'V-Ray Environment'
    bl_icon   = 'WORLD'

    vray_type   = 'NONE'
    vray_plugin = 'NONE'

    def init(self, context):
        plugin = getPluginModule('SettingsEnvironment')
        
        bgColorDefault = getPluginAttr(plugin, 'bg_tex')['default'][:3]
        giColorDefault = getPluginAttr(plugin, 'gi_tex')['default'][:3]
        reflectColorDefault = getPluginAttr(plugin, 'reflect_tex')['default'][:3]
        refractColorDefault = getPluginAttr(plugin, 'refract_tex')['default'][:3]
        secondaryMatteColorDefault = getPluginAttr(plugin, 'secondary_matte_tex')['default'][:3]

        addInput(self, 'VRaySocketEnvironment', "Background", 'bg_tex', "SettingsEnvironment").setValue(bgColorDefault)
        addInput(self, 'VRaySocketEnvironmentOverride', "GI", 'gi_tex', "SettingsEnvironment").setValue(giColorDefault)
        addInput(self, 'VRaySocketEnvironmentOverride', "Reflection", 'reflect_tex', "SettingsEnvironment").setValue(reflectColorDefault)
        addInput(self, 'VRaySocketEnvironmentOverride', "Refraction", 'refract_tex', "SettingsEnvironment").setValue(refractColorDefault)
        addInput(self, 'VRaySocketEnvironmentOverride', "Secondary Matte", 'secondary_matte_tex', "SettingsEnvironment").setValue(secondaryMatteColorDefault)

        addOutput(self, 'VRaySocketObject', "Environment")


########  ########  ######   ####  ######  ######## ########     ###    ######## ####  #######  ##    ##
##     ## ##       ##    ##   ##  ##    ##    ##    ##     ##   ## ##      ##     ##  ##     ## ###   ##
##     ## ##       ##         ##  ##          ##    ##     ##  ##   ##     ##     ##  ##     ## ####  ##
########  ######   ##   ####  ##   ######     ##    ########  ##     ##    ##     ##  ##     ## ## ## ##
##   ##   ##       ##    ##   ##        ##    ##    ##   ##   #########    ##     ##  ##     ## ##  ####
##    ##  ##       ##    ##   ##  ##    ##    ##    ##    ##  ##     ##    ##     ##  ##     ## ##   ###
##     ## ########  ######   ####  ######     ##    ##     ## ##     ##    ##    ####  #######  ##    ##

def getRegClasses():
    return (
        VRaySocketEnvironment,
        VRaySocketEnvironmentOverride,
        VRayNodeWorldOutput,
        VRayNodeEnvironment,
    )


def register():
    for regClass in getRegClasses():
        bpy.utils.register_class(regClass)


def unregister():
    for regClass in getRegClasses():
        bpy.utils.unregister_class(regClass)
