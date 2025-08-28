
import bpy

from vray_blender.lib.mixin import VRayNodeBase
from vray_blender.nodes.sockets import addOutput
from vray_blender.lib import plugin_utils, draw_utils
from vray_blender.lib.defs import ExporterContext, PluginDesc
from vray_blender.plugins import PLUGINS

class VRayObjectProps(VRayNodeBase):
    """ Base class for the object property nodes """

    visibleAttrs = ()
    pluginWidgetIdx = ""
    bl_width_default = 160


    def draw_buttons_ext(self, context, layout):
        if obj := context.active_object:
            pluginModule = PLUGINS["MISC"]["VRayObjectProperties"]
            
            if (widgetsList := pluginModule.Widget['widgets']) and \
                (widget := next((w for w in widgetsList if w["name"] == self.pluginWidgetIdx), None)):

                uiPainter = draw_utils.UIPainter(context, pluginModule, obj.vray.VRayObjectProperties, self)
                layout.use_property_split = True
                uiPainter.renderWidgetAttributes(widget, layout)
            else:
                from vray_blender import debug
                debug.printError(f"Plugin VRayObjectProperties has no widget with at index '{self.pluginWidgetIdx}'")


class VRayObjectMatteProps(VRayObjectProps):
    bl_idname = 'VRayNodeObjectMatteProps'
    bl_label  = 'V-Ray Matte Properties'
    bl_icon   = 'SCENE_DATA'

    vray_type   = 'NONE'
    vray_plugin = 'NONE'

    visibleAttrs = (
        "matte_surface",
        "alpha_contribution",
        "shadows",
        "affect_alpha",
        "shadow_tint_color",
        "shadow_brightness",
        "reflection_amount",
        "refraction_amount",
        "gi_amount",
        "no_gi_on_other_mattes",
        "matte_for_secondary_rays"
    )
    pluginWidgetIdx = "matte surface"

    def init(self, context):
        addOutput(self, 'VRaySocketObjectProps', "Matte")



class VRayObjectSurfaceProps(VRayObjectProps):
    bl_idname = 'VRayNodeObjectSurfaceProps'
    bl_label  = 'V-Ray Surface Properties'
    bl_icon   = 'SCENE_DATA'

    vray_type   = 'NONE'
    vray_plugin = 'NONE'

    visibleAttrs = (
        "generate_gi",
        "receive_gi",
        "gi_quality_multiplier",
        "receive_caustics",
        "gi_surface_id",
        "generate_render_elements"
    ) 
    pluginWidgetIdx = "surface properties"

    def init(self, context):
        addOutput(self, 'VRaySocketObjectProps', "Surface")


class VRayObjectVisibilityProps(VRayObjectProps):
    bl_idname = 'VRayNodeObjectVisibilityProps'
    bl_label  = 'V-Ray Visibility Properties'
    bl_icon   = 'SCENE_DATA'

    vray_type   = 'NONE'
    vray_plugin = 'NONE'

    visibleAttrs = (
        "visibility",
        "camera_visibility",
        "gi_visibility",
        "reflections_visibility",
        "refractions_visibility",
        "shadows_visibility",
        "shadows_receive"
    )
    pluginWidgetIdx = "visibility options"

    def init(self, context):
        addOutput(self, 'VRaySocketObjectProps', "Visibility")

    def fillReflectAndRefractLists(self, exporterCtx: ExporterContext, pluginDesc: PluginDesc):
        """ Fills the reflection and refraction exclusion lists with objects selected in the property panel of visibility props.

            @param pluginDesc - The VRayObjectProperties plugin description where the data will be filled.
        """
        for listType in ("reflection",):
            plugin_utils.setIncludeExcludeList(exporterCtx, pluginDesc, f"{listType}_object_selector")


def getRegClasses():
    return (
        VRayObjectMatteProps,
        VRayObjectSurfaceProps,
        VRayObjectVisibilityProps,
   )


def register():
    for regClass in getRegClasses():
        bpy.utils.register_class(regClass)


def unregister():
    for regClass in getRegClasses():
        bpy.utils.unregister_class(regClass)