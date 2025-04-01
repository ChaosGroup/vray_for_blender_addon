
from vray_blender.lib import plugin_utils
from vray_blender.exporting.view_export import RENDER_CAMERA_BASE_NAME

plugin_utils.loadPluginOnModule(globals(), __name__)

def sceneNameGet(propGroup, attrName):
    return RENDER_CAMERA_BASE_NAME