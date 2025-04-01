
import bpy
from vray_blender.lib import plugin_utils

plugin_utils.loadPluginOnModule(globals(), __name__)


def onUpdateCamType(propGroup, context: bpy.types.Context, attrName: str):
    if camera := propGroup.id_data:
        camType = getattr(propGroup, attrName)

        if camera.type != "ORTHO" and camType == "7":
            camera.type = "ORTHO"
        elif camera.type == "ORTHO" and camType != "7":
            camera.type = "PERSP"

    

