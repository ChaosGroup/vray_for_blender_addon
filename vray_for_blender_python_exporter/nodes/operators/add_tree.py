
import bpy

from vray_blender import debug
from vray_blender.nodes.operators.misc import _redrawNodeEditor
from vray_blender.nodes import tree_defaults
from vray_blender.lib import blender_utils


class VRAY_OT_add_nodetree_light(bpy.types.Operator):
    bl_idname       = "vray.add_nodetree_light"
    bl_label        = "Add Light Nodetree"
    bl_description  = "Add light nodetree"
    bl_options      = {'INTERNAL'}
    
    def execute(self, context):
        from vray_blender.nodes.utils import getLightOutputNode

        light = context.object.data

        # Check for both the node tree and a valid root node as this operator
        # may be used for creating a new tree or just adding a missing root
        # node to an existing tree.
        if (light.node_tree) and (getLightOutputNode(light.node_tree)):
            return {'CANCELLED'}
        
        tree_defaults.addLightNodeTree(light)
        bpy.ops.vray.show_ntree(data='OBJECT')
        return {'FINISHED'}



class VRAY_OT_add_nodetree_object(bpy.types.Operator):
    bl_idname      = "vray.add_nodetree_object"
    bl_label       = "Add Object Nodetree"
    bl_description = "Add object nodetree"
    bl_options     = {'INTERNAL'}

    def execute(self, context):
        tree_defaults.addObjectNodeTree(context.object)
        bpy.ops.vray.show_ntree(data='OBJECT')
        return {'FINISHED'}


class VRAY_OT_add_nodetree_object_lamp(bpy.types.Operator):
    bl_idname      = "vray.add_nodetree_object_lamp"
    bl_label       = "Add Object / Light Nodetree"
    bl_description = "Add object / light nodetree"
    bl_options     = {'INTERNAL'}

    def execute(self, context):
        ob = context.object

        if ob.type in blender_utils.NonGeometryTypes:
            if ob.type == 'LIGHT':
                tree_defaults.addLightNodeTree(ob.data)
            else:
                self.report({'ERROR'}, "Object type doesn't support node tree!")
                return {'CANCELLED'}
        else:
            tree_defaults.addObjectNodeTree(ob)

        bpy.ops.vray.show_ntree(data='OBJECT')

        return {'FINISHED'}


class VRAY_OT_add_nodetree_world(bpy.types.Operator):
    bl_idname      = "vray.add_nodetree_world"
    bl_label       = "Add World Nodetree"
    bl_description = "Add world nodetree"
    bl_options     = {'INTERNAL'}

    def execute(self, context):
        tree_defaults.addWorldNodeTree(context.scene.world)
        bpy.ops.vray.show_ntree(data='WORLD')
        return {'FINISHED'}


class VRAY_OT_replace_nodetree_material(bpy.types.Operator):
    """ Replace the active material's nodetree with a V-Ray material nodetree """

    bl_idname      = "vray.replace_nodetree_material"
    bl_label       = "Replace Material Nodetree"
    bl_description = "Add material nodetree"
    bl_options     = {'INTERNAL'}

    def execute(self, context):
        if activeMtl := getattr(context.object, 'active_material'):
            # Replace the nodetree of the actve material. This case will be executed
            # when a non-vray material is converted to V-Ray, or when the operator is
            # invoked manually.
            tree_defaults.addMaterialNodeTree(activeMtl)

            _redrawNodeEditor()
            return {'FINISHED'}
        else:
            self.report({'ERROR_INVALID_CONTEXT'}, "No active material!")
            return {'CANCELLED'}

class VRAY_OT_convert_nodetree_material(bpy.types.Operator):
    """ Covnert the active material's nodetree with a V-Ray material nodetree """

    bl_idname      = "vray.convert_nodetree_material"
    bl_label       = "Convert Cycles Material"
    bl_description = "Convert Cycles Material to its V-Ray"
    bl_options     = {'INTERNAL'}

    def execute(self, context):
        from vray_blender.exporting.plugin_tracker import FakeScopedNodeTracker, FakeObjTracker
        from vray_blender.exporting.tools import FakeTimeStats
        from vray_blender.lib.defs import ExporterContext, AttrPlugin, ExporterContext, RendererMode, NodeContext, ExporterType, PluginDesc
        from vray_blender.engine import NODE_TRACKERS, OBJ_TRACKERS

        _fakeNodeTrackers = dict([(t, FakeScopedNodeTracker()) for t in NODE_TRACKERS])
        _fakeObjTrackers = dict([(t, FakeObjTracker()) for t in OBJ_TRACKERS])

        if activeMtl := getattr(context.object, 'active_material'):
            exporterContext = ExporterContext()
            exporterContext.rendererMode = RendererMode.Preview
            exporterContext.objTrackers     = _fakeObjTrackers
            exporterContext.nodeTrackers    = _fakeNodeTrackers
            exporterContext.ctx             = bpy.context
            exporterContext.fullExport      = True
            exporterContext.ts              = FakeTimeStats()

            vrsceneDict = []
            def vrsceneDictCollector(nodeCtx: NodeContext, pluginDesc: PluginDesc):
                for key, value in pluginDesc.attrs.items():
                    if isinstance(value, AttrPlugin):
                        pluginDesc.attrs[key] = value.name
                    if isinstance(value, list) and all(isinstance(item, AttrPlugin) for item in value):
                        plNames = []
                        for pl in value:
                            plNames.append(pl.name)
                        pluginDesc.attrs[key] = plNames
                vrsceneDict.append({
                    "ID"         : pluginDesc.type,
                    "Name"       : pluginDesc.name,
                    "Attributes" : pluginDesc.attrs,
                })
                return AttrPlugin(pluginDesc.name, pluginDesc.type)

            from vray_blender.nodes.operators.import_file import importMaterial
            from vray_blender.exporting.mtl_export import MtlExporter
            mtlExporter = MtlExporter(exporterContext)
            nodeCtx = NodeContext(exporterContext, bpy.context.scene, bpy.data, None)
            nodeCtx.rootObj = activeMtl
            nodeCtx.nodeTracker = _fakeNodeTrackers['MTL']
            nodeCtx.ntree = activeMtl.node_tree
            nodeCtx.customHandler = vrsceneDictCollector
            mtlSingleBRDF, _ = mtlExporter.exportMtl(activeMtl, nodeCtx)
            importMaterial(vrsceneDict, mtlSingleBRDF.name, activeMtl.node_tree)
            activeMtl.vray.is_vray_class = True

            _redrawNodeEditor()
            return {'FINISHED'}
        else:
            self.report({'ERROR_INVALID_CONTEXT'}, "No active material!")
            return {'CANCELLED'}

class VRAY_OT_add_new_material(bpy.types.Operator):
    """ Add a new vray material to the active object's material slot """

    bl_idname      = "vray.add_new_material"
    bl_label       = "Add New V-Ray Material"
    bl_description = "Add a new V-Ray material"
    bl_options     = {'INTERNAL'}
    
    def execute(self, context):
        if ob := getattr(context, 'active_object'):
            if ob.type in blender_utils.NonGeometryTypes:
                self.report({'ERROR'}, "Object type doesn't support materials!")
                return {'CANCELLED'}

            # Create a new V-Ray material
            newMtl = bpy.data.materials.new("Material")
            tree_defaults.addMaterialNodeTree(newMtl)

            # We are using the main node tree of the material to attach V-Ray nodes to. We don't have
            # control over its creation, and Blender is free to attach some default nodes to it. E.g. 
            # for Cycles/EEVEE/Workbench a Principal BRDF + Material Output are atttached.
            # We cannot export these nodes, so we remove them here and leave only V-Ray nodes in 
            # the newly created tree.
            tree_defaults.removeNonVRayNodes(newMtl.node_tree)
            
            if len(ob.material_slots) == 0:
                # Object has no material slots, add the new material to a new slot
                ob.data.materials.append(newMtl)
            else:
                # Set the new material to the active material slot
                ob.material_slots[ob.active_material_index].material = newMtl

            _redrawNodeEditor()
            return {'FINISHED'}
        else:
            self.report({'ERROR_INVALID_CONTEXT'}, "No active object!")
            return {'CANCELLED'}


class VRAY_OT_copy_material(bpy.types.Operator):
    """ Add a new vray material to the active object's material slot """

    bl_idname      = "vray.copy_material"
    bl_label       = "Add New V-Ray Material"
    bl_description = "Copy the active V-Ray material to a new one"
    bl_options     = {'INTERNAL'}
    
    def execute(self, context):
        if ((ob := getattr(context, 'active_object')) is None) or (not ob.material_slots):
            return {'CANCELLED'}
        
        if ob.type in blender_utils.NonGeometryTypes:
            self.report({'ERROR'}, "Object type doesn't support materials!")
            return {'CANCELLED'}

        mtlSlot = ob.material_slots[ob.active_material_index]
        mtlSlot.material = mtlSlot.material.copy()

        _redrawNodeEditor()
        return {'FINISHED'}


class VRAY_OT_copy_world(bpy.types.Operator):
    """ Add a new vray world node tree to the active scene """

    bl_idname      = "vray.copy_world"
    bl_label       = "Copy V-Ray World"
    bl_description = "Copy V-Ray world"
    bl_options     = {'INTERNAL'}
    
    def execute(self, context):
        if not (context.scene.world and  context.scene.world.vray.is_vray_class):
            # No world tree, or the tree is not a V-Ray tree
            return {'CANCELLED'}
        
        context.scene.world = context.scene.world.copy()
        
        _redrawNodeEditor()
        return {'FINISHED'}
    


########  ########  ######   ####  ######  ######## ########     ###    ######## ####  #######  ##    ##
##     ## ##       ##    ##   ##  ##    ##    ##    ##     ##   ## ##      ##     ##  ##     ## ###   ##
##     ## ##       ##         ##  ##          ##    ##     ##  ##   ##     ##     ##  ##     ## ####  ##
########  ######   ##   ####  ##   ######     ##    ########  ##     ##    ##     ##  ##     ## ## ## ##
##   ##   ##       ##    ##   ##        ##    ##    ##   ##   #########    ##     ##  ##     ## ##  ####
##    ##  ##       ##    ##   ##  ##    ##    ##    ##    ##  ##     ##    ##     ##  ##     ## ##   ###
##     ## ########  ######   ####  ######     ##    ##     ## ##     ##    ##    ####  #######  ##    ##

def getRegClasses():
    return (
        VRAY_OT_add_nodetree_light,
        VRAY_OT_add_nodetree_object,
        VRAY_OT_add_nodetree_object_lamp,
        VRAY_OT_replace_nodetree_material,
        VRAY_OT_convert_nodetree_material,
        VRAY_OT_add_new_material,
        VRAY_OT_copy_material,
        VRAY_OT_add_nodetree_world,
        VRAY_OT_copy_world,
    )


def register():
    for regClass in getRegClasses():
        bpy.utils.register_class(regClass)


def unregister():
    for regClass in getRegClasses():
        bpy.utils.unregister_class(regClass)
