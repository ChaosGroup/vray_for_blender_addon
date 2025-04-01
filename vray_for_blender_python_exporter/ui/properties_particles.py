
import bpy

from vray_blender.ui import classes


class VRAY_PT_hair(classes.VRayParticlePanel):
    bl_label   = "Fur"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return super().poll(context) and context.particle_system.settings.type == 'HAIR'

    def drawPanelCheckBox(self, context):
        self.layout.label(text="")

    def draw(self, context):
        layout= self.layout

        particle_settings= context.particle_system.settings

        VRayFur= particle_settings.vray.VRayFur

        split= layout.split()
        col= split.column()
        col.prop(VRayFur, 'width')
        col.prop(VRayFur, 'widths_in_pixels')
        col.prop(VRayFur, 'make_thinner')


def getRegClasses():
    return (
        VRAY_PT_hair,
    )


def register():
    from bl_ui import properties_particle
    from vray_blender.lib.class_utils import registerClass
    
    for member in dir(properties_particle):
        subclass = getattr(properties_particle, member)
        try:
            for compatEngine in classes.VRayEngines:
                subclass.COMPAT_ENGINES.add(compatEngine)
        except:
            pass
    del properties_particle

    for regClass in getRegClasses():
        registerClass(regClass)


def unregister():
    from bl_ui import properties_particle
    for member in dir(properties_particle):
        subclass = getattr(properties_particle, member)
        try:
            for compatEngine in classes.VRayEngines:
                subclass.COMPAT_ENGINES.remove(compatEngine)
        except:
            pass
    del properties_particle

    for regClass in reversed(getRegClasses()):
        bpy.utils.unregister_class(regClass)
