
import bpy

from vray_blender.ui import classes
from vray_blender.lib import lib_utils


class VRAY_PT_RenderChannels(classes.VRayRenderLayersPanel):
    bl_label    = "Render Channels"
    bl_icon     = "VRAY_PLACEHOLDER"

    def draw(self, context):
        from vray_blender.nodes.nodes import VRayNodeTypes # circular import protection
        from vray_blender.plugins.channel.RenderChannelsPanel import VRayChannelNodeSubtypes # circular import protection

        hasWorldNodeTree = context.scene.world is not None and context.scene.world.node_tree is not None

        # Indication that there isn't a node tree created
        if not hasWorldNodeTree:
            self.layout.column().label(icon="ERROR", text="World node tree required.")

        if not context.scene.world:
            # If for some reason there is no world in the scene, show
            # the UI for creating one as the render channels that will
            # be shown are a property of the world.
            self.layout.operator('vray.add_nodetree_world', text="Create a V-Ray World Node Tree.")
            return
        
        vrayRenderChannels = context.scene.world.vray.VRayRenderChannels
        
        # LightMix and Denoiser channels are displayed separately
        # until a better solution for their representation is found.
        split = self.layout.split(factor=0.02)
        split.column()
        col = split.column()
        for channel in ("LightMix", "Denoiser"):
            col.prop(getattr(vrayRenderChannels, f"VRayNodeRenderChannel{channel}"), "enabled")

        for menuType in VRayChannelNodeSubtypes:
            menuName = menuType.title()
            # Drawing the rollout menu for the current render elements type
            isOpen      = getattr(vrayRenderChannels, menuName) and hasWorldNodeTree
            icon        = "DOWNARROW_HLT" if isOpen else "RIGHTARROW"
            titleRow = self.layout.row()
            titleRow.alignment = 'LEFT'
            titleRow.prop(vrayRenderChannels, menuName, icon=icon, emboss=False)
            titleRow.active = hasWorldNodeTree

            if not isOpen:
                # Rollout is closed
                continue

            split = self.layout.split(factor=0.02)
            split.column()
            col = split.column()
            for t in VRayNodeTypes["RENDERCHANNEL"]:
                if getattr(t, "vray_menu_subtype","") == menuType:
                    elemType = t.bl_rna.identifier
                    col.prop(getattr(vrayRenderChannels, elemType), "enabled")
            



# View Layer settings that lets the user choose which layer should be rendered
# It is mimicking the same "View Layer" panels for EVEE and Cycles
class VRAY_PT_Viewlayer(classes.VRayRenderLayersPanel):
    bl_label = "View Layer"

    def draw(self, context):
        layout = self.layout

        layout.use_property_split = True

        scene = context.scene
        rd = scene.render
        layer = context.view_layer

        col = layout.column()
        col.prop(layer, "use", text="Use for Rendering")
        col.prop(rd, "use_single_layer", text="Render Single Layer")


class VRAY_PT_Materials(classes.VRayRenderLayersPanel):
    bl_label   = "Scene Materials"
    bl_options = {'DEFAULT_CLOSED'}

    def getMaterial(self, context):
        VRayExporter = context.scene.vray.Exporter

        listIndex = VRayExporter.materialListIndex if VRayExporter.materialListIndex >= 0 else 0
        numMaterials = len(bpy.data.materials)

        if numMaterials:
            if listIndex >= numMaterials:
                listIndex = 0
                VRayExporter.materialListIndex = 0

            return bpy.data.materials[listIndex]

    def draw(self, context):
        VRayExporter = context.scene.vray.Exporter

        if context.scene.render.engine in {'VRAY_RENDER_PREVIEW', 'VRAY_RENDER_RT'}:
            expandIcon = 'TRIA_DOWN' if VRayExporter.materialListShowPreview else 'TRIA_RIGHT'

            box = self.layout.box()
            row = box.row(align=True)
            row.prop(VRayExporter, 'materialListShowPreview', text="",  icon=expandIcon, emboss=False)
            row.label(text="Show Preview")

            if VRayExporter.materialListShowPreview:
                    material = self.getMaterial(context)
                    if material:
                        box.template_preview(material, show_buttons=True)

        self.layout.operator('vray.new_material', text="New Material", icon='MATERIAL')

        self.layout.template_list("VRAY_UL_Materials", "", bpy.data, 'materials', VRayExporter, 'materialListIndex', rows=15)
        self.layout.separator()

        material = self.getMaterial(context)
        if material:
            split = self.layout.split()

            col = split.column()
            col.prop(material, 'name')

            row = col.row(align=True)
            row.label(text="Node Tree:")

            op = row.operator("vray.sync_ntree_name", icon='SYNTAX_OFF', text="")
            op.materialName = material.name

            row.prop(material, 'node_tree', text="", icon='NODETREE')


class VRAY_PT_LightLister(classes.VRayRenderLayersPanel):
    bl_label   = "Lights"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout= self.layout

        split= layout.split()
        col= split.column()

        if bpy.data.lights:
            for lamp in bpy.data.lights:
                lightPluginType = lib_utils.getLightPluginType(lamp)
                lightPropGroup = getattr(lamp.vray, lightPluginType)

                sub_t = col.row()
                sub_t.label(text=" %s" % lamp.name, icon='LIGHT_%s' % lamp.type)

                if lightPluginType == 'SunLight':
                    sub = col.row()
                    sub.prop(lightPropGroup, 'enabled', text="")

                    r = sub.row()
                    r.scale_x = 0.4
                    r.prop(lightPropGroup, 'filter_color',   text="")

                    sub.prop(lightPropGroup, 'intensity_multiplier', text="")
                    sub.prop(lightPropGroup, 'shadow_subdivs',       text="")

                else:
                    sub = col.row()
                    sub.prop(lightPropGroup, 'enabled', text="")
                    sub.prop(lightPropGroup, 'color',   text="")

                    if hasattr(lightPropGroup, 'intensity'):
                        sub.prop(lightPropGroup, 'intensity', text="")
                    if hasattr(lightPropGroup, 'power'):
                        sub.prop(lightPropGroup, 'power', text="")
                    if hasattr(lightPropGroup, 'subdivs'):
                        sub.prop(lightPropGroup, 'subdivs', text="")
                    if hasattr(lightPropGroup, 'shadowSubdivs'):
                        sub.prop(lightPropGroup, 'shadowSubdivs', text="")
        else:
            col.label(text="Nothing in bpy.data.lights...")


class VRAY_PT_Includer(classes.VRayRenderLayersPanel):
    bl_label   = "Include *.vrscene"
    bl_options = {'DEFAULT_CLOSED'}

    def drawPanelCheckBox(self, context):
        VRayScene = context.scene.vray
        Includer  = VRayScene.Includer
        self.layout.prop(Includer, 'use', text="")

    def draw(self, context):
        layout= self.layout

        row= layout.row()

        vs= context.scene.vray
        module= vs.Includer

        layout.active= module.use

        row.template_list("VRAY_UL_Use", "", module, 'nodes', module, 'nodes_selected', rows=5)

        col= row.column()
        sub= col.row()
        subsub= sub.column(align=True)
        subsub.operator('vray.includer_add',    text="", icon="ADD")
        subsub.operator('vray.includer_remove', text="", icon="REMOVE")
        sub= col.row()
        subsub= sub.column(align=True)
        subsub.operator("vray.includer_up",   icon='TRIA_UP',   text="")
        subsub.operator("vray.includer_down", icon='TRIA_DOWN', text="")

        if module.nodes_selected >= 0 and len(module.nodes) > 0:
            render_node= module.nodes[module.nodes_selected]

            layout.separator()

            layout.prop(render_node, 'name')
            layout.prop(render_node, 'scene')



def getRegClasses():
    return (
        VRAY_PT_RenderChannels,
        VRAY_PT_Viewlayer,
        # TODO: Fix the functionality of the following panels
        # VRAY_PT_Materials,
        # VRAY_PT_LightLister,
        # VRAY_PT_Includer,
    )


def register():
    from vray_blender.lib.class_utils import registerClass
    for regClass in getRegClasses():
        registerClass(regClass)


def unregister():
    for regClass in reversed(getRegClasses()):
        bpy.utils.unregister_class(regClass)
