
import bpy

from vray_blender.ui import classes

from vray_blender.plugins import getPluginModule


from bl_ui import properties_data_camera
for compatEngine in classes.VRayEngines:
    properties_data_camera.DATA_PT_context_camera.COMPAT_ENGINES.add(compatEngine)
    properties_data_camera.DATA_PT_camera_display.COMPAT_ENGINES.add(compatEngine)
    properties_data_camera.DATA_PT_camera.COMPAT_ENGINES.add(compatEngine)
    properties_data_camera.DATA_PT_lens.COMPAT_ENGINES.add(compatEngine)

del properties_data_camera


class VRAY_PT_camera_focus_distance(classes.VRayCameraPanel):
    bl_label    = "Focus Distance"
    bl_options  = {'DEFAULT_CLOSED'}
    bl_icon     = ""

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True

        camera = context.camera
        dof = camera.dof

        layout.prop(dof, "focus_object", text="Focus on Object")

        sub = layout.column()
        sub.enabled = (dof.focus_object is None)
        if dof.focus_object is None:
            sub.prop(dof, "focus_distance", text="Focus Distance")
        else:
            # Drawing fake property that show the distance to the focus object
            vrayCamera = camera.vray
            sub.prop(vrayCamera, "focus_distance", text="Focus Distance")



class VRAY_PT_camera(classes.VRayCameraPanel):
    bl_label = "Camera Overrides"
    bl_options = {'DEFAULT_CLOSED'}

    def drawPanelCheckBox(self, context):
        settingsCamera = context.camera.vray.SettingsCamera
        self.layout.prop(settingsCamera, 'override_camera_settings', text="")

    def draw(self, context):
        camera = context.camera
        vrayCamera = camera.vray
        renderView         = vrayCamera.RenderView
        settingsCamera     = vrayCamera.SettingsCamera
        
        layout = self.layout
        layout.enabled = settingsCamera.override_camera_settings
        layout.use_property_split = True

        layout.prop(renderView, 'clipping', text="Use Clipping")

        layout.separator()

        classes.drawPluginUI(context, layout, settingsCamera, getPluginModule('SettingsCamera'))


class VRAY_PT_physical_camera(classes.VRayCameraPanel):
    bl_label   = "Physical Camera"
    bl_options = {'DEFAULT_CLOSED'}

    def drawPanelCheckBox(self, context):
        vrayCamera = context.camera.vray
        self.layout.prop(vrayCamera, 'use_physical', text="")

    def draw(self, context):
        vrayCamera = context.camera.vray
        cameraPhysical = vrayCamera.CameraPhysical
        classes.drawPluginUI(context, self.layout, cameraPhysical, getPluginModule('CameraPhysical'))


class VRAY_PT_dome_camera(classes.VRayCameraPanel):
    bl_label   = "Dome Camera"
    bl_options = {'DEFAULT_CLOSED'}

    def drawPanelCheckBox(self, context):
        vrayCamera = context.camera.vray
        self.layout.prop(vrayCamera, 'use_dome', text='')

    def draw(self, context):
        vrayCamera = context.camera.vray
        CameraDome = vrayCamera.CameraDome
        classes.drawPluginUI(context, self.layout, CameraDome, getPluginModule('CameraDome'))


def getRegClasses():
    return (
    VRAY_PT_camera_focus_distance,
    VRAY_PT_physical_camera,
    VRAY_PT_dome_camera,
    VRAY_PT_camera,
)


def register():
    from vray_blender.lib.class_utils import registerClass
    for regClass in getRegClasses():
        registerClass(regClass)


def unregister():
    for regClass in reversed(getRegClasses()):
        bpy.utils.unregister_class(regClass)
