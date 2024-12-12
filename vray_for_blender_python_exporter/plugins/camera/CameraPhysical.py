
import bpy
from vray_blender.lib import plugin_utils
from vray_blender.lib.defs import ExporterContext, PluginDesc
from vray_blender.nodes.utils import getUpdateCallbackPropertyContext

plugin_utils.loadPluginOnModule(globals(), __name__)


def onUpdateFocalLength(src, context: bpy.types.Context, attrName: str):
     scene = context.scene
     propContext = getUpdateCallbackPropertyContext(src, "CameraPhysical")

     if camera := scene.camera:
          focalLength = propContext.get('focal_length')
          # Check for the same value before setting it to avoid infinite recursion
          if camera.data.lens != focalLength:
               camera.data.lens = focalLength



def propertyGet(propGroup, attrName: str):
    if attrName != 'focal_length':
        return None
     
    if camera := propGroup.id_data:
        return camera.lens
    

def propertySet(propGroup, attrName: str, value):
    if attrName != 'focal_length':
        return
    if camera := propGroup.id_data:
        if camera.lens != value:
            camera.lens = value
    

