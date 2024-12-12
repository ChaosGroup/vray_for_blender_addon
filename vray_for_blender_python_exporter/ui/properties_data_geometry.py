
import bpy

from vray_blender.ui import classes


def getContextData(context):
    if context.active_object.type == 'MESH':
        return context.mesh
    return context.curve


class VRAY_PT_tools(classes.VRayGeomPanel):
    bl_label   = "Tools"
    bl_options = {'DEFAULT_CLOSED'}

    def draw_header(self, context):
        self.layout.label(text="")

    def draw(self, context):
        layout= self.layout

        obj = context.active_object

        if obj.type != "MESH":
            return
        
        vrayMesh = obj.data.vray
        geomMeshFile= vrayMesh.GeomMeshFile

        layout.label(text="Generate VRayProxy:")
        layout.prop(geomMeshFile, 'dirpath')
        layout.prop(geomMeshFile, 'filename')
        layout.prop(geomMeshFile, 'animation')
        if geomMeshFile.animation == 'MANUAL':
            row = layout.row(align=True)
            row.prop(geomMeshFile, 'frame_start')
            row.prop(geomMeshFile, 'frame_end')
        if geomMeshFile.animation not in {'NONE'}:
            layout.prop(geomMeshFile, 'add_velocity')
        layout.prop(geomMeshFile, 'apply_transforms')
        layout.separator()

        split= layout.split()
        col= split.column()
        col.operator('vray.create_proxy', icon='OUTLINER_OB_MESH')

        layout.separator()
        layout.prop(geomMeshFile, 'proxy_attach_mode', text="Attach Mode")
        layout.prop(geomMeshFile, 'add_suffix')


def getRegClasses():
    return (
        # TODO: Enable when proxy creation is working
        # VRAY_PT_tools,
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
