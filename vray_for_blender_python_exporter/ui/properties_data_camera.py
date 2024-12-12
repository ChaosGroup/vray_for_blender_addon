
import bpy

from vray_blender.ui import classes


from bl_ui import properties_data_camera
for compatEngine in classes.VRayEngines:
    properties_data_camera.DATA_PT_context_camera.COMPAT_ENGINES.add(compatEngine)
    properties_data_camera.DATA_PT_camera_display.COMPAT_ENGINES.add(compatEngine)
del properties_data_camera


class VRAY_PT_camera(classes.VRayCameraPanel):
    bl_label = "Parameters"

    def draw(self, context):
        layout = self.layout

        ca = context.camera

        vrayCamera = ca.vray

        renderView         = vrayCamera.RenderView
        settingsCamera     = vrayCamera.SettingsCamera
        settingsCameraDof  = vrayCamera.SettingsCameraDof
        settingsMotionBlur = vrayCamera.SettingsMotionBlur
        cameraPhysical     = vrayCamera.CameraPhysical
        cameraDome         = vrayCamera.CameraDome

        wide_ui= context.region.width > classes.narrowui

        if wide_ui:
            layout.prop(ca, 'type', expand=True)
        else:
            layout.prop(ca, 'type', text="")
        layout.separator()

        if ca.type == 'PERSP':
            layout.prop(vrayCamera, 'override_fov')
            if vrayCamera.override_fov:
                layout.prop(vrayCamera, 'fov')
            else:
                split = layout.split()
                col = split.column()
                if ca.lens_unit == 'MILLIMETERS':
                    col.prop(ca, 'lens')
                else:
                    col.prop(ca, 'angle')
                if wide_ui:
                    col = split.column()
                col.prop(ca, 'lens_unit', text="")

        layout.separator()

        if ca.type == 'ORTHO':
            layout.prop(ca, 'ortho_scale')
        else:
            split = layout.split()
            col = split.column()
            col.label(text="Type:")
            col = split.column()
            col.prop(settingsCamera, 'type', text="")
            layout.separator()
        
        #layout.prop(SettingsCamera, 'auto_exposure')
        #layout.prop(SettingsCamera, 'auto_white_balance')
        #layout.prop(SettingsCamera, "auto_exposure_compensation")
        layout.label(text="Clipping:")
        split = layout.split()
        col = split.column()
        col.prop(renderView, 'clip_near')
        sub = col.column()
        sub.active = renderView.clip_near
        sub.prop(ca, 'clip_start', text="Near")
        if wide_ui:
            col = split.column()
        col.prop(renderView, 'clip_far')
        sub = col.column()
        sub.active = renderView.clip_far
        sub.prop(ca, 'clip_end', text="Far")

        split = layout.split()
        split.label(text="Depth of Field:")
        split = layout.split()
        col = split.column(align=True)
        col.prop(ca.dof, 'focus_distance', text="Distance")
        if wide_ui:
            col = split.column()
        col.prop(ca.dof, 'focus_object', text="")

        layout.separator()

        # DOF camera settings
        if not cameraPhysical.use:
            box = layout.box()
            box.prop(settingsCameraDof, 'on')
            if settingsCameraDof.on:
                split = box.split()
                col = split.column(align=True)
                col.prop(settingsCameraDof, 'aperture')
                col.prop(settingsCameraDof, 'center_bias')

                row = box.row(align=True)
                row.prop(settingsCameraDof, 'sides_on')
                row.prop(settingsCameraDof, 'sides_num')

                row = box.row(align=True)
                row.prop(settingsCameraDof, 'anisotropy')
                row.prop(settingsCameraDof, 'rotation')

            box = layout.box()
            box.prop(settingsMotionBlur, 'on')
            if settingsMotionBlur.on:
                box.prop(settingsMotionBlur, 'camera_motion_blur')

                col = box.column(align=True)
                col.prop(settingsMotionBlur, 'duration')
                col.prop(settingsMotionBlur, 'interval_center')

                row = box.row(align=True)
                row.prop(settingsMotionBlur, 'bias')

                row = box.row(align=True)
                row.prop(settingsMotionBlur, 'shutter_efficiency')

                row = box.row(align=True)
                row.prop(settingsMotionBlur, 'low_samples')
                row.prop(settingsMotionBlur, 'geom_samples', text="Geom. Samples")


class VRAY_PT_physical_camera(classes.VRayCameraPanel):
    bl_label   = "V-Ray Physical Camera"
    bl_options = {'DEFAULT_CLOSED'}

    def draw_header(self, context):
        ca= context.camera
        vrayCamera= ca.vray
        self.layout.prop(vrayCamera, 'use_physical', text="")

    def draw(self, context):
        wide_ui= context.region.width > classes.narrowui

        ca= context.camera
        vrayCamera= ca.vray
        cameraPhysical= vrayCamera.CameraPhysical

        layout= self.layout
        layout.active= cameraPhysical.use

        split= layout.split()
        col= split.column()
        col.prop(cameraPhysical, 'type', text="Type")

        split= layout.split()
        col= split.column()
        col.label(text="Parameters:")

        split= layout.split()
        col= split.column()
        sub= col.column(align=True)
        sub.prop(cameraPhysical, 'specify_fov', text="Use FOV")
        if not cameraPhysical.specify_fov:
            sub.prop(cameraPhysical, 'film_width')
            
            subFocalLength = sub.column()
            subFocalLength.enabled = (not ca.vray.override_fov)
            subFocalLength.prop(cameraPhysical, 'focal_length')
            
            sub.prop(cameraPhysical, 'zoom_factor')

        sub= col.column(align=True)
        sub.prop(cameraPhysical, 'distortion')
        if not cameraPhysical.auto_lens_shift:
            sub.prop(cameraPhysical, 'lens_shift')
            sub.operator('vray.lens_shift')
        sub.prop(cameraPhysical, 'auto_lens_shift')

        if wide_ui:
            col= split.column(align=True)

        col.prop(cameraPhysical, 'exposure')
        col.prop(cameraPhysical, 'f_number')
        if cameraPhysical.type == '1':
            col.prop(cameraPhysical, 'shutter_angle')
            col.prop(cameraPhysical, 'shutter_offset')
        elif cameraPhysical.type == '2':
            col.prop(cameraPhysical, 'latency')
        else:
            col.prop(cameraPhysical, 'shutter_speed')
        col.prop(cameraPhysical, 'ISO')

        split= layout.split()
        col= split.column()
        col.label(text="White balance:")
        col.prop(cameraPhysical, 'white_balance', text="")

        if wide_ui:
            col= split.column()

        col.prop(cameraPhysical, 'vignetting', slider= True)

        layout.label(text="Offset:")
        split= layout.split()
        sub = split.row(align=True)
        sub.prop(ca, 'shift_x', text="X")
        sub.prop(ca, 'shift_y', text="Y")

        split= layout.split()
        colL= split.column()
        colL.label(text="Sampling:")

        split= layout.split()
        colL= split.column()
        colR= split.column(align=True)

        colL.prop(cameraPhysical, 'use_dof')
        colL.prop(cameraPhysical, 'use_moblur')

        if cameraPhysical.use_dof:
            colR.prop(cameraPhysical, 'blades_enable')

        if cameraPhysical.use_moblur or cameraPhysical.use_dof:
            colL.prop(vrayCamera.SettingsMotionBlur, 'geom_samples')

            if cameraPhysical.use_dof and cameraPhysical.blades_enable:
                colR.prop(cameraPhysical, 'blades_num')
                colR.prop(cameraPhysical, 'blades_rotation')
                colR.prop(cameraPhysical, 'center_bias')
                colR.prop(cameraPhysical, 'anisotropy')


class VRAY_PT_dome_camera(classes.VRayCameraPanel):
    bl_label   = "V-Ray Dome Camera"
    bl_options = {'DEFAULT_CLOSED'}

    def draw_header(self, context):
        vrayCamera= context.camera.vray
        self.layout.prop(vrayCamera, 'use_dome', text='')

    def draw(self, context):
        vrayCamera= context.camera.vray
        cameraDome= vrayCamera.CameraDome

        layout= self.layout
        layout.active= cameraDome.use

        split= layout.split()
        col= split.column()
        col.prop(vrayCamera, 'fov', text="FOV")
        col.prop(cameraDome, 'flip_x', text="Flip X")
        col.prop(cameraDome, 'flip_y', text="Flip Y" )


def getRegClasses():
    return (
    VRAY_PT_camera,
    VRAY_PT_physical_camera,
    VRAY_PT_dome_camera,
)


def register():
    from vray_blender.lib.class_utils import registerClass
    for regClass in getRegClasses():
        registerClass(regClass)


def unregister():
    for regClass in reversed(getRegClasses()):
        bpy.utils.unregister_class(regClass)
