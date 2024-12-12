
from vray_blender.lib.defs import ExporterContext
from vray_blender.lib import export_utils
from vray_blender.lib import blender_utils
from vray_blender.lib import plugin_utils


plugin_utils.loadPluginOnModule(globals(), __name__)

def exportCustom(ctx: ExporterContext, pluginDesc):
    if ctx.bake:
        return

    camera = blender_utils.getSceneCamera(ctx)
    
    # The scene might not have a camera, or the scene camera might be a non-camera object
    # (the so-called 'look through selected' feature). 
    if camera and (camera.type == 'CAMERA'):
        vrayCamera = camera.data.vray
        pluginDesc.setAttribute('aperture', vrayCamera.SettingsCameraDof.aperture)

    # NOTE We are currently using VRayStereoscopicSettings for stereo exports. 
    # It is an alternative to exporting through RenderView ( which does
    # not work correctly atm )

    return export_utils.exportPluginCommon(ctx, pluginDesc)
