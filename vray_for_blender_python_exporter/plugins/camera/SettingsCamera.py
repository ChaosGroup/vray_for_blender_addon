
import bpy
from vray_blender.lib.defs import ExporterContext
from vray_blender.lib import plugin_utils, blender_utils
from vray_blender.lib.names import Names

plugin_utils.loadPluginOnModule(globals(), __name__)


def onUpdateCamType(propGroup, context: bpy.types.Context, attrName: str):
    if camera := propGroup.id_data:
        camType = getattr(propGroup, attrName)

        STANDARD_CAMERA = '7'

        if camera.type != "ORTHO" and camType == STANDARD_CAMERA:
            camera.type = "ORTHO"
        elif camera.type == "ORTHO" and camType != STANDARD_CAMERA:
            camera.type = "PERSP"

        # Changing the overriding camera type cannot be done reliably in IPR 
        # without resetting the whole SettingsCamera plugin.
        pluginName = Names.singletonPlugin("SettingsCamera")
        ExporterContext.pluginsToRecreate.add(pluginName)


def onUpdateOverrideCameraSettings(propGroup, context: bpy.types.Context, attrName: str):
    # Switching the camera type in IPR is flaky at best. Re-create all associated
    # plugins to minimize the chance of incorrect settings sticking around after 
    # the change.
    ExporterContext.pluginsToRecreate.add(Names.singletonPlugin("SettingsCamera"))
    ExporterContext.pluginsToRecreate.add(Names.singletonPlugin("RenderView"))

