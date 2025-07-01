
import bpy

from vray_blender.lib import blender_utils
from vray_blender.lib.draw_utils import UIPainter
from vray_blender.plugins import getPluginModule
from vray_blender.ui import classes


def getContextData(context):
    if context.active_object.type == 'MESH':
        return context.mesh
    return context.curve


class VRAY_PT_VRayProxy(classes.VRayGeomPanel):
    bl_label   = "Proxy"
    bl_options = {'DEFAULT_CLOSED'}


    @classmethod
    def poll(cls, context):
        return classes.VRayGeomPanel.poll(context) and \
                    (context.active_object.vray.VRayAsset.assetType == blender_utils.VRAY_ASSET_TYPE["Proxy"])

    def draw(self, context):
        layout = self.layout

        obj = context.active_object

        if obj.type != "MESH":
            return
        
        # Disabling the proxy panel in edit mode
        # as generating new preview mesh while in edit mode
        # can lead to unexpected results.
        classes.disableLayoutInEditMode(layout, context)

        geomMeshFile = obj.data.vray.GeomMeshFile

        gemMeshFileModule = getPluginModule('GeomMeshFile')
        painter = UIPainter(context, gemMeshFileModule, geomMeshFile)
        painter.renderPluginUI(layout)


class VRAY_PT_VRayScene(classes.VRayGeomPanel):
    bl_label   = "Scene"
    bl_options = {'DEFAULT_CLOSED'}


    @classmethod
    def poll(cls, context):
        return classes.VRayGeomPanel.poll(context) and \
                    (context.active_object.vray.VRayAsset.assetType == blender_utils.VRAY_ASSET_TYPE["Scene"])

    def draw(self, context):
        layout= self.layout

        obj = context.active_object

        if obj.type != "MESH":
            return
        
        vrayScene = obj.data.vray.VRayScene

        vraySceneModule = getPluginModule('VRayScene')
        painter = UIPainter(context, vraySceneModule, vrayScene)
        painter.renderPluginUI(layout)


def getRegClasses():
    return (
        VRAY_PT_VRayScene,
        VRAY_PT_VRayProxy,
    )


def register():
    from bl_ui import properties_data_mesh
    for member in dir(properties_data_mesh):
        subclass = getattr(properties_data_mesh, member)
        try:
            for compatEngine in classes.VRayEngines:
                subclass.COMPAT_ENGINES.add(compatEngine)
        except:
            pass
    del properties_data_mesh

    for regClass in getRegClasses():
        bpy.utils.register_class(regClass)


def unregister():
    from bl_ui import properties_data_mesh
    for member in dir(properties_data_mesh):
        subclass = getattr(properties_data_mesh, member)
        try:
            for compatEngine in classes.VRayEngines:
                subclass.COMPAT_ENGINES.remove(compatEngine)
        except:
            pass
    del properties_data_mesh

    for regClass in reversed(getRegClasses()):
        bpy.utils.unregister_class(regClass)
