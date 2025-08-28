
import bpy

from vray_blender.lib.mixin import VRayOperatorBase

TYPE = 'SETTINGS'
ID   = 'Includer'
NAME = 'Includer'
DESC = "Include additional vrscene files"


class IncluderList(bpy.types.PropertyGroup):
    scene: bpy.props.StringProperty(
        name = "Filepath",
        subtype = 'FILE_PATH',
        description = "Path to a *.vrscene file"
    )

    use: bpy.props.BoolProperty(
        name = "",
        description = "Use scene",
        default = True
    )


class Includer(bpy.types.PropertyGroup):
    use: bpy.props.BoolProperty(
        name        = "Use Includer",
        description = "Add additional *.vrscene files",
        default     = False
    )

    nodes: bpy.props.CollectionProperty(
        name = "Scene Name",
        type =  IncluderList,
        description = "Custom name scene"
    )

    nodes_selected: bpy.props.IntProperty(
        name = "Scene Index",
        default = -1,
        min = -1,
        max = 100
    )


class VRAY_OT_includer_add(VRayOperatorBase):
    bl_idname=      'vray.includer_add'
    bl_label=       "Add Include"
    bl_description= "Add Include *.vrsene"
    bl_options     = {'INTERNAL'}

    def execute(self, context):
        vs= context.scene.vray
        module= vs.Includer

        module.nodes.add()
        module.nodes[-1].name= "Include Scene"

        return {'FINISHED'}


class VRAY_OT_includer_remove(VRayOperatorBase):
    bl_idname=      'vray.includer_remove'
    bl_label=       "Remove Include"
    bl_description= "Remove Include *.vrsene"
    bl_options     = {'INTERNAL'}

    def execute(self, context):
        vs= context.scene.vray
        module= vs.Includer

        if module.nodes_selected >= 0:
           module.nodes.remove(module.nodes_selected)
           module.nodes_selected-= 1

        return {'FINISHED'}


class VRAY_OT_includer_up(VRayOperatorBase):
    bl_idname=      'vray.includer_up'
    bl_label=       "Up Include"
    bl_description= "Up Include *.vrsene"
    bl_options     = {'INTERNAL'}

    def execute(self, context):
        vs= context.scene.vray
        module= vs.Includer

        if module.nodes_selected <= 0:
            return {'CANCELLED'}

        module.nodes.move(module.nodes_selected,
                                 module.nodes_selected - 1)
        module.nodes_selected-= 1

        return {'FINISHED'}


class VRAY_OT_includer_down(VRayOperatorBase):
    bl_idname=      'vray.includer_down'
    bl_label=       "Down Include"
    bl_description= "Down Include *.vrsene"
    bl_options     = {'INTERNAL'}
    
    def execute(self, context):
        vs= context.scene.vray
        module= vs.Includer

        if module.nodes_selected < 0 or module.nodes_selected >= len(module.nodes) - 1:
            return {'CANCELLED'}

        module.nodes.move(module.nodes_selected,
                                 module.nodes_selected + 1)
        module.nodes_selected+= 1

        return {'FINISHED'}


def getRegClasses():
    return (
        IncluderList,
        Includer,

        VRAY_OT_includer_add,
        VRAY_OT_includer_remove,
        VRAY_OT_includer_up,
        VRAY_OT_includer_down,
    )


def register():
    for regClass in getRegClasses():
        bpy.utils.register_class(regClass)

    from vray_blender import plugins

    plugins.VRayScene.__annotations__['Includer'] = bpy.props.PointerProperty(
        name = "Includes",
        type =  Includer,
        description = "Include additional *.vrscene files"
    )


def unregister():
    for regClass in getRegClasses():
        bpy.utils.unregister_class(regClass)
